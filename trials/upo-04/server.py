from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Literal

import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP
from starlette.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parent
TRIAL_SRC = ROOT / "src"
RECOMMENDER_SRC = ROOT.parent / "upo-01d" / "src"
for path in (TRIAL_SRC, RECOMMENDER_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from gallery_bind import bind_gallery
from gallery_models import FeedbackAck, GalleryPayload
from upo_recommender.adapters import from_booru_pictag_search_posts
from upo_recommender.models import Candidate, RecommendationRequest, RecommendationResult
from upo_recommender.recommend import recommend

VIEW_URI = "ui://media/recommendations"
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "3001"))
UPSTREAM_COMMAND = os.environ.get("BOORU_MCP_COMMAND", "booru-pictag-get-mcp")

mcp = FastMCP("PicMCP Gallery Trial04", stateless_http=True, host=HOST, port=PORT)

STATIC_IMAGES = [
    (
        "https://cdn.donmai.us/original/8a/a2/8aa22ae544f403e95bd30e7b60d1e312.png",
        "https://danbooru.donmai.us/posts/12196303",
    ),
    (
        "https://cdn.donmai.us/original/80/5c/805c50b845c5b8738ca5f23871f74a92.jpg",
        "https://danbooru.donmai.us/posts/12194821",
    ),
    (
        "https://cdn.donmai.us/original/b5/cc/b5cc22c55386073e4bda3e60f1d25ab2.jpg",
        "https://danbooru.donmai.us/posts/12193875",
    ),
]
STATIC_TAGS = [
    ("hatsune_miku", "twintails", "blue_hair"),
    ("hatsune_miku", "red_eyes", "solo"),
    ("hatsune_miku", "black_hair", "smile"),
    ("hatsune_miku", "twintails", "portrait"),
    ("hatsune_miku", "red_eyes", "long_hair"),
    ("hatsune_miku", "black_hair", "looking_at_viewer"),
    ("hatsune_miku", "twintails", "solo"),
    ("hatsune_miku", "red_eyes", "portrait"),
]


def _static_recommendation(page: int, liked_tags: list[str], limit: int) -> RecommendationResult:
    candidates: list[Candidate] = []
    for i in range(8):
        image_url, page_url = STATIC_IMAGES[i % len(STATIC_IMAGES)]
        source_id = 400000 + page * 100 + i
        candidates.append(
            Candidate(
                candidate_id=f"trial04-fixture:{source_id}",
                provider="trial04-fixture",
                source_id=source_id,
                post_url=page_url,
                tags=STATIC_TAGS[i],
                source_score=float(100 - i),
                preview_url=image_url,
                file_url=image_url,
            )
        )
    req = RecommendationRequest(
        liked_tags=liked_tags or ["hatsune_miku", "twintails"],
        limit=min(limit, len(candidates)),
        diversity_lambda=0.75,
    )
    return recommend(candidates, req, assembly_mode="fixture_contract_trial")


async def _live_recommendation(page: int, liked_tags: list[str], limit: int) -> RecommendationResult:
    query_tags = liked_tags or ["hatsune_miku"]
    params = StdioServerParameters(
        command=UPSTREAM_COMMAND,
        args=[],
        env={**os.environ},
        cwd=str(ROOT),
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=90),
        ) as session:
            await session.initialize()
            tools = await session.list_tools()
            if "search_posts" not in {tool.name for tool in tools.tools}:
                raise RuntimeError("pinned upstream server does not expose search_posts")

            result = await session.call_tool(
                "search_posts",
                arguments={
                    "tags": " ".join(query_tags),
                    "provider": "danbooru",
                    "order": "popular",
                    "page": max(1, page),
                    "limit": max(8, min(20, limit + 4)),
                    "rating": "safe",
                    "random_seed": max(1, page),
                    "include_preview": True,
                    "include_file_url": True,
                },
                read_timeout_seconds=timedelta(seconds=60),
            )
            if getattr(result, "isError", False):
                text = "\n".join(getattr(block, "text", "") for block in result.content or [])
                raise RuntimeError(f"upstream search_posts failed: {text}")

            payload = getattr(result, "structuredContent", None)
            if not isinstance(payload, dict) or not isinstance(payload.get("posts"), list):
                raise RuntimeError("upstream search_posts returned no structuredContent.posts[]")

            candidates = from_booru_pictag_search_posts(payload)
            req = RecommendationRequest(
                liked_tags=query_tags,
                limit=min(limit, len(candidates)),
                diversity_lambda=0.75,
            )
            return recommend(candidates, req, assembly_mode="live_mcp")


