import ipaddress

import httpx
import pytest

from provcheck.core.security.ssrf import (
    BlockedURL,
    FetchError,
    SafeFetcher,
    URLPolicy,
    check_target,
    ip_block_reason,
    parse_target,
    trusted_client,
)

POLICY = URLPolicy()

BLOCKED_IPS = [
    "127.0.0.1",
    "127.255.255.254",
    "10.0.0.1",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.1.1",
    "169.254.169.254",
    "100.64.0.1",
    "0.0.0.0",
    "0.1.2.3",
    "198.18.0.1",
    "192.0.0.8",
    "224.0.0.1",
    "239.255.255.250",
    "255.255.255.255",
    "240.0.0.1",
    "192.0.2.1",
    "203.0.113.5",
    "::",
    "::1",
    "fe80::1",
    "fc00::1",
    "fd12:3456::1",
    "ff02::1",
    "2001:db8::1",
    "::ffff:127.0.0.1",
    "::ffff:169.254.169.254",
    "::ffff:10.0.0.1",
    "2002:7f00:1::",  # 6to4 wrapping 127.0.0.1
    "2002:a9fe:a9fe::",  # 6to4 wrapping 169.254.169.254
    "64:ff9b::a9fe:a9fe",  # NAT64 wrapping 169.254.169.254
    "64:ff9b::7f00:1",  # NAT64 wrapping 127.0.0.1
    "::127.0.0.1",  # IPv4-compatible (deprecated)
]
PUBLIC_IPS = [
    "8.8.8.8",
    "1.1.1.1",
    "93.184.215.14",
    "2606:4700:4700::1111",
    "2a00:1450:4001:82a::200e",
    "::ffff:8.8.8.8",
    "64:ff9b::808:808",
]


@pytest.mark.parametrize("ip", BLOCKED_IPS)
def test_blocked_ips(ip):
    assert ip_block_reason(ipaddress.ip_address(ip)) is not None


@pytest.mark.parametrize("ip", PUBLIC_IPS)
def test_public_ips_allowed(ip):
    assert ip_block_reason(ipaddress.ip_address(ip)) is None


def test_allowlist_exempts_only_listed_range():
    allow = (ipaddress.ip_network("172.31.250.0/24"),)
    assert ip_block_reason(ipaddress.ip_address("172.31.250.10"), allow) is None
    assert ip_block_reason(ipaddress.ip_address("172.31.251.10"), allow) is not None
    assert ip_block_reason(ipaddress.ip_address("127.0.0.1"), allow) is not None


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/",
        "javascript:alert(1)",
        "data:text/html,hi",
        "dict://example.com:11211/",
        "http://user:pw@example.com/",
        "http://@example.com/",
        "http://example.com:22/",
        "http://example.com:6379/",
        "https://example.com:8443/",
        "http://2130706433/",
        "http://0x7f.0.0.1/",
        "http://0177.0.0.1/",
        "http://127.1/",
        "http://localhost/",
        "http://LOCALHOST./",
        "http://foo.localhost/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[fd00::1]/",
        "http://exa\nmple.com/",
        "http://example.com/\x00",
        "http:///nohost",
        "http://" + "a" * 2050 + ".com/",
        "",
    ],
)
def test_parse_target_rejects(url):
    with pytest.raises(BlockedURL):
        parse_target(url, POLICY)


def test_parse_target_normalizes():
    t = parse_target("HTTP://Example.COM/a/b?x=1#frag", POLICY)
    assert t.url == "http://example.com/a/b?x=1"
    assert (t.scheme, t.host, t.port) == ("http", "example.com", 80)
    assert parse_target("https://example.com", POLICY).url == "https://example.com/"


def test_parse_target_idn():
    t = parse_target("https://bücher.example/", POLICY)
    assert t.host == "xn--bcher-kva.example"


def _resolver(mapping):
    async def resolve(host, port):
        return [ipaddress.ip_address(a) for a in mapping[host]]

    return resolve


async def test_check_target_requires_every_address_public():
    t = parse_target("http://mixed.example/", POLICY)
    with pytest.raises(BlockedURL, match="blocked address"):
        await check_target(t, POLICY, _resolver({"mixed.example": ["8.8.8.8", "10.0.0.5"]}))


