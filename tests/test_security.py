"""Fetch boundaries: all DNS and HTTP traffic is mocked."""

import asyncio
import ipaddress
import socket

import httpx
import pytest

from seo_audit import security
from seo_audit.security import FetchError, RobotsDisallowed, UnsafeURL, safe_fetch, validate_url


PUBLIC_IP = "93.184.216.34"
CONTACT = "https://agency.example/contact"


@pytest.fixture
def dns(monkeypatch):
    answers = {"example.com": [PUBLIC_IP], "other.example": [PUBLIC_IP]}
    calls = []

    def resolve(host, port, *, type):
        calls.append((host, port))
        assert type == socket.SOCK_STREAM
        addresses = answers.get(host)
        if addresses is None:
            try:
                addresses = [str(ipaddress.ip_address(host))]
            except ValueError:
                raise AssertionError(f"Unmocked DNS hostname: {host}") from None
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET,
             socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))
            for address in addresses
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    return answers, calls


def html_response(text="<html><body>Public page</body></html>", **kwargs):
    return httpx.Response(200, headers={"content-type": "text/html"}, text=text, **kwargs)


def robots_response(text="User-agent: *\nAllow: /\n", status=200, headers=None):
    return httpx.Response(status, headers=headers or {"content-type": "text/plain"}, text=text)


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.1.2.3", "172.16.1.1", "192.168.1.1", "169.254.169.254",
    "169.254.1.2", "0.0.0.0", "224.0.0.1", "240.0.0.1", "100.64.0.1",
    "192.0.2.1", "::", "::1", "fe80::1", "fc00::1", "ff02::1",
    "2001:db8::1", "fec0::1", "::ffff:127.0.0.1", "::ffff:169.254.169.254",
])
async def test_blocks_every_nonpublic_address(dns, address):
    dns[0]["example.com"] = [address]
    with pytest.raises(UnsafeURL):
        await validate_url("https://example.com/")


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/", "https://user:secret@example.com/",
    "https://user@example.com/", "https://@example.com/", "https://example.com:8080/",
    "https://example.com:0/", "https://example.com:65536/", "https://example.com:/",
    "https:///missing-host", "//example.com/", "https://example.com/\r\nx: y",
    " https://example.com/", "https://example.com/a b", "https://example.com\\@127.0.0.1/",
    "http://[fe80::1%25eth0]/", "https://%31%32%37.0.0.1/", "http://[broken/",
    "https://example.com/" + "a" * 2048,
])
async def test_rejects_malformed_or_forbidden_urls_before_dns(dns, url):
    with pytest.raises(UnsafeURL):
        await validate_url(url)
    assert dns[1] == []


@pytest.mark.parametrize("host", [
    "metadata.google.internal", "METADATA.GOOGLE.INTERNAL", "metadata.google.internal.",
])
async def test_blocks_metadata_hostname_without_dns(dns, host):
    with pytest.raises(UnsafeURL):
        await validate_url(f"http://{host}/computeMetadata/v1/")
    assert dns[1] == []


@pytest.mark.parametrize("host", ["2130706433", "0x7f000001", "0177.0.0.1", "localhost"])
async def test_blocks_obfuscated_loopback_through_dns(dns, host):
    dns[0][host] = ["127.0.0.1"]
    with pytest.raises(UnsafeURL):
        await validate_url(f"http://{host}/")


async def test_mixed_dns_answers_block_entire_host(dns):
    dns[0]["example.com"] = [PUBLIC_IP, "2606:4700:4700::1111", "127.0.0.1"]
    with pytest.raises(UnsafeURL):
        await validate_url("https://example.com/")


@pytest.mark.parametrize("url,expected", [
    ("HTTPS://EXAMPLE.COM:443/path?q=one#fragment", "https://example.com/path?q=one"),
    ("http://example.com:80/", "http://example.com/"),
    ("http://93.184.216.34/", "http://93.184.216.34/"),
    ("https://[2606:4700:4700::1111]/", "https://[2606:4700:4700::1111]/"),
])
async def test_accepts_and_normalizes_public_urls(dns, url, expected):
    assert await validate_url(url) == expected


async def test_empty_dns_answer_refuses_fetch(dns):
    dns[0]["example.com"] = []
    with pytest.raises(FetchError):
        await validate_url("https://example.com/")


