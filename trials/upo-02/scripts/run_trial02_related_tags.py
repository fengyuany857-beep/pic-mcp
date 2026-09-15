from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
TRIAL01 = REPO_ROOT / "trials" / "upo-01d"
sys.path.insert(0, str(TRIAL01 / "src"))
sys.path.insert(0, str(ROOT / "src"))

from tag_expansion import (  # noqa: E402
    cooccurrence_expansions,
    expanded_preference_tags,
    implication_expansions,
    parse_tool_json,
)
from upo_recommender.adapters import from_booru_pictag_search_posts  # noqa: E402
from upo_recommender.models import RecommendationRequest  # noqa: E402
from upo_recommender.recommend import recommend  # noqa: E402

ARTIFACTS = ROOT / "artifacts" / "trial02"
WIRE = ARTIFACTS / "mcp-wire.jsonl"
RESULT = ARTIFACTS / "result.json"
SOURCE_TAG = "hatsune_miku"
UPSTREAM_COMMIT = "ed234568fc376517d0abc5a377d65af37981b059"


def dump_model(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


async def run() -> dict[str, Any]:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    if WIRE.exists():
        WIRE.unlink()

    tap = TRIAL01 / "scripts" / "stdio_tap.py"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(tap), str(WIRE), "booru-pictag-get-mcp"],
        env={**os.environ},
        cwd=str(ROOT),
    )

    evidence: dict[str, Any] = {
        "trial": "02",
        "source_tag": SOURCE_TAG,
        "upstream_repository": "echo-xianyu/Booru-Pictag-Get-MCP",
        "upstream_commit": UPSTREAM_COMMIT,
        "capabilities": ["tag_cooccurrence_expansion", "tag_implication_expansion"],
    }

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream, read_timeout_seconds=timedelta(seconds=90)) as session:
            init = await session.initialize()
            evidence["initialize"] = dump_model(init)
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            evidence["tool_names"] = tool_names
            required = {"danbooru_search_character", "danbooru_get_tag_implications", "search_posts"}
            missing = sorted(required - set(tool_names))
            if missing:
                raise RuntimeError(f"missing required upstream tools: {missing}")

            related_result = await session.call_tool(
                "danbooru_search_character",
                arguments={
                    "tag": SOURCE_TAG,
                    "limit": 8,
                    "category": "character",
                    "min_frequency": 0.0,
                    "exclude_meta": True,
                    "response_format": "json",
                    "auto_resolve": True,
                },
                read_timeout_seconds=timedelta(seconds=60),
            )
            if getattr(related_result, "isError", False):
                raise RuntimeError("danbooru_search_character returned MCP error")
            related_payload = parse_tool_json(related_result)
            co = cooccurrence_expansions(related_payload, limit=6)
            if not co:
                raise RuntimeError("co-occurrence expansion returned no tags")

            implication_result = await session.call_tool(
                "danbooru_get_tag_implications",
                arguments={"tag": SOURCE_TAG, "limit": 20, "response_format": "json"},
                read_timeout_seconds=timedelta(seconds=60),
            )
            if getattr(implication_result, "isError", False):
                raise RuntimeError("danbooru_get_tag_implications returned MCP error")
            implication_payload = parse_tool_json(implication_result)
            imps = implication_expansions(implication_payload, limit=6)

            preference_tags = expanded_preference_tags(co, imps, limit=6)
            if not preference_tags:
                raise RuntimeError("typed expansion produced no usable preference tags")

            # Use the top co-occurrence tags as real retrieval expansion. We do
            # not modify the frozen Trial 01 recommender core.
            retrieval_tags = [x.tag for x in sorted(co, key=lambda x: -x.weight)[:3]]
            combined_posts: list[dict[str, Any]] = []
            retrieval: list[dict[str, Any]] = []
            for tag in retrieval_tags:
                result = await session.call_tool(
                    "search_posts",
                    arguments={
                        "tags": tag,
                        "provider": "danbooru",
                        "order": "popular",
                        "page": 1,
                        "limit": 5,
                        "rating": "safe",
                        "include_preview": False,
                        "include_file_url": False,
                    },
                    read_timeout_seconds=timedelta(seconds=60),
                )
                if getattr(result, "isError", False):
                    retrieval.append({"tag": tag, "status": "MCP_ERROR"})
                    continue
                payload = parse_tool_json(result)
                posts = payload.get("posts") or []
                retrieval.append({"tag": tag, "status": "PASS", "post_count": len(posts)})
                combined_posts.extend(posts)

            if not combined_posts:
                raise RuntimeError("expanded tags produced zero live post candidates")

            candidates = from_booru_pictag_search_posts({"posts": combined_posts})
            request = RecommendationRequest(
                liked_tags=preference_tags,
                disliked_tags=[],
                limit=min(10, len(candidates)),
                diversity_lambda=0.75,
            )
            rec = recommend(candidates, request, assembly_mode="live_mcp")
            if not rec.items:
                raise RuntimeError("recommender produced zero results after expansion")

            evidence.update({
                "cooccurrence_raw": related_payload,
                "implication_raw": implication_payload,
                "cooccurrence_expansions": [x.to_dict() for x in co],
                "implication_expansions": [x.to_dict() for x in imps],
                "expanded_preference_tags": preference_tags,
                "retrieval_tags": retrieval_tags,
                "retrieval": retrieval,
                "candidate_count": len(candidates),
                "recommendation": rec.model_dump(mode="json"),
            })

    wire = WIRE.read_text(encoding="utf-8") if WIRE.exists() else ""
    markers = {
        "initialize": "initialize" in wire,
        "tools/list": "tools/list" in wire,
        "related_tool_call": "danbooru_search_character" in wire,
        "implication_tool_call": "danbooru_get_tag_implications" in wire,
        "search_posts_call": "search_posts" in wire,
    }
    if not wire.strip() or not all(markers.values()):
        raise RuntimeError(f"wire attestation incomplete: {markers}")
    evidence["wire_attestation"] = {"nonempty": True, "markers": markers}
    evidence["status"] = "PASS"
    RESULT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


def main() -> int:
    try:
        ev = asyncio.run(run())
    except Exception as exc:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        failure = {"trial": "02", "status": "FAIL", "exception": f"{type(exc).__name__}: {exc}"}
        RESULT.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({
        "status": ev["status"],
        "source_tag": ev["source_tag"],
        "cooccurrence_expansions": ev["cooccurrence_expansions"],
        "implication_expansions": ev["implication_expansions"],
        "retrieval": ev["retrieval"],
        "candidate_count": ev["candidate_count"],
        "top_recommendations": [
            {"candidate_id": x["candidate_id"], "final_score": x["final_score"]}
            for x in ev["recommendation"]["items"][:5]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
