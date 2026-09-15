# Migration provenance

PicMCP was split out of temporary image-recommendation trials that were hosted in `fengyuany857-beep/chatmcp` only as a CI execution shell.

## Frozen source

Source repository: `fengyuany857-beep/chatmcp`

Combined source branch: `trial/upo-03-near-duplicate`

Source head commit: `7c33954a91ddd2d7506ff5ae32e5710b9893637b`

Frozen trial trees:

- Trial 01D: `ed5737b6f64829a4f90fdb91109f78e8f77b07f4`
- Trial 02: `3679e4768e82a0209563488231877d11ae75a145`
- Trial 03: `d88f23daec8f18c7cd9ee5079276dc9555ff8994`

## Migration policy

- Copy first, verify destination, retain source as rollback/provenance.
- No image algorithm or Contract behavior is intentionally changed during migration.
- The only planned CI delta is repository-local triggering on `main`, with path filters so each migrated trial reruns only when its own dependency surface changes.
- Temporary `chatmcp` trial branches are not deleted by this migration.

## Boundary

`pic-mcp` owns image-domain implementation and evidence.

`forge-mcp` owns the generic capability discovery / inspection / selection / composition / assembly / verification methodology. Image-specific code must not migrate into `forge-mcp` merely because PicMCP was used as a ForgeMCP case study.
