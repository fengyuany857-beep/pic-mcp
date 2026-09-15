# Trial 06｜Pixiv Mobile OAuth Helper Bridge

## Goal

Verify a mobile-first Pixiv authentication path for PicMCP:

iPhone helper
→ Apple `ASWebAuthenticationSession`
→ Pixiv PKCE login
→ `pixiv://` authorization callback
→ one-time PicMCP auth session
→ server-side token exchange
→ server-side refresh-token storage
→ later `get_user_bookmarks()` verification.

The helper must never receive or store the Pixiv refresh token.

## Frozen target

- Repository: `fengyuany857-beep/pic-mcp`
- Branch: `trial/upo-06-pixiv-mobile-oauth`
- Base: `077f802e395e3d78e38e96292b4203655939bdd4`
- Prior Trial 01D-04 paths are immutable for this trial.
- Trial 05 remains on its own branch and is not merged into Trial 06.

## Capability split

- `CAP-PIXIV-MOBILE-AUTH-UI`
  - Responsibility: open the trusted Apple system browser session.
  - Does not own token storage or Pixiv recommendation logic.
- `CAP-PIXIV-PKCE-CALLBACK`
  - Responsibility: generate PKCE and recover the one-time authorization code.
  - Does not expose refresh/access tokens.
- `CAP-AUTH-BRIDGE`
  - Responsibility: bind a one-time PicMCP session to returned code + verifier.
  - Session is short-lived and single-use.
- `CAP-SERVER-TOKEN-EXCHANGE`
  - Responsibility: exchange code for tokens on the server and persist only there.
  - HTTP response deliberately omits tokens.

## Reuse / provenance

- Apple AuthenticationServices (`ASWebAuthenticationSession`): `DIRECT_REUSE`
- Apple CryptoKit + Security: `DIRECT_REUSE`
- `eggplants/get-pixivpy-token@20993cfa75d1654073d16405079f2d86a5baa9d0`
  - MIT
  - pinned Pixiv PKCE/token-contract reference.
- `youshen2/Hanairo@183e2b4230b9846bfab0fc12c3a18238c2732bac`
  - MPL-2.0
  - static behavioral reference only; no Hanairo source file is copied.
- XcodeGen `2.46.0`
  - build tooling only
  - pinned artifact SHA-256: `4d9e34b62172d645eed6457cac13fc222569974098ef4ee9c3368bedf0196806`.

## Security boundary

Public HTTP does **not** expose a route that anonymously creates an auth session.

Expected production flow:

1. PicMCP tool `start_pixiv_mobile_auth()` creates a high-entropy, 5-minute, one-time session.
2. User supplies that session ID to the helper. This is manual in Trial 06; deep link / QR is a later UX capability.
3. Helper performs Pixiv login and sends only `session_id`, authorization `code`, and PKCE `code_verifier`.
4. PicMCP consumes the session before exchange to prevent replay.
5. PicMCP exchanges the code and stores refresh token server-side.
6. Token material is not returned to the helper and must not appear in logs.

## Verification gates

- `BRIDGE_CORE_CONTRACT_PASS = PASS`
- `SESSION_EXPIRY_PASS = PASS`
- `SESSION_REPLAY_GUARD_PASS = PASS`
- `TOKEN_RESPONSE_REDACTION_PASS = PASS`
- `TOKEN_EXCHANGE_REQUEST_SHAPE_PASS = PASS`
- `IOS_HELPER_BUILD_PASS = PASS`
- `UNSIGNED_IPA_PASS = PASS`
- `IPHONE_SYSTEM_AUTH_SESSION_PASS = UNKNOWN` — requires real signed install on user device
- `PIXIV_CALLBACK_PASS = UNKNOWN` — requires real user login
- `SERVER_TOKEN_EXCHANGE_LIVE_PASS = UNKNOWN` — requires reachable HTTPS PicMCP bridge
- `USER_BOOKMARKS_READ_PASS = UNKNOWN` — final live gate

## CI evidence

- GitHub Actions run: `34997655140`
- Tested head: `00ef890d3febdfdd3b1f49ce5f148003d53a2b4e`
- `bridge-contract`: SUCCESS
- `ios-helper-build`: SUCCESS
- prior Trial 01D-04 immutability check: SUCCESS

Artifacts:

- `trial-06-pixiv-mobile-oauth-helper-1`
  - Artifact ID: `10408825174`
  - archive digest: `sha256:d3041f2334327e644a0d33f458e9a5e897bf7f40f50c4167f7c35a210dcc5ff0`
  - contains unsigned IPA + Xcode/XcodeGen/build evidence
- `trial-06-pixiv-mobile-oauth-bridge-1`
  - Artifact ID: `10408257852`
  - archive digest: `sha256:574f1c158d14ad371f22ae96a27b391857a0db83dee68d3be4bd65cfa8647aad`
  - contains Python contract-test evidence

## Current state

Build/contract stage: `PASS`.

Real user outcome remains `PARTIAL` until a signed helper runs on the user's iPhone against a reachable HTTPS PicMCP bridge, captures the real Pixiv callback, completes server-side token exchange, and reads the user's bookmarks.

No deployment is authorized by this trial. A public HTTPS bridge must not be deployed automatically.
