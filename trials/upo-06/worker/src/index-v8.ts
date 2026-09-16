import oauthWorker from "./index-v7";

interface Env {
  AUTH_DB: unknown;
  OAUTH_KV: unknown;
  OAUTH_PROVIDER?: unknown;
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

/**
 * Trial 06 v8 is deliberately a thin discovery adapter over v7.
 *
 * ChatGPT's production CIMD currently advertises private_key_jwt as its
 * legacy preference while also supporting none. Cloudflare's pinned OAuth
 * provider supports the public-client `none` path but not private_key_jwt.
 * The v7 human E2E reached completeAuthorization but ChatGPT never exchanged
 * the authorization code. To remove this transition ambiguity, v8 stops
 * advertising CIMD and lets ChatGPT use the already-supported DCR endpoint.
 *
 * Authorization, PKCE, token issuance, refresh-token rotation, MCP access,
 * D1 library reads, and the mobile sync bridge remain owned by v7.
 */
export default {
  async fetch(request: Request, env: Env, ctx: any): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/.well-known/oauth-authorization-server") {
      const response = await oauthWorker.fetch(request, env as any, ctx);
      const metadata = await response.clone().json().catch(() => null) as Record<string, unknown> | null;
      if (!response.ok || !metadata) return response;

      delete metadata.client_id_metadata_document_supported;
      return json(metadata, response.status);
    }

    if (request.method === "GET" && url.pathname === "/health") {
      const response = await oauthWorker.fetch(request, env as any, ctx);
      const body = await response.clone().json().catch(() => ({})) as Record<string, unknown>;
      return json({ ...body, oauth_client_mode: "dcr" }, response.status);
    }

    return oauthWorker.fetch(request, env as any, ctx);
  },
};
