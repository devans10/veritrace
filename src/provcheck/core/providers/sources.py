"""Curated seed-list loader.

Reads ``satire.yaml``, ``factcheckers.yaml``, and ``reputation.yaml`` from
``config.sources_dir`` at startup. Lookups are by registrable domain
(tldextract) so subdomains and ports don't matter.

The lists are small and curated; see each YAML file for the citation of each
entry's basis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from provcheck.core.providers.registrable import registrable_domain

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceEntry:
    domain: str
    source: str
    tier: str | None = None  # for reputation list: wire | newspaper | broadcaster


@dataclass
class SourceLists:
    satire: dict[str, SourceEntry] = field(default_factory=dict)
    factcheckers: dict[str, SourceEntry] = field(default_factory=dict)
    reputation: dict[str, SourceEntry] = field(default_factory=dict)

    def is_satire(self, url_or_domain: str) -> SourceEntry | None:
        return self.satire.get(_normalize(url_or_domain))

    def is_factchecker(self, url_or_domain: str) -> SourceEntry | None:
        return self.factcheckers.get(_normalize(url_or_domain))

    def is_reputable(self, url_or_domain: str) -> SourceEntry | None:
        return self.reputation.get(_normalize(url_or_domain))

    @property
    def factchecker_domains(self) -> list[str]:
        return sorted(self.factcheckers)


def _normalize(url_or_domain: str) -> str:
    reg = registrable_domain(url_or_domain)
    return reg or url_or_domain.lower()


def _load_yaml(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        log.warning("source list missing: %s", path)
        return []
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        log.warning("failed to parse %s: %s", path, e)
        return []
    if not isinstance(data, list):
        log.warning("source list %s is not a YAML list", path)
        return []
    return [item for item in data if isinstance(item, dict)]


def _index(items: list[dict[str, Any]]) -> dict[str, SourceEntry]:
    out: dict[str, SourceEntry] = {}
    for raw in items:
        domain = str(raw.get("domain") or "").strip().lower()
        if not domain:
            continue
        out[domain] = SourceEntry(
            domain=domain,
            source=str(raw.get("source") or ""),
            tier=str(raw["tier"]) if raw.get("tier") else None,
        )
    return out


def load_source_lists(sources_dir: str) -> SourceLists:
    root = Path(sources_dir)
    return SourceLists(
        satire=_index(_load_yaml(root / "satire.yaml")),
        factcheckers=_index(_load_yaml(root / "factcheckers.yaml")),
        reputation=_index(_load_yaml(root / "reputation.yaml")),
    )
