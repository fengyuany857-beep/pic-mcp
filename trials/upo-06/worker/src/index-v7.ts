import {
  AuthorizationError,
  OAuthProvider,
  type AuthRequest,
  type OAuthHelpers,
} from "@cloudflare/workers-oauth-provider";
import { McpServer } from "@modelcontextprotocol/server";
import { createMcpHandler } from "agents/mcp/server";
import { z } from "zod";
import baseWorker from "./index-v6";

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

interface Env {
  AUTH_DB: D1Database;
  OAUTH_KV: any;
  OAUTH_PROVIDER?: OAuthHelpers;
}

interface OAuthProps {
  source: "pixiv";
  accountId: string;
  scopes: string[];
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

const ORIGIN = "https://pic-mcp-auth-trial06.3254849126a.workers.dev";
const MCP_RESOURCE = `${ORIGIN}/mcp`;
const READ_SCOPE = "favorites:read";
const CSRF_COOKIE = "__Host-picmcp_oauth_csrf";

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

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function randomToken(byteCount = 24): string {
  const bytes = crypto.getRandomValues(new Uint8Array(byteCount));
  let binary = "";
  for (const value of bytes) binary += String.fromCharCode(value);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function cookieValue(request: Request, name: string): string | null {
  const cookie = request.headers.get("cookie") ?? "";
  for (const part of cookie.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return rest.join("=");
  }
  return null;
}

function timingSafeStringEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let index = 0; index < a.length; index += 1) {
    diff |= a.charCodeAt(index) ^ b.charCodeAt(index);
  }
  return diff === 0;
}

async function ensureOwnerTokenSchema(env: Env): Promise<void> {
  await env.AUTH_DB.prepare(`CREATE TABLE IF NOT EXISTS mcp_access_tokens (
    source TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL
  )`).run();
}

async function verifyOwnerToken(env: Env, token: string): Promise<string | null> {
  if (!/^[A-Za-z0-9_-]{40,256}$/.test(token)) return null;
  await ensureOwnerTokenSchema(env);
  const tokenHash = await sha256Hex(token);
  const row = await env.AUTH_DB.prepare(
    "SELECT account_id, token_hash FROM mcp_access_tokens WHERE source = 'pixiv'"
  ).first<{ account_id: string; token_hash: string }>();
  if (!row || !/^\d{1,32}$/.test(row.account_id)) return null;
  if (!timingSafeStringEqual(tokenHash, row.token_hash)) return null;
  return row.account_id;
}

