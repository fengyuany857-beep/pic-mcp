from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
ARTIFACTS = ROOT / "artifacts" / "trial05"
RESULT = ARTIFACTS / "six-source-matrix.json"

sys.path.insert(0, str(REPO_ROOT / "trials" / "upo-01d" / "src"))
sys.path.insert(0, str(ROOT / "src"))

from upo_recommender.adapters import from_booru_pictag_search_posts  # noqa: E402
from media_gateway import fetch_media_probe  # noqa: E402


BOORU_UPSTREAM_COMMIT = "ed234568fc376517d0abc5a377d65af37981b059"
PIXIV_UPSTREAM_COMMIT = "c02e5e67c82f7774b028ccf669567a4d386d1016"

PROVIDER_CASES = {
    "danbooru": {"tags": "hatsune_miku", "rating": "safe"},
    "aibooru": {"tags": "1girl", "rating": "safe"},
    "gelbooru": {"tags": "hatsune_miku", "rating": "safe"},
    "rule34": {"tags": "1girl", "rating": "safe"},
    "e621": {"tags": "solo", "rating": "safe"},
}

AUTH_ENV = {
    "danbooru": ("DANBOORU_USERNAME", "DANBOORU_API_KEY"),
    "aibooru": (),
    "gelbooru": ("GELBOORU_USER_ID", "GELBOORU_API_KEY"),
    "rule34": ("RULE34_USER_ID", "RULE34_API_KEY"),
    "e621": (),
    "pixiv": ("PIXIV_REFRESH_TOKEN",),
}


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
    raise RuntimeError("search_posts returned no payload containing posts[]")


def secret_presence(provider: str) -> dict[str, bool]:
    return {name: bool(os.getenv(name)) for name in AUTH_ENV.get(provider, ())}


def classify_failure(provider: str, exc_text: str) -> str:
    low = exc_text.lower()
    if "401" in low or "unauthorized" in low:
        return "AUTH_REQUIRED_OR_INVALID"
    if "403" in low or "forbidden" in low:
        if provider in {"gelbooru", "rule34"} and not all(secret_presence(provider).values()):
            return "AUTH_REQUIRED"
        return "UPSTREAM_OR_ENVIRONMENT_FORBIDDEN"
    if "429" in low:
        return "RATE_LIMITED"
    if "timeout" in low:
        return "TIMEOUT"
    return "UNCLASSIFIED_FAILURE"


async def probe_aibooru_browser_headers() -> dict[str, Any]:
    """Diagnostic only: determine whether AIBooru's 403 is tied to the upstream UA/header profile.

    This does not become an implementation automatically. It only records whether
    the same public endpoint behaves differently under a browser-like request.
    """
    url = "https://aibooru.online/posts.json"
    params = {
        "limit": 4,
        "page": 1,
        "tags": "1girl order:rank rating:general",
    }
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/143 Safari/537.36",
        "Referer": "https://aibooru.online/",
    }
    try:
        async with httpx.AsyncClient(
            headers=headers,
            timeout=20,
            follow_redirects=True,
            http2=True,
        ) as client:
            response = await client.get(url, params=params)
        result: dict[str, Any] = {
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "ok": response.status_code < 400,
        }
        if response.status_code < 400:
            try:
                parsed = response.json()
            except Exception:
                parsed = None
            result["json_shape"] = type(parsed).__name__ if parsed is not None else None
            result["post_count"] = len(parsed) if isinstance(parsed, list) else None
        return result
    except Exception as exc:
        return {
            "ok": False,
            "exception": f"{type(exc).__name__}: {exc}",
        }


async def call_search_posts(
    session: ClientSession,
    provider: str,
    *,
    tags: str,
    rating: str,
) -> tuple[Any, dict[str, Any]]:
    args = {
        "tags": tags,
        "provider": provider,
        "order": "popular",
        "page": 1,
        "limit": 4,
        "rating": rating,
        "random_seed": 7,
        "include_preview": True,
        "include_file_url": False,
    }
    result = await session.call_tool(
        "search_posts",
        arguments=args,
        read_timeout_seconds=timedelta(seconds=60),
    )
    return result, args


