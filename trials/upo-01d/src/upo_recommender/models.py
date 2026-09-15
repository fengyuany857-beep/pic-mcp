from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Literal

class RawBooruPost(BaseModel):
    id: int
    provider: str
    rating: str = ""
    score: int = 0
    post_url: str
    raw_tags: str = ""
    preview_url: str | None = None
    file_url: str | None = None

class Candidate(BaseModel):
    candidate_id: str
    provider: str
    source_id: int
    post_url: str
    tags: tuple[str, ...]
    source_score: float = 0.0
    preview_url: str | None = None
    file_url: str | None = None
    relevance_score: float = 0.0
    final_score: float = 0.0

class RecommendationRequest(BaseModel):
    liked_tags: list[str] = Field(default_factory=list)
    disliked_tags: list[str] = Field(default_factory=list)
    limit: int = 10
    diversity_lambda: float = 0.75

class RecommendationResult(BaseModel):
    items: list[Candidate]
    assembly_mode: Literal["fixture_contract_trial", "live_provider_web_reconstruction", "mcp_stdio_transport_harness", "live_mcp"] = "fixture_contract_trial"
    known_gaps: list[str] = Field(default_factory=list)
