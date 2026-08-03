"""Compare recorded agent answers against the gold set using an LLM judge.

This is the CHEAP, REPEATABLE step: it reads the gold set and the agent
outputs already recorded by scripts/run_agent_on_gold.py and never touches
retrieval or generation — only settings.llm is loaded, to act as judge. That
means you can tweak the judge prompt/thresholds and re-run this as many times
as you like without re-running the (expensive) pipeline.

Output: a JSON comparison file (for tooling) and a Markdown report (for
humans), plus the same Markdown printed to stdout.

Gold questions are numbered (see the "id" field in data/eval/gold_qa.json, e.g.
"1", "2", "13"). Use --id to target specific ones instead of the whole set.

Usage:
    python scripts/compare_with_judge.py
    python scripts/compare_with_judge.py --gold data/eval/gold_qa.json --agent-results data/eval/agent_results.json
    python scripts/compare_with_judge.py --limit 3
    python scripts/compare_with_judge.py --id 13,14,15   # only these ids (comma-separated)
    python scripts/compare_with_judge.py --id 13 --id 14 # or repeat the flag — same effect
        # The Markdown/JSON report still covers every question previously judged —
        # this does NOT re-judge the rest.
"""

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "src")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.llm.base import Message  # noqa: E402
from gernas_rag.llm.factory import get_llm  # noqa: E402

_JUDGE_SYSTEM_PROMPT = (
    "You are a strict grading assistant for a RAG system evaluation. You will be given "
    "a QUESTION, a GOLD ANSWER (reference facts), and an AGENT ANSWER produced by the "
    "system under test.\n\n"
    "Grade the AGENT ANSWER on whether the facts it states are CORRECT, not on whether it "
    "restates every detail present in the GOLD ANSWER. The GOLD ANSWER may include "
    "supplementary facts beyond what the QUESTION literally asks — do not penalize the "
    "AGENT ANSWER for omitting a fact the QUESTION did not ask for. Ignore differences in "
    "phrasing, formatting, citation markers, and minor rounding/unit-formatting.\n\n"
    "- CORRECT: every fact the AGENT ANSWER states in response to the QUESTION is accurate "
    "and consistent with the GOLD ANSWER. Omitting a bonus/unrequested detail from the GOLD "
    "ANSWER is fine and still CORRECT.\n"
    "- PARTIALLY_CORRECT: the AGENT ANSWER addresses the question but gets at least one "
    "fact wrong, hedges instead of answering, or omits a fact the QUESTION explicitly asked "
    "for (as opposed to a bonus detail only the GOLD ANSWER added).\n"
    "- INCORRECT: the AGENT ANSWER contradicts the GOLD ANSWER on the core fact(s) asked, "
    "fabricates information, or fails to answer the question at all.\n\n"
    "Respond in EXACTLY this format, nothing else:\n"
    "VERDICT: <CORRECT|PARTIALLY_CORRECT|INCORRECT>\n"
    "REASON: <one or two sentences>"
)

_VERDICT_RE = re.compile(r"VERDICT:\s*(CORRECT|PARTIALLY_CORRECT|INCORRECT)", re.IGNORECASE)
_REASON_RE = re.compile(r"REASON:\s*(.+)", re.IGNORECASE | re.DOTALL)


def _clause_match(expected: str, actual: str) -> bool:
    """Tolerant clause match: exact, or one is a dot-boundary prefix of the other.

    Docling-derived clause labels aren't always normalized the same way twice, so
    exact-only matching would under-count genuine hits. The dot boundary matters:
    a plain substring test counts "2.1" as a hit against "12.14" and inflates the
    retrieval hit rate.

    NOTE: clause_reference is derived from chunk text (and for media chunks from
    transcribed caption text), so it is not a reliable identity key at all — see
    eval/stage2_enrichment/integrity.py. The eval/ suite matches on chunk_id
    instead and supersedes this script; this fix only stops the legacy number
    from being actively misleading.
    """
    if not expected or not actual:
        return expected == actual
    e, a = expected.strip().lower(), actual.strip().lower()
    if e == a:
        return True
    for short, long in ((e, a), (a, e)):
        if long.startswith(short) and long[len(short):].startswith("."):
            return True
    return False


