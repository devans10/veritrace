# Threat model (v0.1)

**Primary adversary:** the content being checked. Every byte of a fetched page
— its HTML, redirects, subresources, scripts, uploaded media, EXIF, and C2PA
manifest — is attacker-controlled.

**Secondary adversary:** a logged-in user attempting to abuse the system
(DoS, uploading hostile files). Auth and quotas themselves are **out of scope
for v0.1** (build step 6); the API binds `127.0.0.1` by default so the
operator decides the exposure.

## Controls (what we block, how, where it's tested)

| Threat | Control | Tested by |
|---|---|---|
| SSRF via submitted URL | `parse_target` (scheme/port/credentials/IP-literal/legacy-IP/localhost) + `check_target` (every resolved IP must be global) at API and again in the worker | `tests/unit/test_ssrf.py`, `test_api_rejects_unsafe_urls` |
| SSRF via redirect | SafeFetcher re-validates every hop; the browser goes through Smokescreen, which re-checks on its own resolution; final URL re-checked | `test_fetch_blocks_redirect_*`, `test_redirect_to_internal_fails_closed` |
| DNS rebinding | SafeFetcher pins the connection to the vetted IP (no proxy) or relies on the proxy's resolve-and-check (with proxy) | `test_fetch_pins_to_vetted_ip_*`, `test_fetch_blocks_redirect_to_rebinding_host` |
| Page JS reaching internal services | Browser container sits only on the internal `sandbox` network; route guard blocks localhost/non-global IP literals; `<-loopback>` removes Chromium's implicit proxy bypass; Smokescreen denies private ranges (407 + `X-Smokescreen-Error`, treated as blocked) | `test_hostile_page_subresources_blocked`, `test_browser_container_cannot_reach_*`, `test_proxy_refuses_internal_destinations` |
| Browser escape | Non-root `pwuser`, Chromium sandbox on via Playwright's seccomp profile, `cap_drop: ALL`, `no-new-privileges`, read-only rootfs, memory/CPU/PID limits | manual (compose config) |
| Stored XSS via captured HTML | Artifacts are served with `CSP: sandbox; default-src 'none'`, `nosniff`; HTML is served as `text/plain` attachment | `test_capture_stores_snapshot_with_verifiable_hashes` |
| Tampering with the investigation record | Hash-chained audit log; DB trigger rejects UPDATE/DELETE/TRUNCATE (even for the owner); app role has INSERT/SELECT only on it and no DDL | `tests/unit/test_audit.py`, `test_audit_log_is_append_only`, `test_app_role_cannot_ddl` |
| Queue payload code execution | All jobs call one `dispatch` entrypoint that looks tasks up in a fixed registry | `test_task_registry_rejects_unknown_tasks` |
| Secret leakage | `.env` gitignored and generated locally; `*_FILE` support; `SecretStr` keeps secrets out of reprs | `test_secrets_not_in_repr` |
| Prompt injection via captured text | LLM has no tools; captured text is passed only inside a fenced `<content>` block that the system prompt declares to be data; model output is schema-constrained via `complete_json`; every extracted claim must include a verbatim `source_span`, and claims whose span is NOT present in the captured text are dropped | `tests/unit/test_extract_pure.py::test_find_span_returns_none_for_hallucinated_claim`, integration (fakes return deterministic results) |
| Fact-check / rating ambiguity | Textual ratings pass through a conservative lexicon (`core/investigators/rating_lexicon.py`); unmapped ratings are **neutral**, with the hit recorded on the could-not-verify list rather than coerced | `tests/unit/test_rating_lexicon.py`, `tests/unit/test_claimcheck_mapping.py::test_unmapped_rating_becomes_unverified_entry` |
| Silent gaps in investigation | Every investigator always writes something: evidence when it has signal, an `unverified_items` row otherwise (`not_configured` / `provider_unavailable` / `no_results` / `unsupported_platform`). The report always renders the could-not-verify list. | `test_full_dod_url_case_produces_report` (asserts `unverified_total >= 1`) |
| Unsafe uploads | Magic-byte sniffing (`filetype`) ignores client-supplied Content-Type; MIME allowlist; 25 MB size cap; re-encoded preview; media artifacts never served with their original Content-Type (`SAFE_MIME['media'] = application/octet-stream`); every artifact response carries `nosniff` | `tests/unit/test_uploads.py`, `test_upload_rejects_non_image_payload`, `test_upload_rejects_oversize` |
| Media-URL SSRF (step 4) | Media investigator downloads images from the captured HTML only through `SafeFetcher`, which runs the same DNS + redirect + egress-proxy pipeline as the capture itself; `allowed_content_types=("image/",)` rejects HTML redirects | `test_hostile_page_subresources_blocked` covers the egress-proxy side |
| Over-aggressive AI-detection claims | No AI-generation detector is enabled in v0.1 (`NullDetector`). Every case records "detector disabled" on the could-not-verify list rather than producing a weak signal that would move the band. | `test_full_dod_url_case_produces_report` (verifies the unverified list is non-empty) |
| ELA false positives | `media.ela` is weighted 0 in `config/scoring.yaml`; it stores the diff artifact for human review but never moves the band | `tests/unit/test_scorer.py::test_shipped_scoring_yaml_loads` (ensures ELA weight=0) |
| Reporting unmapped signals as 0 | Investigators that emit a signal name NOT declared in `scoring.yaml` have that signal recorded in `score.unknown_signals` and contributed 0 — new signals must be explicitly added to the config | `tests/unit/test_scorer.py::test_unknown_signals_are_collected_not_scored` |
| Un-reproducible scores | Every `scores` row stores `scorer_version` + `config_sha256`; changing a weight bumps `config_sha256` and invalidates downstream caches | `test_full_dod_url_case_produces_report` asserts both fields present |

## Deadlock prevention

The investigator fan-out contends on a hash-chained audit log that serialises
via a Postgres advisory lock. The ordering invariant: `audit.append` must
acquire the advisory lock BEFORE any pending INSERTs flush — otherwise the
FK SHARE lock on the referenced case row sits ahead of the advisory lock,
inverting the order used by `advance_investigation` and causing a deadlock
once the fifth investigator is in play. `audit.append` wraps the lock
acquisition in `session.no_autoflush` to enforce the correct order. (See
step 4 commit for the fix.)

## Known gaps (tracked)

- **Dev overlay exempts `172.31.250.0/24` (fixture site) from SSRF blocking.**
  `ENV=production` refuses `SSRF_ALLOW_CIDRS` at startup.
- **The dev overlay** puts postgres/redis/seaweedfs on a bridge network to
  publish localhost debug ports. Not used in production compose.
- **A case whose worker is killed mid-stage** (e.g. OOM) stays in a
  non-terminal state; a stale-case reaper in the orchestrator is planned but
  not yet shipped.
- **No auth or rate limiting** on the API (step 6). API bind stays
  `127.0.0.1` by default. HTMX uses `hx-get` polling, which also benefits
  from being gated behind auth before any public exposure.
- **HTMX is loaded from unpkg CDN with SRI**. Operators who want zero CDN
  dependencies can self-host the vendor JS by overriding `base.html`.
- **Social-platform signals** (account age, posting history, coordination)
  are always reported as `unsupported_platform` — no platform APIs are
  configured in v0.1.
- **Live calibration data** (Tier B eval, confusion matrices against
  real-world labeled sets) are deferred. The scorer ships band-only; no
  raw-probability output leaks via the report.
