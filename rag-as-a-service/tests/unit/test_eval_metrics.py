"""Unit tests for the evaluation suite's pure metric functions.

The metrics gate CI, so they need their own tests: a scorer that silently
returns 0.0 where it should return None turns "no data" into "everything failed",
and an off-by-one in nDCG turns a ranking regression into a pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The eval package lives at the repo root, next to src/ and tests/.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from eval.core.metrics import (  # noqa: E402
    cohens_kappa,
    count_demotions,
    first_relevant_rank,
    histogram,
    mean,
    ndcg_at_k,
    percentile,
    precision_at_k,
    prf,
    recall_at_k,
    reciprocal_rank,
)
from eval.core.numeric import compare_numeric, numeric_facts, unsupported_facts  # noqa: E402
from eval.core.text import (  # noqa: E402
    char_error_rate,
    levenshtein,
    looks_truncated,
    strip_citations,
)


# ══════════════════════════════════════════════════════════════════════
# Ranking metrics
# ══════════════════════════════════════════════════════════════════════
class TestRankingMetrics:
    def test_recall_at_k_counts_only_the_window(self):
        ranked = ["a", "b", "c", "d"]
        assert recall_at_k(ranked, {"a", "d"}, 2) == 0.5
        assert recall_at_k(ranked, {"a", "d"}, 4) == 1.0

    def test_no_relevant_items_is_none_not_zero(self):
        # "Nothing was judged" must never average in as a failure.
        assert recall_at_k(["a"], set(), 5) is None
        assert reciprocal_rank(["a"], set()) is None
        assert ndcg_at_k(["a"], {}, 10) is None

    def test_relevant_but_missed_is_zero(self):
        assert recall_at_k(["x"], {"a"}, 5) == 0.0
        assert reciprocal_rank(["x"], {"a"}) == 0.0

    def test_precision_at_k_uses_window_size(self):
        assert precision_at_k(["a", "x", "y", "z"], {"a"}, 4) == 0.25
        assert precision_at_k([], {"a"}, 5) is None

    def test_reciprocal_rank_is_one_over_first_hit(self):
        assert reciprocal_rank(["x", "y", "a"], {"a"}) == pytest.approx(1 / 3)
        assert first_relevant_rank(["x", "y", "a"], {"a"}) == 3
        assert first_relevant_rank(["x"], {"a"}) is None

    def test_ndcg_is_one_for_ideal_ordering(self):
        grades = {"a": 3, "b": 2, "c": 1}
        assert ndcg_at_k(["a", "b", "c"], grades, 10) == pytest.approx(1.0)

    def test_ndcg_penalises_reordering(self):
        grades = {"a": 3, "b": 1}
        ideal = ndcg_at_k(["a", "b"], grades, 10)
        reordered = ndcg_at_k(["b", "a"], grades, 10)
        assert reordered < ideal

    def test_ndcg_rewards_a_higher_position(self):
        grades = {"a": 3}
        assert ndcg_at_k(["a", "x", "y"], grades, 10) > ndcg_at_k(["x", "y", "a"], grades, 10)


class TestRankStability:
    def test_demotion_and_drop_are_distinguished(self):
        before = ["a", "b", "c"]
        after = ["b", "a"]  # 'a' demoted 1->2, 'c' dropped out entirely.
        demoted, dropped = count_demotions(before, after, {"a", "c"})
        assert (demoted, dropped) == (1, 1)

    def test_promotion_is_not_a_demotion(self):
        assert count_demotions(["x", "a"], ["a", "x"], {"a"}) == (0, 0)

    def test_histogram_buckets_and_misses(self):
        result = histogram([1, 2, 4, 20, None], [1, 3, 5, 10])
        assert result["<=1"] == 1
        assert result["<=3"] == 1
        assert result["<=5"] == 1
        assert result[">10"] == 1
        assert result["miss"] == 1


class TestAggregation:
    def test_mean_ignores_none(self):
        assert mean([1.0, None, 3.0]) == 2.0
        assert mean([None, None]) is None

    def test_percentile_interpolates(self):
        assert percentile([0, 10], 50) == 5.0
        assert percentile([], 50) is None

    def test_prf(self):
        precision, recall, f1 = prf({"a", "b"}, {"b", "c"})
        assert precision == 0.5 and recall == 0.5 and f1 == pytest.approx(0.5)

    def test_kappa_detects_agreement_above_chance(self):
        perfect = cohens_kappa(["A", "B", "A", "B"], ["A", "B", "A", "B"])
        assert perfect == pytest.approx(1.0)
        # Both raters using one identical label leaves kappa undefined.
        assert cohens_kappa(["A", "A"], ["A", "A"]) is None


# ══════════════════════════════════════════════════════════════════════
# Numeric fact extraction — the deterministic backbone of stages 2 and 4
# ══════════════════════════════════════════════════════════════════════
class TestNumericExtraction:
    def _keys(self, text: str) -> set[tuple[str, str]]:
        return {f.key for f in numeric_facts(text)}

    def test_basis_points_and_percent_are_separate_families(self):
        # 0.5% and 50bps are numerically equal; treating them as the same fact
        # would let a unit error pass silently.
        assert self._keys("50 bps") != self._keys("0.5%")
        assert ("bps", "50") in self._keys("50 bps")
        assert ("pct", "18.4") in self._keys("18.4%")

    def test_unit_spellings_normalise(self):
        assert self._keys("160bps") == self._keys("160 basis points")
        assert self._keys("5 percent") == self._keys("5%")

    def test_scale_words_fold_into_the_value(self):
        assert ("ccy:aed", "2000000000") in self._keys("AED 2.0 billion")
        assert self._keys("AED 50 million") == self._keys("AED 50M")

    def test_dates_in_the_same_month_do_not_collide(self):
        # Regression: formatting keys with %g rendered 20240315 as 2.02403e+07,
        # making every date in a month compare equal.
        assert self._keys("15 March 2024") != self._keys("16 March 2024")

    def test_thousands_separators(self):
        assert ("", "1200") in self._keys("1,200")

    def test_dates_are_canonicalised_across_formats(self):
        assert self._keys("31-Dec-2024") == self._keys("31 December 2024")
        assert ("date", "20240315") in self._keys("15 March 2024")
        assert ("date", "20240701") in self._keys("2024-07-01")

    def test_day_month_without_year_is_its_own_family(self):
        keys = self._keys("by 31 March each year")
        assert ("date_md", "331") in keys
        assert not any(unit == "date" for unit, _ in keys)

    def test_clause_references_are_not_quantities(self):
        # "2.1.1" is a clause number; parsing it would inject phantom facts.
        assert self._keys("see clause 2.1.1") == set()

    def test_citation_markers_are_stripped(self):
        assert self._keys("The floor is 160 bps [1][2]") == self._keys("The floor is 160 bps")

    def test_spelled_numbers_only_count_with_a_unit(self):
        assert ("year", "2") in self._keys("two years of audited statements")
        assert self._keys("one of the requirements") == set()


class TestNumericComparison:
    def test_recall_and_missing(self):
        result = compare_numeric("160 bps and 40 bps", "the floor is 160 bps")
        assert result.recall == 0.5
        assert result.missing == ["40 bps"]

    def test_transposed_digit_is_caught(self):
        result = compare_numeric("21%", "12%")
        assert result.recall == 0.0
        assert result.hallucination_rate == 1.0

    def test_formatting_differences_do_not_count_as_errors(self):
        result = compare_numeric("AED 2.0 billion", "AED 2 billion")
        assert result.recall == 1.0
        assert result.extra == []

    def test_empty_reference_is_undefined(self):
        assert compare_numeric("", "50 bps").recall is None

    def test_unsupported_facts_flags_only_ungrounded_numbers(self):
        unsupported, total = unsupported_facts(
            "The rate is 6.65% based on a 4.85% FTP",
            ["FTP is 4.85% for this tenor"],
        )
        assert unsupported == ["6.65%"]
        assert total == 2

    def test_unsupported_facts_with_no_numbers(self):
        assert unsupported_facts("No figures here.", ["context"]) == ([], 0)


# ══════════════════════════════════════════════════════════════════════
# Text helpers
# ══════════════════════════════════════════════════════════════════════
class TestTextHelpers:
    def test_levenshtein(self):
        assert levenshtein("kitten", "sitting") == 3
        assert levenshtein("", "abc") == 3
        assert levenshtein("same", "same") == 0

    def test_cer_normalises_whitespace_and_case(self):
        assert char_error_rate("Total  50 BPS", "total 50 bps") == 0.0

    def test_cer_is_undefined_for_an_empty_reference(self):
        assert char_error_rate("", "anything") is None

    def test_cer_is_capped_at_one(self):
        assert char_error_rate("a", "a totally different and much longer string") == 1.0

    def test_strip_citations(self):
        assert strip_citations("Floor is 160 bps [1][2].") == "Floor is 160 bps."
        assert strip_citations("Value 4.85 【1 · 4.85】 applies") == "Value 4.85 applies"

    def test_truncation_heuristic(self):
        assert looks_truncated("| Tier 1 | 31-Dec-2024 |\n| Tier 2 |")
        assert looks_truncated("Rates are (see table")
        # A legitimate transcription often ends on a bare value.
        assert not looks_truncated("Floor Rate 35 bps")
        assert not looks_truncated("")


# ══════════════════════════════════════════════════════════════════════
# Stage-level pure helpers
# ══════════════════════════════════════════════════════════════════════
class TestStageHelpers:
    def test_clause_matching_requires_a_dot_boundary(self):
        from eval.core.corpus import clause_matches

        assert clause_matches("2.1", "2.1")
        assert clause_matches("2.1", "2.1.1")
        # The bug the substring test had: "2.1" must not match "12.14".
        assert not clause_matches("2.1", "12.14")
        assert not clause_matches("2.1", "2.14")

    def test_citation_parsing(self):
        from eval.stage4_generation.scoring import citations_in

        assert citations_in("Grounded [1] and also [2][3].") == [1, 2, 3]
        assert citations_in("Combined [1, 2]") == [1, 2]
        assert citations_in("No citations here") == []

    def test_refusal_detection(self):
        from eval.stage4_generation.scoring import looks_like_refusal

        assert looks_like_refusal("The provided context does not contain this information.")
        assert looks_like_refusal("I could not find any relevant policy context.")
        assert not looks_like_refusal("The floor is 160 bps for BB-rated clients.")

    def test_judge_verdict_parsing_never_defaults_to_a_pass(self):
        from eval.stage4_generation.judge import parse_verdict

        verdict, reason = parse_verdict("VERDICT: CORRECT\nREASON: matches the gold answer")
        assert verdict == "CORRECT" and "matches" in reason
        # Unparseable output is missing data, not a verdict.
        assert parse_verdict("the model rambled")[0] == "UNKNOWN"
        assert parse_verdict("")[0] == "UNKNOWN"

    def test_fragmented_table_detection(self):
        from eval.stage2_enrichment.integrity import _is_fragmented_table

        complete = "| Rating | Floor |\n|---|---|\n| BB | 160 |"
        fragment = "| BB | 160 |\n| B | 210 |\n| CCC | 320 |"
        assert not _is_fragmented_table(complete)
        assert _is_fragmented_table(fragment)
        assert not _is_fragmented_table("plain prose with no table")