async def test_check_target_public_ok():
    t = parse_target("http://ok.example/", POLICY)
    ips = await check_target(t, POLICY, _resolver({"ok.example": ["93.184.215.14"]}))
    assert [str(i) for i in ips] == ["93.184.215.14"]


def _fetcher(handler, mapping, **kw):
    return SafeFetcher(
        POLICY,
        proxy_url=None,
        resolver=_resolver(mapping),
        transport=httpx.MockTransport(handler),
        **kw,
    )


async def test_fetch_pins_to_vetted_ip_and_keeps_host_header():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["host"] = request.headers["host"]
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>hi</p>")

    f = _fetcher(handler, {"news.example": ["93.184.215.14"]})
    r = await f.fetch("https://news.example/story")
    assert seen["url"] == "https://93.184.215.14/story"
    assert seen["host"] == "news.example"
    assert seen["sni"] == "news.example"
    assert r.final_url == "https://news.example/story" and r.content == b"<p>hi</p>"


async def test_fetch_blocks_redirect_to_private():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})

    f = _fetcher(handler, {"evil.example": ["93.184.215.14"]})
    with pytest.raises(BlockedURL):
        await f.fetch("http://evil.example/")


async def test_fetch_blocks_redirect_to_rebinding_host():
    def handler(request):
        return httpx.Response(301, headers={"location": "http://rebind.example/x"})

    f = _fetcher(handler, {"a.example": ["93.184.215.14"], "rebind.example": ["127.0.0.1"]})
    with pytest.raises(BlockedURL):
        await f.fetch("http://a.example/")


async def test_fetch_follows_public_redirect_and_records_chain():
    def handler(request):
        if request.headers["host"] == "a.example":
            return httpx.Response(301, headers={"location": "https://b.example/final"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"ok")

    f = _fetcher(handler, {"a.example": ["93.184.215.14"], "b.example": ["93.184.215.15"]})
    r = await f.fetch("http://a.example/start")
    assert r.final_url == "https://b.example/final"
    assert r.redirect_chain == ["http://a.example/start"]


async def test_fetch_redirect_limit():
    def handler(request):
        return httpx.Response(302, headers={"location": "/again"})

    f = _fetcher(handler, {"loop.example": ["93.184.215.14"]}, max_redirects=3)
    with pytest.raises(FetchError, match="too many redirects"):
        await f.fetch("http://loop.example/")


async def test_fetch_size_cap_declared_and_streamed():
    def big_declared(request):
        return httpx.Response(200, headers={"content-length": "999999999"}, content=b"x")

    f = _fetcher(big_declared, {"big.example": ["93.184.215.14"]}, max_bytes=1000)
    with pytest.raises(FetchError, match="too large"):
        await f.fetch("http://big.example/")

    def big_body(request):
        async def chunks():
            for _ in range(5):
                yield b"x" * 1000

        return httpx.Response(200, content=chunks())  # no content-length: streamed

    f = _fetcher(big_body, {"big.example": ["93.184.215.14"]}, max_bytes=1000)
    with pytest.raises(FetchError, match="exceeded"):
        await f.fetch("http://big.example/")


async def test_fetch_content_type_allowlist():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/x-msdownload"})

    f = _fetcher(handler, {"dl.example": ["93.184.215.14"]})
    with pytest.raises(FetchError, match="content type"):
        await f.fetch("http://dl.example/", allowed_content_types=["text/html", "image/*"])


async def test_trusted_client_refuses_other_hosts():
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    async with trusted_client("https://api.provider.example", transport=transport) as c:
        assert (await c.get("/ok")).status_code == 200
        with pytest.raises(BlockedURL):
            await c.get("http://169.254.169.254/")


async def test_fetch_treats_proxy_denial_as_blocked():
    def handler(request):
        return httpx.Response(
            407, headers={"x-smokescreen-error": "denied by rule 'Deny: Private Range'"}
        )

    f = _fetcher(handler, {"sneaky.example": ["93.184.215.14"]})
    with pytest.raises(BlockedURL, match="egress proxy"):
        await f.fetch("http://sneaky.example/")
