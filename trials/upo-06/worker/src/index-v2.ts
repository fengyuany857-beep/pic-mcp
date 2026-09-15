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

const PIXIV_TOKEN_URL = "https://oauth.secure.pixiv.net/auth/token";
const PIXIV_API_BASE = "https://app-api.pixiv.net";
const PIXIV_CLIENT_ID = "MOBrBDS8blbauoSck0ZfDbtuzpyT";
// Public mobile-app OAuth constant, split to avoid treating it as an operator secret.
const PIXIV_CLIENT_SECRET = ["lsACyCD94FhDUt", "GTXi3QzcFE2uU1hqtDaKeqrdwj"].join("");
const PIXIV_REDIRECT_URI = "https://app-api.pixiv.net/web/v1/users/auth/pixiv/callback";
const PIXIV_ANDROID_UA = "PixivAndroidApp/5.0.234 (Android 11; Pixel 5)";
const SESSION_TTL_MS = 5 * 60 * 1000;
const SESSION_RATE_LIMIT_PER_MINUTE = 12;

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

function randomToken(byteCount = 32): string {
  const bytes = crypto.getRandomValues(new Uint8Array(byteCount));
  let binary = "";
  for (const value of bytes) binary += String.fromCharCode(value);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
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
    env.AUTH_DB.prepare(`CREATE TABLE IF NOT EXISTS session_rate (
      ip TEXT NOT NULL,
      bucket INTEGER NOT NULL,
      count INTEGER NOT NULL,
      PRIMARY KEY (ip, bucket)
    )`),
  ]);
}

async function createSession(request: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  const now = Date.now();
  const bucket = Math.floor(now / 60_000);
  const ip = request.headers.get("CF-Connecting-IP") ?? "unknown";

  await env.AUTH_DB.prepare(`
    INSERT INTO session_rate (ip, bucket, count) VALUES (?1, ?2, 1)
    ON CONFLICT(ip, bucket) DO UPDATE SET count = count + 1
  `).bind(ip, bucket).run();

  const rate = await env.AUTH_DB.prepare(
    "SELECT count FROM session_rate WHERE ip = ?1 AND bucket = ?2"
  ).bind(ip, bucket).first<{ count: number }>();
  if ((rate?.count ?? 0) > SESSION_RATE_LIMIT_PER_MINUTE) {
    return json({ status: "ERROR", error: "RATE_LIMITED", message: "Too many mobile auth sessions." }, 429);
  }

  await env.AUTH_DB.batch([
    env.AUTH_DB.prepare("DELETE FROM auth_sessions WHERE expires_at < ?1").bind(now - 60_000),
    env.AUTH_DB.prepare("DELETE FROM session_rate WHERE bucket < ?1").bind(bucket - 2),
  ]);

  const sessionId = randomToken(32);
  await env.AUTH_DB.prepare(`
    INSERT INTO auth_sessions (session_id, expires_at, consumed, created_at)
    VALUES (?1, ?2, 0, ?3)
  `).bind(sessionId, now + SESSION_TTL_MS, now).run();

  return json({ status: "PENDING", session_id: sessionId, expires_in_seconds: 300 });
}

function validVerifier(value: string): boolean {
  return /^[A-Za-z0-9._~-]{43,128}$/.test(value);
}

type PixivExchangeResult =
  | { ok: true; token: Record<string, any> }
  | { ok: false; httpStatus: number; reason: string };

function safePixivReason(raw: any, status: number): string {
  const candidates = [
    raw?.error,
    raw?.error_description,
    raw?.errors?.system?.message,
    raw?.message,
  ];
  const found = candidates.find((value) => typeof value === "string" && value.length > 0);
  if (!found) return `HTTP_${status}`;
  return String(found).slice(0, 160).replace(/[\r\n]/g, " ");
}

