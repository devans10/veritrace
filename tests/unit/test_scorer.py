"""Scorer: golden contribution math, caps, coverage gate, band thresholds.

Includes Hypothesis properties for the invariants in the plan:
- Scores are monotonic in weight (holding everything else fixed).
- Evidence that is entirely neutral always lands in `uncertain`/`low`.
- Low-coverage cases always land in `uncertain`/`low` regardless of signal
  strength.
"""

from __future__ import annotations

import uuid
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from provcheck.core.db.models import EvidenceItem
from provcheck.core.scoring import (
    BandConfig,
    CoverageGateConfig,
    FamilyConfig,
    ScoringConfig,
    SignalConfig,
    score_case,
)


def _signal(
    name: str, weight: float, family: str, *, category: str | None = None, cap: float = 10.0
) -> SignalConfig:
    return SignalConfig(name=name, weight=weight, family=family, category=category, cap=cap)


def _config(signals: dict[str, SignalConfig], **kw: Any) -> ScoringConfig:
    return ScoringConfig(
        scorer_version="test-v1",
        bias=float(kw.get("bias", 0.0)),
        bands=BandConfig(
            low_risk_upper=float(kw.get("low_risk_upper", 0.3)),
            high_risk_lower=float(kw.get("high_risk_lower", 0.7)),
        ),
        coverage_gate=CoverageGateConfig(
            min_families=int(kw.get("min_families", 2)),
            min_abs_sum=float(kw.get("min_abs_sum", 0.3)),
        ),
        families={f: FamilyConfig(name=f, cap=10.0) for f in {s.family for s in signals.values()}},
        signals=signals,
        config_sha256="deadbeef",
    )


def _ev(
    signal: str,
    direction: str,
    *,
    strength: float = 1.0,
    confidence: float = 1.0,
    case_id: uuid.UUID | None = None,
) -> EvidenceItem:
    case_id = case_id or uuid.uuid4()
    return EvidenceItem(
        id=uuid.uuid4(),
        case_id=case_id,
        investigator="t",
        signal=signal,
        finding="",
        direction=direction,
        strength=strength,
        confidence=confidence,
    )


def test_contribution_math_supports_sign():
    cfg = _config({"s": _signal("s", 1.0, "fA"), "t": _signal("t", 1.0, "fB")})
    ev = [_ev("s", "supports"), _ev("t", "supports")]
    r = score_case(ev, cfg)
    # Each supports contributes +1 → logit=+2 → risk ≈ sigmoid(-2) ≈ 0.12 → low_risk.
    assert r.band == "low_risk"
    assert r.raw_logit > 0.0


def test_contribution_math_undermines_sign():
    cfg = _config({"s": _signal("s", 1.0, "fA"), "t": _signal("t", 1.0, "fB")})
    ev = [_ev("s", "undermines"), _ev("t", "undermines")]
    r = score_case(ev, cfg)
    # logit = -2 → risk = sigmoid(2) ≈ 0.88 → high_risk.
    assert r.band == "high_risk"
    assert r.raw_logit < 0.0


def test_neutral_contributes_zero():
    cfg = _config({"s": _signal("s", 1.0, "fA")})
    r = score_case([_ev("s", "neutral")], cfg)
    assert r.raw_logit == 0.0


def test_per_signal_cap_applied():
    cfg = _config({"s": _signal("s", 100.0, "fA", cap=0.5)}, min_families=1, min_abs_sum=0.0)
    r = score_case([_ev("s", "undermines", strength=1, confidence=1)], cfg)
    # Weight is huge but cap pulls contribution to |0.5|.
    assert abs(r.raw_logit) == 0.5


def test_per_family_cap_applied():
    cfg = ScoringConfig(
        scorer_version="t",
        bias=0.0,
        bands=BandConfig(0.3, 0.7),
        coverage_gate=CoverageGateConfig(min_families=1, min_abs_sum=0.0),
        families={"fA": FamilyConfig(name="fA", cap=1.0)},  # family cap = 1.0
        signals={
            "a": _signal("a", 1.0, "fA", cap=10.0),
            "b": _signal("b", 1.0, "fA", cap=10.0),
            "c": _signal("c", 1.0, "fA", cap=10.0),
        },
        config_sha256="x",
    )
    r = score_case([_ev("a", "undermines"), _ev("b", "undermines"), _ev("c", "undermines")], cfg)
    # Three raw -1.0 contributions clamped to a family total of -1.0.
    assert r.raw_logit == -1.0


def test_coverage_gate_forces_uncertain_with_single_family():
    cfg = _config({"s": _signal("s", 2.0, "fA")}, min_families=2)
    r = score_case([_ev("s", "undermines", strength=1, confidence=1)], cfg)
    assert r.band == "uncertain"
    assert r.confidence_level == "low"


def test_coverage_gate_forces_uncertain_with_low_abs_sum():
    cfg = _config(
        {"s": _signal("s", 1.0, "fA"), "t": _signal("t", 1.0, "fB")},
        min_abs_sum=5.0,  # unreachable
    )
    r = score_case([_ev("s", "undermines"), _ev("t", "undermines")], cfg)
    assert r.band == "uncertain"
    assert r.confidence_level == "low"


