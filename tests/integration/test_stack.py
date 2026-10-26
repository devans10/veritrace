"""Integration tests against the dev stack: `make up-dev` then `make test-integration`."""

import hashlib
import json
import os
import subprocess
import time
import uuid

import httpx
import psycopg
import pytest

pytestmark = pytest.mark.integration

API = os.environ.get("TEST_API_URL", "http://127.0.0.1:8000")
COMPOSE = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.dev.yml"]
FIXTURE = "http://fixture-site"


def submit(url: str, **extra) -> dict:
    r = httpx.post(f"{API}/v1/cases", json={"url": url, "archive": False, **extra}, timeout=30)
    assert r.status_code in (200, 202), r.text
    return r.json()


def wait(case_id: str, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = httpx.get(f"{API}/v1/cases/{case_id}", timeout=10).json()
        if d["status"] in ("complete", "failed"):
            return d
        time.sleep(0.5)
    raise AssertionError(f"case {case_id} did not finish: {d['status']}")


def fresh(path: str) -> str:
    return f"{FIXTURE}{path}?nocache={uuid.uuid4().hex}"


def test_readyz():
    r = httpx.get(f"{API}/readyz", timeout=10)
    assert r.status_code == 200 and r.json()["ok"], r.text


def test_capture_stores_snapshot_with_verifiable_hashes():
    case = wait(submit(fresh("/article.html"))["id"])
    assert case["status"] == "complete", case["error"]
    cap = case["captures"][0]
    assert cap["http_status"] == 200
    assert cap["title"] == "Harbor Bridge Reopens After Two-Year Renovation"
    assert cap["claimed_published_at"] == "2026-03-14T09:30:00Z"
    assert cap["archive"]["status"] == "opted_out"
    kinds = {a["kind"]: a for a in cap["artifacts"]}
    assert set(kinds) == {"html", "screenshot", "text"}
    for a in kinds.values():
        r = httpx.get(f"{API}{a['href']}", timeout=10)
        assert r.status_code == 200
        assert hashlib.sha256(r.content).hexdigest() == a["sha256"] == r.headers["x-content-sha256"]
        assert "sandbox" in r.headers["content-security-policy"]
    assert kinds["screenshot"]["mime"] == "image/png"
    text = httpx.get(f"{API}{kinds['text']['href']}").text
    assert "84 million dollars" in text
    # Captured HTML is never served as HTML on our origin.
    html = httpx.get(f"{API}{kinds['html']['href']}")
    assert html.headers["content-type"].startswith("text/plain")
    assert "attachment" in html.headers["content-disposition"]


def test_redirect_chain_is_recorded():
    case = wait(submit(f"{FIXTURE}/redirect", refresh=True)["id"])
    cap = case["captures"][0]
    assert cap["final_url"] == f"{FIXTURE}/article.html"
    assert cap["redirect_chain"] == [f"{FIXTURE}/redirect"]


def test_result_cache_and_refresh():
    url = fresh("/article.html")
    first = wait(submit(url)["id"])
    again = httpx.post(f"{API}/v1/cases", json={"url": url, "archive": False})
    assert again.status_code == 200 and again.json()["cached"] is True
    assert again.json()["id"] == first["id"]
    forced = submit(url, refresh=True)
    assert forced["id"] != first["id"] and forced["cached"] is False


def test_audit_chain_for_case():
    case = wait(submit(fresh("/article.html"))["id"])
    d = httpx.get(f"{API}/v1/cases/{case['id']}/audit").json()
    assert d["entries_valid"] and d["chain_valid"], d["chain_detail"]
    events = [e["event_type"] for e in d["entries"]]
    assert events == [
        "case.created",
        "stage.started",
        "capture.stored",
        "archive.requested",
        "stage.transition",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://postgres/",  # resolves to a private docker address
        "http://redis:6379/",
        "http://egress-proxy:4750/",
        "http://browser:3000/capture",
        "http://localhost:8000/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://2130706433/",
        "file:///etc/passwd",
        "gopher://example.com/",
        "http://user:pass@example.com/",
    ],
)
def test_api_rejects_unsafe_urls(url):
    r = httpx.post(f"{API}/v1/cases", json={"url": url})
    assert r.status_code == 422, r.text


def test_hostile_page_subresources_blocked():
    case = wait(submit(fresh("/hostile.html"))["id"])
    assert case["status"] == "complete", case["error"]
    # 169.254/127.0.0.1/localhost/[::1] blocked in-browser; postgres/seaweedfs/redis/
    # egress-proxy hostnames refused by the proxy.
    assert case["captures"][0]["blocked_requests"] >= 6


@pytest.mark.parametrize("path", ["/redirect-to-metadata", "/redirect-to-postgres"])
def test_redirect_to_internal_fails_closed(path):
    case = wait(submit(fresh(path))["id"])
    assert case["status"] == "failed"
    assert case["error"].startswith("blocked_by_policy")
    assert case["captures"] == []


def _db(user: str, password: str):
    return psycopg.connect(
        host="127.0.0.1",
        port=55432,
        dbname="provcheck",
        user=user,
        password=password,
        autocommit=True,
    )


@pytest.mark.parametrize(
    "who", [("provcheck_app", "APP_DB_PASSWORD"), ("provcheck_owner", "DB_OWNER_PASSWORD")]
)
@pytest.mark.parametrize(
    "sql", ["UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log", "TRUNCATE audit_log"]
)
def test_audit_log_is_append_only(who, sql):
    user, env = who
    with _db(user, os.environ[env]) as conn, pytest.raises(psycopg.Error):
        conn.execute(sql)


def test_app_role_cannot_ddl():
    with _db("provcheck_app", os.environ["APP_DB_PASSWORD"]) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("CREATE TABLE evil (x int)")
        with pytest.raises(psycopg.Error):
            conn.execute("DROP TABLE cases")


PROBE = r"""
import json, socket
out = {}
for host, port in json.loads(TARGETS):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.settimeout(3)
    try:
        s.connect((socket.gethostbyname(host), port)); out[f"{host}:{port}"] = "OPEN"
    except Exception as e:
        out[f"{host}:{port}"] = type(e).__name__
    finally:
        s.close()
print(json.dumps(out))
"""


def _probe_from(service: str, targets: list[tuple[str, int]]) -> dict:
    code = PROBE.replace("TARGETS", repr(json.dumps(targets)))
    res = subprocess.run(
        [*COMPOSE, "exec", "-T", service, "python3", "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_browser_container_cannot_reach_internal_or_internet_directly():
    targets = [
        ("postgres", 5432),
        ("redis", 6379),
        ("seaweedfs", 8333),
        ("api", 8000),
        ("fixture-site", 80),
        ("169.254.169.254", 80),
        ("1.1.1.1", 443),
        ("8.8.8.8", 53),
    ]
    result = _probe_from("browser", targets)
    assert all(v != "OPEN" for v in result.values()), result
    # ...its only way out is the proxy.
    assert _probe_from("browser", [("egress-proxy", 4750)]) == {"egress-proxy:4750": "OPEN"}


def test_proxy_refuses_internal_destinations():
    code = r"""
import httpx
for u in ["http://postgres:5432/", "http://seaweedfs:8333/", "http://169.254.169.254/"]:
    r = httpx.get(u, proxy="http://egress-proxy:4750", timeout=10, trust_env=False)
    print(u, r.status_code, "x-smokescreen-error" in r.headers)
"""
    res = subprocess.run(
        [*COMPOSE, "exec", "-T", "worker-capture", "python", "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    lines = res.stdout.strip().splitlines()
    assert len(lines) == 3 and all(line.endswith("407 True") for line in lines), res.stdout
