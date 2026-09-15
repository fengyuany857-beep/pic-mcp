from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RenderableMediaRef(BaseModel):
    id: str
    preview_url: str
    original_url: str
    page_url: str
    width: int | None = None
    height: int | None = None
    mime_type: str | None = None
    source: str


class GalleryItem(BaseModel):
    id: str
    source: str
    previewUrl: str
    originalUrl: str
    pageUrl: str
    width: int | None = None
    height: int | None = None
    mimeType: str | None = None
    score: float = 0.0
    tags: list[str] = Field(default_factory=list)


class GalleryPayload(BaseModel):
    status: Literal["PASS", "PARTIAL"] = "PASS"
    mode: Literal["static", "live"]
    page: int = 1
    items: list[GalleryItem] = Field(default_factory=list)
    unrenderableIds: list[str] = Field(default_factory=list)
    source: str = "recommendation_result_binding"


class FeedbackAck(BaseModel):
    status: Literal["PASS"] = "PASS"
    candidate_id: str
    value: Literal["like", "dislike"]
    persistence: Literal["ephemeral_trial_only"] = "ephemeral_trial_only"
