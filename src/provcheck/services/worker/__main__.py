"""RQ worker. ``WORKER_QUEUES`` (comma-separated) selects which pools this process serves."""

from __future__ import annotations

import os
import socket
import uuid

from redis import Redis
from rq import Worker

from provcheck import logging_setup
from provcheck.config import get_settings


def main() -> None:
    settings = get_settings()
    logging_setup.configure(settings.log_level)
    queues = [q.strip() for q in os.environ.get("WORKER_QUEUES", "").split(",") if q.strip()]
    if not queues:
        raise SystemExit("WORKER_QUEUES is required")
    redis = Redis.from_url(settings.redis_url)
    name = f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
    Worker(queues, connection=redis, name=name).work(with_scheduler=True)


if __name__ == "__main__":
    main()