async def probe_booru_sources() -> dict[str, Any]:
    params = StdioServerParameters(
        command="booru-pictag-get-mcp",
        args=[],
        env={**os.environ},
        cwd=str(ROOT),
    )
    output: dict[str, Any] = {}
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=90),
        ) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            if "search_posts" not in tool_names:
                raise RuntimeError("Booru upstream missing search_posts")

            for provider, case in PROVIDER_CASES.items():
                record: dict[str, Any] = {
                    "provider": provider,
                    "auth_env_present": secret_presence(provider),
                    "source_search": "PENDING",
                    "canonicalize": "PENDING",
                    "media_gateway": "PENDING",
                    "render_url_contract": "PENDING",
                }
                try:
                    tool_result, args = await call_search_posts(
                        session,
                        provider,
                        tags=case["tags"],
                        rating=case["rating"],
                    )
                    record["search_arguments"] = args
                    record["tool_is_error"] = bool(getattr(tool_result, "isError", False))
                    if record["tool_is_error"]:
                        text = json.dumps(dump_model(tool_result), ensure_ascii=False)
                        record["source_search"] = classify_failure(provider, text)
                        if provider == "aibooru":
                            diagnostic = await probe_aibooru_browser_headers()
                            record["browser_header_diagnostic"] = diagnostic
                            if diagnostic.get("ok"):
                                record["source_search"] = "UPSTREAM_HEADER_PROFILE_GAP"
                        output[provider] = record
                        continue

                    payload, payload_source = extract_payload(tool_result)
                    record["payload_source"] = payload_source
                    record["post_count"] = len(payload.get("posts", []))

                    if provider == "rule34" and not payload.get("posts"):
                        fallback_result, fallback_args = await call_search_posts(
                            session,
                            provider,
                            tags="hatsune_miku",
                            rating="all",
                        )
                        record["fallback_search_arguments"] = fallback_args
                        record["fallback_tool_is_error"] = bool(
                            getattr(fallback_result, "isError", False)
                        )
                        if not record["fallback_tool_is_error"]:
                            fallback_payload, fallback_source = extract_payload(fallback_result)
                            record["fallback_payload_source"] = fallback_source
                            record["fallback_post_count"] = len(
                                fallback_payload.get("posts", [])
                            )
                            if fallback_payload.get("posts"):
                                payload = fallback_payload
                                payload_source = fallback_source
                                record["fixture_fallback_used"] = True
                                record["post_count"] = len(payload.get("posts", []))

                    # The selected upstream explicitly documents Rule34 API
                    # credentials as required since 2025-08. In our live run,
                    # the unauthenticated endpoint returns HTTP 200 with an
                    # empty list for both safe and all-rating fixtures. Treat
                    # that as a credential gate, not evidence that the source
                    # contains no matching posts.
                    if (
                        provider == "rule34"
                        and not payload.get("posts")
                        and not all(secret_presence("rule34").values())
                    ):
                        record["source_search"] = "AUTH_REQUIRED"
                        record["canonicalize"] = "PENDING_AUTH"
                        record["media_gateway"] = "PENDING_AUTH"
                        record["render_url_contract"] = "PENDING_AUTH"
                        output[provider] = record
                        continue

                    if not payload.get("posts"):
                        record["source_search"] = "EMPTY_VALID"
                        record["canonicalize"] = "NOT_APPLICABLE_EMPTY"
                        record["media_gateway"] = "NOT_APPLICABLE_EMPTY"
                        record["render_url_contract"] = "NOT_APPLICABLE_EMPTY"
                        output[provider] = record
                        continue
                    record["source_search"] = "PASS"

                    candidates = from_booru_pictag_search_posts(payload)
                    record["candidate_count"] = len(candidates)
                    if not candidates:
                        record["canonicalize"] = "FAIL_ZERO_CANDIDATES"
                        output[provider] = record
                        continue
                    record["canonicalize"] = "PASS"

                    first = next(
                        (c for c in candidates if c.preview_url or c.file_url),
                        None,
                    )
                    if first is None:
                        record["media_gateway"] = "NO_MEDIA_URL"
                        output[provider] = record
                        continue
                    media_url = first.preview_url or first.file_url
                    record["sample_candidate_id"] = first.candidate_id
                    record["media_host"] = (
                        media_url.split("/", 3)[2] if "://" in media_url else None
                    )
                    probe = await fetch_media_probe(provider, media_url)
                    record["media_probe"] = probe
                    record["media_gateway"] = "PASS" if probe.get("ok") else "FAIL"
                    record["render_url_contract"] = (
                        "PASS" if probe.get("ok") else "BLOCKED_BY_MEDIA_FETCH"
                    )
                except Exception as exc:
                    exc_text = f"{type(exc).__name__}: {exc}"
                    record["exception"] = exc_text
                    record["source_search"] = classify_failure(provider, exc_text)
                    if provider == "aibooru":
                        diagnostic = await probe_aibooru_browser_headers()
                        record["browser_header_diagnostic"] = diagnostic
                        if diagnostic.get("ok"):
                            record["source_search"] = "UPSTREAM_HEADER_PROFILE_GAP"
                output[provider] = record

            output["_booru_session"] = {
                "initialize": dump_model(init),
                "tool_names": tool_names,
            }
    return output


