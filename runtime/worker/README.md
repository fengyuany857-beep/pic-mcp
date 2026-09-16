# PicMCP runtime/worker

Formal Cloudflare Worker read plane introduced in Build 02.

## Current responsibility

- `/mcp` is public, no-auth, read-only.
- `get_my_favorites` reads only the public portion of the latest iPhone-synchronized Pixiv snapshot from D1.
- Tool annotations declare `readOnlyHint=true`, `destructiveHint=false`, `openWorldHint=false`.
- `/health` reports the formal read-plane boundary.

## Runtime split

Build 02 deliberately separates the system into two services:

```text
iPhone / Pixiv sync writer (existing Trial 06 service)
        ↓ writes bounded snapshot
Cloudflare D1 AUTH_DB
        ↑ reads only
formal PicMCP runtime/worker
        ↓
ChatGPT Remote MCP
```

The formal read plane does not import or execute Trial 06 OAuth/sync source code. Trial 06 remains the currently verified snapshot producer until a later bounded migration.

## Deliberate non-goals for Build 02

- no deployment from this branch
- no source-site writes
- no recommendation ranking yet
- no feedback writes yet
- no BridgeTag / WD14 / cross-source identity
- no rewrite of the verified Trial 06 sync writer

## Verification

```bash
npm install --ignore-scripts --no-audit --no-fund
npm run typecheck
npm run build:dry
```

The Build 02 CI performs those checks only. Deployment remains a separate authorization step.
