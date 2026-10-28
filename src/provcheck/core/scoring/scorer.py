"""Transparent, config-driven scorer.

See :mod:`provcheck.core.scoring.signals` for the config schema and
``config/scoring.yaml`` for the current weights. The algorithm is deterministic
and auditable: every ``Score`` row stores the exact set of contributions and
the sha256 of the config that produced it.
"""

from __future__ import annotations

import logging
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from provcheck.core.db.models import EvidenceItem
from provcheck.core.scoring.signals import ScoringConfig, SignalConfig

log = logging.getLogger(__name__)

Band = Literal["low_risk", "uncertain", "high_risk"]
ConfidenceLevel = Literal["low", "medium", "high"]
Direction = Literal["supports", "undermines", "neutral"]

_DIR_SIGN: dict[str, int] = {"supports": +1, "undermines": -1, "neutral": 0}


@dataclass(frozen=True)
class Contribution:
    evidence_id: uuid.UUID
    signal: str
    family: str
    category: str | None
    direction: Direction
    raw: float  # weight * sign * strength * confidence (uncapped)
    value: float  # after per-signal cap; this is what sums into the family total
    weight: float
    cap: float


@dataclass(frozen=True)
class CategoryBreakdown:
    category: str
    total_abs: float
    evidence_ids: list[uuid.UUID]


@dataclass
class ScoreResult:
    scorer_version: str
    config_sha256: str
    band: Band
    confidence_level: ConfidenceLevel
    raw_logit: float
    risk: float
    coverage: dict[str, object] = field(default_factory=dict)
    category_breakdown: list[CategoryBreakdown] = field(default_factory=list)
    contributions: list[Contribution] = field(default_factory=list)
    unknown_signals: list[str] = field(default_factory=list)


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _contribution(ev: EvidenceItem, sig: SignalConfig) -> Contribution:
    sign = _DIR_SIGN.get(ev.direction, 0)
    raw = sig.weight * sign * float(ev.strength) * float(ev.confidence)
    value = _clamp(raw, -sig.cap, +sig.cap) if sig.cap > 0 else 0.0
    direction: Direction = ev.direction  # type: ignore[assignment]
    return Contribution(
        evidence_id=ev.id,
        signal=ev.signal,
        family=sig.family,
        category=sig.category,
        direction=direction,
        raw=raw,
        value=value,
        weight=sig.weight,
        cap=sig.cap,
    )


def _confidence_level(
    families_with_evidence: set[str],
    agreement: dict[str, int],  # family -> net sign (+1 supports, -1 undermines)
) -> ConfidenceLevel:
    n_families = len(families_with_evidence)
    if n_families >= 3:
        signs = {s for s in agreement.values() if s != 0}
        if len(signs) <= 1:
            return "high"
        return "medium"
    if n_families == 2:
        return "medium"
    return "low"


def score_case(evidence: Sequence[EvidenceItem], config: ScoringConfig) -> ScoreResult:
    """Compute band + confidence + full breakdown for a case's evidence."""
    contributions: list[Contribution] = []
    unknown: list[str] = []
    for ev in evidence:
        sig = config.get_signal(ev.signal)
        if sig is None:
            unknown.append(ev.signal)
            continue
        contributions.append(_contribution(ev, sig))

    # Group by family, cap per-family total, sum.
    family_totals: dict[str, float] = {}
    family_abs: dict[str, float] = {}
    family_agreement: dict[str, int] = {}
    non_neutral_contribs = [c for c in contributions if c.direction != "neutral"]
    for c in contributions:
        family_totals[c.family] = family_totals.get(c.family, 0.0) + c.value
        family_abs[c.family] = family_abs.get(c.family, 0.0) + abs(c.value)
    for family in family_totals:
        fcfg = config.get_family(family)
        if fcfg is not None and fcfg.cap > 0:
            family_totals[family] = _clamp(family_totals[family], -fcfg.cap, +fcfg.cap)
    # Agreement: net direction per family (only non-neutral count).
    for c in non_neutral_contribs:
        family_agreement[c.family] = family_agreement.get(c.family, 0) + _DIR_SIGN.get(
            c.direction, 0
        )

    logit = config.bias + sum(family_totals.values())
    risk = _sigmoid(-logit)
    abs_sum = sum(abs(v) for v in family_totals.values())
    families_with_evidence = {c.family for c in non_neutral_contribs}

    # Coverage gate.
    gate = config.coverage_gate
    gated = len(families_with_evidence) < gate.min_families or abs_sum < gate.min_abs_sum
    if gated:
        band: Band = "uncertain"
        confidence: ConfidenceLevel = "low"
    else:
        if risk <= config.bands.low_risk_upper:
            band = "low_risk"
        elif risk >= config.bands.high_risk_lower:
            band = "high_risk"
        else:
            band = "uncertain"
        confidence = _confidence_level(families_with_evidence, family_agreement)

    # Per-category breakdown (|c_i| across contributions).
    cat_totals: dict[str, float] = {}
    cat_ids: dict[str, list[uuid.UUID]] = {}
    for c in contributions:
        if c.category is None:
            continue
        cat_totals[c.category] = cat_totals.get(c.category, 0.0) + abs(c.value)
        cat_ids.setdefault(c.category, []).append(c.evidence_id)
    breakdown = [
        CategoryBreakdown(
            category=cat,
            total_abs=cat_totals[cat],
            evidence_ids=cat_ids.get(cat, []),
        )
        for cat in sorted(cat_totals, key=lambda k: -cat_totals[k])
    ]

    return ScoreResult(
        scorer_version=config.scorer_version,
        config_sha256=config.config_sha256,
        band=band,
        confidence_level=confidence,
        raw_logit=logit,
        risk=risk,
        coverage={
            "families_with_evidence": sorted(families_with_evidence),
            "family_totals": family_totals,
            "family_abs": family_abs,
            "abs_sum": abs_sum,
            "gated": gated,
        },
        category_breakdown=breakdown,
        contributions=contributions,
        unknown_signals=unknown,
    )
