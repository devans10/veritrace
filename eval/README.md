# Evaluation set

**Tier A — offline, deterministic.** Each case under `cases/<category>/<slug>/`
is a `label.yaml` describing a hand-authored evidence pattern plus the expected
band. The runner feeds that evidence directly into the scorer and compares the
result to the label, so the whole suite executes in milliseconds without the
full stack.

The suite tests the **scorer and config**, not the investigators. The real
pipeline is covered by the integration test. The two together give us
confidence in both the signal pipeline and the band-mapping policy.

## Layout

```
eval/
  README.md
  run_eval.py          — runner, outputs eval/results/<utc-date>.json
  cases/
    corroborated/<slug>/label.yaml
    fabricated/<slug>/label.yaml
    misleading_context/<slug>/label.yaml
    satire/<slug>/label.yaml
    unverified/<slug>/label.yaml
  live.yaml            — Tier B URLs for ad-hoc runs (not CI)
```

## Running

```sh
make eval            # or:
uv run python eval/run_eval.py
```

The runner exits non-zero on a safety-gate failure (any high_risk on
corroborated / satire cases). Band confusion is printed per-category.

`eval/results/` is **gitignored** — the files it writes are per-run outputs,
not fixtures. CI uploads them as artifacts; locally they are just left on
disk for post-mortems.

## Adding cases

Each `label.yaml` has this shape:

```yaml
category: fabricated          # ground-truth category
expected_band: high_risk      # low_risk | uncertain | high_risk
rationale: "a short note explaining the labeler's reasoning"
evidence:                     # list of EvidenceItem rows the investigators WOULD emit
  - investigator: source
    signal: source.domain_age_lt_30d
    direction: undermines
    strength: 0.9
    confidence: 0.8
    finding: "domain registered 12 days ago"
    category_hint: fabricated
  - investigator: claimcheck
    signal: factcheck.claimreview_false
    direction: undermines
    strength: 0.9
    confidence: 0.8
    finding: "AP Fact Check rated this False"
```

The runner enforces that every `signal` name is declared in
`config/scoring.yaml`, so authors can't accidentally invent new signals.