async def test_dns_error_is_friendly(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("internal resolver detail")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(FetchError) as error:
        await validate_url("https://example.com/")
    assert "internal resolver detail" not in str(error.value)


async def test_robots_checked_before_page_and_client_is_hardened(dns, monkeypatch):
    calls, options = [], {}
    original_client = httpx.AsyncClient

    def observed_client(**kwargs):
        options.update(kwargs)
        return original_client(**kwargs)

    monkeypatch.setattr(security.httpx, "AsyncClient", observed_client)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")

    def handle(request):
        calls.append(str(request.url))
        assert request.headers["user-agent"] == f"SEO-Audit-Demo/1.0 (+contact: {CONTACT})"
        assert request.extensions["timeout"]["connect"] == 5
        assert request.headers["accept-encoding"] == "identity"
        return robots_response() if request.url.path == "/robots.txt" else html_response()

    result = await safe_fetch("https://example.com/page#ignored", contact_url=CONTACT,
                              transport=httpx.MockTransport(handle))
    assert calls == ["https://example.com/robots.txt", "https://example.com/page"]
    assert result.url == "https://example.com/page"
    assert "Public page" in result.html
    assert result.content_type == "text/html"
    assert options["trust_env"] is False
    assert options["follow_redirects"] is False
    assert options["verify"] is True


async def test_redirect_rechecks_destination_robots(dns):
    calls = []

    def handle(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return robots_response()
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "https://other.example/final"})
        return html_response()

    result = await safe_fetch("https://example.com/start", contact_url=CONTACT,
                              transport=httpx.MockTransport(handle))
    assert result.url == "https://other.example/final"
    assert calls == ["https://example.com/robots.txt", "https://example.com/start",
                     "https://other.example/robots.txt", "https://other.example/final"]


@pytest.mark.parametrize("location", [
    "http://127.0.0.1/", "http://[::ffff:127.0.0.1]/", "http://metadata.google.internal/",
    "https://other.example:8080/", "https://user:password@other.example/",
])
async def test_redirect_to_unsafe_target_never_requested(dns, location):
    calls = []

    def handle(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return robots_response()
        return httpx.Response(302, headers={"location": location})

    with pytest.raises(UnsafeURL):
        await safe_fetch("https://example.com/", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))
    assert calls == ["https://example.com/robots.txt", "https://example.com/"]


@pytest.mark.parametrize("redirects", [3, 4])
async def test_redirect_limit_and_relative_locations(dns, redirects):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return robots_response()
        index = int(request.url.path[1:])
        if index < redirects:
            return httpx.Response(301, headers={"location": f"/{index + 1}"})
        return html_response()

    call = safe_fetch("https://example.com/0", contact_url=CONTACT,
                      transport=httpx.MockTransport(handle))
    if redirects == 3:
        assert (await call).url == "https://example.com/3"
    else:
        with pytest.raises(FetchError, match="redirect"):
            await call
        assert "/4" not in paths
    assert paths.count("/robots.txt") == 1


async def test_redirect_missing_location_is_rejected(dns):
    def handle(request):
        return robots_response() if request.url.path == "/robots.txt" else httpx.Response(302)

    with pytest.raises(FetchError, match="redirect"):
        await safe_fetch("https://example.com/", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))


@pytest.mark.parametrize("mime", ["text/html; charset=utf-8", "application/xhtml+xml", "TEXT/HTML"])
async def test_accepts_html_content_types(dns, mime):
    def handle(request):
        if request.url.path == "/robots.txt":
            return robots_response()
        return httpx.Response(200, headers={"content-type": mime}, content=b"<p>ok</p>")

    assert (await safe_fetch("https://example.com/", contact_url=CONTACT,
                            transport=httpx.MockTransport(handle))).html == "<p>ok</p>"


@pytest.mark.parametrize("mime", ["application/json", "text/plain", "image/png", "text/htmlx", ""])
async def test_rejects_non_html_or_missing_content_type(dns, mime):
    def handle(request):
        if request.url.path == "/robots.txt":
            return robots_response()
        return httpx.Response(200, headers={"content-type": mime}, content=b"payload")

    with pytest.raises(FetchError, match="content type"):
        await safe_fetch("https://example.com/", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    async def aclose(self):
        self.closed = True


async def test_stream_aborts_oversized_body_and_closes_connection(dns):
    stream = ChunkStream([b"a" * (2 * 1024 * 1024), b"b", b"never read"])

    def handle(request):
        if request.url.path == "/robots.txt":
            return robots_response()
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=stream)

    with pytest.raises(FetchError, match="size limit"):
        await safe_fetch("https://example.com/", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))
    assert stream.reads == 2
    assert stream.closed


