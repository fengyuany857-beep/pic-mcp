from __future__ import annotations

import asyncio
import io
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from PIL import Image
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
TRIAL01 = REPO_ROOT / "trials" / "upo-01d"
TRIAL02 = REPO_ROOT / "trials" / "upo-02"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(TRIAL02 / "src"))

from near_duplicate import compare_fingerprints, fingerprint_bytes  # noqa: E402
from tag_expansion import parse_tool_json  # noqa: E402

ARTIFACTS = ROOT / "artifacts" / "trial03"
WIRE = ARTIFACTS / "mcp-wire-cross-provider.jsonl"
RESULT = ARTIFACTS / "cross-provider-result.json"
SOURCE_TAG = "hatsune_miku"


def tool_text(result: Any) -> str:
    texts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            texts.append(str(text))
    return "\n".join(texts)


async def download_image(client: httpx.AsyncClient, url: str) -> bytes:
    response = await client.get(url, timeout=45.0, follow_redirects=True)
    response.raise_for_status()
    data = response.content
    with Image.open(io.BytesIO(data)) as img:
        img.verify()
    return data


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

    provider_payloads: dict[str, dict[str, Any]] = {}
    provider_status: dict[str, dict[str, Any]] = {}

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream, read_timeout_seconds=timedelta(seconds=90)) as session:
            await session.initialize()
            for provider in ("danbooru", "gelbooru", "aibooru"):
                try:
                    result = await session.call_tool(
                        "search_posts",
                        arguments={
                            "tags": SOURCE_TAG,
                            "provider": provider,
                            "order": "popular",
                            "page": 1,
                            "limit": 6,
                            "rating": "safe",
                            "include_preview": False,
                            "include_file_url": True,
                        },
                        read_timeout_seconds=timedelta(seconds=60),
                    )
                except Exception as exc:
                    provider_status[provider] = {
                        "status": "CALL_EXCEPTION",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    continue

                if getattr(result, "isError", False):
                    provider_status[provider] = {
                        "status": "MCP_ERROR",
                        "text": tool_text(result)[:2000],
                    }
                    continue

                try:
                    payload = parse_tool_json(result)
                except Exception as exc:
                    provider_status[provider] = {
                        "status": "UNPARSEABLE_RESULT",
                        "error": str(exc),
                        "text": tool_text(result)[:2000],
                    }
                    continue

                posts = [p for p in (payload.get("posts") or []) if p.get("file_url")]
                provider_payloads[provider] = payload
                provider_status[provider] = {
                    "status": "PASS" if posts else "EMPTY_OR_UNAVAILABLE",
                    "post_count": len(posts),
                    "raw_status": payload.get("status"),
                    "error": payload.get("error"),
                }

    downloaded: dict[str, list[dict[str, Any]]] = {}
    async with httpx.AsyncClient(http2=True, headers={"User-Agent": "UPO-Trial03B/0.1"}) as client:
        for provider, payload in provider_payloads.items():
            downloaded[provider] = []
            for post in payload.get("posts") or []:
                url = post.get("file_url")
                if not url:
                    continue
                try:
                    data = await download_image(client, str(url))
                except Exception as exc:
                    downloaded[provider].append({
                        "post_id": post.get("id"),
                        "status": "DOWNLOAD_FAIL",
                        "error": str(exc),
                    })
                    continue
                fp = fingerprint_bytes(data)
                downloaded[provider].append({
                    "post_id": post.get("id"),
                    "status": "PASS",
                    "file_url": url,
                    "fingerprint": fp.to_dict(),
                    "fp_obj": fp,
                })
                if sum(x.get("status") == "PASS" for x in downloaded[provider]) >= 3:
                    break

    dan = [x for x in downloaded.get("danbooru", []) if x.get("status") == "PASS"]
    if not dan:
        raise RuntimeError("Danbooru baseline produced no downloadable images")

    comparisons: list[dict[str, Any]] = []
    observed_matches: list[dict[str, Any]] = []
    alternate_providers_exercised: list[str] = []

    for provider in ("gelbooru", "aibooru"):
        others = [x for x in downloaded.get(provider, []) if x.get("status") == "PASS"]
        if not others:
            continue
        alternate_providers_exercised.append(provider)
        for left in dan:
            for right in others:
                cmp = compare_fingerprints(left["fp_obj"], right["fp_obj"])
                record = {
                    "left_provider": "danbooru",
                    "left_post_id": left["post_id"],
                    "right_provider": provider,
                    "right_post_id": right["post_id"],
                    "decision": cmp.decision,
                    "phash_distance": cmp.phash_distance,
                    "crop_match": cmp.crop_match,
                    "crop_distance": cmp.crop_distance,
                }
                comparisons.append(record)
                if cmp.decision != "DISTINCT":
                    observed_matches.append(record)

    # Remove non-serializable helper objects.
    serial_downloaded: dict[str, list[dict[str, Any]]] = {}
    for provider, rows in downloaded.items():
        serial_downloaded[provider] = [
            {k: v for k, v in row.items() if k != "fp_obj"}
            for row in rows
        ]

    gel_status = provider_status.get("gelbooru", {})
    if gel_status.get("status") != "PASS" and not (os.getenv("GELBOORU_USER_ID") and os.getenv("GELBOORU_API_KEY")):
        gel_status["availability_classification"] = "CREDENTIAL_BLOCKED_OR_EMPTY_WITHOUT_CREDENTIALS"

    evidence = {
        "trial": "03B",
        "status": "PASS" if alternate_providers_exercised else "PARTIAL_NO_ALTERNATE_PROVIDER",
        "source_tag": SOURCE_TAG,
        "provider_status": provider_status,
        "downloaded": serial_downloaded,
        "alternate_providers_exercised": alternate_providers_exercised,
        "pairwise_comparison_count": len(comparisons),
        "observed_cross_provider_matches": observed_matches,
        "cross_provider_match_observation": (
            "OBSERVED" if observed_matches else "NOT_OBSERVED_IN_SAMPLE"
        ),
        "interpretation": (
            "Cross-provider execution path was exercised with the same fingerprint contract. "
            "Absence of a match in this bounded sample is not proof that cross-site duplicates do not exist."
        ),
    }
    RESULT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return evidence


def main() -> int:
    try:
        ev = asyncio.run(run())
    except Exception as exc:
        failure = {"trial": "03B", "status": "FAIL", "exception": f"{type(exc).__name__}: {exc}"}
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1
    return 0 if ev["status"] in {"PASS", "PARTIAL_NO_ALTERNATE_PROVIDER"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