function renderAuthorizeForm(request: Request, oauthRequest: AuthRequest, csrf: string): Response {
  const url = new URL(request.url);
  const requestedScopes = oauthRequest.scope.length ? oauthRequest.scope.join(" ") : READ_SCOPE;
  const action = `${url.pathname}${url.search}`;
  const html = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>授权 ChatGPT 访问 PicMCP</title>
<style>
:root{color-scheme:light dark}body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;padding:24px;background:#111;color:#f5f5f7}.card{max-width:520px;margin:8vh auto;background:#1c1c1e;border:1px solid #333;border-radius:22px;padding:24px}h1{font-size:24px;margin:0 0 12px}p{line-height:1.55;color:#c7c7cc}.scope{padding:12px;border-radius:12px;background:#2c2c2e;margin:16px 0}label{display:block;margin:18px 0 8px}input{box-sizing:border-box;width:100%;padding:14px;border-radius:12px;border:1px solid #555;background:#111;color:#fff;font-size:16px}button{width:100%;margin-top:18px;padding:15px;border:0;border-radius:14px;background:#0a84ff;color:white;font-size:17px;font-weight:600}.note{font-size:13px;color:#8e8e93}</style>
</head>
<body><main class="card">
<h1>授权 ChatGPT 访问 PicMCP</h1>
<p>这只允许 ChatGPT 读取你已经同步到 PicMCP 的 Pixiv 收藏快照。不会把 Pixiv 密码、access token 或 refresh token 提供给 ChatGPT。</p>
<div class="scope"><strong>请求权限</strong><br>${escapeHtml(requestedScopes)}</div>
<form method="post" action="${escapeHtml(action)}" autocomplete="off">
<input type="hidden" name="csrf" value="${escapeHtml(csrf)}">
<label for="owner_token">PicMCP 连接令牌</label>
<input id="owner_token" name="owner_token" type="password" required inputmode="text" autocapitalize="none" autocomplete="off" placeholder="从 PicMCP Auth Helper 复制">
<button type="submit">允许只读访问</button>
</form>
<p class="note">令牌只用于本次身份确认，不会写入日志或 OAuth 元数据。成功后 ChatGPT 会获得独立、可刷新、仅限 PicMCP 的 OAuth 凭据。</p>
</main></body></html>`;
  return new Response(html, {
    status: 200,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "referrer-policy": "no-referrer",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
      "set-cookie": `${CSRF_COOKIE}=${csrf}; Path=/; Max-Age=600; HttpOnly; Secure; SameSite=Lax`,
    },
  });
}

function authorizationError(error: unknown): Response {
  const code = error instanceof AuthorizationError ? error.code : "invalid_request";
  return json({
    error: code,
    error_description: error instanceof Error ? String(error.message).slice(0, 180) : "Authorization request failed.",
  }, 400);
}

async function handleAuthorize(request: Request, env: Env): Promise<Response> {
  if (!env.OAUTH_PROVIDER) return json({ error: "oauth_provider_unavailable" }, 503);

  let oauthRequest: AuthRequest;
  try {
    oauthRequest = await env.OAUTH_PROVIDER.parseAuthRequest(request);
  } catch (error) {
    return authorizationError(error);
  }

  if (request.method === "GET") {
    const csrf = randomToken();
    return renderAuthorizeForm(request, oauthRequest, csrf);
  }

  if (request.method !== "POST") {
    return new Response("Method Not Allowed", { status: 405, headers: { allow: "GET, POST" } });
  }

  const form = await request.clone().formData().catch(() => null);
  const submittedCsrf = typeof form?.get("csrf") === "string" ? String(form?.get("csrf")) : "";
  const csrfCookie = cookieValue(request, CSRF_COOKIE) ?? "";
  if (!submittedCsrf || !csrfCookie || !timingSafeStringEqual(submittedCsrf, csrfCookie)) {
    return json({ error: "access_denied", message: "Authorization form expired. Re-open the ChatGPT authorization prompt." }, 403);
  }

  const ownerToken = typeof form?.get("owner_token") === "string" ? String(form?.get("owner_token")) : "";
  const accountId = await verifyOwnerToken(env, ownerToken);
  if (!accountId) {
    return json({ error: "access_denied", message: "PicMCP connection token is invalid or has been rotated." }, 403);
  }

  const requestedScopes = oauthRequest.scope.length ? oauthRequest.scope : [READ_SCOPE];
  const grantedScopes = requestedScopes.filter((scope) => scope === READ_SCOPE);
  if (!grantedScopes.includes(READ_SCOPE)) {
    return json({ error: "invalid_scope", message: "PicMCP requires favorites:read for this MCP server." }, 400);
  }

  try {
    const { redirectTo } = await env.OAUTH_PROVIDER.completeAuthorization({
      request: oauthRequest,
      userId: `pixiv:${accountId}`,
      metadata: { source: "pixiv", access: "synchronized-favorites-read-only" },
      scope: grantedScopes,
      props: {
        source: "pixiv",
        accountId,
        scopes: grantedScopes,
      } satisfies OAuthProps,
    });
    return Response.redirect(redirectTo, 302);
  } catch (error) {
    return authorizationError(error);
  }
}

function parseTags(raw: string): string[] {
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === "string") : [];
  } catch {
    return [];
  }
}

async function readFavorites(
  env: Env,
  principal: OAuthProps,
  visibility: "all" | "public" | "private",
  limit: number,
  offset: number,
): Promise<Record<string, unknown>> {
  const state = await env.AUTH_DB.prepare(`
    SELECT account_id, current_sync_id, public_count, private_count, item_count,
           complete, truncated_reason, updated_at
    FROM user_library_state WHERE source = 'pixiv'
  `).first<LibraryStateRow>();

  if (!state) return { status: "NOT_CONNECTED", source: "pixiv", items: [] };
  if (state.account_id !== principal.accountId) throw new Error("OAuth principal does not match library snapshot.");

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
    page: { filtered_total: filteredTotal, offset, limit, returned: items.length, next_offset: nextOffset },
    items,
  };
}

function createPicMcpServer(env: Env, principal: OAuthProps): McpServer {
  const server = new McpServer({ name: "pic-mcp", version: "0.7.0-trial06" });
  server.registerTool(
    "get_my_favorites",
    {
      description: "Read the authenticated user's synchronized Pixiv favorites from PicMCP. Read-only. Returns the latest iPhone-synchronized snapshot, not a live Pixiv API call.",
      inputSchema: {
        source: z.literal("pixiv").optional(),
        visibility: z.enum(["all", "public", "private"]).optional(),
        limit: z.number().int().min(1).max(100).optional(),
        offset: z.number().int().min(0).max(10000).optional(),
      },
    },
    async ({ visibility, limit, offset }) => {
      if (!principal.scopes.includes(READ_SCOPE)) {
        return { isError: true, content: [{ type: "text", text: JSON.stringify({ status: "ERROR", error: "INSUFFICIENT_SCOPE" }) }] };
      }
      try {
        const result = await readFavorites(env, principal, visibility ?? "all", limit ?? 50, offset ?? 0);
        return { content: [{ type: "text", text: JSON.stringify(result) }] };
      } catch {
        return { isError: true, content: [{ type: "text", text: JSON.stringify({ status: "ERROR", error: "LIBRARY_READ_FAILED" }) }] };
      }
    },
  );
  return server;
}

const oauthApiHandler = {
  async fetch(request: Request, env: Env, ctx: any): Promise<Response> {
    const props = ctx?.props as OAuthProps | undefined;
    if (
      !props ||
      props.source !== "pixiv" ||
      !/^\d{1,32}$/.test(props.accountId) ||
      !Array.isArray(props.scopes) ||
      !props.scopes.includes(READ_SCOPE)
    ) {
      return json({ status: "ERROR", error: "OAUTH_PRINCIPAL_INVALID" }, 403);
    }
    return createMcpHandler(
      () => createPicMcpServer(env, props),
      { route: "/mcp", authContext: { props: props as unknown as Record<string, unknown> } },
    )(request, env, ctx);
  },
};

const defaultHandler = {
  async fetch(request: Request, env: Env, ctx: any): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/authorize") return handleAuthorize(request, env);

    if (request.method === "GET" && url.pathname === "/health") {
      const response = await baseWorker.fetch(request, env as any, ctx);
      const body = await response.clone().json().catch(() => ({})) as Record<string, unknown>;
      return json({
        ...body,
        mcp: "oauth2.1",
        mcp_endpoint: "/mcp",
        oauth_authorize_endpoint: "/authorize",
        oauth_token_endpoint: "/oauth/token",
        oauth_client_registration_endpoint: "/oauth/register",
        oauth_scope: READ_SCOPE,
      }, response.status);
    }

    return baseWorker.fetch(request, env as any, ctx);
  },
};

export default new OAuthProvider<Env>({
  apiRoute: "/mcp",
  apiHandler: oauthApiHandler,
  defaultHandler,
  authorizeEndpoint: "/authorize",
  tokenEndpoint: "/oauth/token",
  clientRegistrationEndpoint: "/oauth/register",
  clientIdMetadataDocumentEnabled: true,
  scopesSupported: [READ_SCOPE],
  accessTokenTTL: 3600,
  refreshTokenTTL: 2592000,
  resourceMetadata: {
    resource: MCP_RESOURCE,
    authorization_servers: [ORIGIN],
    scopes_supported: [READ_SCOPE],
    bearer_methods_supported: ["header"],
    resource_name: "PicMCP synchronized favorites",
  },
});
