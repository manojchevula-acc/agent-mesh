"""Relevance gating and rank fusion — the tuning surface, so it is pinned hard."""

from gernas_rag.retrieval.fusion import gate_images, rrf_fuse
from gernas_rag.vectordb.base import SearchResult
from gernas_rag.vectordb.image_store import ImageSearchResult


def _img(asset_id: str, score: float) -> ImageSearchResult:
    return ImageSearchResult(asset_id=asset_id, score=score, payload={"id": asset_id})


# ── gate_images ──────────────────────────────────────────────────────────
def test_empty_input_gives_empty_output():
    assert gate_images([], 0.1, 0.55, 4) == []


def test_floor_removes_globally_weak_matches():
    """Without a floor the ANN returns its top_k regardless of relevance."""
    results = [_img("a", 0.05), _img("b", 0.03)]
    assert gate_images(results, floor=0.10, margin_ratio=0.0, final_k=4) == []


def test_margin_removes_the_tail_behind_a_strong_hit():
    results = [_img("a", 0.40), _img("b", 0.30), _img("c", 0.15)]
    kept = gate_images(results, floor=0.10, margin_ratio=0.55, final_k=4)
    # 0.55 * 0.40 = 0.22, so 'c' at 0.15 is dropped.
    assert [r.asset_id for r in kept] == ["a", "b"]


def test_final_k_caps_the_output():
    results = [_img(str(i), 0.9 - i * 0.01) for i in range(10)]
    assert len(gate_images(results, 0.1, 0.5, final_k=3)) == 3


def test_results_are_sorted_and_reranked():
    kept = gate_images([_img("a", 0.2), _img("b", 0.9)], 0.1, 0.1, 4)
    assert [r.asset_id for r in kept] == ["b", "a"]
    assert [r.rank for r in kept] == [0, 1]


def test_margin_is_relative_to_the_top_kept_score():
    """A uniformly strong set must survive the margin gate intact."""
    results = [_img("a", 0.90), _img("b", 0.88), _img("c", 0.85)]
    assert len(gate_images(results, 0.1, 0.55, 4)) == 3


# ── rrf_fuse ─────────────────────────────────────────────────────────────
def _text(chunk_id: str) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, text="", score=0.0, metadata={})


def test_rrf_uses_rank_not_score():
    """Scores from two spaces are incomparable, so magnitude must be ignored."""
    text = [_text("t1"), _text("t2")]
    images = [_img("i1", 0.0001)]  # tiny score, but rank 0
    fused = rrf_fuse(text, images, k=60, w_text=1.0, w_image=1.0)
    # Rank 0 in each list ties; the image is not penalised for its raw score.
    assert fused[0].score == fused[1].score


def test_weights_shift_the_ordering():
    text = [_text("t1")]
    images = [_img("i1", 0.5)]
    text_first = rrf_fuse(text, images, 60, w_text=1.0, w_image=0.1)
    assert text_first[0].kind == "text"
    image_first = rrf_fuse(text, images, 60, w_text=0.1, w_image=1.0)
    assert image_first[0].kind == "image"


def test_zero_weight_excludes_a_modality_from_the_top():
    fused = rrf_fuse([_text("t1")], [_img("i1", 0.9)], 60, w_text=1.0, w_image=0.0)
    assert fused[0].kind == "text"
    assert fused[-1].score == 0.0


def test_expected_rrf_values():
    """Hand-computed: 1/(60+0) = 0.016666..., 1/(60+1) = 0.016393..."""
    fused = rrf_fuse([_text("t1"), _text("t2")], [], k=60, w_text=1.0, w_image=1.0)
    assert fused[0].score == 1 / 60
    assert fused[1].score == 1 / 61


def test_ranks_are_assigned_in_order():
    fused = rrf_fuse([_text("a"), _text("b"), _text("c")], [], 60)
    assert [f.rank for f in fused] == [0, 1, 2]


def test_both_modalities_are_present_in_the_output():
    fused = rrf_fuse([_text("t1")], [_img("i1", 0.4)], 60)
    assert {f.kind for f in fused} == {"text", "image"}
