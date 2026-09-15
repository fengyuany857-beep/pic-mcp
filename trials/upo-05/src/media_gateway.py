from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


MAX_MEDIA_BYTES = 20 * 1024 * 1024
DEFAULT_UA = "PicMCP-Source-Stabilization/0.1"


@dataclass(frozen=True)
class MediaPolicy:
    provider: str
    allowed_suffixes: tuple[str, ...]
    referer: str | None = None
    user_agent: str = DEFAULT_UA


POLICIES: dict[str, MediaPolicy] = {
    "danbooru": MediaPolicy(
        "danbooru",
        ("donmai.us",),
        referer="https://danbooru.donmai.us/",
    ),
    "aibooru": MediaPolicy(
        "aibooru",
        ("aibooru.online",),
        referer="https://aibooru.online/",
    ),
    "gelbooru": MediaPolicy(
        "gelbooru",
        ("gelbooru.com",),
        referer="https://gelbooru.com/",
    ),
    "rule34": MediaPolicy(
        "rule34",
        ("rule34.xxx",),
        referer="https://rule34.xxx/",
    ),
    "e621": MediaPolicy(
        "e621",
        ("e621.net",),
        referer="https://e621.net/",
        user_agent="PicMCP/0.1 (source stabilization; GitHub Actions)",
    ),
    "pixiv": MediaPolicy(
        "pixiv",
        ("pximg.net", "pixiv.net"),
        referer="https://www.pixiv.net/",
        user_agent="Mozilla/5.0 (PicMCP Media Gateway Trial)",
    ),
}


def _host_allowed(host: str, suffixes: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)


def validate_media_url(provider: str, url: str) -> MediaPolicy:
    policy = POLICIES.get(provider)
    if policy is None:
        raise ValueError(f"unsupported provider: {provider}")

    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid media URL") from exc

    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("media URL must be credential-free HTTPS")
    if port not in (None, 443):
        raise ValueError("non-standard media port rejected")
    if not host or not _host_allowed(host, policy.allowed_suffixes):
        raise ValueError(f"media host not allowed for {provider}: {host}")
    return policy


async def fetch_media_probe(
    provider: str,
    url: str,
    *,
    timeout: float = 25.0,
    max_bytes: int = MAX_MEDIA_BYTES,
) -> dict:
    """Read-only probe of a provider media URL through the common gateway policy.

    The trial consumes a preview URL rather than an original whenever available.
    It reads at most max_bytes and returns evidence, not the image body.
    """
    policy = validate_media_url(provider, url)
    headers = {"User-Agent": policy.user_agent, "Accept": "image/*"}
    if policy.referer:
        headers["Referer"] = policy.referer

    total = 0
    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
        http2=True,
    ) as client:
        async with client.stream("GET", url) as response:
            status = response.status_code
            ctype = response.headers.get("content-type", "")
            length = response.headers.get("content-length")
            if length:
                try:
                    if int(length) > max_bytes:
                        return {
                            "ok": False,
                            "provider": provider,
                            "status_code": status,
                            "content_type": ctype,
                            "failure": "CONTENT_LENGTH_LIMIT",
                        }
                except ValueError:
                    pass

            if status >= 400:
                return {
                    "ok": False,
                    "provider": provider,
                    "status_code": status,
                    "content_type": ctype,
                    "failure": "HTTP_ERROR",
                }

            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    return {
                        "ok": False,
                        "provider": provider,
                        "status_code": status,
                        "content_type": ctype,
                        "failure": "STREAM_SIZE_LIMIT",
                    }

    return {
        "ok": status < 400 and ctype.lower().startswith("image/"),
        "provider": provider,
        "status_code": status,
        "content_type": ctype,
        "bytes_read": total,
        "referer_applied": bool(policy.referer),
        "failure": None if ctype.lower().startswith("image/") else "NON_IMAGE_CONTENT_TYPE",
    }
