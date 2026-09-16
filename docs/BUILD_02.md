# Build 02 | Promote Trial 06 into formal Remote MCP runtime

Date: 2026-09-16

## Target

Repository: `fengyuany857-beep/pic-mcp`

Build branch: `build/v0.5-mainline`

Base revision: `cad06c92d670e1669734991a11833fb06b26b034`

Base branch: `trial/upo-06-pixiv-mobile-oauth`

Main branch observed separately at: `077f802e395e3d78e38e96292b4203655939bdd4`

## Goal

Stop treating the working Remote MCP + Pixiv D1 snapshot read path as a Trial-only artifact. Promote that verified capability into a formal runtime boundary without rewriting the already working iPhone sync / legacy OAuth path.

## Assembly units

### AU-02-01 Formal Remote MCP read surface

Reuse mode: ADAPTED_REUSE

Source: `trials/upo-06/worker/src/index-v12.ts`

Source blob: `ca6341b4c8e5a59dd0142b4054f60ed2cf271daf`

Promoted behavior:
- no-auth `/mcp`
- public-only Pixiv snapshot reads
- `get_my_favorites`
- explicit read-only annotations
- D1-backed snapshot pagination

### AU-02-02 Library read module

Reuse mode: ADAPTED_REUSE

The D1 snapshot reader is extracted from the Trial 06 v12 transport into `runtime/worker/src/library.ts` so the transport no longer owns query/normalization details.

### AU-02-03 Legacy sync bridge

Reuse mode: GENERATED_GLUE

`runtime/worker/src/legacy-bridge.ts` explicitly delegates non-MCP routes to the already-verified Trial 06 v11 chain.

This is intentionally temporary. It preserves working iPhone sync / legacy OAuth behavior while avoiding a premature rewrite.

## Write scope

Allowed:
- `runtime/worker/**`
- `docs/BUILD_02.md`
- `.github/workflows/build-v0.5-mainline.yml`

Excluded:
- `main` direct mutation
- deployment
- Cloudflare resource mutation
- D1 mutation outside existing runtime behavior
- Trial deletion or rewrite

## Build policy

Direct dependency versions stay aligned with Trial 06:
- `@cloudflare/workers-oauth-provider@0.10.3`
- `@modelcontextprotocol/server@2.0.0`
- `agents@0.20.1`
- `zod@4.4.3`

Build tooling is pinned to the already used project toolchain:
- `typescript@5.9.2`
- `wrangler@4.131.2`

## Verification target

The Build 02 workflow must:
1. install dependencies with lifecycle scripts disabled,
2. TypeScript typecheck the formal runtime,
3. run a Wrangler dry-run bundle,
4. perform no deployment.

A CI green result means `BUILD_PASS` for this bounded runtime promotion only. It does not mean the whole PicMCP recommendation system is complete.

## Deferred

- recommendation core promotion from local Build 01/reference trials
- feedback/EventLedger in production runtime
- Pixiv live related works
- BridgeTag
- WD14
- cross-source identity
- source favorite writes
- MCP Apps Gallery promotion
- removal of the legacy Trial 06 bridge

## Note on the local Python Build 01

The local `PicMCP-v0.5` Python build remains useful executable reference evidence for recommendation + feedback semantics, but it is not the authoritative deployment mainline. The Cloudflare Worker / TypeScript repository is the current formal runtime target.
