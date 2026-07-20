"""Read-only audit of the approved few-shot corpus (prerequisite for the Pattern Retriever).

Everything in the few-shot layer assumes the approved-example corpus EXISTS and is
BALANCED across the governed views. A blank ``agent_db_dsn`` means zero examples (the ranker
is a silent no-op); a skewed corpus makes retrieval systematically over-recommend the
over-represented views. This script prints:

  1. total approved examples,
  2. per-view coverage (how many approved examples touch each view),
  3. views with NO example at all (add 1-2 hand-curated gold examples per uncovered view),
  4. a curation smell test: examples that AVG/SUM a PRE-AGGREGATE column (avg_*/win_rate_pct/
     *_pct) — the exact "pre-aggregate trap" the D-set tests for; fix or unapprove them.

Run:  python -m scripts.audit_example_corpus
Never writes anything.
"""

from __future__ import annotations

import re
from collections import Counter

from sql_agent.memory.example_index import row_metadata
from sql_agent.memory.examples import all_approved_examples
from sql_agent.semantic_layer.loader import ALLOWED_TABLES

# AVG()/SUM() applied to a column whose name marks it as already aggregated at a coarser
# grain (a pre-aggregate). Re-aggregating it is an average-of-averages — wrong number,
# clean run. Same smell the generation prompt warns about.
_PRE_AGG = re.compile(r"(avg|sum)\s*\(\s*[\w.]*(avg_|win_rate|_pct)[\w.]*", re.IGNORECASE)


def main() -> None:
    rows = all_approved_examples()
    print(f"approved examples: {len(rows)}")
    if not rows:
        print("\n!! corpus is EMPTY — check agent_db_dsn is set and examples are seeded/"
              "promoted. The Pattern Retriever is a no-op until this is populated.")
        return

    per_view: Counter[str] = Counter()
    for r in rows:
        for t in row_metadata(r).get("tables") or []:
            per_view[t] += 1

    print("\nper-view coverage:")
    for t in sorted(ALLOWED_TABLES):
        marker = "" if per_view.get(t, 0) else "   <-- no example"
        print(f"  {t:38s} {per_view.get(t, 0):3d}{marker}")

    missing = sorted(t for t in ALLOWED_TABLES if not per_view.get(t, 0))
    print(f"\nviews with NO example ({len(missing)}): {missing}")

    bad = [r for r in rows if _PRE_AGG.search(r.get("validated_sql") or "")]
    print(f"\npotential pre-aggregate-trap examples: {len(bad)}")
    for r in bad:
        print("  -", (r.get("question") or "")[:72])


if __name__ == "__main__":
    main()
