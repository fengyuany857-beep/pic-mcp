from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "trial04"
EVIDENCE = ARTIFACTS / "contract-evidence.json"
VIEW_URI = "ui://media/recommendations"


def dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


def persist(evidence: dict[str, Any]) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


def flatten_exception_tree(exc: BaseException) -> list[dict[str, str]]:
    flattened: list[dict[str, str]] = []

    def walk(current: BaseException, path: str) -> None:
        flattened.append(
            {
                "path": path,
                "type": type(current).__name__,
                "message": str(current),
            }
        )
        if isinstance(current, BaseExceptionGroup):
            for index, child in enumerate(current.exceptions):
                walk(child, f"{path}.{index}")

    walk(exc, "root")
    return flattened


def structured_payload(result: Any) -> dict[str, Any]:
    raw = dump(result)
    payload = raw.get("structuredContent") or raw.get("structured_content")
    if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
        payload = payload["result"]
    if not isinstance(payload, dict):
        raise AssertionError(f"missing structured content: {raw}")
    return payload


def fallback_text(result: Any) -> str:
    raw = dump(result)
    blocks = raw.get("content") or []
    texts = [str(block.get("text", "")) for block in blocks if isinstance(block, dict) and block.get("type") == "text"]
    return "\n".join(texts).strip()


