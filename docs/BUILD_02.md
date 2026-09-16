# Build 02 | Promote Trial 06 read capability into formal Remote MCP runtime

Date: 2026-09-16

## Target

Repository: `fengyuany857-beep/pic-mcp`

Build branch: `build/v0.5-mainline`

Base revision: `cad06c92d670e1669734991a11833fb06b26b034`

Base branch: `trial/upo-06-pixiv-mobile-oauth`

Main branch observed separately at: `077f802e395e3d78e38e96292b4203655939bdd4`

## Goal

Stop treating the working Remote MCP + Pixiv D1 snapshot read path as a Trial-only artifact. Promote the verified public read capability into a formal runtime boundary without rewriting the already working iPhone sync path.

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

The D1 snapshot reader is extracted from Trial 06 v12 transport into `runtime/worker/src/library.ts` so the transport no longer owns query/normalization details.

### AU-02-03 Snapshot producer boundary

Reuse mode: EXISTING_EXTERNAL_RUNTIME_BOUNDARY

The already verified Trial 06 iPhone/Pixiv sync Worker remains a separate snapshot producer. The formal Build 02 runtime does not import Trial 06 OAuth/sync TypeScript.

The integration edge is intentionally reduced to:

```text
Trial 06 sync writer -> D1 snapshot <- formal PicMCP read plane
```

This avoids coupling formal typecheck/build to historical Trial source chains while preserving the working sync path.

## First build attempt and repair

Build 02 attempt 1 commit:
`87ee14e3632c4ddef3ae8398ea28a80590e9f8bf`

CI run:
`35095438201`

Result:
`BUILD_FAIL`

Classification:
`EDGE_INCOMPATIBLE / GLUE_OVERGROWTH_RISK`

Observed failure:
The initial `legacy-bridge.ts` imported Trial 06 v11, which recursively pulled historical Trial TypeScript into the formal strict typecheck. Module resolution then searched dependency context from the Trial tree and produced unresolved package and historical typing errors.

Rejected fix:
- disabling strict mode
- duplicating node_modules
- suppressing historical Trial type errors

Applied structural fix:
- remove the source import bridge entirely
- formal runtime owns only `/mcp` + `/health`
- existing Trial 06 sync service remains an independent D1 producer
- remove unused OAuth provider dependency from the formal read plane

## Write scope

Allowed:
- `runtime/worker/**`
- `docs/BUILD_02.md`
- `.github/workflows/build-v0.5-mainline.yml`

Excluded:
- `main` direct mutation
- deployment
- Cloudflare resource mutation
- Trial deletion or rewrite

## Build policy

Formal read-plane dependencies:
- `@modelcontextprotocol/server@2.0.0`
- `agents@0.20.1`
- `zod@4.4.3`

Build tooling:
- `typescript@5.9.2`
- `wrangler@4.131.2`

Dependency installation uses `--ignore-scripts` in CI.

## Verification target

The Build 02 workflow must:
1. verify prior Trial directories are untouched,
2. install dependencies with lifecycle scripts disabled,
3. TypeScript typecheck the formal runtime,
4. run a Wrangler dry-run bundle,
5. perform no deployment.

A CI green result means `BUILD_PASS` for this bounded formal read-plane promotion only. It does not mean the whole PicMCP recommendation system is complete.

## Deferred

- deployment of formal runtime
- recommendation core promotion from local Build 01/reference trials
- feedback/EventLedger in production runtime
- Pixiv live related works
- BridgeTag
- WD14
- cross-source identity
- source favorite writes
- MCP Apps Gallery promotion
- formal migration/replacement of the separate Trial 06 snapshot writer

## Note on local Python Build 01

The local `PicMCP-v0.5` Python build remains executable reference evidence for recommendation + feedback semantics, but it is not the authoritative deployment mainline. The Cloudflare Worker / TypeScript repository is the formal runtime target.