async def _build_gallery(
    mode: Literal["static", "live"],
    liked_tags: list[str] | None,
    limit: int,
    page: int,
) -> GalleryPayload:
    tags = [tag for tag in (liked_tags or []) if tag]
    bounded_limit = max(1, min(12, limit))
    bounded_page = max(1, page)
    if mode == "live":
        rec = await _live_recommendation(bounded_page, tags, bounded_limit)
    else:
        rec = _static_recommendation(bounded_page, tags, bounded_limit)
    return bind_gallery(rec, mode=mode, page=bounded_page)


@mcp.tool(meta={"ui": {"resourceUri": VIEW_URI}})
async def recommend_gallery(
    mode: Literal["static", "live"] = "static",
    liked_tags: list[str] | None = None,
    limit: int = 8,
    page: int = 1,
) -> GalleryPayload:
    """Return recommendation results bound to the independent MCP Apps gallery contract."""
    return await _build_gallery(mode, liked_tags, limit, page)


@mcp.tool(meta={"ui": {"visibility": ["app"]}})
async def recommend_next(
    mode: Literal["static", "live"] = "static",
    liked_tags: list[str] | None = None,
    limit: int = 8,
    page: int = 1,
) -> GalleryPayload:
    """Request the next recommendation page; ranking stays owned by the existing recommender."""
    return await _build_gallery(mode, liked_tags, limit, page + 1)


@mcp.tool(meta={"ui": {"visibility": ["app"]}})
async def recommend_similar(
    tags: list[str],
    mode: Literal["static", "live"] = "static",
    limit: int = 8,
    page: int = 1,
) -> GalleryPayload:
    """Feed selected item tags back into the existing recommendation request contract."""
    return await _build_gallery(mode, tags[:6], limit, page + 1)


@mcp.tool(meta={"ui": {"visibility": ["app"]}})
def feedback(candidate_id: str, value: Literal["like", "dislike"]) -> FeedbackAck:
    """Trial-only interaction acknowledgement. No preference persistence is implemented here."""
    return FeedbackAck(candidate_id=candidate_id, value=value)


