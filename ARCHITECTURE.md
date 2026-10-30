# veritrace architecture (v0.1)

What's actually built, how the pieces fit together, and the invariants that
hold the system up. Read this after [PROJECT.md](PROJECT.md) (the spec) and
[README.md](README.md) (how to run it); this document is the reference for
contributors making changes.

Current state: v0.1 Definition of Done is met (`e182317`). 218 unit tests
and 36 integration tests pass. The Tier A eval set runs 11 hand-authored
cases through the scorer with 0 safety-gate breaches.

---

## 1. What the system does

Given one of three inputs — a URL, a block of pasted text, or an uploaded
image — veritrace produces a **credibility assessment**:

- **Band**: `low_risk | uncertain | high_risk`
- **Confidence**: `low | medium | high`
- **Category breakdown**: how much evidence lands in each of
  `fabricated | misleading_context | satire | corroborated`
- **Evidence trail**: every signal that moved the band, grouped by
  investigator, with direction, strength, confidence, source link, and
  retrieved_at
- **Could-not-verify list**: every provider that was unavailable, every
  unmapped rating, every unsupported-platform signal — rendered even when
  empty, so a user never sees a silent gap
- **Captured snapshot** (URL cases): HTML, screenshot, extracted text,
  Wayback archive link, hashes
- **Claims**: short, standalone paraphrases with verbatim `source_span`
- **Scorer provenance**: `scorer_version` + `config_sha256`

Three things that are deliberately **not** in the output:
1. A numeric probability — band only until calibration.
2. A verdict — the disclaimer says "assessment with evidence, not a verdict"
   on every page and in every report JSON.
3. Platform-coordination signals, which require platform APIs that aren't
   enabled.

---

## 2. Lifecycle of a case

```
URL submission     ─▶  queued ──▶ capturing ──▶ extracting ──▶ investigating ──▶ scoring ──▶ complete
Text submission    ─▶                           extracting ──▶ investigating ──▶ scoring ──▶ complete
Upload submission  ─▶                                          investigating ──▶ scoring ──▶ complete
Any failure        ─▶                                                                     failed
```

Stage transitions live in `src/provcheck/core/pipeline/state.py`:

| Stage          | Task                | Queue           | Worker service       | What it does |
|---|---|---|---|---|
| `capturing`    | `pipeline.capture`  | `capture`       | worker-capture       | Validates URL (two SSRF stages); renders in the sandboxed browser; stores HTML + screenshot + text as artifacts; hashes everything; submits a Wayback save and polls it. Also computes the screenshot's pHash and writes it into `artifacts.phash` so later cases can match against it. |
| `extracting`   | `pipeline.extract`  | `extract`       | worker-extract       | Loads the captured text (URL case) or `input_text` (text case); calls the LLM via `complete_json`; verifies every claim's `source_span` is a verbatim substring of the content (drops the claim otherwise); embeds verified claims; persists `claims` rows. |
| `investigating`| `pipeline.fan_out`  | `orchestrator`  | orchestrator         | Creates one `investigator_runs` row per entry in `state.INVESTIGATORS` and enqueues each on its own queue. |
| (per inv.)     | `investigator.<name>` | one per inv. | worker-&lt;name&gt;  | See §5 below. Each writes evidence/unverified items and finishes the run; `advance_investigation` promotes the case to `scoring` when every run is terminal. |
| `scoring`      | `pipeline.score`    | `scoring`       | worker-scoring       | Reads evidence, applies `score_case()`, writes a `scores` row, builds the full JSON report, stores it as a `report_json` artifact, writes a `reports` row, advances to `complete`. |

**Fault tolerance.** Stage tasks are idempotent under row locks: `advance`
and `advance_investigation` take `FOR UPDATE` on the `cases` row and only
advance from the exact stage the caller just finished. A duplicate delivery
of the same task is a no-op. A worker killed mid-stage leaves the case in
its current stage — a reaper is a known gap (see threat-model.md).

