"""Bounded public-page fetching. DNS validation is not connection pinning."""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from seo_audit.robots import RobotsPolicy


MAX_URL_LENGTH = 2048
MAX_REDIRECTS = 3
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_ROBOTS_BYTES = 512 * 1024
CONNECT_TIMEOUT_SECONDS = 5
FETCH_TIMEOUT_SECONDS = 10
PRODUCT_TOKEN = "SEO-Audit-Demo"
_REDIRECTS = {301, 302, 303, 307, 308}
_METADATA_HOST = "metadata.google.internal"


class UnsafeURL(ValueError):
    """Malformed URL or a destination outside the public Internet."""


class FetchError(Exception):
    """A friendly fetch failure suitable for an HTTP 502 response."""


class TransientFetchError(FetchError, ConnectionError):
    """A connection, timeout, throttling, or remote server failure worth retrying."""


class RobotsDisallowed(PermissionError):
    """The origin's robots policy disallows the requested page (HTTP 403)."""


@dataclass(frozen=True)
class FetchedPage:
    url: str
    html: str
    content_type: str


@dataclass(frozen=True)
class _Resource:
    url: str
    status: int
    body: bytes
    content_type: str
    encoding: str


def _parse_url(value: str) -> httpx.URL:
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        raise UnsafeURL("Enter an HTTP or HTTPS URL of at most 2048 characters.")
    # Both urllib and HTTP clients normalize some controls. Reject before parsing.
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        raise UnsafeURL("URLs must not contain whitespace, controls, or backslashes.")
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise ValueError
        if parts.username is not None or parts.password is not None:
            raise ValueError
        if parts.port not in {None, 80, 443} or parts.netloc.endswith(":"):
            raise ValueError
        url = httpx.URL(value).copy_with(fragment=None)
        if not url.raw_host or b"%" in url.raw_host:
            raise ValueError
        if len(str(url)) > MAX_URL_LENGTH:
            raise ValueError
        return url
    except (ValueError, UnicodeError, httpx.InvalidURL):
        raise UnsafeURL("Use a valid public HTTP or HTTPS URL without credentials, on port 80 or 443.") from None


async def validate_url(url: str) -> str:
    """Normalize a URL and check *every* resolved IP. Does not fetch the URL."""
    parsed = _parse_url(url)
    hostname = parsed.raw_host.decode("ascii").rstrip(".").lower()
    if hostname == _METADATA_HOST:
        raise UnsafeURL("The cloud metadata host is blocked.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo, hostname, port, type=socket.SOCK_STREAM,
        )
    except OSError:
        raise TransientFetchError("The page hostname could not be resolved.") from None
    if not addresses:
        raise FetchError("The page hostname did not resolve to an address.")
    for family, _, _, _, sockaddr in addresses:
        try:
            if family not in {socket.AF_INET, socket.AF_INET6}:
                raise ValueError
            address = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            raise UnsafeURL("The hostname returned an unsupported address.") from None
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if (
            str(address) == "169.254.169.254" or not address.is_global
            or address.is_private or address.is_loopback or address.is_link_local
            or address.is_multicast or address.is_reserved or address.is_unspecified
            or getattr(address, "is_site_local", False)
        ):
            raise UnsafeURL("The URL resolves to a blocked or non-public address.")
    return str(parsed)


