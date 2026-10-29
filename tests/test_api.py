import asyncio
import json
import logging
import socket
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from app.runtime import AppRuntime
from app.server import MAX_REQUEST_BODY_BYTES, create_app
from seo_audit.config import Settings
from seo_audit.extract import extract_facts
from seo_audit.report import build_report
from seo_audit.security import FetchedPage, FetchError, RobotsDisallowed, UnsafeURL
from seo_audit.service import AuditService


CODE = "private-test-access-code"
URL = "https://example.com/review"
FIXTURES = Path(__file__).parent / "fixtures"


def settings(**changes):
    return Settings.from_env({"ACCESS_CODE": CODE, "MODEL_NAME": "offline-test-model",
                              "CONTACT_URL": "https://agency.example/contact", "DEV_STUB_MODEL": "true"} | changes)


def code_report(url=URL):
    facts = extract_facts("<title>Test page</title><h1>Page</h1>", url=url).facts
    return build_report(facts, [], None, model_name="offline-test-model", ai_review_available=False)


class Backend:
    def __init__(self, error=None):
        self.calls = 0
        self.error = error
        self.active = self.peak = self.cancelled = 0
        self.wait = False
        self.gate = asyncio.Event()

    async def audit(self, url):
        self.calls += 1
        self.active += 1
        self.peak = max(self.active, self.peak)
        try:
            if self.wait:
                await self.gate.wait()
            if self.error:
                raise self.error
            return code_report(url)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1


def test_app(backend=None, **options):
    backend = backend or Backend()
    return create_app(settings=settings(), runtime_factory=lambda config: AppRuntime(backend), **options), backend


# This is a helper, not a test case.
test_app.__test__ = False


def test_form_health_and_security_headers():
    app, _ = test_app()
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert 'type="password"' in page.text and 'action="/audit"' in page.text
        assert CODE not in page.text
        assert client.get("/healthz").json() == {"status": "ok"}
        assert page.headers["cache-control"] == "no-store"
        assert page.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]


@pytest.mark.parametrize("path", ["/audit", "/api/audit"])
@pytest.mark.parametrize("provided", [None, "", "incorrect", 123, "unicode-é"])
def test_auth_rejects_wrong_or_missing_code_before_audit(path, provided):
    app, backend = test_app()
    payload = {"url": URL}
    if provided is not None:
        payload["access_code"] = provided
    with TestClient(app) as client:
        response = client.post(path, **({"json": payload} if path.startswith("/api") else {"data": payload}))
    assert response.status_code == 401
    assert backend.calls == 0
    assert CODE not in response.text


@pytest.mark.parametrize("path", ["/audit", "/api/audit"])
def test_authorized_request_returns_appropriate_format_and_stub_notice(path):
    app, backend = test_app()
    payload = {"url": URL, "access_code": CODE}
    with TestClient(app) as client:
        response = client.post(path, **({"json": payload} if path.startswith("/api") else {"data": payload}))
    assert response.status_code == 200 and backend.calls == 1
    if path.startswith("/api"):
        assert response.json()["url"] == URL
        assert response.json()["review_mode"] == "stub"
    else:
        assert "SEO audit report" in response.text
        assert "no real AI review" in response.text
    assert CODE not in response.text


@pytest.mark.parametrize("failure,status", [
    (UnsafeURL("PRIVATE_DETAIL"), 400), (RobotsDisallowed("PRIVATE_DETAIL"), 403),
    (FetchError("PRIVATE_DETAIL"), 502), (RuntimeError("PRIVATE_DETAIL"), 500),
])
@pytest.mark.parametrize("path", ["/audit", "/api/audit"])
def test_friendly_errors_do_not_reveal_internal_details(failure, status, path):
    app, _ = test_app(Backend(error=failure))
    with TestClient(app, raise_server_exceptions=False) as client:
        payload = {"url": URL, "access_code": CODE}
        response = client.post(path, **({"json": payload} if path.startswith("/api") else {"data": payload}))
    assert response.status_code == status
    assert "PRIVATE_DETAIL" not in response.text and "Traceback" not in response.text


@pytest.mark.parametrize("payload", [{"access_code": CODE}, {"url": 42, "access_code": CODE},
                                      {"url": URL, "access_code": CODE, "extra": "private"}])
