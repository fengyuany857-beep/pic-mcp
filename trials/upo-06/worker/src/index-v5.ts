import baseWorker from "./index-v2";

interface D1RunResult { success: boolean; meta: { changes?: number } }
interface D1PreparedStatement {
  bind(...values: unknown[]): D1PreparedStatement;
  run(): Promise<D1RunResult>;
  first<T = Record<string, unknown>>(): Promise<T | null>;
}
interface D1Database {
  prepare(query: string): D1PreparedStatement;
  batch(statements: D1PreparedStatement[]): Promise<unknown[]>;
}
interface Env { AUTH_DB: D1Database }

const PIXIV_API_BASE = "https://app-api.pixiv.net";
const PIXIV_IOS_UA = "PixivIOSApp/7.13.3 (iOS 14.6; iPhone13,2)";

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

async function ensureSchema(env: Env): Promise<void> {
  await env.AUTH_DB.batch([
    env.AUTH_DB.prepare(`CREATE TABLE IF NOT EXISTS auth_sessions (
      session_id TEXT PRIMARY KEY,
      expires_at INTEGER NOT NULL,
      consumed INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL
    )`),
    env.AUTH_DB.prepare(`CREATE TABLE IF NOT EXISTS pixiv_credentials (
      source TEXT PRIMARY KEY,
      refresh_token TEXT NOT NULL,
      user_id TEXT,
      updated_at INTEGER NOT NULL
    )`),
  ]);
}

async function bookmarkPage(
  accessToken: string,
  userId: string,
  restrict: "public" | "private",
): Promise<{ ok: boolean; count: number; ids: string[]; error?: string }> {
  const url = new URL("/v1/user/bookmarks/illust", PIXIV_API_BASE);
  url.searchParams.set("user_id", userId);
  url.searchParams.set("restrict", restrict);
  url.searchParams.set("filter", "for_ios");
  try {
    const response = await fetch(url, {
      headers: {
        authorization: `Bearer ${accessToken}`,
        "app-os": "ios",
        "app-os-version": "14.6",
        "user-agent": PIXIV_IOS_UA,
        "accept-language": "zh-CN",
      },
    });
    const contentType = response.headers.get("content-type") ?? "unknown";
    const body: any = await response.json().catch(() => null);
    if (!response.ok || !body || !Array.isArray(body.illusts)) {
      const safeType = contentType.split(";", 1)[0].slice(0, 64).replace(/[^a-zA-Z0-9.+\-_/]/g, "_");
      return { ok: false, count: 0, ids: [], error: `HTTP_${response.status}_${safeType}` };
    }
    return {
      ok: true,
      count: body.illusts.length,
      ids: body.illusts.slice(0, 5).map((item: any) => String(item?.id ?? "")).filter(Boolean),
    };
  } catch {
    return { ok: false, count: 0, ids: [], error: "FETCH_FAILED" };
  }
}

function validOpaqueToken(value: string): boolean {
  return value.length >= 20 && value.length <= 4096 && !/\s/.test(value);
}

async function completeFromIOSLocalToken(request: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  let payload: any;
  try { payload = await request.json(); }
  catch { return json({ status: "ERROR", error: "INVALID_JSON", message: "JSON body required." }, 400); }

  if (String(payload?.auth_transport ?? "") !== "ios_local_exchange_v1") {
    return json({ status: "ERROR", error: "AUTH_TRANSPORT_REQUIRED", message: "ios_local_exchange_v1 is required." }, 400);
  }

  const sessionId = String(payload?.session_id ?? "");
  const refreshToken = String(payload?.refresh_token ?? "");
  const accessToken = String(payload?.access_token ?? "");
  const userId = String(payload?.user_id ?? "");

  if (!sessionId || sessionId.length > 256) {
    return json({ status: "ERROR", error: "SESSION_ID_REQUIRED", message: "session_id is required." }, 400);
  }
  if (!validOpaqueToken(refreshToken)) {
    return json({ status: "ERROR", error: "INVALID_REFRESH_TOKEN", message: "Invalid Pixiv refresh token handoff." }, 400);
  }
  if (!validOpaqueToken(accessToken)) {
    return json({ status: "ERROR", error: "INVALID_ACCESS_TOKEN", message: "Invalid Pixiv access token handoff." }, 400);
  }
  if (!/^\d{1,32}$/.test(userId)) {
    return json({ status: "ERROR", error: "INVALID_USER_ID", message: "Invalid Pixiv user id." }, 400);
  }

  const now = Date.now();
  const session = await env.AUTH_DB.prepare(
    "SELECT expires_at, consumed FROM auth_sessions WHERE session_id = ?1"
  ).bind(sessionId).first<{ expires_at: number; consumed: number }>();
  if (!session) return json({ status: "ERROR", error: "SESSION_NOT_FOUND", message: "Unknown mobile auth session." }, 404);
  if (session.consumed) return json({ status: "ERROR", error: "SESSION_ALREADY_USED", message: "Mobile auth session was already used." }, 409);
  if (now >= session.expires_at) return json({ status: "ERROR", error: "SESSION_EXPIRED", message: "Mobile auth session expired." }, 410);

  const consumed = await env.AUTH_DB.prepare(`
    UPDATE auth_sessions SET consumed = 1
    WHERE session_id = ?1 AND consumed = 0 AND expires_at > ?2
  `).bind(sessionId, now).run();
  if ((consumed.meta.changes ?? 0) !== 1) {
    return json({ status: "ERROR", error: "SESSION_RACE_REJECTED", message: "Mobile auth session could not be consumed." }, 409);
  }

  const [publicBookmarks, privateBookmarks] = await Promise.all([
    bookmarkPage(accessToken, userId, "public"),
    bookmarkPage(accessToken, userId, "private"),
  ]);
  if (!publicBookmarks.ok && !privateBookmarks.ok) {
    return json({
      status: "ERROR",
      error: "PIXIV_API_PROBE_FAILED",
      message: "Pixiv token was received from iPhone, but the Worker could not read bookmarks.",
      bookmarks_probe: {
        status: "FAIL",
        errors: [publicBookmarks.error, privateBookmarks.error].filter(Boolean),
      },
    }, 502);
  }

  await env.AUTH_DB.prepare(`
    INSERT INTO pixiv_credentials (source, refresh_token, user_id, updated_at)
    VALUES ('pixiv', ?1, ?2, ?3)
    ON CONFLICT(source) DO UPDATE SET
      refresh_token = excluded.refresh_token,
      user_id = excluded.user_id,
      updated_at = excluded.updated_at
  `).bind(refreshToken, userId, now).run();

  return json({
    status: "CONNECTED",
    source: "pixiv",
    user_id: userId,
    auth_transport: "ios_local_exchange_v1",
    bookmarks_probe: {
      status: publicBookmarks.ok && privateBookmarks.ok ? "PASS" : "PARTIAL",
      public_page_count: publicBookmarks.count,
      private_page_count: privateBookmarks.count,
      sample_ids: Array.from(new Set([...publicBookmarks.ids, ...privateBookmarks.ids])).slice(0, 5),
      errors: [publicBookmarks.error, privateBookmarks.error].filter(Boolean),
    },
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/auth/pixiv/mobile/complete-client-token") {
      return completeFromIOSLocalToken(request, env);
    }
    return baseWorker.fetch(request, env as any);
  },
};
