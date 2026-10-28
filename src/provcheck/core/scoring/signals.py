"""Signal + scoring config schema and loader.

The YAML at ``settings.scoring_config_path`` drives every weight / family / cap.
Signals that an investigator emits but that are not declared here log a
warning and contribute 0 — never a surprise non-zero weight from training-data
memory.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SignalConfig:
    name: str
    weight: float
    family: str
    category: str | None
    cap: float


@dataclass(frozen=True)
class FamilyConfig:
    name: str
    cap: float


@dataclass(frozen=True)
class BandConfig:
    low_risk_upper: float
    high_risk_lower: float


@dataclass(frozen=True)
class CoverageGateConfig:
    min_families: int
    min_abs_sum: float


@dataclass(frozen=True)
class ScoringConfig:
    scorer_version: str
    bias: float
    bands: BandConfig
    coverage_gate: CoverageGateConfig
    families: dict[str, FamilyConfig]
    signals: dict[str, SignalConfig]
    config_sha256: str
    raw_text: str = field(repr=False, default="")

    def get_signal(self, name: str) -> SignalConfig | None:
        return self.signals.get(name)

    def get_family(self, name: str) -> FamilyConfig | None:
        return self.families.get(name)


def _as_float(v: object, *, default: float = 0.0) -> float:
    if v is None:
        return default
    return float(v)  # type: ignore[arg-type]


def load_scoring_config(path: str | Path) -> ScoringConfig:
    p = Path(path)
    raw = p.read_text()
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError(f"scoring config at {p} must be a YAML mapping")

    bands_raw = data.get("bands") or {}
    bands = BandConfig(
        low_risk_upper=_as_float(bands_raw.get("low_risk_upper"), default=0.3),
        high_risk_lower=_as_float(bands_raw.get("high_risk_lower"), default=0.7),
    )
    cov_raw = data.get("coverage_gate") or {}
    coverage = CoverageGateConfig(
        min_families=int(cov_raw.get("min_families") or 2),
        min_abs_sum=_as_float(cov_raw.get("min_abs_sum"), default=0.3),
    )

    families_raw = data.get("families") or {}
    families: dict[str, FamilyConfig] = {}
    for name, entry in families_raw.items():
        if not isinstance(entry, dict):
            continue
        families[name] = FamilyConfig(name=name, cap=_as_float(entry.get("cap"), default=2.0))

    signals_raw = data.get("signals") or {}
    signals: dict[str, SignalConfig] = {}
    for name, entry in signals_raw.items():
        if not isinstance(entry, dict):
            continue
        family = str(entry.get("family") or "")
        if family and family not in families:
            log.warning("scoring: signal %r references unknown family %r", name, family)
        signals[name] = SignalConfig(
            name=name,
            weight=_as_float(entry.get("weight"), default=0.0),
            family=family,
            category=(str(entry["category"]) if entry.get("category") else None),
            cap=_as_float(entry.get("cap"), default=0.0),
        )

    return ScoringConfig(
        scorer_version=str(data.get("scorer_version") or "scoring-v0"),
        bias=_as_float(data.get("bias"), default=0.0),
        bands=bands,
        coverage_gate=coverage,
        families=families,
        signals=signals,
        config_sha256=sha,
        raw_text=raw,
    )


@lru_cache(maxsize=1)
def default_scoring_config() -> ScoringConfig:
    from provcheck.config import get_settings

    return load_scoring_config(get_settings().scoring_config_path)
