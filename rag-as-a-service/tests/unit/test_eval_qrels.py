"""Unit tests for unit-family matching and the stage 3 relevance auto-grader.

These two things decide what "relevant" and "grounded" mean for the whole suite,
so they get direct tests: a grader that quietly stops matching table cells makes
every retrieval metric look like a regression, and one that matches too eagerly
makes a broken retriever look fine.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The eval package lives at the repo root, next to src/ and tests/.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from eval.core.corpus import ChunkIndex, IndexedChunk  # noqa: E402
from eval.core.numeric import (  # noqa: E402
    compare_numeric,
    unit_compatible,
    unsupported_facts,
)
from eval.stage3_retrieval.qrels import derive  # noqa: E402


# ══════════════════════════════════════════════════════════════════════
# Unit-family compatibility
# ══════════════════════════════════════════════════════════════════════
class TestUnitCompatibility:
    """A table cell carries no unit — the unit lives in the column header.

    Requiring both sides to name the same family turns every table lookup into a
    false miss, which is what scored a correct answer as ungrounded and a correct
    chunk as irrelevant.
    """

    def test_identical_families_match(self):
        assert unit_compatible("bps", "bps")
        assert unit_compatible("", "")

    def test_unitless_side_matches_anything(self):
        assert unit_compatible("ccy:aed", "")  # "AED 2.0 billion" vs a "2.0bn" cell
        assert unit_compatible("", "pct")  # an axis reading "21" vs "21%"

    def test_two_different_units_never_match(self):
        # The stage 2b case that must keep failing: same value, wrong unit.
        assert not unit_compatible("bps", "pct")
        assert not unit_compatible("ccy:aed", "ccy:usd")

    def test_dates_never_pair_with_a_bare_number(self):
        # Dates are encoded as the integer YYYYMMDD, so a unit-less pairing would
        # let an unrelated large number satisfy a date.
        assert not unit_compatible("date", "")
        assert not unit_compatible("", "date_md")

    def test_unit_error_still_fails_in_both_modes(self):
        assert compare_numeric("50 bps", "0.5%").recall == 0.0
        assert compare_numeric("50 bps", "0.5%", strict_units=False).recall == 0.0

    def test_lenient_comparison_matches_a_table_cell(self):
        text = "the limit row reads 2.0bn"
        assert compare_numeric("AED 2.0 billion", text).recall == 0.0
        assert compare_numeric("AED 2.0 billion", text, strict_units=False).recall == 1.0

    def test_grounding_accepts_an_answer_restating_a_table_row(self):
        answer = "The cap is AED 3.5 billion."
        sources = ["| Corp-IG (AAA-BBB) | 3.5bn | 14% T1 |"]
        assert unsupported_facts(answer, sources)[0] == ["AED 3.5 billion"]
        assert unsupported_facts(answer, sources, strict_units=False)[0] == []

    def test_hallucinated_value_is_still_unsupported(self):
        answer = "The cap is AED 9.9 billion."
        sources = ["| Corp-IG (AAA-BBB) | 3.5bn | 14% T1 |"]
        assert unsupported_facts(answer, sources, strict_units=False)[0] == ["AED 9.9 billion"]

    def test_shared_unit_suffix_notation_is_not_a_caption_defect(self):
        # A transcription gives the unit to the last value only; the caption
        # attaches it to each. Strict matching reports the same numbers as both
        # missing and hallucinated — the stage 2b failure this fixes.
        transcription = "AAA/AA — 65/80/100/130 bps"
        caption = "AAA/AA: 65 bps, 80 bps, 100 bps, 130 bps"
        assert compare_numeric(transcription, caption).recall < 1.0
        lenient = compare_numeric(transcription, caption, strict_units=False)
        assert lenient.recall == 1.0
        assert lenient.extra == []


class TestWeakFactFiltering:
    """Unit-less small integers are labels, not measured quantities."""

    def test_labels_are_weak_but_units_and_large_values_are_not(self):
        from eval.core.numeric import is_weak_fact

        assert is_weak_fact(("", "3"))  # "Tier 3"
        assert not is_weak_fact(("pct", "3"))  # "3%"
        assert not is_weak_fact(("", "215"))  # a bare axis reading
        assert not is_weak_fact(("bps", "5"))  # small, but carries a unit

    def test_tier_labels_do_not_count_as_missing_quantities(self):
        transcription = "Tier 1 HIGH, Tier 2 MEDIUM, Tier 3 LOW. Re-validation within 6 months."
        caption = "High, Medium and Low risk tiers; re-validation within 6 months."
        assert compare_numeric(transcription, caption).recall < 1.0
        assert compare_numeric(transcription, caption, drop_weak=True).recall == 1.0

    def test_dropping_weak_facts_never_hides_a_real_value(self):
        transcription = "The floor is 165 bps and utilisation is 18.4%."
        caption = "The floor is 165 bps."
        result = compare_numeric(transcription, caption, strict_units=False, drop_weak=True)
        assert result.missing == ["18.4%"]


# ══════════════════════════════════════════════════════════════════════
# Stage 3 qrels auto-grader
# ══════════════════════════════════════════════════════════════════════
def _chunk(chunk_id: str, text: str, document: str = "DocA", clause: str = "") -> IndexedChunk:
    return IndexedChunk(
        chunk_id=chunk_id,
        text=text,
        document=document,
        modality="text",
        clause_reference=clause,
        section_heading="",
        artifact_ref=None,
        source_page=1,
        bbox=None,
        enrichment_model=None,
        parent_chunk_id=None,
        effective_date="",
        is_parent=False,
        deprecated=False,
    )


class TestQrelsAutoGrader:
    """Judgments come from the gold answer, never from retriever output."""

    def _gold(self, **overrides):
        item = {
            "id": "1",
            "name": "pricing",
            "question": "What is the drawn margin floor for a BB-rated revolving facility?",
            "expected_answer": "160 bps over FTP, with a 40 bps commitment fee.",
            "expected_sources": [{"document": "DocA"}],
        }
        item.update(overrides)
        return [item]

    def test_chunk_carrying_the_gold_facts_grades_highest(self):
        index = ChunkIndex(
            [
                _chunk("hit", "RCF pricing: drawn margin floor 160 bps, commitment fee 40 bps."),
                _chunk("miss", "This policy was approved by the board in March."),
            ]
        )
        question = derive(self._gold(), index)[0].questions[0]
        assert question.graded["hit"] == 3
        assert "miss" not in question.relevant_ids
        # Evidence names the facts, which is what makes a judgment auditable.
        assert "160 bps" in question.judgments[0].evidence

    def test_stale_clause_reference_cannot_make_a_chunk_relevant(self):
        # The exact defect that put title pages in the old judgment sets.
        index = ChunkIndex(
            [_chunk("stale", "Title page. Document reference FAB-POL-2024.", clause="2.4")]
        )
        gold = self._gold(
            expected_sources=[{"document": "DocA", "clause_reference": "2.4"}]
        )
        assert "stale" not in derive(gold, index)[0].questions[0].relevant_ids

    def test_grades_stay_inside_the_expected_document(self):
        index = ChunkIndex(
            [_chunk("elsewhere", "Drawn margin floor 160 bps and 40 bps fee.", document="DocB")]
        )
        assert not derive(self._gold(), index)[0].questions[0].relevant_ids

    def test_answer_keys_grade_a_question_with_no_quantities(self):
        gold = self._gold(
            expected_answer="A human reviewer must perform an independent assessment.",
            answer_keys=["independent assessment", "human reviewer"],
        )
        index = ChunkIndex(
            [
                _chunk("hit", "The human reviewer shall carry out an independent assessment."),
                _chunk("partial", "The human reviewer signs the credit file."),
            ]
        )
        question = derive(gold, index)[0].questions[0]
        assert question.graded["hit"] == 3
        assert question.graded["partial"] == 2
        assert question.basis == "answer_keys"

    def test_weak_bare_integers_do_not_make_the_corpus_relevant(self):
        # "Tier 1 / Tier 2 / Tier 3" must not match every chunk containing a 1.
        gold = self._gold(expected_answer="Tier 1, Tier 2 and Tier 3 models.")
        index = ChunkIndex([_chunk("unrelated", "Section 1 covers 2 topics across 3 pages.")])
        assert derive(gold, index)[0].questions[0].basis != "numeric"

    def test_coincidental_number_overlap_alone_does_not_grade_a_chunk_relevant(self):
        # Regression: an EIBOR/liquidity-premium table sharing "20 bps" and
        # "40 bps" with an RCF drawn-margin-floor question's gold answer was
        # graded relevant purely on that overlap, despite being about a
        # completely different topic. Coverage alone must not be enough —
        # some restatement of the answer's own wording is required too.
        gold = self._gold(
            expected_answer="160 bps drawn margin floor and 40 bps commitment fee floor for RCFs."
        )
        index = ChunkIndex(
            [
                _chunk(
                    "unrelated_numbers",
                    "Tenor Bucket EIBOR Tenor Liquidity Premium: 20 bps. Tenor Premium: 40 bps. "
                    "Treasury pricing portal confirms indicative FTP rates for syndicated loans.",
                ),
                _chunk(
                    "hit",
                    "Revolving credit facilities: 160 bps drawn margin floor, 40 bps commitment "
                    "fee floor.",
                ),
            ]
        )
        question = derive(gold, index)[0].questions[0]
        assert "unrelated_numbers" not in question.relevant_ids
        assert question.graded["hit"] == 3

    def test_strong_lexical_restatement_grades_relevant_even_without_the_gold_number(self):
        # Regression: the CBUAE Article 5.1 chunk stating the human-in-the-loop
        # requirement almost word-for-word was left completely unjudged, because
        # the gold answer's "25%, 90 days" figures actually belong to the
        # adjacent Article 5.2 clause, not this one. The chunk is still a
        # legitimate source for the qualitative part of the answer.
        gold = self._gold(
            expected_answer=(
                "Mandatory human-in-the-loop review — a human reviewer must have full "
                "access to the inputs and reasoning, perform a floor check and independent "
                "assessment, and approve or override the recommendation; an override rate "
                "above 25% over a rolling 90 days escalates to the AI Risk Officer."
            )
        )
        index = ChunkIndex(
            [
                _chunk(
                    "right_clause_no_numbers",
                    "Mandatory human-in-the-loop review: a human reviewer must have full "
                    "access to the inputs and reasoning, perform a floor check and "
                    "independent assessment, and approve or override the recommendation.",
                )
            ]
        )
        question = derive(gold, index)[0].questions[0]
        assert question.graded.get("right_clause_no_numbers", 0) >= 2
        assert question.basis == "numeric_lexical"

    def test_a_question_nothing_matches_is_flagged_not_silently_dropped(self):
        index = ChunkIndex([_chunk("only", "Unrelated prose about branch opening hours.")])
        qrels, warnings = derive(self._gold(), index)
        assert warnings and "[1]" in warnings[0]
        assert qrels.questions[0].basis in ("fallback_best", "none")

    def test_context_grade_is_judged_so_it_counts_against_the_unjudged_rate(self):
        index = ChunkIndex(
            [
                _chunk("hit", "Drawn margin floor 160 bps, commitment fee 40 bps."),
                _chunk("ctx", "Revolving facility drawn margin commitment terms, rated clients."),
            ]
        )
        question = derive(self._gold(), index)[0].questions[0]
        assert "ctx" in question.judged_ids
        assert question.judged_ids >= question.relevant_ids

    def test_derivation_never_inspects_retriever_output(self):
        # Guard against reintroducing circularity: derive() takes only the gold
        # set and the index. If a run/ranking argument ever appears here, the
        # metric stops being independent of the thing it measures.
        import inspect

        assert set(inspect.signature(derive).parameters) == {"gold", "index"}


# ══════════════════════════════════════════════════════════════════════
# Stage 3 scoring
# ══════════════════════════════════════════════════════════════════════
def _run(ranked: list[str], question_id: str = "1", texts: dict[str, str] | None = None):
    """A minimal RetrievalRun holding one question's served ordering."""
    from eval.core.models import RankedHit, RetrievalRun, RetrievalRunRecord

    texts = texts or {}
    hits = [
        RankedHit(chunk_id=c, rank=i, score=1.0 - i / 100, text=texts.get(c, ""))
        for i, c in enumerate(ranked)
    ]
    return RetrievalRun(
        records=[
            RetrievalRunRecord(id=question_id, question="q?", hits=hits, latency_ms=12.0)
        ]
    )


