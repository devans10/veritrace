# External API verification log

The rule: no external API behavior is assumed from memory. Each entry records what was
checked against current documentation (with date) and what remains unverified.

| Dependency | Verified (2026-10-26) | Still unverified / to check before use |
|---|---|---|
| Internet Archive SPN2 | `POST https://web.archive.org/save` with `Authorization: LOW key:secret`, `Accept: application/json`, form `url=` returns `url`, `job_id`. `GET /save/status/<job_id>` returns `status` (pending/success/error); success has `timestamp`, `original_url`; error has `status_ext`, `message`. Capture URL `https://web.archive.org/web/<timestamp>/<original_url>`. Limits: 7 captures/min and 30k/day authenticated, 5 captures/URL/day; status kept ~1h. Source: "Save Page Now 2 Public API Docs" (Google Doc). | Not yet exercised live: the request shape is unit-tested with recorded responses only. Behaviour when a URL was archived within `if_not_archived_within` (default 1h) has not been observed. |
| Wayback CDX (step 3) | — | Everything: endpoint, params, rate limits. |
| Google Fact Check Tools (step 2) | `GET https://factchecktools.googleapis.com/v1alpha1/claims:search`; params `query`, `languageCode`, `reviewPublisherSiteFilter`, `maxAgeDays`, `pageSize`, `pageToken`, `offset`; response `claims[]`, `nextPageToken`. | Auth parameter (API key) and `Claim`/`ClaimReview` field names. |
| SearXNG (step 2) | `/search?q=...&format=json`; JSON must be enabled under `search.formats` in settings.yml or the server returns 403. | Health endpoint, engine configuration. |
| Smokescreen v0.1.0 | Release binary + SHA256SUMS from GitHub releases. CLI flags read from source (`cmd/smokescreen.go`). Private ranges blocked by default. Denials observed live: HTTP 407 with `X-Smokescreen-Error`. | `ghcr.io/stripe/smokescreen` image pull was denied, so we install the release tarball. ACL files are not used. |
| SeaweedFS 4.48 | `weed server -s3 -s3.config=<json>` (flags read from the binary); `identities[].credentials[].accessKey/secretKey`, `actions`. `/healthz` on the S3 port answered 200 live. With no identities, the gateway allows everything, so we always configure one. | — |
| MinIO | CE images stopped Oct 2025; repo archived 2026. Not used. The `minio` Python client (7.2.20) is used only as a generic S3 client and works against SeaweedFS (contract test). | minio-py long-term maintenance. |
| Playwright 1.63.0 image | Docs: run as non-root with `seccomp_profile.json` to keep the Chromium sandbox. Profile pinned from tag v1.63.0. | — |
| RDAP, c2pa-python, OpenAI-compatible `response_format` | — | Steps 2–4. |
