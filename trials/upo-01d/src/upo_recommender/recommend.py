from __future__ import annotations
import numpy as np
from sklearn.preprocessing import MultiLabelBinarizer
from sklearn.metrics.pairwise import cosine_similarity
from .models import Candidate, RecommendationRequest, RecommendationResult


def dedupe_exact(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[tuple[str, int]] = set()
    out: list[Candidate] = []
    for c in candidates:
        key = (c.provider, c.source_id)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _score_candidates(candidates: list[Candidate], req: RecommendationRequest) -> tuple[np.ndarray, np.ndarray]:
    tag_sets = [list(c.tags) for c in candidates]
    preference = list(dict.fromkeys(req.liked_tags))
    all_rows = tag_sets + [preference]
    mlb = MultiLabelBinarizer()
    X = mlb.fit_transform(all_rows)
    cand_X = X[:-1]
    pref_X = X[-1:]
    semantic = cosine_similarity(cand_X, pref_X).reshape(-1)

    if req.disliked_tags:
        disliked = set(req.disliked_tags)
        penalties = np.array([sum(1 for t in c.tags if t in disliked) for c in candidates], dtype=float)
        semantic = np.maximum(0.0, semantic - np.minimum(0.75, penalties * 0.25))

    raw_scores = np.array([max(0.0, c.source_score) for c in candidates], dtype=float)
    source_norm = raw_scores / raw_scores.max() if raw_scores.size and raw_scores.max() > 0 else raw_scores
    relevance = 0.85 * semantic + 0.15 * source_norm
    return relevance, cand_X


def _mmr_order(relevance: np.ndarray, feature_matrix: np.ndarray, limit: int, lam: float) -> list[int]:
    n = len(relevance)
    if n == 0:
        return []
    sim = cosine_similarity(feature_matrix)
    remaining = set(range(n))
    selected: list[int] = []
    while remaining and len(selected) < limit:
        if not selected:
            idx = max(remaining, key=lambda i: float(relevance[i]))
        else:
            idx = max(
                remaining,
                key=lambda i: lam * float(relevance[i]) - (1 - lam) * max(float(sim[i, j]) for j in selected),
            )
        selected.append(idx)
        remaining.remove(idx)
    return selected


def recommend(candidates: list[Candidate], req: RecommendationRequest, assembly_mode: str = "fixture_contract_trial") -> RecommendationResult:
    candidates = dedupe_exact(candidates)
    if not candidates:
        return RecommendationResult(items=[], assembly_mode=assembly_mode, known_gaps=["no_candidates"])

    relevance, features = _score_candidates(candidates, req)
    order = _mmr_order(relevance, features, min(req.limit, len(candidates)), req.diversity_lambda)
    items: list[Candidate] = []
    for rank, idx in enumerate(order):
        c = candidates[idx].model_copy(deep=True)
        c.relevance_score = round(float(relevance[idx]), 6)
        c.final_score = round(float(relevance[idx]) - rank * 1e-6, 6)
        items.append(c)

    transport_gaps = (
        [
            "host_side_mcp_stdio_transport_verified_with_contract_harness",
            "upstream_fastmcp_runtime_not_executed_in_sandbox",
        ]
        if assembly_mode == "mcp_stdio_transport_harness"
        else ["live_mcp_transport_not_executed_in_sandbox"]
    )
    return RecommendationResult(
        items=items,
        assembly_mode=assembly_mode,
        known_gaps=[
            "cross_provider_visual_duplicate_detection_not_implemented",
            *transport_gaps,
            "persistent_user_preference_learning_not_implemented",
        ],
    )
