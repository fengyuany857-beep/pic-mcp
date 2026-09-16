import oauthWorker from "./index-v8";

interface Env {
  AUTH_DB: unknown;
  OAUTH_KV: unknown;
  OAUTH_PROVIDER?: unknown;
}

const ORIGIN = "https://pic-mcp-auth-trial06.3254849126a.workers.dev";

function redirectKind(url: URL): string {
  if (url.hostname !== "chatgpt.com") return "other_host";
  if (url.pathname === "/connector_platform_oauth_redirect") return "stable_platform";
  if (url.pathname.startsWith("/connector/oauth/")) return "connector_specific";
  return "chatgpt_other";
}

/**
 * Trial 06 v9 is a telemetry-only wrapper over v8.
 *
 * It does not modify authorization requests or responses. For POST /authorize,
 * it records only boolean/enum properties of the returned redirect so we can
 * distinguish a valid OAuth authorization response from a callback-validation
 * failure without logging authorization codes, state values, owner tokens,
 * PKCE material, access tokens, refresh tokens, client secrets, or full URLs.
 */
export default {
  async fetch(request: Request, env: Env, ctx: any): Promise<Response> {
    const requestUrl = new URL(request.url);
    const response = await oauthWorker.fetch(request, env as any, ctx);

    if (request.method === "POST" && requestUrl.pathname === "/authorize") {
      const location = response.headers.get("location");
      const requestState = requestUrl.searchParams.get("state");
      let safe: Record<string, unknown> = {
        status: response.status,
        redirect_present: Boolean(location),
      };

      if (location) {
        try {
          const redirect = new URL(location, request.url);
          const responseState = redirect.searchParams.get("state");
          const issuer = redirect.searchParams.get("iss");
          safe = {
            ...safe,
            redirect_kind: redirectKind(redirect),
            code_present: Boolean(redirect.searchParams.get("code")),
            state_present: Boolean(responseState),
            state_matches_request: Boolean(requestState && responseState && requestState === responseState),
            iss_present: Boolean(issuer),
            iss_matches_origin: issuer === ORIGIN,
            error_present: Boolean(redirect.searchParams.get("error")),
          };
        } catch {
          safe = { ...safe, redirect_kind: "invalid_location" };
        }
      }

      console.log("PICMCP_AUTH_REDIRECT_SAFE", JSON.stringify(safe));
    }

    return response;
  },
};
