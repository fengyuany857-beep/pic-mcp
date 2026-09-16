import oauthWorker from "./index-v9";

interface Env {
  AUTH_DB: unknown;
  OAUTH_KV: unknown;
  OAUTH_PROVIDER?: unknown;
}

const OFFLINE_SCOPE = "offline_access";

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

/**
 * Trial 06 v10 is a narrow ChatGPT OAuth compatibility adapter over v9.
 *
 * OpenAI's current custom MCP guidance expects OAuth providers that issue
 * refresh tokens to advertise offline_access (or an equivalent refresh-access
 * scope) in authorization-server discovery. Cloudflare OAuthProvider already
 * issues rotating refresh tokens because refreshTokenTTL is enabled in v7.
 *
 * This adapter therefore advertises offline_access only as an authorization
 * server capability. It deliberately does NOT add offline_access to RFC 9728
 * protected-resource scopes; the MCP resource still requires only
 * favorites:read. Authorization, PKCE, grants, token issuance, refresh-token
 * rotation, MCP auth, tool execution, and D1 access remain owned by v9/v7.
 */
export default {
  async fetch(request: Request, env: Env, ctx: any): Promise<Response> {
    const url = new URL(request.url);
    const response = await oauthWorker.fetch(request, env as any, ctx);

    if (request.method === "GET" && url.pathname === "/.well-known/oauth-authorization-server") {
      const metadata = await response.clone().json().catch(() => null) as Record<string, unknown> | null;
      if (!response.ok || !metadata) return response;

      const current = Array.isArray(metadata.scopes_supported)
        ? metadata.scopes_supported.filter((value): value is string => typeof value === "string")
        : [];
      metadata.scopes_supported = Array.from(new Set([...current, OFFLINE_SCOPE]));
      return json(metadata, response.status);
    }

    if (request.method === "GET" && url.pathname === "/health") {
      const body = await response.clone().json().catch(() => ({})) as Record<string, unknown>;
      return json({ ...body, oauth_offline_access_advertised: true }, response.status);
    }

    return response;
  },
};