def test_invalid_input_is_a_sanitized_400(payload):
    app, backend = test_app()
    with TestClient(app) as client:
        response = client.post("/api/audit", json=payload)
    assert response.status_code == 400 and backend.calls == 0
    assert CODE not in response.text


@pytest.mark.parametrize("body", [b"{", b"[]", b'{"access_code":"one","access_code":"two"}'])
def test_malformed_or_ambiguous_json_is_rejected(body):
    app, backend = test_app()
    with TestClient(app) as client:
        response = client.post("/api/audit", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 400 and backend.calls == 0


def test_request_body_is_bounded_before_auth_or_work():
    app, backend = test_app()
    with TestClient(app) as client:
        response = client.post("/api/audit", content=b"x" * (MAX_REQUEST_BODY_BYTES + 1),
                               headers={"content-type": "application/json"})
    assert response.status_code == 413 and backend.calls == 0


def test_form_multipart_is_supported_without_file_uploads():
    app, backend = test_app()
    with TestClient(app) as client:
        response = client.post("/audit", files={"url": (None, URL), "access_code": (None, CODE)})
        rejected = client.post("/audit", files={"upload": ("private.txt", b"private")})
    assert response.status_code == 200 and backend.calls == 1
    assert rejected.status_code == 400


def test_sliding_window_and_untrusted_forwarded_headers():
    now = [0.0]
    app, backend = test_app(clock=lambda: now[0])
    with TestClient(app) as client:
        for index in range(5):
            assert client.post("/api/audit", json={"url": URL, "access_code": CODE},
                               headers={"x-forwarded-for": f"198.51.100.{index}"}).status_code == 200
        limited = client.post("/api/audit", json={"url": URL, "access_code": CODE})
        assert limited.status_code == 429 and int(limited.headers["retry-after"]) == 3600
        now[0] = 3600
        assert client.post("/api/audit", json={"url": URL, "access_code": CODE}).status_code == 200
    assert backend.calls == 6


async def test_semaphore_limits_active_work_to_two():
    backend = Backend()
    backend.wait = True
    app, _ = test_app(backend)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            tasks = [asyncio.create_task(client.post("/api/audit", json={"url": URL, "access_code": CODE})) for _ in range(3)]
            async with asyncio.timeout(5):
                while backend.calls < 2:
                    await asyncio.sleep(0.01)
            await asyncio.sleep(0.03)
            assert backend.calls == 2 and backend.peak == 2
            backend.gate.set()
            assert all(response.status_code == 200 for response in await asyncio.gather(*tasks))
    assert backend.active == 0


def test_timeout_cancels_work_and_releases_the_permit():
    backend = Backend()
    backend.wait = True
    app, _ = test_app(backend, request_timeout_seconds=0.2)
    with TestClient(app) as client:
        assert client.post("/api/audit", json={"url": URL, "access_code": CODE}).status_code == 504
        assert backend.cancelled == 1 and backend.active == 0
        backend.wait = False
        assert client.post("/api/audit", json={"url": URL, "access_code": CODE}).status_code == 200


def test_logs_are_json_and_never_include_code_page_url_or_error(caplog, capsys):
    app, _ = test_app(Backend(error=RuntimeError("PRIVATE_ERROR")))
    caplog.set_level(logging.DEBUG)
    with TestClient(app) as client:
        client.post("/api/audit?PRIVATE_QUERY", json={"url": URL + "?PRIVATE_PAGE_QUERY", "access_code": CODE})
    output = capsys.readouterr().out
    for secret in (CODE, "PRIVATE_ERROR", "PRIVATE_QUERY", "PRIVATE_PAGE_QUERY"):
        assert secret not in output and secret not in caplog.text
    records = [json.loads(line) for line in output.splitlines() if line.strip()]
    assert any(record["event"] == "audit_request_finished" and record["status"] == 500 for record in records)


class HTTPStubLlm(BaseLlm):
    fail: bool = False
    delay: float = 0

    async def generate_content_async(self, llm_request, stream=False):
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ConnectionError("PRIVATE_MODEL_ERROR")
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text='{"issues": []}')]))


