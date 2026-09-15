# Super Assembly Trial 04 | MCP Apps Gallery Rendering

## Goal

Prove that the existing recommendation result can be bound to an independent MCP Apps gallery without modifying the recommendation core, while retaining a non-UI fallback.

## Frozen boundary

- Existing Trial 01D recommender is imported as-is.
- Existing Trial 01D/02/03 files are read-only for this trial.
- No image proxy, cache, CDN, database, or persistent preference store is introduced.
- Model vision is not part of the success criteria.
- Official MCP Apps reference implementation is pinned to `modelcontextprotocol/ext-apps@v2.0.0`.
- Upstream Booru organ remains pinned to `echo-xianyu/Booru-Pictag-Get-MCP@ed234568fc376517d0abc5a377d65af37981b059`.

## Atomic capabilities under test

- `CAP-MEDIA-REF-RESOLVE`: Candidate -> RenderableMediaRef
- `CAP-GALLERY-RESULT-BIND`: RecommendationResult -> GalleryPayload
- `CAP-MCP-APP-RESOURCE-SERVE`: `ui://media/recommendations`
- `CAP-GALLERY-INTERACTION-BRIDGE`: View -> host -> MCP tool
- `CAP-HOST-UI-NEGOTIATE`: UI metadata plus ordinary tool-result fallback

## Evidence gates

The workflow records these independently:

- MCP_TRANSPORT_PASS
- UI_RESOURCE_PASS
- GALLERY_BIND_PASS
- REAL_RECOMMENDATION_BIND_PASS
- IMAGE_FETCH_PASS
- FALLBACK_PASS
- GALLERY_RENDER_PASS
- IMAGE_DISPLAY_PASS
- UI_ACTION_PASS
- RECOMMENDATION_REFRESH_PASS
- REFERENCE_HOST_GALLERY_RENDER_PASS
- CHATGPT_HOST_GALLERY_RENDER
- MODEL_VISION_PASS

## Status

`PENDING_FIRST_REAL_RUN`

The final status must be updated only from observed GitHub Actions evidence. A reference-host PASS must not be promoted to a ChatGPT-host PASS.
