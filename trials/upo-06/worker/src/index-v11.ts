import oauthWorker from "./index-v10";

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
 * Trial 06 v11 restores metadata consistency for ChatGPT's actual OAuth client mode.
 *
 * v7 enables Client ID Metadata Documents (CIMD) in the pinned Cloudflare OAuth
 * provider. v8 hid that capability from authorization-server discovery while
 * attempting to steer ChatGPT toward DCR. Real ChatGPT traffic continued to use
 * an HTTPS CIMD client_id and never called the DCR endpoint, so the server was
 * accepting CIMD while advertising that it did not support CIMD.
 *
 * v11 restores only the CIMD discovery capability. All authorization, PKCE,
 * grant/token issuance, refresh-token handling, safe callback telemetry, MCP
 * authorization, tool execution, and D1 reads remain delegated to v10/v9/v7.
 */
export default {
  async fetch(request: Request, env: Env, ctx: any): Promise<Response> {
    const url = new URL(request.url);
    const response = await oauthWorker.fetch(request, env as any, ctx);

    if (request.method === "GET" && url.pathname === "/.well-known/oauth-authorization-server") {
      const metadata = await response.clone().json().catch(() => null) as Record<string, unknown> | null;
      if (!response.ok || !metadata) return response;

      metadata.client_id_metadata_document_supported = true;
      return json(metadata, response.status);
    }

    if (request.method === "GET" && url.pathname === "/health") {
      const body = await response.clone().json().catch(() => ({})) as Record<string, unknown>;
      return json({
        ...body,
        oauth_client_mode: "cimd",
        oauth_cimd_advertised: true,
      }, response.status);
    }

    return response;
  },
};