@pytest.mark.parametrize("fail", [False, True])
def test_http_route_runs_real_workflow_with_stub_and_recovers(fail, monkeypatch):
    def resolve(host, port, *, type):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    fixture = (FIXTURES / "xss_title.html").read_text()

    async def fetch(url, *, contact_url):
        return FetchedPage(url, fixture, "text/html")

    service = AuditService(model=HTTPStubLlm(model="offline-test-model", fail=fail),
                           contact_url="https://agency.example/contact", fetcher=fetch, retry_delay_seconds=0.001)
    app = create_app(settings=settings(), runtime_factory=lambda config: AppRuntime(service))
    with TestClient(app) as client:
        response = client.post("/audit", data={"url": URL, "access_code": CODE})
    assert response.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<script" not in response.text.lower()
    assert ("AI review unavailable" in response.text) is fail
    assert "PRIVATE_MODEL_ERROR" not in response.text


def test_invalid_unicode_code_is_unauthorized_not_a_server_error():
    app, backend = test_app()
    with TestClient(app) as client:
        response = client.post("/api/audit", content=b'{"url":"https://example.com/","access_code":"\\ud800"}',
                               headers={"content-type": "application/json"})
    assert response.status_code == 401 and backend.calls == 0


def test_unauthorized_requests_do_not_consume_the_audit_allowance():
    app, backend = test_app()
    with TestClient(app) as client:
        for _ in range(6):
            assert client.post("/api/audit", json={"url": URL, "access_code": "wrong"}).status_code == 401
        for _ in range(5):
            assert client.post("/api/audit", json={"url": URL, "access_code": CODE}).status_code == 200
    assert backend.calls == 5


def test_html_and_json_share_the_same_quota():
    app, _ = test_app()
    with TestClient(app) as client:
        for _ in range(3):
            assert client.post("/api/audit", json={"url": URL, "access_code": CODE}).status_code == 200
        for _ in range(2):
            assert client.post("/audit", data={"url": URL, "access_code": CODE}).status_code == 200
        assert client.post("/audit", data={"url": URL, "access_code": CODE}).status_code == 429


async def test_distinct_asgi_peer_ips_have_separate_windows():
    app, backend = test_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("198.51.100.1", 1)), base_url="http://testserver") as first:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("198.51.100.2", 2)), base_url="http://testserver") as second:
                for _ in range(5):
                    assert (await first.post("/api/audit", json={"url": URL, "access_code": CODE})).status_code == 200
                assert (await first.post("/api/audit", json={"url": URL, "access_code": CODE})).status_code == 429
                assert (await second.post("/api/audit", json={"url": URL, "access_code": CODE})).status_code == 200
    assert backend.calls == 6


async def test_disconnect_cancels_active_work_and_releases_permit():
    backend = Backend()
    backend.wait = True
    app, _ = test_app(backend)
    payload = json.dumps({"url": URL, "access_code": CODE}).encode()
    messages, incoming = [], asyncio.Queue()
    await incoming.put({"type": "http.request", "body": payload, "more_body": False})
    scope = {"type": "http", "http_version": "1.1", "asgi": {"version": "3.0"},
             "method": "POST", "scheme": "http", "path": "/api/audit", "raw_path": b"/api/audit",
             "query_string": b"", "headers": [(b"content-type", b"application/json")],
             "client": ("198.51.100.1", 1), "server": ("testserver", 80)}

    async def send(message):
        messages.append(message)

    async with app.router.lifespan_context(app):
        task = asyncio.create_task(app(scope, incoming.get, send))
        async with asyncio.timeout(5):
            while not backend.active:
                await asyncio.sleep(0.01)
            await incoming.put({"type": "http.disconnect"})
            await task
        assert backend.cancelled == 1 and backend.active == 0
        assert next(message["status"] for message in messages if message["type"] == "http.response.start") == 499
        backend.wait = False
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            assert (await client.post("/api/audit", json={"url": URL, "access_code": CODE})).status_code == 200


async def test_queued_requests_time_out_and_release_all_permits():
    backend = Backend()
    backend.wait = True
    app, _ = test_app(backend, request_timeout_seconds=0.2)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            responses = await asyncio.gather(*(client.post("/api/audit", json={"url": URL, "access_code": CODE}) for _ in range(3)))
            assert all(response.status_code == 504 for response in responses)
            assert backend.peak == 2 and backend.active == 0
            backend.wait = False
            assert (await client.post("/api/audit", json={"url": URL, "access_code": CODE})).status_code == 200


