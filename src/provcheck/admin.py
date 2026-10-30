"""Operational commands: ``provcheck-admin init-storage | verify-audit | migrate``."""

from __future__ import annotations

import argparse
import asyncio
import sys

from provcheck.config import get_settings


def _init_storage() -> int:
    from provcheck.core.providers.objectstore import S3ObjectStore
    from provcheck.core.providers.registry import build_object_store

    store = build_object_store(get_settings())
    if isinstance(store, S3ObjectStore):
        asyncio.run(store.ensure_bucket())
        print(f"bucket {store.bucket} ready")
    return 0


def _verify_audit() -> int:
    from provcheck.core.db import audit
    from provcheck.core.db.session import session_scope

    with session_scope() as s:
        result = audit.verify_chain(s)
    print(result)
    return 0 if result.ok else 1


def _migrate() -> int:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="provcheck-admin")
    parser.add_argument("command", choices=["init-storage", "verify-audit", "migrate"])
    args = parser.parse_args()
    handlers = {"init-storage": _init_storage, "verify-audit": _verify_audit, "migrate": _migrate}
    sys.exit(handlers[args.command]())


if __name__ == "__main__":
    main()
