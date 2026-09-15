# Trial 05 | Six-Source Stabilization

Date: 2026-09-15

Status: PARTIAL

## Goal

Stabilize the first PicMCP source set without expanding to additional sites:

- Danbooru
- AIBooru
- Gelbooru
- Rule34
- e621
- Pixiv

The target chain is assessed per source as independent evidence dimensions:

`Source Search -> Canonical ImageCandidate -> Media Gateway -> Render URL Contract`

A green CI job is not treated as proof that every provider passed.

## Scope / authorization

Read-only external network tests only. No account mutation, favorite mutation, credential creation, login automation, UPO protocol edits, or Capability Baseline edits.

Old Trial 01D-04 paths were protected by a baseline diff gate against PicMCP main at:

`077f802e395e3d78e38e96292b4203655939bdd4`

## Selected implementations

### Booru providers

Repository: `echo-xianyu/Booru-Pictag-Get-MCP`

Pinned commit:

`ed234568fc376517d0abc5a377d65af37981b059`

It supplies the current implementations for Danbooru, AIBooru, Gelbooru, Rule34 and e621.

### Pixiv

Repository: `222wcnm/pixiv-mcp-server`

Pinned commit:

`c02e5e67c82f7774b028ccf669567a4d386d1016`

The selected implementation already contains a Pixiv preview proxy contract with HTTPS/host validation, Pixiv Referer handling and a bounded response size. Its existing URL-validation test suite passed 6/6 in the Trial environment.

## Common Media Gateway

Trial 05 adds one provider-aware media boundary rather than exposing upstream image URLs directly to Gallery code.

Current trial policy:

- HTTPS only
- no URL credentials
- standard HTTPS port only
- provider host allowlist
- provider-specific Referer / User-Agent where needed
- bounded media response size: 20 MiB

This is a thin integration boundary, not a CDN/cache/image infrastructure project.

## Final live matrix

GitHub Actions Run: `34991156923`

Artifact: `trial-05-six-source-evidence-4`

Artifact ID: `10406105536`

Artifact ZIP SHA-256:

`2a057deffe68e073c625c53d54113db6b7fe52ba2655b18436dd76de739f0ec4`

| Source | Final state | Evidence |
| --- | --- | --- |
| Danbooru | `LIVE_PUBLIC_CHAIN_PASS` | Live API 200 -> canonical candidates -> common Media Gateway passed |
| AIBooru | `ENVIRONMENT_OR_PROVIDER_BLOCKED` | `/posts.json` returned 403 repeatedly from GitHub Runner; browser-like UA + Referer diagnostic also returned 403 |
| Gelbooru | `CREDENTIAL_BLOCKED` | Live API returned 401 without `user_id + api_key` |
| Rule34 | `CREDENTIAL_BLOCKED` | Public API returned HTTP 200 but empty for two fixtures without credentials; selected upstream explicitly documents `user_id + api_key` as required |
| e621 | `LIVE_PUBLIC_CHAIN_PASS` | Live API 200 -> canonical candidates -> common Media Gateway passed |
| Pixiv | `CREDENTIAL_BLOCKED` | Selected preview proxy contract tests 6/6 PASS; live Pixiv search/media not promoted because no refresh token was provided |

Overall:

`PARTIAL`

## Run history

### Run 1

Did not reach provider testing. The old-Trial immutability gate could not resolve the frozen baseline because the checkout was shallow.

Classification:

`CI_GUARD_BUG`

Fix:

Use full history checkout for the Trial branch. No provider implementation changed.

### Run 2

First usable six-source matrix:

- Danbooru PASS
- e621 PASS
- AIBooru 403
- Gelbooru 401
- Rule34 HTTP 200 / empty
- Pixiv credential gate; preview proxy tests 6/6 PASS

### Run 3

Added diagnostics only:

- AIBooru direct browser-like UA + Referer still returned 403
- Rule34 second public fixture also returned HTTP 200 / empty

No bypass scraper was introduced.

### Run 4

Final clean classification:

- Danbooru `LIVE_PUBLIC_CHAIN_PASS`
- AIBooru `ENVIRONMENT_OR_PROVIDER_BLOCKED`
- Gelbooru `CREDENTIAL_BLOCKED`
- Rule34 `CREDENTIAL_BLOCKED`
- e621 `LIVE_PUBLIC_CHAIN_PASS`
- Pixiv `CREDENTIAL_BLOCKED`

## Findings

### T05-01 | Workflow green != provider PASS

CI success only means the bounded evidence run completed. Each source keeps an independent stabilization state.

### T05-02 | Search / Canonicalization / Media Fetch are separate attestations

A source should not receive a single boolean PASS. A successful search does not prove its media URL can be rendered, and a valid media fetch does not prove search/auth works.

### T05-03 | Credential gates have different observable failure semantics

Gelbooru exposed its missing credential gate as HTTP 401.

Rule34 exposed the unauthenticated path as HTTP 200 with an empty result for both tested fixtures. This cannot be treated as evidence that matching content is absent because the selected implementation explicitly documents credentials as required.

### T05-04 | AIBooru is not proven unavailable

The GitHub Runner received 403 from the API path, including a browser-like Header diagnostic. This proves the current execution environment/API path is blocked. It does not prove the AIBooru source is globally unavailable.

### T05-05 | Common Media Gateway has live evidence from two sources

The same Media Gateway contract successfully consumed live media references from Danbooru and e621. Gelbooru, Rule34 and Pixiv require their credential gates to be cleared before equivalent live media evidence can be collected. AIBooru requires a different permitted execution environment/path test.

### T05-06 | Pixiv proxy contract PASS != live Pixiv media PASS

The selected Pixiv implementation's proxy URL-validation tests passed 6/6. Live Pixiv API/search/media remains unverified without a refresh token. The contract result is deliberately not promoted to a live outcome.

## Credential gates for the next run

Do not paste credentials into chat or commit them to the repository.

Configure repository Actions secrets only:

- `GELBOORU_USER_ID`
- `GELBOORU_API_KEY`
- `RULE34_USER_ID`
- `RULE34_API_KEY`
- `PIXIV_REFRESH_TOKEN`

Optional for future authenticated Danbooru user-library features:

- `DANBOORU_USERNAME`
- `DANBOORU_API_KEY`

No AIBooru credential requirement was established by this Trial. Its current blocker is the GitHub Runner/API execution path, not a proven missing secret.

## Remaining blockers

1. Authenticated Gelbooru live chain.
2. Authenticated Rule34 live chain.
3. Authenticated Pixiv live search -> canonicalization -> Media Gateway chain.
4. AIBooru API execution from a permitted alternative environment/path.
5. Gallery/browser rendering remains owned by Trial 04 and is not promoted by this Trial.

## Next gate

Do not start Source Discovery for yande.re / Konachan / Sankaku / Zerochan yet.

First clear the three credential gates and obtain one valid AIBooru execution path. Then rerun this same matrix without changing the canonical/media contracts.

## Outcome

`PARTIAL`

The six-source set is now bounded and precisely classified, but only Danbooru and e621 currently have full live public-chain evidence in the Trial 05 environment. The remaining four sources are not dropped; their blockers are now explicit and testable.
