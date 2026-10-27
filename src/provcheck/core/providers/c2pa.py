"""C2PA (Coalition for Content Provenance and Authenticity) verifier.

Uses the ``c2pa`` Python SDK. v0.1 verifies the manifest and reports:
  * ``valid``       — manifest validation succeeded (``validation_state="Valid"``).
  * ``invalid``     — manifest present but failed validation.
  * ``missing``     — no manifest found in the asset.
  * ``unverified``  — the library returned a result we don't understand.

Manifests themselves are passed through under ``manifests`` as a list of
dicts so the report can show provenance claims.
"""

from __future__ import annotations

import logging
from typing import Any

from provcheck.core.interfaces import (
    C2PAReading,
    NotConfigured,
    ProviderError,
    ProviderHealth,
)

log = logging.getLogger(__name__)


class C2PAPythonVerifier:
    name = "c2pa_python"

    def __init__(self) -> None:
        try:
            import c2pa
        except ImportError as e:
            raise NotConfigured("c2pa-python not installed") from e
        self._c2pa = c2pa

    async def verify(self, data: bytes, *, mime: str | None = None) -> C2PAReading:
        import asyncio

        return await asyncio.to_thread(self._verify_sync, data, mime)

    def _verify_sync(self, data: bytes, mime: str | None) -> C2PAReading:
        c2pa = self._c2pa
        try:
            Reader = c2pa.Reader
        except AttributeError as e:
            raise ProviderError(f"c2pa Reader not available: {e}") from e

        try:
            try:
                reader = Reader.from_stream(data, mime) if mime else Reader.from_bytes(data)
            except AttributeError:
                reader = Reader(data)  # older SDKs
        except Exception as e:
            msg = str(e).lower()
            if "no claim" in msg or "no manifest" in msg or "not found" in msg:
                return C2PAReading(state="missing")
            return C2PAReading(state="invalid", error=f"{type(e).__name__}: {e}")

        try:
            manifest_store = getattr(reader, "json", lambda: None)()
            manifests = _parse_manifest_store(manifest_store)
            validation_state = _validation_state(manifest_store)
        except Exception as e:
            return C2PAReading(state="invalid", error=f"parse error: {type(e).__name__}: {e}")

        if validation_state is None:
            return C2PAReading(state="unverified", manifests=manifests)
        if validation_state.lower() == "valid":
            return C2PAReading(state="valid", manifests=manifests)
        return C2PAReading(state="invalid", manifests=manifests, error=validation_state)

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "")


class NullC2PAVerifier:
    name = "none"

    async def verify(self, data: bytes, *, mime: str | None = None) -> C2PAReading:
        raise NotConfigured("c2pa verifier disabled")

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "disabled")


def _parse_manifest_store(obj: Any) -> list[dict[str, Any]]:
    import json

    if obj is None:
        return []
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError:
            return []
    if not isinstance(obj, dict):
        return []
    out: list[dict[str, Any]] = []
    manifests = obj.get("manifests") or {}
    if isinstance(manifests, dict):
        for label, m in manifests.items():
            if isinstance(m, dict):
                out.append(
                    {
                        "label": label,
                        "title": m.get("title"),
                        "format": m.get("format"),
                        "claim_generator": m.get("claim_generator"),
                        "signature_info": m.get("signature_info"),
                    }
                )
    return out


def _validation_state(obj: Any) -> str | None:
    import json

    if obj is None:
        return None
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError:
            return None
    if isinstance(obj, dict):
        v = obj.get("validation_state") or obj.get("validation_status")
        if isinstance(v, str):
            return v
    return None