async def verify_image_urls(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(follow_redirects=True, timeout=30, http2=True) as client:
        for item in items:
            url = item.get("previewUrl")
            if not url or url in seen:
                continue
            seen.add(url)
            response = await client.get(url, headers={"Range": "bytes=0-2047"})
            content_type = response.headers.get("content-type", "")
            observation = {
                "url": url,
                "status_code": response.status_code,
                "content_type": content_type,
                "bytes_received": len(response.content),
            }
            observations.append(observation)
            if response.status_code not in (200, 206) or not content_type.lower().startswith("image/"):
                raise AssertionError(f"direct image fetch failed: {observation}")
            if len(observations) >= 3:
                break
    if not observations:
        raise AssertionError("no renderable image URLs to verify")
    return observations


async def run() -> dict[str, Any]:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "server.py"), "--stdio"],
        cwd=str(ROOT),
        env={**os.environ},
    )

    evidence: dict[str, Any] = {
        "trial": "04",
        "test_mode": "READ_ONLY",
        "target": "MCP Apps gallery binding",
        "view_uri": VIEW_URI,
        "status_dimensions": {},
        "checkpoint": "STARTED",
    }
    persist(evidence)

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=120),
        ) as session:
            init = await session.initialize()
            evidence["initialize"] = dump(init)
            evidence["status_dimensions"]["MCP_TRANSPORT_PASS"] = True
            evidence["checkpoint"] = "MCP_INITIALIZED"
            persist(evidence)

            tools_result = await session.list_tools()
            tools = [dump(tool) for tool in tools_result.tools]
            evidence["tools"] = tools
            gallery_tool = next((tool for tool in tools if tool.get("name") == "recommend_gallery"), None)
            if gallery_tool is None:
                raise AssertionError("recommend_gallery missing from tools/list")
            meta = gallery_tool.get("_meta") or gallery_tool.get("meta") or {}
            if (meta.get("ui") or {}).get("resourceUri") != VIEW_URI:
                raise AssertionError(f"tool UI binding mismatch: {meta}")

            resource = await session.read_resource(VIEW_URI)
            resource_raw = dump(resource)
            evidence["resource"] = resource_raw
            contents = resource_raw.get("contents") or []
            if not contents:
                raise AssertionError("resources/read returned no contents")
            first_content = contents[0]
            mime = first_content.get("mimeType") or first_content.get("mime_type")
            html = first_content.get("text") or ""
            resource_meta = first_content.get("_meta") or first_content.get("meta") or {}
            csp = (resource_meta.get("ui") or {}).get("csp") or {}
            if mime != "text/html;profile=mcp-app":
                raise AssertionError(f"wrong resource mime: {mime}")
            if "data-gallery-root" not in html or "@modelcontextprotocol/ext-apps@2.0.0/app-with-deps" not in html:
                raise AssertionError("gallery HTML missing required MCP Apps markers")
            if "https://cdn.donmai.us" not in (csp.get("resourceDomains") or []):
                raise AssertionError(f"Danbooru CDN absent from CSP resourceDomains: {csp}")
            evidence["status_dimensions"]["UI_RESOURCE_PASS"] = True
            evidence["checkpoint"] = "UI_RESOURCE_VERIFIED"
            persist(evidence)

            static_result = await session.call_tool(
                "recommend_gallery",
                arguments={"mode": "static", "liked_tags": ["hatsune_miku", "twintails"], "limit": 8, "page": 1},
            )
            static_payload = structured_payload(static_result)
            evidence["static_payload"] = static_payload
            if len(static_payload.get("items") or []) != 8:
                raise AssertionError(f"static gallery expected 8 items: {static_payload}")
            text = fallback_text(static_result)
            if not text:
                raise AssertionError("non-UI fallback content is empty")
            evidence["fallback_text_prefix"] = text[:300]
            evidence["status_dimensions"]["FALLBACK_PASS"] = True
            evidence["status_dimensions"]["GALLERY_BIND_PASS"] = True

            first_id = static_payload["items"][0]["id"]
            next_result = await session.call_tool(
                "recommend_next",
                arguments={"mode": "static", "liked_tags": ["hatsune_miku"], "limit": 8, "page": 1},
            )
            next_payload = structured_payload(next_result)
            if next_payload.get("page") != 2 or next_payload["items"][0]["id"] == first_id:
                raise AssertionError("recommend_next did not produce an observable refreshed result")

            feedback_result = await session.call_tool(
                "feedback",
                arguments={"candidate_id": first_id, "value": "like"},
            )
            feedback_payload = structured_payload(feedback_result)
            if feedback_payload.get("status") != "PASS" or feedback_payload.get("persistence") != "ephemeral_trial_only":
                raise AssertionError(f"feedback bridge contract failed: {feedback_payload}")
            evidence["status_dimensions"]["UI_ACTION_TOOL_CONTRACT_PASS"] = True
            evidence["status_dimensions"]["RECOMMENDATION_REFRESH_CONTRACT_PASS"] = True
            evidence["checkpoint"] = "STATIC_AND_INTERACTION_CONTRACT_VERIFIED"
            persist(evidence)

            live_result = await session.call_tool(
                "recommend_gallery",
                arguments={"mode": "live", "liked_tags": ["hatsune_miku"], "limit": 8, "page": 1},
                read_timeout_seconds=timedelta(seconds=120),
            )
            evidence["raw_live_tool_result"] = dump(live_result)
            evidence["checkpoint"] = "LIVE_TOOL_RESULT_RECEIVED"
            persist(evidence)

            live_payload = structured_payload(live_result)
            evidence["live_payload"] = live_payload
            live_items = live_payload.get("items") or []
            if not live_items:
                raise AssertionError(f"live recommendation binding returned zero renderable items: {live_payload}")
            if live_payload.get("mode") != "live":
                raise AssertionError(f"live mode lost at gallery boundary: {live_payload}")
            evidence["status_dimensions"]["REAL_RECOMMENDATION_BIND_PASS"] = True
            evidence["checkpoint"] = "LIVE_RECOMMENDATION_BOUND"
            persist(evidence)

            image_observations = await verify_image_urls(live_items)
            evidence["image_fetch_observations"] = image_observations
            evidence["status_dimensions"]["IMAGE_FETCH_PASS"] = True
            evidence["checkpoint"] = "DIRECT_IMAGE_FETCH_VERIFIED"
            persist(evidence)

    evidence["status_dimensions"]["MODEL_VISION_PASS"] = "NOT_IN_SCOPE"
    evidence["status_dimensions"]["REFERENCE_HOST_GALLERY_RENDER_PASS"] = "PENDING_SEPARATE_BROWSER_TRIAL"
    evidence["status_dimensions"]["CHATGPT_HOST_GALLERY_RENDER"] = "UNATTESTED"
    evidence["overall"] = "CONTRACT_AND_LIVE_BIND_PASS_REFERENCE_HOST_PENDING"
    evidence["checkpoint"] = "COMPLETE"
    persist(evidence)
    return evidence


def main() -> int:
    try:
        evidence = asyncio.run(run())
    except BaseException as exc:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if EVIDENCE.exists():
            try:
                existing = json.loads(EVIDENCE.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        existing.update(
            {
                "trial": "04",
                "overall": "FAIL",
                "exception": f"{type(exc).__name__}: {exc}",
                "exception_tree": flatten_exception_tree(exc),
                "traceback": "".join(traceback.format_exception(exc)),
            }
        )
        persist(existing)
        print(json.dumps(existing, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({"overall": evidence["overall"], "status_dimensions": evidence["status_dimensions"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
