export interface D1AllResult<T> { success: boolean; results: T[] }
export interface D1PreparedStatement {
  bind(...values: unknown[]): D1PreparedStatement;
  first<T = Record<string, unknown>>(): Promise<T | null>;
  all<T = Record<string, unknown>>(): Promise<D1AllResult<T>>;
}
export interface D1Database {
  prepare(query: string): D1PreparedStatement;
}

interface LibraryStateRow {
  current_sync_id: string;
  public_count: number;
  complete: number;
  truncated_reason: string | null;
  updated_at: number;
}

interface LibraryItemRow {
  item_id: string;
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

function parseTags(raw: string): string[] {
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((value): value is string => typeof value === "string")
      : [];
  } catch {
    return [];
  }
}

export async function readPublicFavorites(
  db: D1Database,
  limit: number,
  offset: number,
): Promise<Record<string, unknown>> {
  const state = await db.prepare(`
    SELECT current_sync_id, public_count, complete, truncated_reason, updated_at
    FROM user_library_state WHERE source = 'pixiv'
  `).first<LibraryStateRow>();

  if (!state) {
    return {
      status: "NOT_CONNECTED",
      source: "pixiv",
      visibility: "public",
      items: [],
      message: "No Pixiv library snapshot has been synchronized yet.",
    };
  }

  const countRow = await db.prepare(`
    SELECT COUNT(*) AS count
    FROM user_library_items
    WHERE source = 'pixiv' AND sync_id = ?1 AND library_visibility = 'public'
  `).bind(state.current_sync_id).first<{ count: number }>();
  const filteredTotal = Number(countRow?.count ?? 0);

  const rows = await db.prepare(`
    SELECT item_id, title, creator_id, creator_name, tags_json,
           page_url, preview_url, original_url, media_type, content_rating
    FROM user_library_items
    WHERE source = 'pixiv' AND sync_id = ?1 AND library_visibility = 'public'
    ORDER BY CAST(item_id AS INTEGER) DESC
    LIMIT ?2 OFFSET ?3
  `).bind(state.current_sync_id, limit, offset).all<LibraryItemRow>();

  const items = rows.results.map((row) => ({
    source: "pixiv",
    post_id: row.item_id,
    visibility: "public",
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
    visibility: "public",
    snapshot: {
      complete: state.complete === 1,
      truncated_reason: state.truncated_reason,
      public_count: state.public_count,
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
