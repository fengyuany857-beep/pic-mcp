import { McpServer } from "@modelcontextprotocol/server";
import { createMcpHandler } from "agents/mcp/server";
import { z } from "zod";
import legacyWorker from "./legacy-bridge";
import { type D1Database, readPublicFavorites } from "./library";

interface Env { AUTH_DB: D1Database }

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

function createPublicReadMcpServer(env: Env): McpServer {
  const server = new McpServer({ name: "pic-mcp", version: "0.5.0-build02" });

  server.registerTool(
    "get_my_favorites",
    {
      description: "Read the public portion of the latest iPhone-synchronized Pixiv favorites snapshot from PicMCP. No OAuth is required. Read-only; this is not a live Pixiv API call.",
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        openWorldHint: false,
      },
      inputSchema: {
        source: z.literal("pixiv").optional(),
        visibility: z.literal("public").optional(),
        limit: z.number().int().min(1).max(100).optional(),
        offset: z.number().int().min(0).max(10000).optional(),
      },
    },
    async ({ limit, offset }) => {
      try {
        const result = await readPublicFavorites(env.AUTH_DB, limit ?? 50, offset ?? 0);
        return {
          content: [{ type: "text", text: JSON.stringify(result) }],
        };
      } catch {
        return {
          isError: true,
          content: [{ type: "text", text: JSON.stringify({ status: "ERROR", error: "LIBRARY_READ_FAILED" }) }],
        };
      }
    },
  );

  return server;
}

export default {
  async fetch(request: Request, env: Env, ctx: any): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/mcp") {
      return createMcpHandler(
        () => createPublicReadMcpServer(env),
        { route: "/mcp" },
      )(request, env, ctx);
    }

    if (request.method === "GET" && url.pathname === "/health") {
      const response = await legacyWorker.fetch(request, env as any, ctx);
      const body = await response.clone().json().catch(() => ({})) as Record<string, unknown>;
      return json({
        ...body,
        runtime: "pic-mcp-v0.5-build02",
        mcp: "no-auth-read-only",
        mcp_auth: "none",
        mcp_endpoint: "/mcp",
        mcp_visibility: "public-only",
        library_read: "d1-snapshot",
        promoted_from: "trial/upo-06 index-v12",
        legacy_bridge: "trial/upo-06 index-v11",
      }, response.status);
    }

    return legacyWorker.fetch(request, env as any, ctx);
  },
};
