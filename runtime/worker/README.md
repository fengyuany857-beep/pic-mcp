# PicMCP runtime/worker

Formal Cloudflare Worker mainline introduced in Build 02.

## Current responsibility

- `/mcp` is public, no-auth, read-only.
- `get_my_favorites` reads only the public portion of the latest iPhone-synchronized Pixiv snapshot from D1.
- Tool annotations declare `readOnlyHint=true`, `destructiveHint=false`, `openWorldHint=false`.
- Existing Trial 06 OAuth/mobile-sync behavior is temporarily reused through `src/legacy-bridge.ts`.

## Deliberate non-goals for Build 02

- no deployment from this branch
- no source-site writes
- no recommendation ranking yet
- no feedback writes yet
- no BridgeTag / WD14 / cross-source identity
- no attempt to rewrite the verified Trial 06 sync chain

## Verification

```bash
npm install --ignore-scripts --no-audit --no-fund
npm run typecheck
npm run build:dry
```

The Build 02 CI performs those checks only. Deployment remains a separate authorization step.
