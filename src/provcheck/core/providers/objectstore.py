"""ObjectStore implementations: any S3-compatible server (via minio-py) and in-memory."""

from __future__ import annotations

import asyncio
import hashlib
import io
from collections.abc import Mapping
from datetime import timedelta

from minio import Minio
from minio.error import S3Error

from provcheck.core.interfaces import ProviderHealth, ProviderUnavailable, StoredObject


def content_key(prefix: str, sha256_hex: str) -> str:
    prefix = prefix.strip("/")
    if not prefix or ".." in prefix:
        raise ValueError("invalid prefix")
    return f"{prefix}/sha256/{sha256_hex[:2]}/{sha256_hex}"


class S3ObjectStore:
    """Works with SeaweedFS, MinIO/AIStor, Garage, Ceph RGW or cloud S3; config only."""

    name = "s3"

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
        region: str = "us-east-1",
    ) -> None:
        self.bucket = bucket
        self._client = Minio(
            endpoint,
            access_key=access_key or None,
            secret_key=secret_key or None,
            secure=secure,
            region=region,
        )

    async def ensure_bucket(self) -> None:
        def _ensure() -> None:
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)

        await asyncio.to_thread(_ensure)

    async def put(
        self,
        data: bytes,
        *,
        content_type: str,
        prefix: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredObject:
        digest = hashlib.sha256(data).hexdigest()
        key = content_key(prefix, digest)
        meta = {"sha256": digest, **(metadata or {})}

        def _put() -> None:
            self._client.put_object(
                self.bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
                metadata=meta,  # type: ignore[arg-type]
            )

        try:
            if not await self.exists(key):
                await asyncio.to_thread(_put)
        except S3Error as e:
            raise ProviderUnavailable(f"s3 put failed: {e.code}") from e
        return StoredObject(key=key, sha256=digest, size=len(data), content_type=content_type)

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            resp = self._client.get_object(self.bucket, key)
            try:
                return bytes(resp.read())
            finally:
                resp.close()
                resp.release_conn()

        try:
            return await asyncio.to_thread(_get)
        except S3Error as e:
            if e.code == "NoSuchKey":
                raise KeyError(key) from e
            raise ProviderUnavailable(f"s3 get failed: {e.code}") from e

    async def exists(self, key: str) -> bool:
        def _stat() -> bool:
            try:
                self._client.stat_object(self.bucket, key)
                return True
            except S3Error as e:
                if e.code in {"NoSuchKey", "NoSuchObject", "NotFound"}:
                    return False
                raise

        return await asyncio.to_thread(_stat)

    async def presigned_get_url(self, key: str, expires: timedelta) -> str:
        return await asyncio.to_thread(self._client.presigned_get_object, self.bucket, key, expires)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.remove_object, self.bucket, key)

    async def health(self) -> ProviderHealth:
        try:
            ok = await asyncio.to_thread(self._client.bucket_exists, self.bucket)
        except Exception as e:
            return ProviderHealth(self.name, False, f"{type(e).__name__}: {e}")
        return ProviderHealth(self.name, ok, "" if ok else f"bucket {self.bucket} missing")


class MemoryObjectStore:
    """For tests and single-process dev. Not durable."""

    name = "memory"

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str, dict[str, str]]] = {}

    async def put(
        self,
        data: bytes,
        *,
        content_type: str,
        prefix: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredObject:
        digest = hashlib.sha256(data).hexdigest()
        key = content_key(prefix, digest)
        self.objects.setdefault(key, (data, content_type, dict(metadata or {})))
        return StoredObject(key=key, sha256=digest, size=len(data), content_type=content_type)

    async def get(self, key: str) -> bytes:
        return self.objects[key][0]

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def presigned_get_url(self, key: str, expires: timedelta) -> str:
        return f"memory://{key}?expires={int(expires.total_seconds())}"

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True)
