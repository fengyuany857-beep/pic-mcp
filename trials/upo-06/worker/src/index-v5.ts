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

type LibraryVisibility = "public" | "private";

interface LibraryItem {
  postId: string;
  visibility: LibraryVisibility;
  title: string | null;
  creatorId: string | null;
  creatorName: string | null;
  tags: string[];
  pageUrl: string;
  previewUrl: string | null;
  originalUrl: string | null;
  mediaType: string | null;
  contentRating: number | null;
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

async function ensureLibrarySchema(env: Env): Promise<void> {
  await env.AUTH_DB.batch([
    env.AUTH_DB.prepare(`CREATE TABLE IF NOT EXISTS auth_sessions (
      session_id TEXT PRIMARY KEY,
      expires_at INTEGER NOT NULL,
      consumed INTEGER NOT NULL DEFAULT 0,
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

function safeText(value: unknown, maxLength: number): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  return trimmed.slice(0, maxLength);
}

function safeHTTPSURL(value: unknown, allowedHosts?: string[]): string | null {
  if (typeof value !== "string" || value.length > 2048) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:") return null;
    if (allowedHosts && !allowedHosts.some((host) => url.hostname === host || url.hostname.endsWith(`.${host}`))) {
      return null;
    }
    return url.toString();
  } catch {
    return null;
  }
}

function normalizeLibraryItem(raw: any): LibraryItem | null {
  const postId = String(raw?.post_id ?? "");
  if (!/^\d{1,32}$/.test(postId)) return null;

  const visibility = String(raw?.visibility ?? "");
  if (visibility !== "public" && visibility !== "private") return null;

  const pageUrl = safeHTTPSURL(raw?.page_url, ["pixiv.net"]);
  if (!pageUrl || !pageUrl.includes(`/artworks/${postId}`)) return null;

  const previewUrl = raw?.preview_url == null ? null : safeHTTPSURL(raw.preview_url, ["pximg.net"]);
  const originalUrl = raw?.original_url == null ? null : safeHTTPSURL(raw.original_url, ["pximg.net"]);
  if (raw?.preview_url != null && !previewUrl) return null;
  if (raw?.original_url != null && !originalUrl) return null;

  const creatorIdRaw = raw?.creator_id == null ? null : String(raw.creator_id);
  if (creatorIdRaw != null && !/^\d{1,32}$/.test(creatorIdRaw)) return null;

  const tagsRaw = Array.isArray(raw?.tags) ? raw.tags : [];
  if (tagsRaw.length > 100) return null;
  const tags = tagsRaw
    .filter((tag: unknown) => typeof tag === "string")
    .map((tag: string) => tag.trim().slice(0, 256))
    .filter(Boolean);
  if (tags.length !== tagsRaw.length) return null;

  let contentRating: number | null = null;
  if (raw?.content_rating != null) {
    const candidate = Number(raw.content_rating);
    if (!Number.isInteger(candidate) || candidate < 0 || candidate > 10) return null;
    contentRating = candidate;
  }

  return {
    postId,
    visibility,
    title: safeText(raw?.title, 512),
    creatorId: creatorIdRaw,
    creatorName: safeText(raw?.creator_name, 256),
    tags,
    pageUrl,
    previewUrl,
    originalUrl,
    mediaType: safeText(raw?.media_type, 64),
    contentRating,
  };
}

async function consumeSession(sessionId: string, env: Env): Promise<Response | null> {
  if (!sessionId || sessionId.length > 256) {
    return json({ status: "ERROR", error: "SESSION_ID_REQUIRED", message: "session_id is required." }, 400);
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
  return null;
}

async function completeFromIOSLibrary(request: Request, env: Env): Promise<Response> {
  await ensureLibrarySchema(env);

  let payload: any;
  try { payload = await request.json(); }
  catch { return json({ status: "ERROR", error: "INVALID_JSON", message: "JSON body required." }, 400); }

  if (String(payload?.auth_transport ?? "") !== "ios_local_library_v1") {
    return json({ status: "ERROR", error: "AUTH_TRANSPORT_REQUIRED", message: "ios_local_library_v1 is required." }, 400);
  }

  const sessionId = String(payload?.session_id ?? "");
  const userId = String(payload?.user_id ?? "");
  if (!/^\d{1,32}$/.test(userId)) {
    return json({ status: "ERROR", error: "INVALID_USER_ID", message: "Invalid Pixiv user id." }, 400);
  }

  const rawLibrary = payload?.library;
  if (!rawLibrary || !Array.isArray(rawLibrary.items) || rawLibrary.items.length > 1000) {
    return json({ status: "ERROR", error: "INVALID_LIBRARY", message: "Library payload is missing or exceeds the 1000-item trial bound." }, 400);
  }

  const publicCount = Number(rawLibrary.public_count ?? -1);
  const privateCount = Number(rawLibrary.private_count ?? -1);
  const complete = rawLibrary.complete === true;
  const truncatedReason = safeText(rawLibrary.truncated_reason, 128);
  if (!Number.isInteger(publicCount) || publicCount < 0 || !Number.isInteger(privateCount) || privateCount < 0) {
    return json({ status: "ERROR", error: "INVALID_LIBRARY_COUNTS", message: "Invalid library counts." }, 400);
  }

  const items = rawLibrary.items.map(normalizeLibraryItem);
  if (items.some((item: LibraryItem | null) => item == null)) {
    return json({ status: "ERROR", error: "INVALID_LIBRARY_ITEM", message: "At least one normalized Pixiv library item is invalid." }, 400);
  }
  const normalizedItems = items as LibraryItem[];
  if (publicCount + privateCount !== normalizedItems.length) {
    return json({ status: "ERROR", error: "LIBRARY_COUNT_MISMATCH", message: "Library counts do not match the normalized item payload." }, 400);
  }

  const sessionFailure = await consumeSession(sessionId, env);
  if (sessionFailure) return sessionFailure;

  const now = Date.now();
  const syncId = crypto.randomUUID();
  const chunkSize = 60;

  for (let offset = 0; offset < normalizedItems.length; offset += chunkSize) {
    const statements = normalizedItems.slice(offset, offset + chunkSize).map((item) =>
      env.AUTH_DB.prepare(`
        INSERT INTO user_library_items (
          source, item_id, library_visibility, title, creator_id, creator_name,
          tags_json, page_url, preview_url, original_url, media_type, content_rating,
          sync_id, synced_at
        ) VALUES ('pixiv', ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)
        ON CONFLICT(source, item_id) DO UPDATE SET
          library_visibility = excluded.library_visibility,
          title = excluded.title,
          creator_id = excluded.creator_id,
          creator_name = excluded.creator_name,
          tags_json = excluded.tags_json,
          page_url = excluded.page_url,
          preview_url = excluded.preview_url,
          original_url = excluded.original_url,
          media_type = excluded.media_type,
          content_rating = excluded.content_rating,
          sync_id = excluded.sync_id,
          synced_at = excluded.synced_at
      `).bind(
        item.postId,
        item.visibility,
        item.title,
        item.creatorId,
        item.creatorName,
        JSON.stringify(item.tags),
        item.pageUrl,
        item.previewUrl,
        item.originalUrl,
        item.mediaType,
        item.contentRating,
        syncId,
        now,
      )
    );
    if (statements.length) await env.AUTH_DB.batch(statements);
  }

  const finalStatements: D1PreparedStatement[] = [
    env.AUTH_DB.prepare(`
      INSERT INTO user_library_state (
        source, account_id, current_sync_id, public_count, private_count,
        item_count, complete, truncated_reason, updated_at
      ) VALUES ('pixiv', ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
      ON CONFLICT(source) DO UPDATE SET
        account_id = excluded.account_id,
        current_sync_id = excluded.current_sync_id,
        public_count = excluded.public_count,
        private_count = excluded.private_count,
        item_count = excluded.item_count,
        complete = excluded.complete,
        truncated_reason = excluded.truncated_reason,
        updated_at = excluded.updated_at
    `).bind(
      userId,
      syncId,
      publicCount,
      privateCount,
      normalizedItems.length,
      complete ? 1 : 0,
      truncatedReason,
      now,
    ),
  ];

  if (complete) {
    finalStatements.push(
      env.AUTH_DB.prepare(
        "DELETE FROM user_library_items WHERE source = 'pixiv' AND sync_id <> ?1"
      ).bind(syncId)
    );
  }
  await env.AUTH_DB.batch(finalStatements);

  return json({
    status: "CONNECTED",
    source: "pixiv",
    user_id: userId,
    auth_transport: "ios_local_library_v1",
    token_storage: "NONE",
    library_sync: {
      status: complete ? "PASS" : "PARTIAL_TRUNCATED",
      public_count: publicCount,
      private_count: privateCount,
      synced_items: normalizedItems.length,
      complete,
      truncated_reason: truncatedReason,
      sample_ids: normalizedItems.slice(0, 5).map((item) => item.postId),
    },
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "POST" && url.pathname === "/auth/pixiv/mobile/complete-client-library") {
      return completeFromIOSLibrary(request, env);
    }

    if (request.method === "POST" && url.pathname === "/auth/pixiv/mobile/complete-client-token") {
      return json({
        status: "ERROR",
        error: "LEGACY_TOKEN_HANDOFF_DISABLED",
        message: "Legacy token handoff is disabled. Use iPhone-local library sync.",
      }, 410);
    }

    return baseWorker.fetch(request, env as any);
  },
};
