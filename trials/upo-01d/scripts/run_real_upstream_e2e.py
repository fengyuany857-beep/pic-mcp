from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from upo_recommender.adapters import from_booru_pictag_search_posts
from upo_recommender.models import RecommendationRequest
from upo_recommender.recommend import recommend


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "trial01d"
WIRE = ARTIFACTS / "mcp-wire.jsonl"
RESULT = ARTIFACTS / "result.json"
UPSTREAM_COMMIT = "ed234568fc376517d0abc5a377d65af37981b059"


def dump_model(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


def extract_payload(tool_result: Any) -> tuple[dict[str, Any], str]:
    structured = getattr(tool_result, "structuredContent", None)
    if isinstance(structured, dict) and isinstance(structured.get("posts"), list):
        return structured, "structuredContent"

    for block in getattr(tool_result, "content", []) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("posts"), list):
            return parsed, "text_content_json"

    raise RuntimeError("search_posts returned no directly consumable payload containing posts[]")


async def run() -> dict[str, Any]:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    if WIRE.exists():
        WIRE.unlink()

    tap = ROOT / "scripts" / "stdio_tap.py"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(tap), str(WIRE), "booru-pictag-get-mcp"],
        env={**os.environ},
        cwd=str(ROOT),
    )

    evidence: dict[str, Any] = {
        "trial": "01D",
        "upstream_repository": "echo-xianyu/Booru-Pictag-Get-MCP",
        "upstream_commit": UPSTREAM_COMMIT,
        "python": sys.version,
        "platform": platform.platform(),
        "provider_attempts": [],
    }

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream, read_timeout_seconds=timedelta(seconds=90)) as session:
            init = await session.initialize()
            evidence["initialize"] = dump_model(init)

            tools_result = await session.list_tools()
            tool_names = [tool.name for tool in tools_result.tools]
            evidence["tool_names"] = tool_names
            if "search_posts" not in tool_names:
                raise RuntimeError(f"upstream server does not expose search_posts; tools={tool_names}")

            selected_payload: dict[str, Any] | None = None
            selected_provider: str | None = None
            selected_result: Any = None
            payload_source: str | None = None

            for provider in ("danbooru", "aibooru", "gelbooru"):
                arguments = {
                    "tags": "red_eyes",
                    "provider": provider,
                    "order": "popular",
                    "page": 1,
                    "limit": 8,
                    "rating": "safe",
                    "random_seed": 1,
                    "include_preview": False,
                    "include_file_url": False,
                }
                attempt: dict[str, Any] = {"provider": provider, "arguments": arguments}
                try:
                    result = await session.call_tool(
                        "search_posts",
                        arguments=arguments,
                        read_timeout_seconds=timedelta(seconds=60),
                    )
                    attempt["tool_result"] = dump_model(result)
                    attempt["isError"] = bool(getattr(result, "isError", False))
                    if attempt["isError"]:
                        evidence["provider_attempts"].append(attempt)
                        continue
                    payload, source = extract_payload(result)
                    attempt["payload_source"] = source
                    attempt["post_count"] = len(payload.get("posts", []))
                    evidence["provider_attempts"].append(attempt)
                    if payload.get("posts"):
                        selected_payload = payload
                        selected_provider = provider
                        selected_result = result
                        payload_source = source
                        break
                except Exception as exc:
                    attempt["exception"] = f"{type(exc).__name__}: {exc}"
                    evidence["provider_attempts"].append(attempt)

            if selected_payload is None:
                raise RuntimeError("no live upstream provider returned a non-empty search_posts payload")

            candidates = from_booru_pictag_search_posts(selected_payload)
            if not candidates:
                raise RuntimeError("adapter produced zero candidates from non-empty upstream posts[]")

            request = RecommendationRequest(
                liked_tags=["red_eyes", "black_hair", "twintails"],
                disliked_tags=[],
                limit=min(8, len(candidates)),
                diversity_lambda=0.75,
            )
            rec = recommend(candidates, request, assembly_mode="live_mcp")
            if not rec.items:
                raise RuntimeError("recommender produced zero items from valid live candidates")

            evidence.update(
                {
                    "selected_provider": selected_provider,
                    "payload_source": payload_source,
                    "raw_tool_result": dump_model(selected_result),
                    "raw_payload": selected_payload,
                    "adapter_candidate_count": len(candidates),
                    "request": request.model_dump(mode="json"),
                    "recommendation": rec.model_dump(mode="json"),
                }
            )

    wire_text = WIRE.read_text(encoding="utf-8") if WIRE.exists() else ""
    required_wire_markers = ["initialize", "tools/list", "tools/call"]
    evidence["wire_attestation"] = {
        "path": str(WIRE.relative_to(ROOT)),
        "nonempty": bool(wire_text.strip()),
        "markers": {marker: marker in wire_text for marker in required_wire_markers},
    }
    if not evidence["wire_attestation"]["nonempty"] or not all(evidence["wire_attestation"]["markers"].values()):
        raise RuntimeError(f"wire transcript incomplete: {evidence['wire_attestation']}")

    evidence["status"] = "PASS"
    RESULT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


def main() -> int:
    try:
        evidence = asyncio.run(run())
    except Exception as exc:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        failure = {
            "trial": "01D",
            "status": "FAIL",
            "upstream_commit": UPSTREAM_COMMIT,
            "exception": f"{type(exc).__name__}: {exc}",
            "python": sys.version,
            "platform": platform.platform(),
        }
        RESULT.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1

    summary = {
        "status": evidence["status"],
        "selected_provider": evidence["selected_provider"],
        "adapter_candidate_count": evidence["adapter_candidate_count"],
        "top_recommendations": [
            {
                "candidate_id": item["candidate_id"],
                "relevance_score": item["relevance_score"],
                "final_score": item["final_score"],
            }
            for item in evidence["recommendation"]["items"][:5]
        ],
        "wire_attestation": evidence["wire_attestation"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