async def test_chunked_body_limit_is_enforced_without_content_length():
    app, backend = test_app()

    async def chunks():
        for _ in range(3):
            yield b"x" * (MAX_REQUEST_BODY_BYTES // 2)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post("/api/audit", content=chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 413 and backend.calls == 0


def test_runtime_is_closed_at_shutdown_even_after_failed_audit():
    closed = []

    async def close():
        closed.append(True)

    app = create_app(settings=settings(), runtime_factory=lambda config: AppRuntime(Backend(RuntimeError("private")), close))
    with TestClient(app) as client:
        assert client.post("/api/audit", json={"url": URL, "access_code": CODE}).status_code == 500
    assert closed == [True]


def test_default_stub_runtime_runs_safe_fetch_and_workflow_without_model_credentials(monkeypatch):
    def resolve(host, port, *, type):
        assert host == "example.com"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    original = httpx.AsyncClient

    def handle(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"}, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<h1>Fetched page</h1>")

    def client(**kwargs):
        return original(**(kwargs | {"transport": httpx.MockTransport(handle)}))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    app = create_app(settings=settings())
    with TestClient(app) as browser:
        response = browser.post("/api/audit", json={"url": URL, "access_code": CODE})
    assert response.status_code == 200
    assert response.json()["review_mode"] == "stub"
    assert response.json()["facts"]["h1_count"] == 1


def test_health_in_vertex_mode_does_not_initialize_adc_or_model_client(monkeypatch):
    from google.adk.models.google_llm import Gemini

    def forbidden(model):
        raise AssertionError("Health must not initialize the model client")

    monkeypatch.setattr(Gemini, "api_client", property(forbidden))
    config = settings(DEV_STUB_MODEL="false", GOOGLE_CLOUD_PROJECT="test-project", GOOGLE_CLOUD_LOCATION="test-location")
    with TestClient(create_app(settings=config)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


def test_http_deadline_cleans_real_adk_session_and_next_request_can_run(monkeypatch):
    from google.adk.sessions import InMemorySessionService

    def resolve(host, port, *, type):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    async def fetch(url, *, contact_url):
        return FetchedPage(url, "<h1>Page</h1>", "text/html")

    class Sessions(InMemorySessionService):
        def __init__(self):
            super().__init__()
            self.created = []
            self.deleted = []

        async def create_session(self, **kwargs):
            self.created.append(kwargs["session_id"])
            return await super().create_session(**kwargs)

        async def delete_session(self, **kwargs):
            await super().delete_session(**kwargs)
            assert await self.get_session(**kwargs) is None
            self.deleted.append(kwargs["session_id"])

    sessions = Sessions()
    model = HTTPStubLlm(model="offline-test-model", delay=10)
    service = AuditService(model=model, contact_url="https://agency.example/contact", fetcher=fetch,
                           session_service=sessions, retry_delay_seconds=0.001)
    app = create_app(settings=settings(), runtime_factory=lambda config: AppRuntime(service), request_timeout_seconds=0.5)
    with TestClient(app) as client:
        assert client.post("/api/audit", json={"url": URL, "access_code": CODE}).status_code == 504
        assert sessions.created == sessions.deleted and len(sessions.created) == 1
        model.delay = 0
        assert client.post("/api/audit", json={"url": URL, "access_code": CODE}).status_code == 200
    assert sessions.created == sessions.deleted and len(sessions.created) == 2


def test_service_failure_does_not_leak_or_hold_a_semaphore_slot():
    backend = Backend(error=RuntimeError("private"))
    app, _ = test_app(backend)
    with TestClient(app) as client:
        for _ in range(3):
            assert client.post("/api/audit", json={"url": URL, "access_code": CODE}).status_code == 500
        backend.error = None
        assert client.post("/api/audit", json={"url": URL, "access_code": CODE}).status_code == 200
    assert backend.active == 0


def test_missing_startup_settings_fail_safely_before_runtime_creation(monkeypatch, capsys):
    from seo_audit.config import ConfigurationError

    for key in ("MODEL_NAME", "CONTACT_URL", "GOOGLE_API_KEY", "GEMINI_API_KEY", "K_SERVICE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ACCESS_CODE", CODE)

    def forbidden(config):
        raise AssertionError("Invalid settings must fail before runtime creation")

    with pytest.raises(ConfigurationError):
        with TestClient(create_app(runtime_factory=forbidden)):
            pass
    output = capsys.readouterr().out
    assert CODE not in output
    assert any(json.loads(line)["event"] == "configuration_error" for line in output.splitlines())
