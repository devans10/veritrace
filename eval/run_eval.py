"""Tier A evaluation runner.

For each ``eval/cases/<category>/<slug>/label.yaml``:
  1. Hydrate the evidence list into in-memory ``EvidenceItem`` rows.
  2. Call the scorer with the shipped ``config/scoring.yaml``.
  3. Compare the resulting band to the label's ``expected_band``.
  4. Enforce the safety gate: no case labeled ``corroborated`` or ``satire``
     may score ``high_risk`` — the scorer must never slander either kind.

Writes a timestamped JSON summary to ``eval/results/`` and prints a
confusion matrix. Exits non-zero on safety-gate failures.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from provcheck.core.db.models import EvidenceItem
from provcheck.core.scoring import ScoringConfig, load_scoring_config, score_case

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = REPO_ROOT / "eval" / "cases"
RESULTS_DIR = REPO_ROOT / "eval" / "results"
SCORING_CONFIG = REPO_ROOT / "config" / "scoring.yaml"

SAFETY_GATE_CATEGORIES = frozenset({"corroborated", "satire"})


def _ev_from_dict(case_id: uuid.UUID, raw: dict[str, Any]) -> EvidenceItem:
    return EvidenceItem(
        id=uuid.uuid4(),
        case_id=case_id,
        investigator=str(raw.get("investigator") or "unknown"),
        signal=str(raw.get("signal") or ""),
        finding=str(raw.get("finding") or ""),
        direction=str(raw.get("direction") or "neutral"),
        strength=float(raw.get("strength") or 0.0),
        confidence=float(raw.get("confidence") or 0.0),
        category_hint=raw.get("category_hint"),
        source_url=raw.get("source_url"),
        details=raw.get("details") or {},
    )


def _load_case(label_path: Path) -> dict[str, Any]:
    data = yaml.safe_load(label_path.read_text()) or {}
    data["path"] = str(label_path.relative_to(REPO_ROOT))
    data["category"] = str(data.get("category") or label_path.parent.parent.name)
    data["slug"] = label_path.parent.name
    return data


def _run_case(data: dict[str, Any], config: ScoringConfig) -> dict[str, Any]:
    case_id = uuid.uuid4()
    ev_rows = [_ev_from_dict(case_id, raw) for raw in (data.get("evidence") or [])]
    # Guard: every signal must be known to the config.
    unknown = [e.signal for e in ev_rows if config.get_signal(e.signal) is None]
    if unknown:
        return {
            "path": data["path"],
            "category": data["category"],
            "slug": data["slug"],
            "ok": False,
            "error": f"undeclared signals in label: {sorted(set(unknown))}",
        }
    result = score_case(ev_rows, config)
    expected = str(data.get("expected_band") or "uncertain")
    return {
        "path": data["path"],
        "category": data["category"],
        "slug": data["slug"],
        "expected_band": expected,
        "actual_band": result.band,
        "confidence_level": result.confidence_level,
        "raw_logit": result.raw_logit,
        "match": result.band == expected,
        "ok": True,
    }


def main() -> int:
    if not SCORING_CONFIG.exists():
        print(f"missing scoring config: {SCORING_CONFIG}", file=sys.stderr)
        return 2
    config = load_scoring_config(SCORING_CONFIG)
    label_paths = sorted(CASES_DIR.glob("*/*/label.yaml"))
    if not label_paths:
        print(f"no eval cases under {CASES_DIR}", file=sys.stderr)
        return 2

    results = [_run_case(_load_case(p), config) for p in label_paths]
    matches = sum(1 for r in results if r.get("match"))
    safety_breaches = [
        r for r in results if r.get("category") in SAFETY_GATE_CATEGORIES
        and r.get("actual_band") == "high_risk"
    ]
    confusion: Counter[tuple[str, str]] = Counter()
    for r in results:
        if r.get("ok"):
            confusion[(r["category"], r["actual_band"])] += 1

    print(f"\neval cases: {len(results)}; matches: {matches}; "
          f"safety breaches: {len(safety_breaches)}")
    print("\nConfusion (category -> actual_band):")
    cats = sorted({r.get("category") for r in results if r.get("ok")})
    bands = ("low_risk", "uncertain", "high_risk")
    header = f"  {'category':<22} " + "  ".join(f"{b:>10}" for b in bands)
    print(header)
    for cat in cats:
        row = "  ".join(f"{confusion[(cat, b)]:>10}" for b in bands)
        print(f"  {cat:<22} {row}")

    if safety_breaches:
        print("\nSAFETY GATE FAILED — the following cases must NEVER be high_risk:")
        for r in safety_breaches:
            print(f"  {r['category']}/{r['slug']}: {r['path']}")

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"{dt.datetime.now(dt.UTC).date().isoformat()}.json"
    out_path.write_text(
        json.dumps(
            {
                "scorer_version": config.scorer_version,
                "config_sha256": config.config_sha256,
                "cases": results,
                "safety_breaches": len(safety_breaches),
                "matches": matches,
                "total": len(results),
            },
            indent=2,
        )
    )
    print(f"\nwrote {out_path.relative_to(REPO_ROOT)}")
    return 1 if safety_breaches else 0


if __name__ == "__main__":
    raise SystemExit(main())
