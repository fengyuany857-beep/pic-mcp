from __future__ import annotations

from urllib.parse import urlparse

from upo_recommender.models import Candidate, RecommendationResult

from gallery_models import GalleryItem, GalleryPayload, RenderableMediaRef


_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


class UnrenderableMediaError(ValueError):
    pass


def _mime_from_url(url: str) -> str | None:
    path = urlparse(url).path.lower()
    for ext, mime in _MIME_BY_EXT.items():
        if path.endswith(ext):
            return mime
    return None


def resolve_media_ref(candidate: Candidate) -> RenderableMediaRef:
    preview = candidate.preview_url or candidate.file_url
    original = candidate.file_url or candidate.preview_url
    if not preview or not original:
        raise UnrenderableMediaError(candidate.candidate_id)

    return RenderableMediaRef(
        id=candidate.candidate_id,
        preview_url=preview,
        original_url=original,
        page_url=candidate.post_url,
        mime_type=_mime_from_url(original),
        source=candidate.provider,
    )


def bind_gallery(result: RecommendationResult, *, mode: str, page: int) -> GalleryPayload:
    items: list[GalleryItem] = []
    unrenderable: list[str] = []

    for candidate in result.items:
        try:
            media = resolve_media_ref(candidate)
        except UnrenderableMediaError:
            unrenderable.append(candidate.candidate_id)
            continue

        items.append(
            GalleryItem(
                id=candidate.candidate_id,
                source=media.source,
                previewUrl=media.preview_url,
                originalUrl=media.original_url,
                pageUrl=media.page_url,
                width=media.width,
                height=media.height,
                mimeType=media.mime_type,
                score=candidate.final_score,
                tags=list(candidate.tags),
            )
        )

    return GalleryPayload(
        status="PASS" if items and not unrenderable else "PARTIAL",
        mode=mode,
        page=page,
        items=items,
        unrenderableIds=unrenderable,
    )