async def test_exact_body_limit_is_accepted(dns):
    def handle(request):
        if request.url.path == "/robots.txt":
            return robots_response()
        return html_response("a" * (2 * 1024 * 1024))

    result = await safe_fetch("https://example.com/", contact_url=CONTACT,
                              transport=httpx.MockTransport(handle))
    assert len(result.html) == 2 * 1024 * 1024


@pytest.mark.parametrize("rules", [
    "User-agent: *\nDisallow: /private\n",
    "User-agent: SEO-Audit-Demo\nDisallow: /private\n",
])
async def test_robots_disallow_never_fetches_page(dns, rules):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        return robots_response(rules)

    with pytest.raises(RobotsDisallowed, match="robots.txt"):
        await safe_fetch("https://example.com/private/report?q=1", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))
    assert paths == ["/robots.txt"]


async def test_relative_redirect_into_disallowed_path_is_blocked(dns):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return robots_response("User-agent: *\nDisallow: /private\n")
        return httpx.Response(302, headers={"location": "/private"})

    with pytest.raises(RobotsDisallowed):
        await safe_fetch("https://example.com/public", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))
    assert paths == ["/robots.txt", "/public"]


@pytest.mark.parametrize("status", [404, 410])
async def test_missing_robots_allows_page(dns, status):
    def handle(request):
        return robots_response(status=status) if request.url.path == "/robots.txt" else html_response()

    assert (await safe_fetch("https://example.com/", contact_url=CONTACT,
                            transport=httpx.MockTransport(handle))).html


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_robots_errors_fail_closed(dns, status):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        return robots_response(status=status)

    with pytest.raises(FetchError, match="robots.txt"):
        await safe_fetch("https://example.com/", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))
    assert paths == ["/robots.txt"]


@pytest.mark.parametrize("mode", ["private", "loop", "oversized", "wrong_type", "network"])
async def test_robots_uses_bounded_safe_fetch_path(dns, mode):
    calls = []

    def handle(request):
        calls.append(str(request.url))
        assert "robots" in request.url.path
        if mode == "private":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/robots.txt"})
        if mode == "loop":
            return httpx.Response(302, headers={"location": "/robots.txt"})
        if mode == "oversized":
            return robots_response("#" * (512 * 1024 + 1))
        if mode == "wrong_type":
            return robots_response(headers={"content-type": "application/json"})
        raise httpx.ConnectError("sensitive transport details", request=request)

    with pytest.raises((FetchError, UnsafeURL)) as error:
        await safe_fetch("https://example.com/", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))
    assert "sensitive transport details" not in str(error.value)
    assert len(calls) <= 4


async def test_cross_origin_robots_redirect_is_checked_without_recursion(dns):
    paths = []

    def handle(request):
        paths.append(str(request.url))
        if str(request.url) == "https://example.com/robots.txt":
            return httpx.Response(302, headers={"location": "https://other.example/robots-policy"})
        if request.url.host == "other.example":
            return robots_response("User-agent: *\nDisallow: /private\n")
        raise AssertionError("Disallowed page was fetched")

    with pytest.raises(RobotsDisallowed):
        await safe_fetch("https://example.com/private", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))
    assert paths == ["https://example.com/robots.txt", "https://other.example/robots-policy"]


async def test_cookies_are_not_forwarded_to_page_or_redirect(dns):
    def handle(request):
        assert "cookie" not in request.headers
        if request.url.path == "/robots.txt":
            return robots_response(headers={"content-type": "text/plain", "set-cookie": "token=secret; Path=/"})
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final", "set-cookie": "id=secret; Path=/"})
        return html_response()

    assert (await safe_fetch("https://example.com/start", contact_url=CONTACT,
                            transport=httpx.MockTransport(handle))).url.endswith("/final")


async def test_total_deadline_covers_robots_and_page(dns, monkeypatch):
    monkeypatch.setattr(security, "FETCH_TIMEOUT_SECONDS", 0.02)

    async def handle(request):
        await asyncio.sleep(0.1)
        return robots_response()

    with pytest.raises(FetchError, match="timed out"):
        await safe_fetch("https://example.com/", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))


async def test_page_transport_failure_does_not_expose_internal_details(dns):
    def handle(request):
        if request.url.path == "/robots.txt":
            return robots_response()
        raise httpx.ConnectError("internal network topology", request=request)

    with pytest.raises(FetchError) as error:
        await safe_fetch("https://example.com/", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))
    assert "internal network topology" not in str(error.value)


