"""Application settings.

Everything is configured through environment variables. Any field can instead be
read from a file by setting ``<FIELD>_FILE`` (docker/k8s secrets), e.g.
``DB_PASSWORD_FILE=/run/secrets/db_password``.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

log = logging.getLogger(__name__)


class FileSecretsSource(PydanticBaseSettingsSource):
    """Reads ``<NAME>_FILE`` env vars and loads the referenced file's contents."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        path = os.environ.get(f"{field_name.upper()}_FILE")
        if not path:
            return None, field_name, False
        return Path(path).read_text().strip(), field_name, False

    def __call__(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, field in self.settings_cls.model_fields.items():
            value, key, _ = self.get_field_value(field, name)
            if value is not None:
                out[key] = value
        return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    env: Literal["development", "test", "production"] = "development"
    service_name: str = "provcheck"
    log_level: str = "INFO"

    # --- Postgres -------------------------------------------------------
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "provcheck"
    db_user: str = "provcheck_app"
    db_password: SecretStr = SecretStr("")
    # Owner credentials, used only by migrations.
    db_owner_user: str = "provcheck_owner"
    db_owner_password: SecretStr = SecretStr("")

    # --- Redis / queue ----------------------------------------------------
    queue: Literal["rq", "inline"] = "rq"
    redis_url: str = "redis://redis:6379/0"

    # --- Object store -----------------------------------------------------
    object_store: Literal["s3", "memory"] = "s3"
    s3_endpoint: str = "seaweedfs:8333"
    s3_secure: bool = False
    s3_region: str = "us-east-1"
    s3_bucket: str = "provcheck"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")

    # --- Browser capture service -------------------------------------------
    browser_url: str = "http://browser:3000"
    browser_timeout_s: float = 60.0

    # --- Untrusted fetching / SSRF ----------------------------------------
    egress_proxy_url: str | None = "http://egress-proxy:4750"
    fetch_allowed_ports: Annotated[list[int], NoDecode] = Field(default_factory=lambda: [80, 443])
    fetch_max_bytes: int = 10 * 1024 * 1024
    fetch_timeout_s: float = 20.0
    fetch_max_redirects: int = 5
    # DEV/TEST ONLY: CIDRs exempted from the SSRF block list (e.g. the fixture site).
    ssrf_allow_cidrs: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Archive (Wayback Save Page Now 2) -------------------------------------
    archive_provider: Literal["wayback", "none"] = "wayback"
    archive_save_enabled: bool = True
    ia_s3_access_key: SecretStr = SecretStr("")
    ia_s3_secret_key: SecretStr = SecretStr("")
    wayback_base_url: str = "https://web.archive.org"
    archive_poll_interval_s: int = 20
    archive_poll_max_attempts: int = 30

    # --- API ------------------------------------------------------------------
    max_url_length: int = 2048
    result_cache_ttl_s: int = 6 * 3600

    @field_validator("ssrf_allow_cidrs", mode="before")
    @classmethod
    def _split_cidrs(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [c.strip() for c in v.split(",") if c.strip()]
        return v

    @field_validator("fetch_allowed_ports", mode="before")
    @classmethod
    def _split_ports(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [int(p) for p in v.split(",") if p.strip()]
        return v

    @model_validator(mode="after")
    def _check_ssrf_allowlist(self) -> Settings:
        for cidr in self.ssrf_allow_cidrs:
            ipaddress.ip_network(cidr, strict=False)
        if self.ssrf_allow_cidrs:
            if self.env == "production":
                raise ValueError("SSRF_ALLOW_CIDRS must not be set when ENV=production")
            log.warning(
                "SSRF_ALLOW_CIDRS is set (%s): private ranges are reachable. DEV/TEST ONLY.",
                self.ssrf_allow_cidrs,
            )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, FileSecretsSource(settings_cls))

    def _db_url(self, user: str, password: SecretStr) -> str:
        pw = quote(password.get_secret_value(), safe="")
        return f"postgresql+psycopg://{user}:{pw}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def database_url(self) -> str:
        return self._db_url(self.db_user, self.db_password)

    @property
    def migration_database_url(self) -> str:
        return self._db_url(self.db_owner_user, self.db_owner_password)

    @property
    def archive_configured(self) -> bool:
        return bool(
            self.ia_s3_access_key.get_secret_value() and self.ia_s3_secret_key.get_secret_value()
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