async def _judge(llm, question: str, gold_answer: str, agent_answer: str) -> dict:
    messages = [
        Message(role="system", content=_JUDGE_SYSTEM_PROMPT),
        Message(
            role="user",
            content=f"QUESTION: {question}\n\nGOLD ANSWER: {gold_answer}\n\nAGENT ANSWER: {agent_answer}",
        ),
    ]
    raw = await llm.generate(messages)
    verdict_match = _VERDICT_RE.search(raw)
    reason_match = _REASON_RE.search(raw)
    return {
        "verdict": verdict_match.group(1).upper() if verdict_match else "UNKNOWN",
        "reason": reason_match.group(1).strip() if reason_match else raw.strip(),
        "raw": raw,
    }


async def run(
    gold_path: Path, agent_results_path: Path, limit: int | None, ids: list[str] | None
) -> list[dict]:
    gold_set = json.loads(gold_path.read_text(encoding="utf-8"))
    agent_results = {r["id"]: r for r in json.loads(agent_results_path.read_text(encoding="utf-8"))}

    if ids:
        by_id = {item["id"]: item for item in gold_set}
        missing_ids = [i for i in ids if i not in by_id]
        if missing_ids:
            raise SystemExit(f"Unknown gold question id(s): {missing_ids}")
        gold_set = [by_id[i] for i in ids]
    elif limit:
        gold_set = gold_set[:limit]

    missing = [item["id"] for item in gold_set if item["id"] not in agent_results]
    if missing:
        raise SystemExit(
            f"{len(missing)} gold question(s) have no recorded agent output: {missing}\n"
            f"Run scripts/run_agent_on_gold.py first (or check --agent-results points at the right file)."
        )

    settings = get_settings()
    llm = get_llm(settings.llm)

    results: list[dict] = []
    for item in gold_set:
        agent = agent_results[item["id"]]

        source_checks = []
        for expected in item["expected_sources"]:
            hit = any(
                r["document"] == expected["document"] and _clause_match(expected["clause_reference"], r["clause_reference"])
                for r in agent["retrieved_sources"]
            )
            source_checks.append({"expected": expected, "hit": hit})

        judged = await _judge(llm, item["question"], item["expected_answer"], agent["agent_answer"])

        results.append(
            {
                "id": item["id"],
                "name": item.get("name", ""),
                "question": item["question"],
                "expected_answer": item["expected_answer"],
                "agent_answer": agent["agent_answer"],
                "source_checks": source_checks,
                "retrieved_sources": agent["retrieved_sources"],
                "judge": judged,
            }
        )
        print(f"  done: {item['id']}  retrieval_hit={all(s['hit'] for s in source_checks)}  verdict={judged['verdict']}")

    return results


