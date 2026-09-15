import baseWorker from "./index-v2";

interface Env {
  AUTH_DB: unknown;
}

function diagnosticMessage(status: number, reason: string): string {
  const safeStatus = Number.isFinite(status) ? String(status) : "unknown";
  const safeReason = String(reason || "unknown").slice(0, 160).replace(/[\r\n]/g, " ");
  return `Pixiv token exchange failed (HTTP ${safeStatus}: ${safeReason}).`;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const response = await baseWorker.fetch(request, env as any);
    const url = new URL(request.url);

    if (
      request.method === "POST"
      && url.pathname === "/auth/pixiv/mobile/complete"
      && response.status >= 400
    ) {
      const contentType = response.headers.get("content-type") ?? "";
      if (contentType.includes("application/json")) {
        try {
          const payload = await response.clone().json() as Record<string, unknown>;
          if (payload.error === "PIXIV_TOKEN_EXCHANGE_FAILED") {
            const status = Number(payload.pixiv_http_status ?? response.status);
            const reason = String(payload.pixiv_reason ?? "unknown");
            payload.message = diagnosticMessage(status, reason);
            return new Response(JSON.stringify(payload), {
              status: response.status,
              headers: response.headers,
            });
          }
        } catch {
          // Preserve the original response if safe diagnostic parsing fails.
        }
      }
    }

    return response;
  },
};
