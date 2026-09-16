import { McpServer } from "@modelcontextprotocol/server";
import { createMcpHandler } from "agents/mcp/server";
import { z } from "zod";
import baseWorker from "./index-v5";

interface D1RunResult { success: boolean; meta: { changes?: number } }
interface D1AllResult<T> { success: boolean; results: T[] }
interface D1PreparedStatement {
  bind(...values: unknown[]): D1PreparedStatement;
  run(): Promise<D1RunResult>;
  first<T = Record<string, unknown>>(): Promise<T | null>;
  all<T = Record<string, unknown>>(): Promise<D1AllResult<T>>;
}
interface D1Database {
  prepare(query: string): D1PreparedStatement;
  batch(statements: D1PreparedStatement[]): Promise<unknown[]>;
}
interface Env { AUTH_DB: D1Database }

interface McpPrincipal {
  source: "pixiv";
  accountId: string;
}

interface LibraryStateRow {
  account_id: string;
  current_sync_id: string;
  public_count: number;
  private_count: number;
  item_count: number;
  complete: number;
  truncated_reason: string | null;
  updated_at: number;
}

interface LibraryItemRow {
  item_id: string;
  library_visibility: "public" | "private";
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

function json(data: unknown, status = 200, extraHeaders: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      ...extraHeaders,
    },
  });
}

function randomToken(byteCount = 32): string {
  const bytes = crypto.getRandomValues(new Uint8Array(byteCount));
  let binary = "";
  for (const value of bytes) binary += String.fromCharCode(value);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function ensureMcpSchema(env: Env): Promise<void> {
  await env.AUTH_DB.batch([
    env.AUTH_DB.prepare(`CREATE TABLE IF NOT EXISTS mcp_access_tokens (
      source TEXT PRIMARY KEY,
      account_id TEXT NOT NULL,
      token_hash TEXT NOT NULL UNIQUE,
      created_at INTEGER NOT NULL
    )`),
    env.AUTH_DB.prepare(`CREATE TABLE IF NOT EXISTS user_library_items (
      source TEXT NOT NULL,
      item_id TEXT NOT NULL,
      library_visibility TEXT NOT NULL,
      title TEXT,
      creator_id TEXT,
      creator_name TEXT,
      tags_json TEXT NOT NULL,
      page_url TEXT NOT NULL,
      preview_url TEXT,
      original_url TEXT,
      media_type TEXT,
      content_rating INTEGER,
      sync_id TEXT NOT NULL,
      synced_at INTEGER NOT NULL,
      PRIMARY KEY (source, item_id)
    )`),
    env.AUTH_DB.prepare(`CREATE TABLE IF NOT EXISTS user_library_state (
      source TEXT PRIMARY KEY,
      account_id TEXT NOT NULL,
      current_sync_id TEXT NOT NULL,
      public_count INTEGER NOT NULL,
      private_count INTEGER NOT NULL,
      item_count INTEGER NOT NULL,
      complete INTEGER NOT NULL,
      truncated_reason TEXT,
      updated_at INTEGER NOT NULL
    )`),
  ]);
}

async function rotateMcpAccessToken(env: Env, accountId: string): Promise<string> {
  await ensureMcpSchema(env);
  const token = randomToken(32);
  const tokenHash = await sha256Hex(token);
  await env.AUTH_DB.prepare(`
    INSERT INTO mcp_access_tokens (source, account_id, token_hash, created_at)
    VALUES ('pixiv', ?1, ?2, ?3)
    ON CONFLICT(source) DO UPDATE SET
      account_id = excluded.account_id,
      token_hash = excluded.token_hash,
      created_at = excluded.created_at
  `).bind(accountId, tokenHash, Date.now()).run();
  return token;
}

function unauthorized(message: string, status = 401): Response {
  return json(
    { status: "ERROR", error: "MCP_AUTH_REQUIRED", message },
    status,
    { "www-authenticate": "Bearer realm=\"pic-mcp\"" },
  );
}

async function authenticateMcp(request: Request, env: Env): Promise<McpPrincipal | Response> {
  await ensureMcpSchema(env);
  const authorization = request.headers.get("authorization") ?? "";
  const match = /^Bearer ([A-Za-z0-9_-]{40,256})$/.exec(authorization);
  if (!match) return unauthorized("A PicMCP bearer token is required.");

  const tokenHash = await sha256Hex(match[1]);
  const row = await env.AUTH_DB.prepare(
    "SELECT source, account_id FROM mcp_access_tokens WHERE token_hash = ?1"
  ).bind(tokenHash).first<{ source: string; account_id: string }>();

  if (!row || row.source !== "pixiv" || !/^\d{1,32}$/.test(row.account_id)) {
    return unauthorized("The PicMCP bearer token is invalid or has been rotated.", 403);
  }
  return { source: "pixiv", accountId: row.account_id };
}

function parseTags(raw: string): string[] {
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((value): value is string => typeof value === "string");
  } catch {
    return [];
  }
}

