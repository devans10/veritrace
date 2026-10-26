# veritrace — Provenance Check

Local-first, container-portable content verification. Given a URL it captures and
archives the page, and (as later build steps land) extracts claims, runs investigators,
and returns a **credibility score band with an evidence trail and a "could not verify"
list. It is an assessment, never a verdict.** See [PROJECT.md](PROJECT.md) for the spec.

## Status

| Build step | State |
|---|---|
| 1. API + Postgres + capture/archive with hashing | done |
| 2. Claim extraction + claim-check investigator | next |
| 3. Provenance, source, corroboration investigators | planned |
| 4. Media forensics | planned |
| 5. Scorer, evaluation set, report UI | planned |

## Quick start

```sh
make env        # writes .env with random secrets (gitignored); add IA keys if you have them
make up         # docker compose up -d --build --wait
curl -s -XPOST localhost:8000/v1/cases -H 'content-type: application/json' \
     -d '{"url": "https://example.com/", "archive": false}'
curl -s localhost:8000/v1/cases/<id>
curl -s localhost:8000/v1/cases/<id>/audit
```

`"archive": true` (the default) submits the URL to the Internet Archive's Save Page Now,
which makes the URL **publicly visible**. It needs `IA_S3_ACCESS_KEY`/`IA_S3_SECRET_KEY`;
without them the capture records `archive.status = not_configured`.

## Development

```sh
uv sync                 # Python 3.12 toolchain
make check              # ruff + mypy --strict + unit/contract tests
make up-dev             # stack + fixture site + localhost debug ports (DEV ONLY)
make test-integration   # runs against the dev stack
```

## Layout

```
src/provcheck/core/interfaces   provider protocols (ObjectStore, Queue, BrowserCapture, ArchiveProvider, ...)
src/provcheck/core/providers    implementations + registry (env -> impl)
src/provcheck/core/security     SSRF guard / SafeFetcher / trusted provider client
src/provcheck/core/pipeline     state machine + stages
src/provcheck/core/db           SQLAlchemy models, hash-chained audit log
src/provcheck/services          api (FastAPI) and worker (RQ)
services/browser                sandboxed Playwright capture service
docker/                         Dockerfiles, postgres init, seaweedfs entrypoint, seccomp
migrations/                     Alembic
docs/                           architecture, threat model, external API verification log
```