**The audit chain** (`src/provcheck/core/db/audit.py`) is append-only and
hash-chained via a transaction-scoped advisory lock. Every stage transition,
provider call, and evidence write records an entry. A DB trigger rejects
UPDATE/DELETE/TRUNCATE on `audit_log`.

---

## 3. Compose topology

```
┌─────────────── frontend ────────────────┐    ┌────────── egress (bridge) ──────────┐
│                                         │    │                                     │
│  api (FastAPI + Jinja + HTMX)           │    │  egress-proxy (Smokescreen)         │
│    127.0.0.1:8000                       │    │  searxng (optional)                 │
│                                         │    │  worker-{capture, extract,          │
└──────────┬──────────────────────────────┘    │                claimcheck,          │
           │                                   │                provenance,          │
┌──────────┴─── backend (internal) ──┐         │                source,              │
│                                    │         │                corroboration, media │
│  postgres (pgvector)               │         │                                   } │
│  redis                             │         └────────┬────────────────────────────┘
│  seaweedfs (S3)                    │                  │
│  orchestrator                      │                  │
│  worker-scoring                    │    ┌─────── sandbox (internal) ──────────┐
│  worker-{capture, extract, ...}    ├────┤                                     │
│  migrate (one-shot)                │    │  browser (Playwright)               │
│                                    │    │  egress-proxy  <── only link out    │
└────────────────────────────────────┘    └─────────────────────────────────────┘
```

- **`backend` (internal)**: no internet; the app services + data stores sit
  here. No service on `backend` can call out directly.
- **`sandbox` (internal)**: just the browser and (one leg of) the egress
  proxy. The browser can only talk to the proxy; the proxy enforces the
  deny-list a second time on its own DNS resolution.
- **`egress` (bridge)**: trusted provider calls (LLM, Voyage, Google,
  Wayback, SearXNG) go out here. The egress proxy, SearXNG, and workers
  that call providers sit on `egress`.
- **`frontend` (bridge)**: the API's published port lives here.

The dev overlay (`docker-compose.dev.yml`) adds a `fixtures` network with
a fixture site at `172.31.250.0/24`, exempts that subnet from SSRF blocking,
and swaps in in-process fake providers for the LLM / embeddings /
fact-check / search so integration tests have no external deps and no API
keys.

---

## 4. Provider interfaces (the swap points)

Every external dependency is reached only through a `typing.Protocol` in
`src/provcheck/core/interfaces/`. The registry
(`src/provcheck/core/providers/registry.py`) is the one place that names
concrete impls, driven by `*_PROVIDER` env vars.

