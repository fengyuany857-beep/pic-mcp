import { McpServer } from "@modelcontextprotocol/server";
import { createMcpHandler } from "agents/mcp/server";
import { z } from "zod";
import legacyWorker from "./index-v11";

interface D1AllResult<T> { success: boolean; results: T[] }
interface D1PreparedStatement {
  bind(...values: unknown[]): D1PreparedStatement;
  first<T = Record<string, unknown>>(): Promise<T | null>;
  all<T = Record<string, unknown>>(): Promise<D1AllResult<T>>;
}
interface D1Database {
  prepare(query: string): D1PreparedStatement;
}
interface Env { AUTH_DB: D1Database }

interface LibraryStateRow {
  current_sync_id: string;
  public_count: number;
  complete: number;
  truncated_reason: string | null;
  updated_at: number;
}

interface LibraryItemRow {
  item_id: string;
  title: string | null;
  creator_id: string | null;
  creator_name: string | null;
  tags_json: string;
  page_url: string;
  preview_url: string | null;
  original_url: string | null;
  media_type: string | null;
  content_rating: number | null;
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

function parseTags(raw: string): string[] {
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((value): value is string => typeof value === "string")
      : [];
  } catch {
    return [];
  }
}

/**
 * Read only the public portion of the latest iPhone-synchronized Pixiv snapshot.
 *
 * This is intentionally separate from the legacy OAuth principal path. The
 * no-auth ChatGPT surface must never expose private favorites or mutate D1.
 */
async function readPublicFavorites(
  env: Env,
  limit: number,
  offset: number,
): Promise<Record<string, unknown>> {
  const state = await env.AUTH_DB.prepare(`
    SELECT current_sync_id, public_count, complete, truncated_reason, updated_at
    FROM user_library_state WHERE source = 'pixiv'
  `).first<LibraryStateRow>();

  if (!state) {
    return {
      status: "NOT_CONNECTED",
      source: "pixiv",
      visibility: "public",
      items: [],
      message: "No Pixiv library snapshot has been synchronized yet.",
    };
  }

  const countRow = await env.AUTH_DB.prepare(`
    SELECT COUNT(*) AS count
    FROM user_library_items
    WHERE source = 'pixiv' AND sync_id = ?1 AND library_visibility = 'public'
  `).bind(state.current_sync_id).first<{ count: number }>();
  const filteredTotal = Number(countRow?.count ?? 0);

  const rows = await env.AUTH_DB.prepare(`
    SELECT item_id, title, creator_id, creator_name, tags_json,
           page_url, preview_url, original_url, media_type, content_rating
    FROM user_library_items
    WHERE source = 'pixiv' AND sync_id = ?1 AND library_visibility = 'public'
    ORDER BY CAST(item_id AS INTEGER) DESC
    LIMIT ?2 OFFSET ?3
  `).bind(state.current_sync_id, limit, offset).all<LibraryItemRow>();

  const items = rows.results.map((row) => ({
    source: "pixiv",
    post_id: row.item_id,
    visibility: "public",
    title: row.title,
    creator_id: row.creator_id,
    creator_name: row.creator_name,
    tags: parseTags(row.tags_json),
    page_url: row.page_url,
    preview_url: row.preview_url,
    original_url: row.original_url,
    media_type: row.media_type,
    content_rating: row.content_rating,
  }));

  const nextOffset = offset + items.length < filteredTotal ? offset + items.length : null;
  return {
    status: "PASS",
    source: "pixiv",
    visibility: "public",
    snapshot: {
      complete: state.complete === 1,
      truncated_reason: state.truncated_reason,
      public_count: state.public_count,
      updated_at: new Date(state.updated_at).toISOString(),
    },
    page: {
      filtered_total: filteredTotal,
      offset,
      limit,
      returned: items.length,
      next_offset: nextOffset,
    },
    items,
  };
}

function createPublicReadMcpServer(env: Env): McpServer {
  const server = new McpServer({ name: "pic-mcp", version: "0.12.0-trial06" });

  server.registerTool(
    "get_my_favorites",
    {
      description: "Read the public portion of the latest iPhone-synchronized Pixiv favorites snapshot from PicMCP. No OAuth is required. Read-only; this is not a live Pixiv API call.",
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        openWorldHint: false,
      },
      inputSchema: {
        source: z.literal("pixiv").optional(),
        visibility: z.literal("public").optional(),
        limit: z.number().int().min(1).max(100).optional(),
        offset: z.number().int().min(0).max(10000).optional(),
      },
    },
    async ({ limit, offset }) => {
      try {
        const result = await readPublicFavorites(env, limit ?? 50, offset ?? 0);
        return {
          content: [{ type: "text", text: JSON.stringify(result) }],
        };
      } catch {
        return {
          isError: true,
          content: [{ type: "text", text: JSON.stringify({ status: "ERROR", error: "LIBRARY_READ_FAILED" }) }],
        };
      }
    },
  );

  return server;
}

/**
 * Trial 06 v12 removes OAuth from the ChatGPT read surface only.
 *
 * /mcp: public, no-auth, read-only, public favorites only.
 * Everything else: delegated unchanged to v11, including iPhone session/library
 * sync, Owner Token verification, OAuth legacy routes, health dependencies, and
 * all existing D1 write behavior.
 */
export default {
  async fetch(request: Request, env: Env, ctx: any): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/mcp") {
      return createMcpHandler(
        () => createPublicReadMcpServer(env),
        { route: "/mcp" },
      )(request, env, ctx);
    }

    if (request.method === "GET" && url.pathname === "/health") {
      const response = await legacyWorker.fetch(request, env as any, ctx);
      const body = await response.clone().json().catch(() => ({})) as Record<string, unknown>;
      return json({
        ...body,
        mcp: "no-auth-read-only",
        mcp_auth: "none",
        mcp_endpoint: "/mcp",
        mcp_visibility: "public-only",
        library_read: "d1-snapshot",
        oauth_legacy_routes_retained: true,
      }, response.status);
    }

    return legacyWorker.fetch(request, env as any, ctx);
  },
};
