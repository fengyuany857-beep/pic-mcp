# PicMCP

Independent image browsing and recommendation MCP project.

This repository is intentionally separate from `forge-mcp` (the generic assembly/orchestration project) and from Personal MCP Hub.

Current migrated evidence-backed trials:

- Trial 01D: real upstream Booru MCP E2E
- Trial 02: related-tag / implication assembly
- Trial 03: near-duplicate detection with ImageHash

Migration source: `fengyuany857-beep/chatmcp` temporary UPO trial branches. Legacy branches are retained temporarily as rollback/provenance sources until migration verification completes.

## Boundaries

PicMCP owns image-domain capabilities such as source retrieval, tag expansion, near-duplicate detection, visual similarity, session preference, ranking and the future image MCP surface.

It does not own the generic Universal Project Organ / ForgeMCP methodology.