| Interface | Concrete impls (step) | Env | Notes |
|---|---|---|---|
| `ObjectStore` | `S3ObjectStore` (minio-py, 1), `MemoryObjectStore` (tests) | `OBJECT_STORE` | SeaweedFS by default; `s3://` endpoint via env. |
| `Queue` | `RQJobQueue` (1), `InlineQueue` (tests) | `QUEUE` | Fixed task registry in `services/worker/tasks.py`; dispatchable names only. |
| `BrowserCapture` | `HTTPBrowserCapture` (1) | `BROWSER_URL` | Playwright capture service runs in its own locked-down container. |
| `ArchiveProvider` | `WaybackArchive` (1) + CDX history (3), `NullArchive` | `ARCHIVE_PROVIDER` | `.history` is the Wayback CDX endpoint. |
| `LLMClient` | `AnthropicLLM` (2, default), `OpenAICompatLLM` (2), `FakeLLM` | `LLM_PROVIDER` | Structured output via `complete_json(schema=PydanticModel, cache_system=True)`. Anthropic uses `messages.parse` + `cache_control: ephemeral` on the system block. openai_compat falls back from `response_format=json_schema` to `json_object` + one repair retry. |
| `EmbeddingProvider` | `VoyageEmbed` (2, default), `LocalEmbed` sentence-transformers (2, optional extra), `OpenAICompatEmbed` (2), `FakeEmbed` | `EMBED_PROVIDER` | Split from `LLMClient` because Anthropic ships no embedding endpoint. `dim` must match the pgvector column size chosen in migration 2 (default 1024). |
| `SearchProvider` | `SearXNGSearch` (2), `NullSearch`, `FakeSearch` | `SEARCH_PROVIDER` | JSON format must be enabled in `settings.yml` or SearXNG returns 403. |
| `FactCheckProvider` | `GoogleFactCheck` (2), `NullFactCheck`, `FakeFactCheck` | `FACTCHECK_PROVIDER` | Google Fact Check Tools `claims:search`; textual rating → direction via the conservative lexicon. |
| `DomainInfoProvider` | `RDAPDomainInfo` (3), `NullDomainInfo` | `DOMAININFO_PROVIDER` | IANA bootstrap, follows redirect to the authoritative TLD RDAP server. Some TLDs 404 → `NotConfigured` → investigator records unverified. |
| `ReverseImageProvider` | `LocalPHashReverseImage` (3), `NullReverseImage` | `REVERSE_IMAGE_PROVIDER` | Self-building index over `artifacts.phash`; threshold = Hamming distance. |
| `ExifReader` | `PillowExifReader` (4) | — | Reads the Exif sub-IFD at tag `0x8769` for user-level tags; surfaces GPS presence without exposing coordinates. |
| `C2PAVerifier` | `C2PAPythonVerifier` (4), `NullC2PAVerifier` | — | Reports `valid | invalid | missing | unverified`. Install note: `c2pa-python != 0.38.2` (that version dropped Linux wheels). |
| `ELAProvider` | `PillowELA` (4) | — | Re-saves JPEG at known quality, emits diff PNG + metrics. Scorer weight is 0 (false positives). |
| `Detector` | `NullDetector` (4) | — | No AI-generation detector enabled in v0.1 — current detectors are too unreliable for a band-level signal. Every case records "detector disabled" on the could-not-verify list. |

Three secondary interfaces live inside the security module rather than
`interfaces/`: `SafeFetcher`, `URLPolicy`, and the `trusted_client` factory.

---

## 5. Investigators

Five investigators, one process per queue, all triggered by `fan_out`.
Each writes `evidence_items` (contributes to scoring) and/or
`unverified_items` (goes to the could-not-verify list) and never silently
drops a case into a gap.