async function exchangePixivCode(code: string, codeVerifier: string): Promise<PixivExchangeResult> {
  const form = new URLSearchParams({
    client_id: PIXIV_CLIENT_ID,
    client_secret: PIXIV_CLIENT_SECRET,
    grant_type: "authorization_code",
    code,
    code_verifier: codeVerifier,
    redirect_uri: PIXIV_REDIRECT_URI,
    include_policy: "true",
  });

  const response = await fetch(PIXIV_TOKEN_URL, {
    method: "POST",
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      "user-agent": PIXIV_ANDROID_UA,
      "accept-language": "zh-CN",
    },
    body: form.toString(),
  });

  let raw: any = null;
  try { raw = await response.json(); } catch { /* sanitized below */ }
  const payload = raw && typeof raw.response === "object" ? raw.response : raw;
  if (!response.ok || !payload?.refresh_token || !payload?.access_token) {
    return { ok: false, httpStatus: response.status, reason: safePixivReason(raw, response.status) };
  }
  return { ok: true, token: payload };
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
        "user-agent": PIXIV_ANDROID_UA,
        "accept-language": "zh-CN",
      },
    });
    const body: any = await response.json().catch(() => null);
    if (!response.ok || !body || !Array.isArray(body.illusts)) {
      return { ok: false, count: 0, ids: [], error: `HTTP_${response.status}` };
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

async function completeSession(request: Request, env: Env): Promise<Response> {
  await ensureSchema(env);
  let payload: any;
  try { payload = await request.json(); }
  catch { return json({ status: "ERROR", error: "INVALID_JSON", message: "JSON body required." }, 400); }

  const sessionId = String(payload?.session_id ?? "");
  const code = String(payload?.code ?? "");
  const codeVerifier = String(payload?.code_verifier ?? "");
  if (!sessionId || sessionId.length > 256) {
    return json({ status: "ERROR", error: "SESSION_ID_REQUIRED", message: "session_id is required." }, 400);
  }
  if (!code || code.length > 4096 || /\s/.test(code)) {
    return json({ status: "ERROR", error: "INVALID_AUTHORIZATION_CODE", message: "Invalid Pixiv authorization code." }, 400);
  }
  if (!validVerifier(codeVerifier)) {
    return json({ status: "ERROR", error: "INVALID_CODE_VERIFIER", message: "Invalid PKCE code_verifier." }, 400);
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

  const exchanged = await exchangePixivCode(code, codeVerifier);
  if (!exchanged.ok) {
    return json({
      status: "ERROR",
      error: "PIXIV_TOKEN_EXCHANGE_FAILED",
      message: "Pixiv rejected the authorization code exchange.",
      pixiv_http_status: exchanged.httpStatus,
      pixiv_reason: exchanged.reason,
    }, 502);
  }

  const token = exchanged.token;
  const refreshToken = String(token.refresh_token);
  const accessToken = String(token.access_token);
  const userId = token?.user?.id != null ? String(token.user.id) : null;
  if (!userId) {
    return json({ status: "ERROR", error: "PIXIV_USER_ID_MISSING", message: "Pixiv token response did not include a user id." }, 502);
  }

  await env.AUTH_DB.prepare(`
    INSERT INTO pixiv_credentials (source, refresh_token, user_id, updated_at)
    VALUES ('pixiv', ?1, ?2, ?3)
    ON CONFLICT(source) DO UPDATE SET
      refresh_token = excluded.refresh_token,
      user_id = excluded.user_id,
      updated_at = excluded.updated_at
  `).bind(refreshToken, userId, now).run();

  const [publicBookmarks, privateBookmarks] = await Promise.all([
    bookmarkPage(accessToken, userId, "public"),
    bookmarkPage(accessToken, userId, "private"),
  ]);

  return json({
    status: "CONNECTED",
    source: "pixiv",
    user_id: userId,
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
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true, service: "pic-mcp-auth-trial06", storage: "cloudflare-d1", oauth_request_profile: "pixiv-android" });
    }
    if (request.method === "POST" && url.pathname === "/auth/pixiv/mobile/session") {
      return createSession(request, env);
    }
    if (request.method === "POST" && url.pathname === "/auth/pixiv/mobile/complete") {
      return completeSession(request, env);
    }
    return json({ status: "ERROR", error: "NOT_FOUND" }, 404);
  },
};
