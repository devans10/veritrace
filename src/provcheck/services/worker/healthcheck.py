"""Exit 0 if an RQ worker from this container is registered and heartbeating."""

from __future__ import annotations

import socket
import sys

from redis import Redis
from rq import Worker

from provcheck.config import get_settings


def main() -> int:
    redis = Redis.from_url(get_settings().redis_url, socket_timeout=3)
    host = socket.gethostname()
    alive = [w for w in Worker.all(connection=redis) if w.name.startswith(f"{host}-")]
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
