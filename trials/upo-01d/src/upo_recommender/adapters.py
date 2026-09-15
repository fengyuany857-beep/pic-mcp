from __future__ import annotations
from .models import RawBooruPost, Candidate


def from_booru_pictag_search_posts(payload: dict) -> list[Candidate]:
    """Adapter for Booru-Pictag-Get-MCP search_posts output.

    Confirmed upstream fields: id/provider/rating/score/post_url/raw_tags and
    optional preview_url/file_url. Unknown extra fields are intentionally ignored.
    """
    posts = payload.get("posts", payload.get("results", []))
    out: list[Candidate] = []
    for raw in posts:
        p = RawBooruPost.model_validate(raw)
        tags = tuple(sorted({t for t in p.raw_tags.split() if t}))
        out.append(Candidate(
            candidate_id=f"{p.provider}:{p.id}",
            provider=p.provider,
            source_id=p.id,
            post_url=p.post_url,
            tags=tags,
            source_score=float(p.score),
            preview_url=p.preview_url,
            file_url=p.file_url,
        ))
    return out