| Investigator | Signals it emits | Providers it touches |
|---|---|---|
| **claimcheck** (step 2) | `factcheck.claimreview_false`, `factcheck.claimreview_true`, `factcheck.claimreview_mixed` | `FactCheckProvider` |
| **provenance** (step 3) | `provenance.earliest_appearance` (undermines when an exact-phrase copy predates the article's claimed date), `provenance.archive_earliest` (informational), `provenance.earlier_copy` (local pHash hit on a prior capture) | `SearchProvider`, `ArchiveProvider.history`, `ReverseImageProvider`. Platform-coordination signals → `unsupported_platform`. |
| **source** (step 3) | `source.satire_domain` (neutral + `category_hint=satire`), `source.reputable_outlet` (supports), `source.domain_age_lt_30d` (undermines), `source.domain_age_established` (weak supports) | `DomainInfoProvider`, seed lists under `config/sources/`. Account signals → `unsupported_platform`. |
| **corroboration** (step 3) | `corroboration.independent_outlets` (supports when ≥2 reputable domains cover the same story) | `SearchProvider` news category; `tldextract` groups by registrable domain. |
| **media** (step 4) | `media.c2pa_valid` (supports), `media.c2pa_invalid` (undermines), `media.exif_date_mismatch` (undermines when EXIF date is >365d from the article's claimed date), `media.ela` (neutral, informational, weighted 0) | `ExifReader`, `C2PAVerifier`, `ELAProvider`, `Detector`, `SafeFetcher`. For URL cases, parses captured HTML and downloads up to `MEDIA_MAX_IMAGES_PER_CASE` images via SafeFetcher. For upload cases, operates on the uploaded `media` artifact. |

### Pattern

Every investigator follows the same template:

1. `orchestrator.start_run(case_id, run_id)` — row-locks the run, flips it
   to `running`, writes `investigator.started` to the audit log. Returns
   `None` if someone else already picked it up (idempotent under retries).
2. Load whatever it needs from the DB in a brief session_scope and
   expunge for use outside the transaction.
3. `asyncio.run(_run_async(...))` — call providers, build evidence +
   unverified items in memory (`case_id`/`run_id` placeholders overwritten
   if the item was constructed in a helper).
4. One session_scope persists `evidence_items`, `unverified_items`, any
   investigator-specific artifacts (ELA diffs), and an `*.wrote` audit
   entry.
5. `orchestrator.finish_run(status, providers_used, counts)` — flips the
   run terminal, writes `investigator.finished`, and calls
   `advance_investigation`. When this is the last run, the case moves to
   `scoring` and `pipeline.score` is enqueued.

---

## 6. Scorer

Config is `config/scoring.yaml`. The scorer is deterministic and
auditable: every `scores` row stores `scorer_version` + `config_sha256`
plus the full `contributions[]` list.

### Algorithm (`src/provcheck/core/scoring/scorer.py`)

```
For each evidence item e with signal s (where s is declared in scoring.yaml):
    direction_sign = +1 if supports, -1 if undermines, 0 if neutral
    raw_i   = s.weight * direction_sign * e.strength * e.confidence
    value_i = clamp(raw_i, -s.cap, +s.cap)              # per-signal cap

For each family f (provenance/source/claim/corroboration/media):
    family_total[f] = clamp(sum of value_i for i in f, -f.cap, +f.cap)

logit = bias + sum of family_total[f]
risk  = sigmoid(-logit)   # internal only; NOT exposed in the report
```

### Coverage gate

Before mapping to a band, we check:
- At least `min_families` families (default 2) have non-neutral evidence, AND
- The sum of `|family_total[f]|` is at least `min_abs_sum` (default 0.3)

If either fails, the band is forced to `uncertain` and confidence to `low`.
Absence of evidence is not evidence of fabrication.

### Band mapping (when the gate is cleared)

- `risk ≤ bands.low_risk_upper`  (default 0.30) → `low_risk`
- `risk ≥ bands.high_risk_lower` (default 0.70) → `high_risk`
- else → `uncertain`

### Confidence level

- 3+ families with non-neutral evidence, all agreeing in direction → `high`
- 3+ families with disagreement → `medium`
- 2 families → `medium`
- fewer → `low`

### Unknown signals

If an investigator emits a signal name that isn't declared in
`scoring.yaml`, it's recorded on `score.unknown_signals` and contributed 0.
New signals must be explicitly added to the config — never a silent
non-zero weight.

### Hypothesis-tested invariants

- Score is monotonic in a signal's weight (holding evidence fixed).
- Evidence that is entirely neutral always lands in `uncertain`/`low`.
- Single-family evidence always lands in `uncertain` regardless of
  signal strength.

---

## 7. Report

Built by `src/provcheck/core/report/builder.py`, stored once as an
immutable `report_json` artifact, served with `X-Content-SHA256`.

### Shape (schema v1)

```jsonc
{
  "schema_version": "report-schema-v1",
  "disclaimer": "...assessment, not a verdict...",
  "case": {
    "id": "...",
    "input_type": "url|text|upload",
    "input_url": "...",
    "normalized_url": "...",
    "created_at": "..."
  },
  "assessment": {
    "band": "low_risk|uncertain|high_risk",
    "confidence_level": "low|medium|high",
    "scorer_version": "scoring-v1-...",
    "config_sha256": "..."
  },
  "coverage": { "families_with_evidence": [...], "family_totals": {...}, "abs_sum": ..., "gated": ... },
  "categories": [
    { "category": "fabricated", "total_abs": 1.4, "evidence_ids": [...] },
    ...
  ],
  "contributions": [...],
  "evidence_by_investigator": { "claimcheck": [{...}, ...], ... },
  "unverified_by_investigator": { "media": [{...}, ...], ... },
  "claims": [{"ordinal", "text", "source_span", "claim_type", "checkworthiness"}, ...],
  "captures": [{"requested_url", "final_url", "redirect_chain", "http_status",
                "fetched_at", "title", "archive", "artifacts": [...]}],
  "ela_artifacts": [{"sha256", "size", "href"}]
}
```

### Not in the JSON

- `raw_logit`, `risk` — band-only until calibration.
- Any numeric probability.
- Private artifact bytes — URLs are referenced via `/v1/cases/{id}/artifacts/{id}`
  with the case ID in the path for scoping.

### HTML UI

Server-rendered Jinja templates under `src/provcheck/services/api/templates/`:

- `base.html` — layout, minimal inline CSS, HTMX via unpkg CDN with SRI.
- `index.html` — the three submit forms (URL, text, upload).
- `case.html` — polls `/ui/cases/{id}/fragment` every 2 seconds via
  `hx-trigger="load, every 2s"` until the case is terminal, then renders
  the full fragment.
- `_case_fragment.html` — the terminal view: band chip, category cards,
  evidence trail grouped by investigator with direction chips,
  could-not-verify details, extracted claims with their source spans,
  capture details, ELA diff links.

Routes:

| Path | What it serves |
|---|---|
| `GET /` | Home page with the three submit forms. |
| `GET /cases/{id}` | HTML page; polls while running, renders when terminal. |
| `GET /ui/cases/{id}/fragment` | HTMX fragment: the inner report body. |
| `POST /ui/submit-url`, `POST /ui/submit-text`, `POST /ui/submit-upload` | Form endpoints; 303-redirect to `/cases/{id}`. |
| `GET /v1/cases` (POST) | JSON API submit (URL or text). |
| `GET /v1/cases/upload` (POST) | JSON API upload. |
| `GET /v1/cases/{id}` | JSON case status. |
| `GET /v1/cases/{id}/report` | HTML report (just the fragment). |
| `GET /v1/cases/{id}/report.json` | Immutable JSON report, served from the object store. |
| `GET /v1/cases/{id}/artifacts/{artifact_id}` | Captured HTML / screenshot / text / media / ELA diff. HTML served as `text/plain` attachment; media served as `application/octet-stream`; CSP sandbox + nosniff on everything. |
| `GET /v1/cases/{id}/audit` | Hash-chained audit log plus a chain-validity check. |
| `GET /healthz` | Liveness. |
| `GET /readyz` | Deps (postgres + queue + object store). 503 if any provider is unhealthy. |

---

## 8. Data model

```
cases ────────┬──▶ captures  ──▶ artifacts (html, screenshot, text)
              ├──▶ claims     (pgvector embedding)
              ├──▶ investigator_runs ──┬──▶ evidence_items
              │                        └──▶ unverified_items
              ├──▶ artifacts  (media on upload cases; ela on scored cases; report_json on completed)
              ├──▶ scores     ──▶ reports ──▶ artifact (report_json)
              └──▶ audit_log  (hash-chained; append-only via trigger)
```

Enumerations are stored as VARCHAR + CHECK constraints for portability:

| Column | Values |
|---|---|
| `cases.status` | `queued`, `capturing`, `extracting`, `investigating`, `scoring`, `complete`, `failed` |
| `cases.input_type` | `url`, `text`, `upload` |
| `cases.depth` | `quick`, `full` (reserved for v0.2) |
| `artifacts.kind` | `html`, `screenshot`, `text`, `media`, `ela`, `report_json` |
| `captures.archive_status` | `saved`, `pending`, `failed`, `disabled`, `not_configured`, `opted_out` |
| `investigator_runs.status` | `queued`, `running`, `done`, `failed`, `skipped` |
| `claims.claim_type` | `event`, `statistic`, `quote`, `attribution`, `other` |
| `evidence_items.direction` | `supports`, `undermines`, `neutral` |
| `unverified_items.reason` | `not_configured`, `provider_unavailable`, `no_results`, `blocked_by_policy`, `timeout`, `unsupported_platform`, `error` |
| `scores.band` | `low_risk`, `uncertain`, `high_risk` |
| `scores.confidence_level` | `low`, `medium`, `high` |

Migration order:
- `0001_initial` — cases, artifacts, captures, investigator_runs, audit_log,
  the append-only trigger, and `GRANT`s to the `provcheck_app` role.
- `0002_claims_evidence` — enables pgvector, creates claims (with
  `Vector(EMBED_DIM)`), evidence_items, unverified_items.
- `0003_scores_reports` — scores + reports.

Two DB roles:
- `provcheck_owner` runs migrations (full DDL).
- `provcheck_app` has least-privilege: `SELECT, INSERT, UPDATE, DELETE` on
  the data tables; `SELECT, INSERT` only on `audit_log`; no DDL.

---

## 9. Security model

Full detail in [docs/threat-model.md](docs/threat-model.md). The invariants
in one line each:

- **Fetched content is data, never instructions.** The LLM has no tools and
  the captured text is wrapped in a fenced `<content>` block whose contents
  the system prompt declares are data. Every extracted claim must include a
  verbatim `source_span`, which the pipeline checks against the captured
  text (whitespace-normalised); claims whose span is absent are dropped.
- **Every URL goes through two SSRF layers.** The API runs
  `validate_url(URLPolicy)` on submit. Workers that fetch anything from a
  URL do so through `SafeFetcher`, which re-runs the policy, pins the
  connection to the vetted IP (defeating DNS rebinding), re-validates on
  every redirect, enforces size and content-type caps, and routes via the
  egress proxy — which applies the same deny list a second time on its own
  DNS resolution.
- **The browser lives on an internal-only network** with the egress proxy
  as its only route out. Chromium runs non-root with the Playwright seccomp
  profile, `cap_drop: ALL`, `no-new-privileges`, read-only rootfs, and
  memory/CPU/PID limits.
- **Uploads are sniffed by magic bytes**, size-capped, MIME-allowlisted,
  and never served back with their claimed Content-Type.
- **The audit log is hash-chained and immutable.** A transaction-scoped
  advisory lock serialises appends; `audit.append` wraps the lock
  acquisition in `session.no_autoflush` so the advisory lock is taken
  before any pending INSERT acquires its FK SHARE locks (otherwise the
  five-investigator fan-out can deadlock). A DB trigger rejects
  UPDATE/DELETE/TRUNCATE.
- **Secrets never land in logs.** Every secret is a `pydantic.SecretStr`
  and keys can be loaded from `_FILE` paths.
- **Every provider failure is visible.** Investigators that get
  `NotConfigured` or `ProviderUnavailable` write an `unverified_items` row
  rather than silently emitting no evidence. The report always renders the
  could-not-verify list — even when empty — with an explanation.

---

## 10. Evaluation

`eval/README.md` has the full story. Short version:

- **Tier A** (`eval/cases/<category>/<slug>/label.yaml`): each case is a
  hand-authored evidence pattern + the expected band. `make eval` feeds
  each pattern into `score_case()`, compares to the expected band, prints
  a confusion matrix, and enforces the safety gate: zero `high_risk` on
  any case labeled `corroborated` or `satire`.
- **Tier B** (`eval/live.yaml`): reserved for live URLs with published
  fact-check labels. Deliberately empty in v0.1 — authoring requires
  auditing current ClaimReview records.
- Each run writes `eval/results/<utc-date>.json` with `scorer_version`,
  `config_sha256`, every case's result, and the safety-breach count.

Current: 11/11 cases match expected band; 0 safety breaches.

---

## 11. Build-step summary

Each step is one commit on `build/v0.1`. The memory file `v01-done.md`
pins the Definition of Done.

| Step | Commit | What landed |
|---|---|---|
| 1 | `13c8b86` | API, Postgres (with pgvector-ready image), SeaweedFS S3, Redis + RQ, sandboxed Playwright capture, Smokescreen egress proxy, SSRF guard + SafeFetcher + trusted client, hash-chained audit log, Alembic 0001, Wayback SPN2. |
| 2 | `67364a4` | `LLMClient` + `EmbeddingProvider` + `SearchProvider` + `FactCheckProvider` interfaces; AnthropicLLM, OpenAICompatLLM, VoyageEmbed, LocalEmbed, OpenAICompatEmbed, SearXNG, GoogleFactCheck adapters + Null and Fake providers; migration 0002 (claims + evidence + unverified + pgvector); extract pipeline stage + claim-check investigator + rating lexicon + fan-out orchestrator; API `text` input; `audit.append` lock-ordering fix. |
| 3 | `b84caba` | `DomainInfoProvider` + `ReverseImageProvider` interfaces; RDAPDomainInfo, LocalPHashReverseImage, Wayback CDX, curated seed lists (satire + fact-checkers + reputation with basis cited per entry); provenance + source + corroboration investigators; text helpers (distinctive sentences, link extraction, image URL discovery via selectolax lexbor); registrable-domain extraction via tldextract offline PSL. |
| 4 | `00f331b` | `ExifReader` + `C2PAVerifier` + `ELAProvider` + `Detector` interfaces; PillowExifReader (walks sub-IFD 0x8769), C2PAPythonVerifier (`!=0.38.2`), PillowELA, NullDetector; upload input type with magic-byte sniffing + MIME allowlist + 25 MB cap + re-encoded preview; `POST /v1/cases/upload`; media investigator (URL cases download images via SafeFetcher; upload cases operate on the uploaded artifact). |
| 5 | `e182317` | `config/scoring.yaml` + signals registry + scorer + bands + coverage gate; migration 0003 (scores + reports); scoring pipeline stage + worker-scoring; `core/report/builder.py` producing schema-v1 JSON; Jinja + HTMX templates at `/` + `/cases/{id}`; `GET /v1/cases/{id}/report(.json)`; `make eval` with 11 Tier A fixtures + confusion matrix + safety-gate assertion. |

---

## 12. Out of scope for v0.1

- **Auth, metering, Stripe, browser extension** (step 6). The API binds
  `127.0.0.1` by default and ships no auth, so the operator must decide
  what to expose. HTMX `hx-get` polling also benefits from being gated
  behind auth before any public exposure.
- **Helm / Kustomize + cloud-portability pass** (step 7). All deps are
  behind interfaces + env vars, so this is a repackaging task.
- **Live Tier B evaluation**. Authoring requires auditing current public
  ClaimReview records for URLs that will stay live.
- **Score calibration**. Band thresholds are hand-picked and the scorer
  outputs band + confidence only — no numeric probability leaks via the
  report until calibration against labeled data.
- **An AI-generation detector**. The `Detector` interface exists; the only
  bundled impl is `NullDetector`, which raises `NotConfigured`. Every case
  records "detector disabled" on the could-not-verify list.
- **A stale-case reaper**. A worker killed mid-stage leaves the case in
  that stage. Known gap, tracked in the threat model.
- **Platform-coordination signals** (account age, posting history,
  amplification patterns). All three live investigators that could emit
  them (provenance, source, corroboration) report
  `unsupported_platform` on the could-not-verify list instead.
