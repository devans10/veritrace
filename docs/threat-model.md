# Threat model (v0.1, build step 1)

**Primary adversary:** the content being checked. Every byte of a fetched page, its
redirects, its subresources and its scripts is attacker-controlled.

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
| Prompt injection | (step 2) The LLM has no tools; content is fenced as data; claims must quote verified spans | step 2 |

**Known gaps (tracked):**
- Dev overlay exempts `172.31.250.0/24` (fixture site) from SSRF blocking. `ENV=production` refuses `SSRF_ALLOW_CIDRS`.
- The dev overlay puts postgres/redis/seaweedfs on a bridge network to publish localhost debug ports.
- A case whose worker is killed mid-stage (e.g. OOM) stays in a non-terminal state; a stale-case reaper in the orchestrator is planned for step 2.
- No auth or rate limiting on the API yet (build step 6). Bind stays `127.0.0.1` by default.