async function readFavorites(
  env: Env,
  principal: McpPrincipal,
  visibility: "all" | "public" | "private",
  limit: number,
  offset: number,
): Promise<Record<string, unknown>> {
  await ensureMcpSchema(env);
  const state = await env.AUTH_DB.prepare(`
    SELECT account_id, current_sync_id, public_count, private_count, item_count,
           complete, truncated_reason, updated_at
    FROM user_library_state WHERE source = 'pixiv'
  `).first<LibraryStateRow>();

  if (!state) {
    return {
      status: "NOT_CONNECTED",
      source: "pixiv",
      items: [],
      message: "No Pixiv library snapshot has been synchronized yet.",
    };
  }
  if (state.account_id !== principal.accountId) {
    throw new Error("MCP principal does not match the current Pixiv library snapshot.");
  }

  const where = ["source = 'pixiv'", "sync_id = ?1"];
  const values: unknown[] = [state.current_sync_id];
  if (visibility !== "all") {
    values.push(visibility);
    where.push(`library_visibility = ?${values.length}`);
  }

  const countRow = await env.AUTH_DB.prepare(
    `SELECT COUNT(*) AS count FROM user_library_items WHERE ${where.join(" AND ")}`
  ).bind(...values).first<{ count: number }>();
  const filteredTotal = Number(countRow?.count ?? 0);

  const queryValues = [...values, limit, offset];
  const limitIndex = queryValues.length - 1;
  const offsetIndex = queryValues.length;
  const rows = await env.AUTH_DB.prepare(`
    SELECT item_id, library_visibility, title, creator_id, creator_name, tags_json,
           page_url, preview_url, original_url, media_type, content_rating
    FROM user_library_items
    WHERE ${where.join(" AND ")}
    ORDER BY CAST(item_id AS INTEGER) DESC
    LIMIT ?${limitIndex} OFFSET ?${offsetIndex}
  `).bind(...queryValues).all<LibraryItemRow>();

  const items = rows.results.map((row) => ({
    source: "pixiv",
    post_id: row.item_id,
    visibility: row.library_visibility,
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
    visibility,
    snapshot: {
      complete: state.complete === 1,
      truncated_reason: state.truncated_reason,
      public_count: state.public_count,
      private_count: state.private_count,
      item_count: state.item_count,
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

function createPicMcpServer(env: Env, principal: McpPrincipal): McpServer {
  const server = new McpServer({ name: "pic-mcp", version: "0.6.0-trial06" });
  server.registerTool(
    "get_my_favorites",
    {
      description: "Read the authenticated user's synchronized Pixiv favorites from PicMCP. Read-only. The result is the latest iPhone-synchronized snapshot, not a live Pixiv API call.",
      inputSchema: {
        source: z.literal("pixiv").optional(),
        visibility: z.enum(["all", "public", "private"]).optional(),
        limit: z.number().int().min(1).max(100).optional(),
        offset: z.number().int().min(0).max(10000).optional(),
      },
    },
    async ({ visibility, limit, offset }) => {
      try {
        const result = await readFavorites(
          env,
          principal,
          visibility ?? "all",
          limit ?? 50,
          offset ?? 0,
        );
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

async function completeLibraryAndMintMcpToken(request: Request, env: Env): Promise<Response> {
  const response = await baseWorker.fetch(request, env as any);
  if (!response.ok) return response;

  const body = await response.clone().json().catch(() => null) as any;
  if (body?.status !== "CONNECTED" || body?.source !== "pixiv") return response;
  const accountId = String(body?.user_id ?? "");
  if (!/^\d{1,32}$/.test(accountId)) return response;

  const token = await rotateMcpAccessToken(env, accountId);
  return json({
    ...body,
    mcp_access: {
      endpoint: "/mcp",
      scheme: "Bearer",
      token,
      rotation: "ROTATE_ON_SUCCESSFUL_LIBRARY_SYNC",
    },
  });
}

export default {
  async fetch(request: Request, env: Env, ctx: any): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/mcp") {
      const principal = await authenticateMcp(request, env);
      if (principal instanceof Response) return principal;
      return createMcpHandler(
        () => createPicMcpServer(env, principal),
        { route: "/mcp" },
      )(request, env, ctx);
    }

    if (request.method === "POST" && url.pathname === "/auth/pixiv/mobile/complete-client-library") {
      return completeLibraryAndMintMcpToken(request, env);
    }

    if (request.method === "GET" && url.pathname === "/health") {
      const response = await baseWorker.fetch(request, env as any);
      const body = await response.clone().json().catch(() => ({})) as Record<string, unknown>;
      return json({ ...body, mcp: "bearer-gated", mcp_endpoint: "/mcp", library_read: "d1-snapshot" }, response.status);
    }

    return baseWorker.fetch(request, env as any);
  },
};
