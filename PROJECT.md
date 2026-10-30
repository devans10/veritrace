# Provenance Check: Content Verification Agent

A local-first, container-portable system that takes a social media post or article, investigates its origin, and returns a **credibility assessment** with a full evidence trail.

> **Important framing:** The system produces a calibrated *credibility score band* plus evidence, not a verdict. "Fake" is not one thing, and no one has ground truth at scale. Every output must show its evidence and an explicit "could not verify" list.

---

## 1. Goals

- Accept a URL, pasted text, or uploaded media.
- Trace provenance (where and when it first appeared, who originated it).
- Check source reputation, media authenticity, and the factual claims made.
- Return a score band, categorized findings, and an auditable evidence ledger.
- Run entirely locally in containers; port later to any cloud with config changes only.
- Eventually offer it as a website, API, and browser extension, with paid tiers.

## 2. Non-goals

- Not a truth oracle. No claim of certainty.
- Not tied to any single cloud vendor (the Azure stack is intentionally excluded).
- No auto-publishing of "fake" labels about named individuals without human review.

## 3. Output model

Findings are categorized rather than collapsed to one number:

| Category | Meaning |
|---|---|
| Fabricated | Content or media appears invented or synthetic |
| Misleading context | Real content, wrong date/place/framing |
| Satire / parody | Originates from a known satirical source |
| Unverified | Insufficient evidence either way |
| Corroborated | Independent credible sources confirm |

The overall result is a **band** (low / uncertain / high risk) with a confidence level. Numeric probabilities are shown only after calibration against labeled data.

## 4. Architecture (docker compose, later Kubernetes)

| Container | Role |
|---|---|
| `api` | FastAPI. Accepts submissions, returns reports, serves the web UI. |
| `orchestrator` | LangGraph or a plain Python state machine. Runs the pipeline and maintains the evidence ledger. |
| `workers` | Celery or RQ. One pool per investigator type. |
| `redis` | Queue and cache. |
| `postgres` (+ pgvector) | Cases, claims, evidence, scores, audit log. |
| `minio` | S3-compatible storage for snapshots, screenshots, media. |
| `browser` | Headless Playwright. Sandboxed, restricted egress. |
| `llm` | Ollama or vLLM behind an OpenAI-compatible API. |
| `searxng` | Self-hosted metasearch for provenance and corroboration. |
| `observability` | OpenTelemetry collector, Grafana, Loki. |

## 5. Pipeline

1. **Capture:** render and snapshot the page, archive it (Wayback save), hash content.
2. **Claim extraction:** the LLM returns structured JSON of checkable claims.
3. **Parallel investigators**, each emitting evidence items:
   `{finding, source_url, direction (supports|undermines|neutral), strength, confidence}`
   - **Provenance:** earliest-appearance search, archive history, reverse image search, link graph.
   - **Source:** domain age/WHOIS, account age and history, coordination and bot-like signals.
   - **Media:** EXIF, C2PA/Content Credentials, error-level analysis, AI-generation detectors (weak signals).
   - **Claim check:** Google Fact Check API / ClaimReview, wire services, primary sources.
   - **Corroboration:** do independent, credible outlets report the same event?
4. **Scorer:** combine the evidence with a logistic or Bayesian model; calibrate on labeled data.
5. **Report:** score band, category breakdown, evidence trail, "could not verify" list.

## 6. Portability rules

- Put every external dependency behind an interface: `LLMClient`, `SearchProvider`, `ObjectStore`, `Queue`, `ReverseImageProvider`.
- Configuration via environment variables and secrets files only.
- Stateless application images.
- Standard Postgres and Redis drivers so managed equivalents drop in.
- Write Helm or Kustomize manifests early, even while running on compose.

## 7. Security requirements

- Fetched content is **untrusted data, never instructions** (prompt-injection defense). Never let page text alter agent behavior or tool use.
- Browser container: sandboxed, no access to internal networks, egress allowlist or proxy, resource limits.
- SSRF protection on all URL fetching (block private/link-local ranges).
- Strict file-type and size validation on uploads.
- Audit log of every investigation step.

## 8. Known constraints

- **Search is the hard part.** Self-hosted options (SearXNG, Common Crawl, Wayback API, Google Fact Check API) cover a lot, but reverse image search is weak locally; plan on a pluggable provider (e.g., TinEye).
- **Local LLM limits:** good for claim extraction and summarization, weak at adjudication. Keep judgment in the scorer and evidence.
- **Detector unreliability:** AI-image/text detectors produce false positives and negatives. Treat as weak signals only.
- **Absence of evidence is not evidence of fakery.** Breaking news often looks unverified.
- **Calibration** needs labeled data (FakeNewsNet, LIAR, ClaimReview archives). Until then, bands only.
- **Adversarial pressure:** sources can be SEO-gamed or poisoned.

## 9. Web service and monetization (later phase)

**Web layer:** frontend (paste URL / upload, live investigator progress via SSE, shareable report permalinks), auth and accounts, API keys, per-user rate limits and quotas, usage metering, browser extension.

**Pricing model (recommended):**
- Free tier: a few quick checks per day.
- Pro: more checks, deep analysis, history, for individuals and journalists.
- Metered API: per-check pricing for platforms and developers, priced by **depth** (quick triage vs. full investigation).
- Enterprise: seats, SSO, audit exports, SLAs, optional self-hosted deployment.

**Before charging:** terms of service framing results as assessments not verdicts, per-URL result caching, cheap checks first with deep checks gated, cost-per-check tracking from day one, and abuse safeguards (people may cite "it passed the checker" as proof).

## 10. Suggested build order

1. `api` + `postgres` + capture/archive (with hashing).
2. Claim extraction and the claim-check investigator.
3. Provenance and source investigators.
4. Media forensics.
5. Scorer, calibration dataset, report UI.
6. Auth, metering, billing (Stripe), browser extension.
7. Helm/Kustomize manifests and a cloud-portability pass.

## 11. Repo layout (proposed)

```
provenance-check/
  docker-compose.yml
  .env.example
  services/
    api/
    orchestrator/
    workers/
  core/
    interfaces/        # LLMClient, SearchProvider, ObjectStore, Queue
    providers/         # ollama, searxng, minio, redis implementations
    models/            # evidence, claim, case, score schemas
    investigators/     # provenance, source, media, claimcheck, corroboration
    scoring/
  web/
  deploy/
    helm/
  tests/
  docs/
```

## 12. Definition of done (v0.1)

- `docker compose up` brings up the full stack locally.
- Submitting a URL produces a report with captured snapshot, extracted claims, at least two investigators' evidence, a score band, and a "could not verify" list.
- All external dependencies are swappable through interfaces and config.
- Basic test suite and a small labeled evaluation set are in place.