def probe_pixiv_contract() -> dict[str, Any]:
    from pixiv_mcp_server.preview_proxy import _is_allowed_upstream_url

    auth = secret_presence("pixiv")
    proxy_contract = {
        "accepts_pximg_https": _is_allowed_upstream_url(
            "https://i.pximg.net/img-master/example.jpg"
        ),
        "rejects_http": not _is_allowed_upstream_url(
            "http://i.pximg.net/img-master/example.jpg"
        ),
        "rejects_lookalike": not _is_allowed_upstream_url(
            "https://evilpximg.net/image.jpg"
        ),
    }
    return {
        "provider": "pixiv",
        "auth_env_present": auth,
        "source_search": "READY_FOR_LIVE_AUTH_TEST" if all(auth.values()) else "AUTH_REQUIRED",
        "canonicalize": "PENDING_LIVE_AUTH" if not all(auth.values()) else "READY_FOR_LIVE_AUTH_TEST",
        "media_gateway": "CONTRACT_PASS_LIVE_URL_PENDING_AUTH",
        "render_url_contract": "CONTRACT_PASS_LIVE_URL_PENDING_AUTH",
        "selected_implementation": "222wcnm/pixiv-mcp-server",
        "selected_commit": PIXIV_UPSTREAM_COMMIT,
        "proxy_contract": proxy_contract,
        "proxy_contract_pass": all(proxy_contract.values()),
    }


def stable_state(record: dict[str, Any]) -> str:
    if (
        record.get("source_search") == "PASS"
        and record.get("canonicalize") == "PASS"
        and record.get("media_gateway") == "PASS"
    ):
        return "LIVE_PUBLIC_CHAIN_PASS"
    if record.get("source_search") == "UPSTREAM_HEADER_PROFILE_GAP":
        return "PUBLIC_ENDPOINT_PASS_UPSTREAM_IMPLEMENTATION_GAP"
    if str(record.get("source_search", "")).startswith("AUTH_REQUIRED"):
        return "CREDENTIAL_BLOCKED"
    if record.get("source_search") == "UPSTREAM_OR_ENVIRONMENT_FORBIDDEN":
        return "ENVIRONMENT_OR_PROVIDER_BLOCKED"
    if record.get("source_search") == "EMPTY_VALID":
        return "PUBLIC_SEARCH_EMPTY_VALID_NEEDS_SECOND_FIXTURE"
    if record.get("source_search") == "READY_FOR_LIVE_AUTH_TEST":
        return "AUTH_CONFIGURED_NEEDS_LIVE_RUN"
    return "PARTIAL"


async def run() -> dict[str, Any]:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    matrix = await probe_booru_sources()
    matrix["pixiv"] = probe_pixiv_contract()

    per_provider = {}
    for name in ("danbooru", "aibooru", "gelbooru", "rule34", "e621", "pixiv"):
        matrix[name]["stabilization_state"] = stable_state(matrix[name])
        per_provider[name] = matrix[name]["stabilization_state"]

    evidence = {
        "trial": "05",
        "test_mode": "READ_ONLY",
        "goal": "six-source search/canonical/media-gateway stabilization",
        "booru_upstream_commit": BOORU_UPSTREAM_COMMIT,
        "pixiv_upstream_commit": PIXIV_UPSTREAM_COMMIT,
        "matrix": matrix,
        "per_provider_state": per_provider,
        "overall": (
            "PARTIAL"
            if any(v != "LIVE_PUBLIC_CHAIN_PASS" for v in per_provider.values())
            else "PASS"
        ),
        "notes": [
            "Credential-blocked providers are not promoted to PASS.",
            "Rule34 HTTP 200 empty unauthenticated responses are classified using the selected upstream's documented credential requirement.",
            "Pixiv proxy contract PASS is not a live Pixiv media fetch PASS.",
            "AIBooru browser-header diagnostic is evidence only, not an automatic implementation switch.",
            "Browser Gallery rendering is outside Trial 05 and remains owned by Trial 04.",
        ],
    }
    RESULT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


def main() -> int:
    try:
        evidence = asyncio.run(run())
    except Exception as exc:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        failure = {
            "trial": "05",
            "overall": "FAIL",
            "exception": f"{type(exc).__name__}: {exc}",
        }
        RESULT.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1

    print(
        json.dumps(
            {
                "overall": evidence["overall"],
                "per_provider_state": evidence["per_provider_state"],
                "aibooru_diagnostic": evidence["matrix"]["aibooru"].get(
                    "browser_header_diagnostic"
                ),
                "rule34_fallback_post_count": evidence["matrix"]["rule34"].get(
                    "fallback_post_count"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