async def test_server_ignoring_identity_encoding_is_refused(dns):
    def handle(request):
        if request.url.path == "/robots.txt":
            return robots_response()
        return httpx.Response(200, headers={"content-type": "text/html", "content-encoding": "gzip"},
                              stream=ChunkStream([b"not even a valid gzip stream"]))

    with pytest.raises(FetchError, match="encoding"):
        await safe_fetch("https://example.com/", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))


@pytest.mark.parametrize("contact", ["", "https://example.com/\r\nX-Secret: leaked", "file:///contact"])
async def test_invalid_user_agent_configuration_fails_before_fetch(dns, contact):
    with pytest.raises(ValueError):
        await safe_fetch("https://example.com/", contact_url=contact)
    assert dns[1] == []


@pytest.mark.parametrize("length", ["-1", "not-a-number", str(2 * 1024 * 1024 + 1)])
async def test_invalid_or_oversized_declared_length_is_refused_before_reading(dns, length):
    stream = ChunkStream([b"should not read"])

    def handle(request):
        if request.url.path == "/robots.txt":
            return robots_response()
        return httpx.Response(200, headers={"content-type": "text/html", "content-length": length}, stream=stream)

    with pytest.raises(FetchError):
        await safe_fetch("https://example.com/", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))
    assert stream.reads == 0
    assert stream.closed


async def test_deadline_also_interrupts_a_slow_body_stream(dns, monkeypatch):
    # Allow enough setup time for Windows DNS worker startup; the first body
    # chunk deliberately takes longer than the entire request deadline.
    monkeypatch.setattr(security, "FETCH_TIMEOUT_SECONDS", 1)

    class SlowStream(ChunkStream):
        async def __aiter__(self):
            for chunk in self.chunks:
                await asyncio.sleep(2)
                self.reads += 1
                yield chunk

    stream = SlowStream([b"a"] * 10)

    def handle(request):
        if request.url.path == "/robots.txt":
            return robots_response()
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=stream)

    with pytest.raises(FetchError, match="timed out"):
        await safe_fetch("https://example.com/", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))
    assert stream.reads < 10
    assert stream.closed


async def test_deadline_also_covers_dns_resolution(monkeypatch):
    import time

    def resolve(*args, **kwargs):
        time.sleep(0.1)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_IP, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(security, "FETCH_TIMEOUT_SECONDS", 0.02)

    def handle(request):
        raise AssertionError("Timed-out DNS must not lead to an HTTP request")

    with pytest.raises(FetchError, match="timed out"):
        await safe_fetch("https://example.com/", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))


async def test_broken_stream_is_closed_and_error_is_sanitized(dns):
    class BrokenStream(ChunkStream):
        async def __aiter__(self):
            yield b"start"
            raise httpx.ReadError("sensitive stream detail")

    stream = BrokenStream([])

    def handle(request):
        if request.url.path == "/robots.txt":
            return robots_response()
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=stream)

    with pytest.raises(security.TransientFetchError) as error:
        await safe_fetch("https://example.com/", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))
    assert "sensitive stream detail" not in str(error.value)
    assert stream.closed


@pytest.mark.parametrize("charset,body,expected", [
    ("iso-8859-1", b"<p>caf\xe9</p>", "<p>café</p>"),
    ("unknown-charset", b"<p>utf8</p>", "<p>utf8</p>"),
])
async def test_html_charset_is_respected_with_utf8_fallback(dns, charset, body, expected):
    def handle(request):
        if request.url.path == "/robots.txt":
            return robots_response()
        return httpx.Response(200, headers={"content-type": f"text/html; charset={charset}"}, content=body)

    assert (await safe_fetch("https://example.com/", contact_url=CONTACT,
                            transport=httpx.MockTransport(handle))).html == expected


async def test_invalid_robots_utf8_fails_closed(dns):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"\xffUser-agent: *\nDisallow: /")

    with pytest.raises(FetchError, match="robots.txt"):
        await safe_fetch("https://example.com/", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))
    assert calls == ["/robots.txt"]


async def test_robots_utf8_bom_does_not_hide_disallow_rule(dns):
    def handle(request):
        assert request.url.path == "/robots.txt"
        return robots_response("\ufeffUser-agent: *\nDisallow: /")

    with pytest.raises(RobotsDisallowed):
        await safe_fetch("https://example.com/", contact_url=CONTACT,
                         transport=httpx.MockTransport(handle))


async def test_idn_is_resolved_as_the_same_ascii_host_httpx_requests(dns):
    dns[0]["xn--bcher-kva.example"] = [PUBLIC_IP]
    assert await validate_url("https://bücher.example/") == "https://xn--bcher-kva.example/"
    assert dns[1] == [("xn--bcher-kva.example", 443)]