def _user_agent(contact_url: str) -> str:
    # Configuration validation only: the contact is never fetched or resolved.
    if not isinstance(contact_url, str) or not contact_url or len(contact_url) > 2048:
        raise ValueError("CONTACT_URL must be an HTTP or HTTPS contact URL.")
    if any(ord(char) <= 32 or ord(char) >= 127 for char in contact_url) or any(
        char in contact_url for char in "()\\"
    ):
        raise ValueError("CONTACT_URL must be an ASCII URL without whitespace or controls.")
    try:
        parts = urlsplit(contact_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None:
            raise ValueError
        parts.port  # Validate syntax even though this URL is not fetched.
    except ValueError:
        raise ValueError("CONTACT_URL must be a valid HTTP or HTTPS contact URL.") from None
    return f"{PRODUCT_TOKEN}/1.0 (+contact: {contact_url})"


def validate_contact_url(value: str) -> str:
    """Validate startup configuration without resolving or fetching the contact."""
    _user_agent(value)
    return value


async def _fetch_resource(
    client: httpx.AsyncClient,
    url: str,
    *,
    robots: bool,
    before_request: Callable[[str], Awaitable[None]] | None = None,
) -> _Resource:
    label = "robots.txt" if robots else "page"
    limit = MAX_ROBOTS_BYTES if robots else MAX_BODY_BYTES
    allowed_types = {"text/plain"} if robots else {"text/html", "application/xhtml+xml"}
    for redirects in range(MAX_REDIRECTS + 1):
        checked = await validate_url(url)
        if before_request is not None:
            await before_request(checked)
        # A separate client per safe_fetch plus clearing before every hop prevents
        # cookies set by robots or redirects from being replayed on later requests.
        client.cookies.clear()
        try:
            async with client.stream("GET", checked) as response:
                if response.status_code in _REDIRECTS:
                    location = response.headers.get("location", "")
                    if not location or redirects == MAX_REDIRECTS:
                        raise FetchError(f"The {label} redirect chain is invalid or exceeds 3 redirects.")
                    url = urljoin(checked, location)
                    continue
                if robots and response.status_code in {404, 410}:
                    return _Resource(checked, response.status_code, b"", "", "utf-8")
                if response.status_code == 429 or response.status_code >= 500:
                    raise TransientFetchError(f"The {label} server is temporarily unavailable.")
                if not 200 <= response.status_code < 300:
                    raise FetchError(f"The {label} could not be fetched (HTTP {response.status_code}).")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in allowed_types:
                    raise FetchError(f"The {label} has an unsupported content type.")
                # Request identity and refuse compressed responses rather than
                # letting decompression allocate an unbounded output buffer.
                if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                    raise FetchError(f"The {label} has an unsupported content encoding.")
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError:
                        raise FetchError(f"The {label} has an invalid content length.") from None
                    if declared_length < 0 or declared_length > limit:
                        raise FetchError(f"The {label} exceeds the size limit.")
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(chunks) + len(chunk) > limit:
                        raise FetchError(f"The {label} exceeds the size limit.")
                    chunks.extend(chunk)
                return _Resource(checked, response.status_code, bytes(chunks),
                                 content_type, response.encoding or "utf-8")
        except httpx.HTTPError:
            raise TransientFetchError(f"The {label} could not be fetched due to a connection failure.") from None
    raise AssertionError("Unreachable redirect state")


async def safe_fetch(
    url: str,
    *,
    contact_url: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FetchedPage:
    """Fetch one page, honoring robots on every page hop within a total 10s.

    `transport` is an injection point for offline tests, not a server input.
    DNS is checked separately from HTTPX's connection; see README for the
    residual DNS-rebinding risk. No page resources are followed.
    """
    user_agent = _user_agent(contact_url)
    policies: dict[str, RobotsPolicy] = {}
    try:
        async with asyncio.timeout(FETCH_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                headers={"user-agent": user_agent, "accept-encoding": "identity"},
                follow_redirects=False, trust_env=False, verify=True,
                timeout=httpx.Timeout(FETCH_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=0),
                transport=transport,
            ) as client:
                async def check_robots(target: str) -> None:
                    robots_url = str(httpx.URL(target).copy_with(path="/robots.txt", query=None))
                    if robots_url not in policies:
                        resource = await _fetch_resource(client, robots_url, robots=True)
                        try:
                            text = resource.body.decode("utf-8-sig")
                        except UnicodeDecodeError:
                            raise FetchError("The robots.txt file is not valid UTF-8; audit refused.") from None
                        policies[robots_url] = RobotsPolicy.from_text(text, PRODUCT_TOKEN)
                    if not policies[robots_url].allows(target):
                        raise RobotsDisallowed("This path is disallowed by robots.txt for SEO-Audit-Demo.")

                resource = await _fetch_resource(client, url, robots=False, before_request=check_robots)
                try:
                    html = resource.body.decode(resource.encoding, errors="replace")
                except LookupError:
                    html = resource.body.decode("utf-8", errors="replace")
                return FetchedPage(resource.url, html, resource.content_type)
    except TimeoutError:
        raise TransientFetchError("The page fetch timed out, including DNS, robots.txt, and redirects.") from None
