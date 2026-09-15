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
from PIL import Image, ImageEnhance
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
WIRE = ARTIFACTS / "mcp-wire.jsonl"
RESULT = ARTIFACTS / "result.json"
SOURCE_TAG = "hatsune_miku"
UPSTREAM_COMMIT = "ed234568fc376517d0abc5a377d65af37981b059"
IMAGEHASH_VERSION = "4.3.2"


def dump_model(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


def encode_jpeg(image: Image.Image, *, quality: int) -> bytes:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def make_variants(data: bytes) -> dict[str, bytes]:
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        base = img.convert("RGB")
        w, h = base.size

        resized = base.resize((max(64, w // 2), max(64, h // 2)), Image.Resampling.LANCZOS)
        recompressed = encode_jpeg(base, quality=48)

        margin_x = max(1, int(w * 0.04))
        margin_y = max(1, int(h * 0.04))
        cropped = base.crop((margin_x, margin_y, w - margin_x, h - margin_y))
        cropped = cropped.resize((w, h), Image.Resampling.LANCZOS)

        # Small global tone change is common after reposting/export.
        bright = ImageEnhance.Brightness(base).enhance(1.06)

        return {
            "exact_copy": data,
            "resize_50pct": encode_jpeg(resized, quality=88),
            "recompress_q48": recompressed,
            "light_crop_4pct": encode_jpeg(cropped, quality=88),
            "brightness_106pct": encode_jpeg(bright, quality=88),
        }


async def download_image(client: httpx.AsyncClient, url: str) -> bytes:
    response = await client.get(url, timeout=45.0, follow_redirects=True)
    response.raise_for_status()
    data = response.content
    # Ensure Pillow can decode it before treating it as an image sample.
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

    evidence: dict[str, Any] = {
        "trial": "03",
        "source_tag": SOURCE_TAG,
        "upstream_repository": "echo-xianyu/Booru-Pictag-Get-MCP",
        "upstream_commit": UPSTREAM_COMMIT,
        "near_duplicate_implementation": "JohannesBuchner/ImageHash",
        "imagehash_version": IMAGEHASH_VERSION,
        "capability": "CAP-NEAR-DUPLICATE",
        "threshold_policy_status": "PROVISIONAL_TRIAL_POLICY",
    }

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream, read_timeout_seconds=timedelta(seconds=90)) as session:
            init = await session.initialize()
            evidence["initialize"] = dump_model(init)
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            evidence["tool_names"] = tool_names
            if "search_posts" not in tool_names:
                raise RuntimeError("search_posts missing from upstream MCP")

            result = await session.call_tool(
                "search_posts",
                arguments={
                    "tags": SOURCE_TAG,
                    "provider": "danbooru",
                    "order": "popular",
                    "page": 1,
                    "limit": 10,
                    "rating": "safe",
                    "include_preview": False,
                    "include_file_url": True,
                },
                read_timeout_seconds=timedelta(seconds=60),
            )
            if getattr(result, "isError", False):
                raise RuntimeError("search_posts returned MCP error")
            payload = parse_tool_json(result)
            posts = [p for p in (payload.get("posts") or []) if p.get("file_url")]
            if len(posts) < 2:
                raise RuntimeError(f"need >=2 downloadable live images, got {len(posts)}")

    samples: list[dict[str, Any]] = []
    async with httpx.AsyncClient(http2=True, headers={"User-Agent": "UPO-Trial03/0.1"}) as client:
        for post in posts:
            try:
                data = await download_image(client, str(post["file_url"]))
            except Exception as exc:
                samples.append({"post_id": post.get("id"), "download": "FAIL", "error": str(exc)})
                continue
            samples.append({
                "post_id": post.get("id"),
                "download": "PASS",
                "file_url": post.get("file_url"),
                "data": data,
            })
            if sum(1 for s in samples if s.get("download") == "PASS") >= 2:
                break

    usable = [s for s in samples if s.get("download") == "PASS"]
    if len(usable) < 2:
        raise RuntimeError("could not download two live Pillow-decodable images")

    base = usable[0]
    unrelated = usable[1]
    variants = make_variants(base["data"])

    base_fp = fingerprint_bytes(base["data"])
    pair_results: list[dict[str, Any]] = []

    for name, data in variants.items():
        fp = fingerprint_bytes(data)
        comparison = compare_fingerprints(base_fp, fp)
        pair_results.append({
            "pair": f"base_vs_{name}",
            "expected": "DUPLICATE",
            "fingerprint": fp.to_dict(),
            "comparison": comparison.to_dict(),
        })

    unrelated_fp = fingerprint_bytes(unrelated["data"])
    unrelated_cmp = compare_fingerprints(base_fp, unrelated_fp)
    pair_results.append({
        "pair": "base_vs_unrelated_live_post",
        "expected": "DISTINCT",
        "fingerprint": unrelated_fp.to_dict(),
        "comparison": unrelated_cmp.to_dict(),
    })

    # Trial gate: every controlled positive must be caught, while the live
    # unrelated negative must remain distinct. This validates the mechanism,
    # not universal threshold calibration.
    false_negatives = [
        p["pair"] for p in pair_results
        if p["expected"] == "DUPLICATE" and p["comparison"]["decision"] == "DISTINCT"
    ]
    false_positives = [
        p["pair"] for p in pair_results
        if p["expected"] == "DISTINCT" and p["comparison"]["decision"] != "DISTINCT"
    ]

    evidence.update({
        "live_source_posts": [
            {"post_id": base["post_id"], "file_url": base["file_url"]},
            {"post_id": unrelated["post_id"], "file_url": unrelated["file_url"]},
        ],
        "base_fingerprint": base_fp.to_dict(),
        "pair_results": pair_results,
        "false_negatives": false_negatives,
        "false_positives": false_positives,
        "mechanism_gate": {
            "controlled_positive_count": sum(p["expected"] == "DUPLICATE" for p in pair_results),
            "live_negative_count": sum(p["expected"] == "DISTINCT" for p in pair_results),
        },
    })

    wire = WIRE.read_text(encoding="utf-8") if WIRE.exists() else ""
    markers = {
        "initialize": "initialize" in wire,
        "tools/list": "tools/list" in wire,
        "search_posts_call": "search_posts" in wire,
    }
    evidence["wire_attestation"] = {"nonempty": bool(wire.strip()), "markers": markers}

    if false_negatives or false_positives:
        evidence["status"] = "FAIL_THRESHOLD_OR_MECHANISM"
        RESULT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError(f"dedup gate failed: FN={false_negatives}, FP={false_positives}")
    if not wire.strip() or not all(markers.values()):
        evidence["status"] = "FAIL_WIRE_ATTESTATION"
        RESULT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError(f"wire attestation incomplete: {markers}")

    evidence["status"] = "PASS"
    RESULT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


def main() -> int:
    try:
        ev = asyncio.run(run())
    except Exception as exc:
        # Preserve richer result.json if run() already emitted one.
        if not RESULT.exists():
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            RESULT.write_text(json.dumps({
                "trial": "03",
                "status": "FAIL",
                "exception": f"{type(exc).__name__}: {exc}",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Trial03 FAIL: {type(exc).__name__}: {exc}")
        return 1

    print(json.dumps({
        "status": ev["status"],
        "live_source_posts": ev["live_source_posts"],
        "pairs": [
            {
                "pair": p["pair"],
                "expected": p["expected"],
                "decision": p["comparison"]["decision"],
                "phash_distance": p["comparison"]["phash_distance"],
                "crop_match": p["comparison"]["crop_match"],
                "crop_distance": p["comparison"]["crop_distance"],
            }
            for p in ev["pair_results"]
        ],
        "false_negatives": ev["false_negatives"],
        "false_positives": ev["false_positives"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
