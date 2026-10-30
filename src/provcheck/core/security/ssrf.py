"""SSRF protection for every URL that comes from user input or fetched content.

Two layers:
  1. In-process: URL syntax checks, then DNS resolution where *every* resolved
     address must be globally routable. Without a proxy, the connection is pinned
     to the vetted IP (defeats DNS rebinding). Redirects are re-validated hop by hop.
  2. Network: requests go through the egress proxy (Smokescreen), which repeats
     the private-range check on its own resolution.

Configured provider endpoints (LLM, Google, Internet Archive...) are *trusted* and
use ``trusted_client`` instead, which pins requests to the configured host.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
Resolver = Callable[[str, int], Awaitable[list[IPAddress]]]

# Explicit ranges blocked in addition to anything ``ipaddress`` says is not global.
_EXTRA_BLOCKED: tuple[IPNetwork, ...] = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "100.64.0.0/10",  # CGNAT
        "192.0.0.0/24",
        "198.18.0.0/15",  # benchmarking
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",
        "255.255.255.255/32",
        "64:ff9b:1::/48",  # local-use NAT64
        "fc00::/7",  # ULA
        "fe80::/10",  # link-local
        "fec0::/10",  # site-local (deprecated)
        "ff00::/8",  # multicast
    )
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
# Smokescreen marks its own denial responses (HTTP 407) with this header.
PROXY_ERROR_HEADER = "x-smokescreen-error"


class BlockedURL(Exception):
    """The URL or one of its resolved addresses is not allowed."""

    def __init__(self, reason: str, url: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.url = url


class FetchError(Exception):
    """The fetch failed for a reason other than policy (timeout, size, status...)."""


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    # IPv4-compatible (deprecated) ::a.b.c.d
    if int(ip) >> 32 == 0 and int(ip) > 0xFFFF:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def ip_block_reason(ip: IPAddress, allow: Sequence[IPNetwork] = ()) -> str | None:
    """Return why ``ip`` is blocked, or None if it is a public address."""
    if any(ip in net for net in allow if net.version == ip.version):
        return None
    if isinstance(ip, ipaddress.IPv6Address):
        inner = _embedded_ipv4(ip)
        if inner is not None:
            reason = ip_block_reason(inner, allow)
            return f"embedded IPv4 {inner}: {reason}" if reason else None
    for net in _EXTRA_BLOCKED:
        if net.version == ip.version and ip in net:
            return f"{ip} is in blocked range {net}"
    checks = (
        ("loopback", ip.is_loopback),
        ("private", ip.is_private),
        ("link-local", ip.is_link_local),
        ("multicast", ip.is_multicast),
        ("reserved", ip.is_reserved),
        ("unspecified", ip.is_unspecified),
        ("not globally routable", not ip.is_global),
    )
    for label, hit in checks:
        if hit:
            return f"{ip} is {label}"
    return None


@dataclass(frozen=True)
class Target:
    url: str
    scheme: str
    host: str  # IDNA-encoded, lowercase, no brackets
    port: int
    ip_literal: IPAddress | None


@dataclass(frozen=True)
class URLPolicy:
    allowed_ports: frozenset[int] = frozenset({80, 443})
    max_url_length: int = 2048
    allow_networks: tuple[IPNetwork, ...] = ()

    @classmethod
    def from_settings(cls, settings: object) -> URLPolicy:
        return cls(
            allowed_ports=frozenset(getattr(settings, "fetch_allowed_ports", [80, 443])),
            max_url_length=getattr(settings, "max_url_length", 2048),
            allow_networks=tuple(
                ipaddress.ip_network(c, strict=False)
                for c in getattr(settings, "ssrf_allow_cidrs", [])
            ),
        )


def _is_legacy_ipv4_literal(host: str) -> bool:
    """True for hosts like '2130706433', '0x7f.1', '0177.0.0.1' that libc treats as IPs."""
    try:
        socket.inet_aton(host)
    except OSError:
        return False
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        return True  # inet_aton accepts it but it is not canonical dotted-quad
    return False


def parse_target(url: str, policy: URLPolicy) -> Target:
    """Syntactic validation. Does not touch the network."""
    if not url or len(url) > policy.max_url_length:
        raise BlockedURL("URL is empty or too long", url)
    if any(c in url for c in "\r\n\t\x00") or any(ord(c) < 0x20 for c in url):
        raise BlockedURL("URL contains control characters", url)
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise BlockedURL(f"scheme {scheme!r} not allowed", url)
    if parts.username is not None or parts.password is not None:
        raise BlockedURL("credentials in URL are not allowed", url)
    raw_host = parts.hostname
    if not raw_host:
        raise BlockedURL("URL has no host", url)
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as e:
        raise BlockedURL("invalid port", url) from e
    if port not in policy.allowed_ports:
        raise BlockedURL(f"port {port} not allowed", url)

    ip_literal: IPAddress | None = None
    try:
        ip_literal = ipaddress.ip_address(raw_host.split("%", 1)[0])
        host = str(ip_literal)
    except ValueError:
        if _is_legacy_ipv4_literal(raw_host):
            raise BlockedURL("non-canonical IP literal", url) from None
        try:
            host = raw_host.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as e:
            raise BlockedURL("invalid hostname", url) from e
        if not host:
            raise BlockedURL("invalid hostname", url) from None
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
            raise BlockedURL("localhost is not allowed", url) from None

    if ip_literal is not None:
        reason = ip_block_reason(ip_literal, policy.allow_networks)
        if reason:
            raise BlockedURL(reason, url)

    netloc = f"[{host}]" if ":" in host else host
    if parts.port:
        netloc = f"{netloc}:{port}"
    normalized = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    return Target(url=normalized, scheme=scheme, host=host, port=port, ip_literal=ip_literal)


async def system_resolver(host: str, port: int) -> list[IPAddress]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise BlockedURL(f"could not resolve {host}: {e.strerror}") from e
    out: list[IPAddress] = []
    for info in infos:
        addr = str(info[4][0]).split("%", 1)[0]
        ip = ipaddress.ip_address(addr)
        if ip not in out:
            out.append(ip)
    return out


async def check_target(
    target: Target, policy: URLPolicy, resolver: Resolver = system_resolver
) -> list[IPAddress]:
    """Resolve and require that every address is allowed. Returns the vetted IPs."""
    if target.ip_literal is not None:
        return [target.ip_literal]
    ips = await resolver(target.host, target.port)
    if not ips:
        raise BlockedURL(f"{target.host} resolved to no addresses", target.url)
    for ip in ips:
        reason = ip_block_reason(ip, policy.allow_networks)
        if reason:
            raise BlockedURL(f"{target.host} resolves to blocked address: {reason}", target.url)
    return ips


async def validate_url(url: str, policy: URLPolicy, resolver: Resolver = system_resolver) -> Target:
    target = parse_target(url, policy)
    await check_target(target, policy, resolver)
    return target


@dataclass
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    redirect_chain: list[str] = field(default_factory=list)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()


class SafeFetcher:
    """HTTP GET for untrusted URLs. Never follows redirects without re-validating."""

    def __init__(
        self,
        policy: URLPolicy,
        *,
        proxy_url: str | None,
        timeout_s: float = 20.0,
        max_bytes: int = 10 * 1024 * 1024,
        max_redirects: int = 5,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        user_agent: str = "ProvenanceCheck/0.1 (+content verification research)",
    ) -> None:
        self.policy = policy
        self.proxy_url = proxy_url
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.resolver = resolver
        self.transport = transport
        self.user_agent = user_agent

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            proxy=self.proxy_url if self.transport is None else None,
            transport=self.transport,
            timeout=self.timeout_s,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": self.user_agent},
        )

    async def fetch(
        self, url: str, *, allowed_content_types: Sequence[str] | None = None
    ) -> FetchResult:
        chain: list[str] = []
        current = url
        async with self._client() as client:
            for _ in range(self.max_redirects + 1):
                target = parse_target(current, self.policy)
                ips = await check_target(target, self.policy, self.resolver)
                status, headers, body = await self._get_once(client, target, ips[0])
                location = headers.get("location")
                if status in _REDIRECT_CODES and location:
                    chain.append(target.url)
                    current = urljoin(target.url, location)
                    continue
                result = FetchResult(
                    requested_url=url,
                    final_url=target.url,
                    status_code=status,
                    headers=headers,
                    content=body,
                    redirect_chain=chain,
                )
                if allowed_content_types is not None and not any(
                    result.content_type == t or result.content_type.startswith(t.rstrip("*"))
                    for t in allowed_content_types
                ):
                    raise FetchError(f"content type {result.content_type!r} not allowed")
                return result
        raise FetchError(f"too many redirects (>{self.max_redirects})")

    async def _get_once(
        self, client: httpx.AsyncClient, target: Target, ip: IPAddress
    ) -> tuple[int, dict[str, str], bytes]:
        if self.proxy_url and self.transport is None:
            # The proxy does its own resolution and enforces the same block list.
            request = client.build_request("GET", target.url)
        else:
            # Pin the connection to the vetted address; keep Host and SNI as the name.
            parts = urlsplit(target.url)
            ip_host = f"[{ip}]" if ip.version == 6 else str(ip)
            netloc = ip_host if parts.port is None else f"{ip_host}:{parts.port}"
            pinned = urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
            host_header = parts.netloc
            request = client.build_request(
                "GET",
                pinned,
                headers={"Host": host_header},
                extensions={"sni_hostname": target.host} if target.scheme == "https" else {},
            )
        try:
            response = await client.send(request, stream=True)
        except httpx.ProxyError as e:
            raise BlockedURL(f"egress proxy refused {target.host}: {e}", target.url) from e
        except httpx.TimeoutException as e:
            raise FetchError(f"timeout fetching {target.url}") from e
        except httpx.HTTPError as e:
            raise FetchError(f"error fetching {target.url}: {type(e).__name__}") from e
        try:
            proxy_err = response.headers.get(PROXY_ERROR_HEADER)
            if proxy_err:
                raise BlockedURL(f"egress proxy: {proxy_err[:300]}", target.url)
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self.max_bytes:
                raise FetchError(f"response too large ({declared} bytes)")
            buf = bytearray()
            async for chunk in response.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > self.max_bytes:
                    raise FetchError(f"response exceeded {self.max_bytes} bytes")
            return (
                response.status_code,
                {k.lower(): v for k, v in response.headers.items()},
                bytes(buf),
            )
        finally:
            await response.aclose()


def trusted_client(base_url: str, **kwargs: object) -> httpx.AsyncClient:
    """Client for a *configured* provider endpoint. Refuses requests to any other host."""
    allowed_host = httpx.URL(base_url).host

    async def _pin_host(request: httpx.Request) -> None:
        if request.url.host != allowed_host:
            raise BlockedURL(f"trusted client for {allowed_host} refused {request.url.host}")

    return httpx.AsyncClient(
        base_url=base_url,
        trust_env=False,
        follow_redirects=False,
        event_hooks={"request": [_pin_host]},
        **kwargs,  # type: ignore[arg-type]
    )
