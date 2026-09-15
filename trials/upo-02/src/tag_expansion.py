from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

RelationType = Literal["cooccurrence", "implication"]


@dataclass(frozen=True)
class TagExpansion:
    source_tag: str
    tag: str
    relation_type: RelationType
    weight: float
    provenance: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decode_json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            obj = json.loads(value)
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None
    return None


def parse_tool_json(result: Any) -> dict[str, Any]:
    """Extract the tool's JSON object without changing its semantics.

    FastMCP may wrap a string return value as structuredContent={"result":
    "<json string>"}. Prefer an already-structured domain object; otherwise
    unwrap only that transport wrapper, then fall back to TextContent JSON.
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        if any(k in structured for k in ("related_tags", "implications", "posts", "query", "tag")):
            return structured
        if set(structured) == {"result"}:
            obj = _decode_json_object(structured.get("result"))
            if obj is not None:
                return obj

    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        obj = _decode_json_object(text)
        if obj is not None:
            return obj
    raise ValueError("MCP tool result did not contain a decodable JSON object")


def cooccurrence_expansions(payload: dict[str, Any], *, limit: int = 8) -> list[TagExpansion]:
    source = str(payload.get("tag", {}).get("name") or payload.get("query") or "")
    out: list[TagExpansion] = []
    for row in payload.get("related_tags") or []:
        tag = str(row.get("name") or "")
        if not tag or tag == source:
            continue
        weight = float(row.get("frequency") or 0.0)
        out.append(TagExpansion(
            source_tag=source,
            tag=tag,
            relation_type="cooccurrence",
            weight=weight,
            provenance="booru-pictag:danbooru_search_character",
            metadata={
                "category": row.get("category"),
                "post_count": row.get("post_count"),
                "jaccard_similarity": row.get("jaccard_similarity"),
                "overlap_coefficient": row.get("overlap_coefficient"),
            },
        ))
        if len(out) >= limit:
            break
    return out


def implication_expansions(payload: dict[str, Any], *, limit: int = 8) -> list[TagExpansion]:
    source = str(payload.get("query") or "")
    out: list[TagExpansion] = []
    for row in payload.get("implications") or []:
        if row.get("status") not in (None, "active"):
            continue
        tag = str(row.get("consequent_name") or "")
        if not tag or tag == source:
            continue
        out.append(TagExpansion(
            source_tag=source,
            tag=tag,
            relation_type="implication",
            weight=1.0,
            provenance="booru-pictag:danbooru_get_tag_implications",
            metadata={"implication_id": row.get("id"), "status": row.get("status")},
        ))
        if len(out) >= limit:
            break
    return out


def expanded_preference_tags(cooccurrence: list[TagExpansion], implication: list[TagExpansion], *, limit: int = 8) -> list[str]:
    """Thin composition glue: preserve upstream weighting and relation identity."""
    ordered = sorted(cooccurrence, key=lambda x: -x.weight) + implication
    seen: set[str] = set()
    tags: list[str] = []
    for item in ordered:
        if item.tag not in seen:
            seen.add(item.tag)
            tags.append(item.tag)
        if len(tags) >= limit:
            break
    return tags