def test_unknown_signals_are_collected_not_scored():
    cfg = _config({"s": _signal("s", 1.0, "fA")})
    r = score_case([_ev("s", "undermines"), _ev("mystery.signal", "undermines")], cfg)
    assert "mystery.signal" in r.unknown_signals
    assert len(r.contributions) == 1


def test_category_breakdown_only_includes_contributions_with_categories():
    cfg = _config(
        {
            "s": _signal("s", 1.0, "fA", category="fabricated"),
            "t": _signal("t", 1.0, "fB", category=None),  # no category
        },
        min_families=1,
        min_abs_sum=0.0,
    )
    r = score_case([_ev("s", "undermines"), _ev("t", "undermines")], cfg)
    cats = {b.category for b in r.category_breakdown}
    assert cats == {"fabricated"}


def test_confidence_high_when_3plus_agreeing_families():
    cfg = _config(
        {
            "s": _signal("s", 1.0, "fA"),
            "t": _signal("t", 1.0, "fB"),
            "u": _signal("u", 1.0, "fC"),
        },
        min_families=2,
    )
    r = score_case([_ev("s", "undermines"), _ev("t", "undermines"), _ev("u", "undermines")], cfg)
    assert r.confidence_level == "high"


def test_confidence_medium_when_families_disagree():
    cfg = _config(
        {
            "s": _signal("s", 1.0, "fA"),
            "t": _signal("t", 1.0, "fB"),
            "u": _signal("u", 1.0, "fC"),
        },
        min_families=2,
    )
    r = score_case([_ev("s", "undermines"), _ev("t", "supports"), _ev("u", "undermines")], cfg)
    assert r.confidence_level == "medium"


# ----------- Property tests ------------------------------------------------


_strength = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
_confidence = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
_weight = st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False)


@given(w1=_weight, w2=_weight, s=_strength, c=_confidence)
@settings(max_examples=50)
def test_logit_monotonic_in_weight_for_undermines(w1, w2, s, c):
    """Increasing a signal's weight (holding evidence fixed) can only move the
    logit in the direction opposite to the evidence sign."""
    w_lo, w_hi = sorted([w1, w2])
    ev = [
        _ev("s", "undermines", strength=s, confidence=c),
        _ev("t", "undermines", strength=s, confidence=c),
    ]
    cfg_lo = _config(
        {"s": _signal("s", w_lo, "fA"), "t": _signal("t", 1.0, "fB")},
        min_families=1,
        min_abs_sum=0.0,
    )
    cfg_hi = _config(
        {"s": _signal("s", w_hi, "fA"), "t": _signal("t", 1.0, "fB")},
        min_families=1,
        min_abs_sum=0.0,
    )
    r_lo = score_case(ev, cfg_lo)
    r_hi = score_case(ev, cfg_hi)
    # undermines moves logit negative; higher weight → more negative (or equal).
    assert r_hi.raw_logit <= r_lo.raw_logit + 1e-9


@given(count=st.integers(min_value=0, max_value=20))
@settings(max_examples=30)
def test_neutral_only_lands_in_uncertain_low(count):
    cfg = _config({"s": _signal("s", 1.0, "fA"), "t": _signal("t", 1.0, "fB")}, min_families=2)
    ev = [_ev("s", "neutral") for _ in range(count)]
    r = score_case(ev, cfg)
    assert r.band == "uncertain"
    assert r.confidence_level == "low"


@given(s=_strength, c=_confidence)
@settings(max_examples=30)
def test_single_family_evidence_always_uncertain(s, c):
    cfg = _config({"s": _signal("s", 2.0, "fA")}, min_families=2)
    ev = [_ev("s", "undermines", strength=s, confidence=c)]
    r = score_case(ev, cfg)
    assert r.band == "uncertain"


def test_shipped_scoring_yaml_loads():
    """The config in config/scoring.yaml must load cleanly and declare every
    signal name an investigator emits in the current codebase."""
    from provcheck.core.scoring import load_scoring_config

    cfg = load_scoring_config("config/scoring.yaml")
    assert cfg.scorer_version.startswith("scoring-v1-")
    # Every signal our investigators actually produce must be declared.
    expected = {
        "factcheck.claimreview_false",
        "factcheck.claimreview_true",
        "factcheck.claimreview_mixed",
        "provenance.earliest_appearance",
        "provenance.archive_earliest",
        "provenance.earlier_copy",
        "source.satire_domain",
        "source.reputable_outlet",
        "source.domain_age_lt_30d",
        "source.domain_age_established",
        "corroboration.independent_outlets",
        "media.c2pa_valid",
        "media.c2pa_invalid",
        "media.exif_date_mismatch",
        "media.ela",
    }
    assert expected.issubset(set(cfg.signals.keys())), expected - set(cfg.signals.keys())
    # Every signal must reference a known family.
    for s in cfg.signals.values():
        assert s.family in cfg.families, f"signal {s.name} has unknown family {s.family!r}"
