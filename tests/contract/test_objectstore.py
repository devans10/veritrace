"""ObjectStore contract: every implementation must pass the same tests."""

import hashlib
import os
from datetime import timedelta

import pytest

from provcheck.core.providers.objectstore import MemoryObjectStore, S3ObjectStore


def _stores():
    yield pytest.param(MemoryObjectStore, id="memory")
    yield pytest.param(
        lambda: S3ObjectStore(
            endpoint=os.environ.get("TEST_S3_ENDPOINT", "127.0.0.1:58333"),
            access_key=os.environ.get("S3_ACCESS_KEY", ""),
            secret_key=os.environ.get("S3_SECRET_KEY", ""),
            bucket="provcheck",
        ),
        id="s3",
        marks=pytest.mark.integration,
    )


@pytest.fixture(params=list(_stores()))
def store(request):
    return request.param()


async def test_put_is_content_addressed_and_idempotent(store):
    data = os.urandom(64)
    a = await store.put(data, content_type="application/octet-stream", prefix="contract")
    b = await store.put(data, content_type="application/octet-stream", prefix="contract")
    assert a.key == b.key
    assert a.sha256 == hashlib.sha256(data).hexdigest() and a.size == 64
    assert await store.get(a.key) == data
    assert await store.exists(a.key)
    assert await store.presigned_get_url(a.key, timedelta(minutes=5))
    await store.delete(a.key)
    assert not await store.exists(a.key)


async def test_missing_key(store):
    assert not await store.exists("contract/sha256/00/" + "0" * 64)
    with pytest.raises(KeyError):
        await store.get("contract/sha256/00/" + "0" * 64)


async def test_health(store):
    assert (await store.health()).ok