def _qrels(graded: dict[str, int], question_id: str = "1", expected_answer: str = ""):
    from eval.core.models import QrelQuestion, QrelSet, RelevanceJudgment

    return QrelSet(
        questions=[
            QrelQuestion(
                id=question_id,
                name="q",
                question="q?",
                basis="numeric",
                expected_answer=expected_answer,
                judgments=[
                    RelevanceJudgment(chunk_id=cid, grade=g) for cid, g in graded.items()
                ],
            )
        ]
    )


def _report():
    from eval.core.models import StageReport

    return StageReport(stage="stage3_retrieval", title="t")


class TestStage3Scoring:
    """Scoring needs a recorded run, so it gets a synthetic one — no embedder,
    no cross-encoder, no Qdrant."""

    def _metrics(self, report):
        return {m.name: m.value for m in report.metrics}

    def test_every_requested_metric_is_reported(self):
        from eval.stage3_retrieval.scoring import score

        report = _report()
        score(_run(["a", "b", "c", "d", "e"]), _qrels({"a": 3, "b": 1}), report)
        assert set(self._metrics(report)) == {
            "hit_rate_at_5",
            "hit_rate_at_10",
            "recall_at_5",
            "recall_at_10",
            "mrr",
            "precision_at_5",
            "context_precision_at_10",
            "context_recall",
            "unjudged_rate_at_5",
        }
        assert not report.errors

    def test_perfect_retrieval_scores_full_marks(self):
        from eval.stage3_retrieval.scoring import score

        report = _report()
        score(_run(["a", "x", "y"]), _qrels({"a": 3}), report)
        m = self._metrics(report)
        assert m["hit_rate_at_5"] == 1.0
        assert m["recall_at_5"] == 1.0
        assert m["mrr"] == 1.0

    def test_hit_rate_and_recall_diverge_on_multi_chunk_answers(self):
        from eval.stage3_retrieval.scoring import score

        # Two answer-bearing chunks, only one retrieved: found *an* answer, but
        # not all of it. Hit rate must not hide that; recall must show it.
        report = _report()
        score(_run(["a", "x", "y"]), _qrels({"a": 3, "b": 3}), report)
        m = self._metrics(report)
        assert m["hit_rate_at_5"] == 1.0
        assert m["recall_at_5"] == 0.5

    def test_a_total_miss_is_not_silently_a_pass(self):
        from eval.stage3_retrieval.scoring import score

        report = _report()
        score(_run(["x", "y", "z"]), _qrels({"a": 3}), report)
        m = self._metrics(report)
        assert m["hit_rate_at_5"] == 0.0
        assert m["recall_at_5"] == 0.0
        assert m["mrr"] == 0.0

    def test_precision_counts_supporting_context_not_just_answer_chunks(self):
        from eval.stage3_retrieval.scoring import score

        report = _report()
        score(_run(["a", "b", "c", "d", "e"]), _qrels({"a": 3, "b": 1, "c": 1, "d": 1}), report)
        # Scoring precision on answer chunks alone would cap this at 0.2.
        assert self._metrics(report)["precision_at_5"] == 0.8

    def test_context_precision_rewards_ranking_relevant_chunks_earlier(self):
        from eval.stage3_retrieval.scoring import score

        graded = {"a": 3, "b": 1}
        early, late = _report(), _report()
        score(_run(["a", "b", "x", "y", "z"]), _qrels(graded), early)
        score(_run(["x", "y", "z", "a", "b"]), _qrels(graded), late)
        e, l = self._metrics(early), self._metrics(late)
        # Same set retrieved either way, so plain precision cannot tell them apart.
        assert e["precision_at_5"] == l["precision_at_5"]
        # Context precision is rank-weighted and must.
        assert e["context_precision_at_10"] > l["context_precision_at_10"]

    def test_context_recall_measures_retrieved_text_not_judgments(self):
        from eval.stage3_retrieval.scoring import score

        report = _report()
        score(
            _run(["a", "b"], texts={"a": "the floor is 160 bps", "b": "fee of 40 bps"}),
            _qrels({"a": 3}, expected_answer="160 bps over FTP, with a 40 bps fee."),
            report,
        )
        # Both gold facts appear in the window, even though only "a" is judged.
        assert self._metrics(report)["context_recall"] == 1.0

    def test_context_recall_falls_when_a_fact_is_absent_from_the_window(self):
        from eval.stage3_retrieval.scoring import score

        report = _report()
        score(
            _run(["a"], texts={"a": "the floor is 160 bps"}),
            _qrels({"a": 3}, expected_answer="160 bps over FTP, with a 40 bps fee."),
            report,
        )
        assert self._metrics(report)["context_recall"] == 0.5

    def test_context_recall_tolerates_a_unitless_table_cell(self):
        from eval.stage3_retrieval.scoring import score

        report = _report()
        score(
            _run(["a"], texts={"a": "| Corp-IG | 3.5bn | 14% T1 |"}),
            _qrels({"a": 3}, expected_answer="The cap is AED 3.5 billion."),
            report,
        )
        assert self._metrics(report)["context_recall"] == 1.0

    def test_context_recall_is_na_when_the_gold_answer_has_no_facts(self):
        from eval.stage3_retrieval.scoring import score

        report = _report()
        score(
            _run(["a"], texts={"a": "some prose"}),
            _qrels({"a": 3}, expected_answer="The Country Credit Committee."),
            report,
        )
        # No quantities and no answer_keys — unmeasured, never a silent zero.
        assert self._metrics(report)["context_recall"] is None

    def test_unjudged_rate_counts_slots_the_grader_never_scored(self):
        from eval.stage3_retrieval.scoring import score

        report = _report()
        score(_run(["a", "b", "c", "d", "e"]), _qrels({"a": 3, "b": 1}), report)
        assert self._metrics(report)["unjudged_rate_at_5"] == 0.6

    def test_questions_with_no_judgments_raise_an_error(self):
        from eval.stage3_retrieval.scoring import score

        report = _report()
        score(_run(["a", "b"]), _qrels({}), report)
        assert [f.code for f in report.errors] == ["NO_JUDGED_QUESTIONS"]

    def test_report_meta_records_the_grading_basis(self):
        from eval.stage3_retrieval.scoring import score

        report = _report()
        score(_run(["a"]), _qrels({"a": 3}), report)
        assert "numeric" in report.meta["grading_basis"]

