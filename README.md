# veritrace — Provenance Check

Local-first, container-portable content verification. Given a URL, pasted text,
or an uploaded image, veritrace captures and archives the page (for URLs),
extracts check-worthy claims, runs five independent investigators in parallel,
and produces a **credibility assessment** with a band (low_risk / uncertain /
high_risk), a confidence level, a category breakdown, a full evidence trail,
and an always-present "could not verify" list. It is an **assessment with
evidence, never a verdict**. See [PROJECT.md](PROJECT.md) for the original
spec, and [ARCHITECTURE.md](ARCHITECTURE.md) for how it's put together.

## Status

| Build step | State | Commit |
|---|---|---|
| 1. API + Postgres + capture/archive with hashing | done | `13c8b86` |
| 2. Claim extraction + claim-check investigator | done | `67364a4` |
| 3. Provenance, source, corroboration investigators | done | `b84caba` |
| 4. Media forensics + upload input type | done | `00f331b` |
| 5. Scorer + report + Jinja/HTMX UI + eval harness | done | `e182317` |
| 6. Auth + metering + Stripe + browser extension | out of scope for v0.1 | — |
| 7. Helm / Kustomize + cloud-portability pass | out of scope for v0.1 | — |

**v0.1 Definition of Done is met.** `docker compose up` brings up the full
stack; submitting a URL, text, or image produces a report with band,
confidence, evidence trail, and the could-not-verify list. 218 unit tests +
36 integration tests pass. Tier A eval runs 11 hand-authored cases through
the scorer with 0 safety-gate breaches.

## Quick start

```sh
make env                 # generate .env with random secrets (gitignored)
make up                  # production-ish compose; API on 127.0.0.1:8000
# or
make up-dev              # dev stack with fixture site + fake providers; needed for integration tests
```

Open <http://127.0.0.1:8000/> in a browser for the submit/report UI, or
drive the API directly:

```sh
curl -s -XPOST localhost:8000/v1/cases \
     -H 'content-type: application/json' \
     -d '{"url": "https://example.com/", "archive": false}'
# → 202 Accepted + {"id": "...", "status": "queued", ...}

curl -s localhost:8000/v1/cases/<id>                # status + captures
curl -s localhost:8000/v1/cases/<id>/report.json    # immutable report JSON (when complete)
curl -s localhost:8000/v1/cases/<id>/audit          # hash-chained audit log
```

Pasted-text submissions:
```sh
curl -s -XPOST localhost:8000/v1/cases \
     -H 'content-type: application/json' \
     -d '{"text": "Officials said..."}'
```

Image uploads:
```sh
curl -s -XPOST localhost:8000/v1/cases/upload \
     -F file=@photo.jpg
```

### Keys

- `IA_S3_ACCESS_KEY` / `IA_S3_SECRET_KEY` enable Wayback Save Page Now 2.
  Submitting with `"archive": true` publishes the URL to the Internet Archive;
  without keys the capture records `archive.status = not_configured`.
- `ANTHROPIC_API_KEY` is the default LLM for claim extraction. Alternatives:
  `LLM_PROVIDER=openai_compat` with `LLM_BASE_URL` + `LLM_MODEL` points at
  any OpenAI-compatible endpoint (vLLM, LM Studio, LiteLLM).
- `VOYAGE_API_KEY` is the default embedding provider. `EMBED_PROVIDER=local`
  uses `sentence-transformers` instead (install the `local-embed` extra).
- `GOOGLE_API_KEY` enables Google Fact Check Tools (`FACTCHECK_PROVIDER=google`).

See `.env.example` for the full matrix.

## Development

```sh
uv sync --all-extras     # Python 3.12 toolchain, all optional deps
make check               # ruff + mypy --strict + unit/contract tests (218 tests, no stack)
make up-dev              # dev stack with fixture site + fakes (needed for integration tests)
make test-integration    # runs 36 integration tests against the dev stack
make eval                # Tier A offline eval; prints confusion matrix, writes eval/results/<date>.json
make down                # tear down
```

## Layout

```
src/provcheck/
  config.py                    pydantic-settings (env + *_FILE); every secret is a SecretStr
  core/
    interfaces/                protocol definitions (one swap point per external dep)
    providers/                 concrete impls (Anthropic, Voyage, SearXNG, RDAP, Wayback, ...)
    security/                  SSRF guard + SafeFetcher + trusted provider client + upload validation
    pipeline/                  state machine, stages (capture / extract / fan_out / scoring_stage)
    investigators/             claimcheck / provenance / source / corroboration / media + rating lexicon
    scoring/                   signals registry, scorer, bands, coverage gate
    report/                    JSON report builder
    db/                        SQLAlchemy models + hash-chained audit
  services/
    api/                       FastAPI + Jinja templates (/, /cases/{id}, /v1/...)
    worker/                    RQ workers (one queue per stage/investigator)
services/browser/              sandboxed Playwright capture service (separate image)
config/
  scoring.yaml                 per-signal weights, families, caps, bands, coverage gate
  sources/                     satire / fact-checker / reputation seed lists (basis cited per entry)
  searxng/                     SearXNG settings
docker/                        Dockerfile.app, Dockerfile.browser, Dockerfile.egress, seccomp profile
migrations/                    Alembic (0001 cases/artifacts, 0002 claims+pgvector, 0003 scores+reports)
eval/                          Tier A fixtures + run_eval.py + README (safety-gate assertion)
docs/                          threat-model, external API verification log
tests/                         unit (218) + contract + integration (36) + fixtures
```

## Reports

Each case produces a JSON report (schema v1) and an HTML view sharing one
template fragment. The JSON is stored once as a `report_json` object in
SeaweedFS; `/v1/cases/{id}/report.json` streams that artifact and includes
an `X-Content-SHA256` header. Fields:
`disclaimer`, `case`, `assessment { band, confidence_level, scorer_version,
config_sha256 }`, `coverage`, `categories[]`, `contributions[]`,
`evidence_by_investigator`, `unverified_by_investigator`, `claims[]`,
`captures[]`, `ela_artifacts[]`.

`raw_logit` and the raw sigmoid risk are deliberately **not** in the report —
v0.1 is band-only until calibration against labeled data.

## Security

See [docs/threat-model.md](docs/threat-model.md). High-level: fetched content
is treated as untrusted data, never instructions; URLs go through a two-stage
SSRF guard (DNS resolution + IP policy + egress proxy); the audit log is
hash-chained with a DB trigger rejecting UPDATE/DELETE; uploaded media is
sniffed by magic bytes and never served back with its claimed Content-Type.