def _build_markdown(results: list[dict], gold_path: Path, agent_results_path: Path) -> str:
    total = len(results)
    retrieval_hits = sum(1 for r in results if all(s["hit"] for s in r["source_checks"]))
    verdict_counts = {"CORRECT": 0, "PARTIALLY_CORRECT": 0, "INCORRECT": 0, "UNKNOWN": 0}
    for r in results:
        verdict_counts[r["judge"]["verdict"]] = verdict_counts.get(r["judge"]["verdict"], 0) + 1
    pass_count = verdict_counts["CORRECT"] + verdict_counts["PARTIALLY_CORRECT"]

    lines: list[str] = []
    lines.append("# GERNAS RAG — Gold Set Evaluation Report")
    lines.append("")
    lines.append(f"- Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- Gold set: `{gold_path}` ({total} questions)")
    lines.append(f"- Agent results: `{agent_results_path}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Questions evaluated | {total} |")
    lines.append(f"| Retrieval hit rate | {retrieval_hits}/{total} ({retrieval_hits / total:.1%}) |")
    lines.append(f"| Verdict: CORRECT | {verdict_counts['CORRECT']} |")
    lines.append(f"| Verdict: PARTIALLY_CORRECT | {verdict_counts['PARTIALLY_CORRECT']} |")
    lines.append(f"| Verdict: INCORRECT | {verdict_counts['INCORRECT']} |")
    if verdict_counts["UNKNOWN"]:
        lines.append(f"| Verdict: UNKNOWN (judge parse failure) | {verdict_counts['UNKNOWN']} |")
    lines.append(f"| Overall pass rate (CORRECT + PARTIALLY_CORRECT) | {pass_count}/{total} ({pass_count / total:.1%}) |")
    lines.append("")
    lines.append("## Per-question results")
    lines.append("")

    for i, r in enumerate(results, start=1):
        overall_hit = all(s["hit"] for s in r["source_checks"])
        hit_mark = "✅" if overall_hit else "❌"
        verdict = r["judge"]["verdict"]
        verdict_mark = {"CORRECT": "✅", "PARTIALLY_CORRECT": "⚠️", "INCORRECT": "❌"}.get(verdict, "❔")

        label = f"#{r['id']}" + (f" · {r['name']}" if r.get("name") else "")
        lines.append(f"### {i}. {label} {verdict_mark} {verdict} · retrieval {hit_mark}")
        lines.append("")
        lines.append(f"**Question:** {r['question']}")
        lines.append("")
        lines.append("**Expected source(s):**")
        for s in r["source_checks"]:
            e = s["expected"]
            mark = "HIT" if s["hit"] else "MISS"
            lines.append(f"- [{mark}] `{e['document']}` · clause `{e['clause_reference']}` · modality `{e['modality']}`")
        lines.append("")
        lines.append("**Retrieved source(s):**")
        if r["retrieved_sources"]:
            for rs in r["retrieved_sources"]:
                lines.append(
                    f"- `{rs['document']}` · clause `{rs['clause_reference']}` · modality `{rs['modality']}` · score {rs['score']:.3f}"
                )
        else:
            lines.append("- (none retrieved)")
        lines.append("")
        lines.append(f"**Gold answer:** {r['expected_answer']}")
        lines.append("")
        lines.append(f"**Agent answer:** {r['agent_answer']}")
        lines.append("")
        lines.append(f"**Judge reasoning:** {r['judge']['reason']}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


async def main(
    gold_path: Path,
    agent_results_path: Path,
    limit: int | None,
    ids: list[str] | None,
    output_json: Path,
    output_md: Path,
) -> None:
    print(f"Judging {agent_results_path} against {gold_path}" + (f", ids={ids}" if ids else "") + "...")
    new_results = await run(gold_path, agent_results_path, limit, ids)

    # Merge into whatever's already judged, keyed by id, so re-judging one or a
    # few questions never wipes out previously judged results for the rest.
    existing: dict[str, dict] = {}
    if output_json.exists():
        existing = {r["id"]: r for r in json.loads(output_json.read_text(encoding="utf-8"))}
    for r in new_results:
        existing[r["id"]] = r

    gold_order = [item["id"] for item in json.loads(gold_path.read_text(encoding="utf-8"))]
    merged = [existing[i] for i in gold_order if i in existing]

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print(f"\nJudged {len(new_results)} question(s); {len(merged)} total in {output_json}")

    markdown = _build_markdown(merged, gold_path, agent_results_path)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(markdown, encoding="utf-8")
    print(f"Markdown report written to {output_md}\n")

    print(markdown)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=Path("data/eval/gold_qa.json"))
    parser.add_argument("--agent-results", type=Path, default=Path("data/eval/agent_results.json"))
    parser.add_argument("--limit", type=int, default=None, help="Only judge the first N gold questions.")
    parser.add_argument(
        "--id",
        action="append",
        dest="ids",
        default=None,
        help="Only judge this gold question id, e.g. --id 13 or --id 13,14,15. Repeatable. Takes priority over --limit.",
    )
    parser.add_argument("--output-json", type=Path, default=Path("data/eval/comparison_results.json"))
    parser.add_argument("--output-md", type=Path, default=Path("data/eval/eval_report.md"))
    args = parser.parse_args()
    ids = [i for raw in args.ids for i in raw.split(",")] if args.ids else None
    asyncio.run(
        main(args.gold, args.agent_results, args.limit, ids, args.output_json, args.output_md)
    )