GALLERY_HTML = r'''<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="color-scheme" content="light dark" />
  <style>
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; background: transparent; font: 14px system-ui, sans-serif; }
    body { padding: 12px; }
    #status { min-height: 22px; opacity: .72; margin-bottom: 8px; }
    #toolbar { display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
    button, a.action { border: 1px solid color-mix(in srgb, currentColor 22%, transparent); border-radius: 10px; padding: 7px 10px; background: color-mix(in srgb, Canvas 92%, currentColor 8%); color: inherit; text-decoration: none; cursor: pointer; }
    #gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; }
    .card { border: 1px solid color-mix(in srgb, currentColor 18%, transparent); border-radius: 14px; overflow: hidden; background: color-mix(in srgb, Canvas 96%, currentColor 4%); }
    .card img { width: 100%; aspect-ratio: 1 / 1; object-fit: cover; display: block; background: rgba(127,127,127,.12); }
    .meta { padding: 8px; display: grid; gap: 6px; }
    .tags { opacity: .72; font-size: 12px; line-height: 1.35; max-height: 3.2em; overflow: hidden; }
    .actions { display: flex; flex-wrap: wrap; gap: 6px; }
  </style>
</head>
<body>
  <div id="status" data-status>Connecting…</div>
  <div id="toolbar"><button data-action="next">🔄 换一批</button></div>
  <main id="gallery" data-gallery-root></main>
  <script type="module">
    import { App } from "https://unpkg.com/@modelcontextprotocol/ext-apps@2.0.0/app-with-deps";

    const app = new App({ name: "PicMCP Gallery", version: "0.1.0" });
    let current = null;
    const gallery = document.querySelector("[data-gallery-root]");
    const status = document.querySelector("[data-status]");

    function esc(value) {
      return String(value ?? "").replace(/[&<>\"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"})[ch]);
    }

    function render(payload) {
      current = payload;
      const items = Array.isArray(payload?.items) ? payload.items : [];
      status.textContent = `${payload?.mode ?? "unknown"} · page ${payload?.page ?? "?"} · ${items.length} items`;
      gallery.innerHTML = items.map((item, index) => `
        <article class="card" data-card data-id="${esc(item.id)}">
          <a href="${esc(item.pageUrl)}" target="_blank" rel="noreferrer">
            <img data-gallery-image data-index="${index}" src="${esc(item.previewUrl)}" alt="${esc(item.id)}" loading="eager" />
          </a>
          <div class="meta">
            <strong>${esc(item.id)}</strong>
            <div>score ${Number(item.score ?? 0).toFixed(4)}</div>
            <div class="tags">${esc((item.tags ?? []).slice(0, 10).join(" · "))}</div>
            <div class="actions">
              <button data-like="${esc(item.id)}">❤️ 喜欢</button>
              <button data-similar="${esc(item.id)}" data-tags="${esc(JSON.stringify((item.tags ?? []).slice(0, 6)))}">🖼️ 继续类似</button>
              <a class="action" href="${esc(item.originalUrl)}" target="_blank" rel="noreferrer">↗ 原图</a>
            </div>
          </div>
        </article>`).join("");
    }

    async function callAndRender(name, args) {
      status.textContent = `${name}…`;
      const result = await app.callServerTool({ name, arguments: args });
      if (result?.structuredContent?.items) render(result.structuredContent);
      return result;
    }

    app.ontoolresult = (result) => {
      if (result?.structuredContent?.items) render(result.structuredContent);
    };
    app.onerror = (error) => { status.textContent = `App error: ${error?.message ?? error}`; };

    document.addEventListener("click", async (event) => {
      const next = event.target.closest("[data-action='next']");
      if (next && current) {
        await callAndRender("recommend_next", { mode: current.mode, page: current.page, limit: 8 });
        return;
      }
      const like = event.target.closest("[data-like]");
      if (like) {
        const result = await app.callServerTool({ name: "feedback", arguments: { candidate_id: like.dataset.like, value: "like" } });
        status.textContent = result?.structuredContent?.status === "PASS" ? "Feedback acknowledged (ephemeral)" : "Feedback call returned";
        return;
      }
      const similar = event.target.closest("[data-similar]");
      if (similar && current) {
        let tags = [];
        try { tags = JSON.parse(similar.dataset.tags || "[]"); } catch {}
        await callAndRender("recommend_similar", { tags, mode: current.mode, page: current.page, limit: 8 });
      }
    });

    await app.connect();
    status.textContent = "Connected; waiting for tool result…";
  </script>
</body>
</html>'''


@mcp.resource(
    VIEW_URI,
    mime_type="text/html;profile=mcp-app",
    meta={
        "ui": {
            "csp": {
                "resourceDomains": [
                    "https://unpkg.com",
                    "https://cdn.donmai.us",
                ]
            }
        }
    },
)
def gallery_view() -> str:
    return GALLERY_HTML


if __name__ == "__main__":
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        app = mcp.streamable_http_app()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["mcp-session-id"],
        )
        uvicorn.run(app, host=HOST, port=PORT)
