import pytest

from provcheck.config import Settings


def test_file_secrets(tmp_path, monkeypatch):
    secret = tmp_path / "pw"
    secret.write_text("s3cr3t/with:chars\n")
    monkeypatch.setenv("DB_PASSWORD_FILE", str(secret))
    s = Settings()
    assert s.db_password.get_secret_value() == "s3cr3t/with:chars"
    assert "s3cr3t%2Fwith%3Achars" in s.database_url


def test_comma_lists(monkeypatch):
    monkeypatch.setenv("SSRF_ALLOW_CIDRS", "172.31.250.0/24, 10.9.0.0/16")
    monkeypatch.setenv("FETCH_ALLOWED_PORTS", "80,443,8080")
    s = Settings()
    assert s.ssrf_allow_cidrs == ["172.31.250.0/24", "10.9.0.0/16"]
    assert s.fetch_allowed_ports == [80, 443, 8080]


def test_ssrf_allowlist_refused_in_production(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("SSRF_ALLOW_CIDRS", "10.0.0.0/8")
    with pytest.raises(ValueError, match="must not be set"):
        Settings()


def test_secrets_not_in_repr(monkeypatch):
    monkeypatch.setenv("S3_SECRET_KEY", "topsecret")
    assert "topsecret" not in repr(Settings())
