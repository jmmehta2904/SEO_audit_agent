"""Authenticated FastAPI adapter around the existing audit service."""

import asyncio
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect

from app.limits import RateLimitExceeded, SlidingWindowLimiter
from app.runtime import AppRuntime, create_runtime
from seo_audit.config import ConfigurationError, Settings
from seo_audit.logging_utils import configure_logging
from seo_audit.report import render_html, render_json
from seo_audit.security import FetchError, RobotsDisallowed, UnsafeURL


MAX_REQUEST_BODY_BYTES = 16 * 1024
STUB_NOTICE = "Local stub mode: no real AI review was performed."
_LOG = logging.getLogger("seo_audit.server")
_TEMPLATES = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                         autoescape=True, undefined=StrictUndefined)
_SECURITY_HEADERS = {
    "cache-control": "no-store", "x-content-type-options": "nosniff", "referrer-policy": "no-referrer",
    "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
}


class InputURL(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str = Field(min_length=1, max_length=2048)


class RequestFailure(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


class ResponseHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def secured(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in _SECURITY_HEADERS.items():
                    headers[key] = value
                headers["x-request-id"] = request_id
            await send(message)

        await self.app(scope, receive, secured)


def _error(request, status, message, headers=None):
    request_id = getattr(request.state, "request_id", uuid4().hex)
    response_headers = {**_SECURITY_HEADERS, "x-request-id": request_id, **(headers or {})}
    if request.url.path.startswith("/api/") or request.url.path == "/healthz":
        return JSONResponse({"detail": message, "request_id": request_id}, status_code=status, headers=response_headers)
    return HTMLResponse(_TEMPLATES.get_template("error.html").render(
        status=status, message=message, request_id=request_id,
    ), status_code=status, headers=response_headers)


async def _body(request):
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError:
            raise RequestFailure(400, "Invalid request body.") from None
        if length < 0:
            raise RequestFailure(400, "Invalid request body.")
        if length > MAX_REQUEST_BODY_BYTES:
            raise RequestFailure(413, "Request body exceeds the 16 KiB limit.")
    value = bytearray()
    async for chunk in request.stream():
        if len(value) + len(chunk) > MAX_REQUEST_BODY_BYTES:
            raise RequestFailure(413, "Request body exceeds the 16 KiB limit.")
        value.extend(chunk)
    return bytes(value)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise RequestFailure(400, "Duplicate input fields are not allowed.")
        value[key] = item
    return value


async def _payload(request, api):
    body = await _body(request)
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if api:
        if media != "application/json":
            raise RequestFailure(415, "Use application/json for the API.")
        try:
            value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            raise RequestFailure(400, "Invalid JSON request.") from None
        if not isinstance(value, dict):
            raise RequestFailure(400, "The JSON request must be an object.")
        return value
    if media not in {"application/x-www-form-urlencoded", "multipart/form-data"}:
        raise RequestFailure(415, "Submit the URL and access code using the form.")

    async def replay():
        return {"type": "http.request", "body": body, "more_body": False}

    try:
        # Reparse only the already-bounded bytes with Starlette's public parser.
        async with Request(request.scope, replay).form(max_files=0, max_fields=2, max_part_size=4096) as form:
            if any(len(form.getlist(key)) != 1 for key in form):
                raise RequestFailure(400, "Duplicate input fields are not allowed.")
            return dict(form)
    except (HTTPException, ValueError):
        raise RequestFailure(400, "Invalid form fields; file uploads are not supported.") from None


async def _until_disconnect(request, operation):
    async def watch():
        while True:
            message = await request.receive()
            if message["type"] == "http.disconnect":
                return

    work = asyncio.create_task(operation)
    disconnected = asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait((work, disconnected), return_when=asyncio.FIRST_COMPLETED)
        if work in done:
            return work.result()
        raise ClientDisconnect()
    finally:
        for task in (work, disconnected):
            if not task.done():
                task.cancel()
        await asyncio.gather(work, disconnected, return_exceptions=True)


def create_app(*, settings: Settings | None = None, runtime_factory=create_runtime,
               clock=time.monotonic, request_timeout_seconds=90):
    if request_timeout_seconds <= 0:
        raise ValueError("Request timeout must be positive.")

    @asynccontextmanager
    async def lifespan(application):
        try:
            config = settings if settings is not None else Settings.from_env()
        except ConfigurationError as error:
            configure_logging()
            _LOG.error("configuration_error", extra={"event": "configuration_error", "setting_names": error.fields})
            raise
        configure_logging(config.log_level)
        runtime: AppRuntime = runtime_factory(config)
        application.state.settings = config
        application.state.runtime = runtime
        application.state.limiter = SlidingWindowLimiter(clock=clock)
        application.state.semaphore = asyncio.Semaphore(2)
        _LOG.info("application_started", extra={"event": "application_started"})
        try:
            yield
        finally:
            await runtime.aclose()
            _LOG.info("application_stopped", extra={"event": "application_stopped"})

    application = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    application.add_middleware(ResponseHeaders)

    @application.get("/healthz")
    async def health():
        return {"status": "ok"}

    @application.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return HTMLResponse(_TEMPLATES.get_template("index.html").render(
            stub_mode=request.app.state.settings.dev_stub_model,
        ))

    async def handle(request, api):
        started, status = time.monotonic(), 500
        config = request.app.state.settings
        try:
            async with asyncio.timeout(request_timeout_seconds):
                payload = await _payload(request, api)
                provided = payload.get("access_code")
                try:
                    provided = provided.encode("utf-8") if isinstance(provided, str) and len(provided) <= 512 else b""
                except UnicodeError:
                    provided = b""
                expected = config.access_code.get_secret_value().encode("utf-8")
                if not hmac.compare_digest(provided, expected):
                    raise RequestFailure(401, "Access code is missing or incorrect.")
                if set(payload) - {"url", "access_code"}:
                    raise RequestFailure(400, "Only URL and access code fields are accepted.")
                try:
                    url = InputURL.model_validate({"url": payload.get("url")}).url
                except ValidationError:
                    raise RequestFailure(400, "Enter a URL of at most 2048 characters.") from None
                peer = request.client.host if request.client is not None else "unknown"
                await request.app.state.limiter.check(peer)

                async def perform():
                    async with request.app.state.semaphore:
                        report = await request.app.state.runtime.service.audit(url)
                        if config.dev_stub_model:
                            report = report.model_copy(update={"review_mode": "stub", "limitations": [STUB_NOTICE, *report.limitations]})
                        if api:
                            return Response(await asyncio.to_thread(render_json, report), media_type="application/json")
                        return HTMLResponse(await asyncio.to_thread(render_html, report))

                response = await _until_disconnect(request, perform())
                status = response.status_code
                return response
        except RequestFailure as error:
            status = error.status
            return _error(request, status, error.message)
        except RateLimitExceeded as error:
            status = 429
            return _error(request, status, "Audit limit reached. Try again after the current hourly window.",
                          {"retry-after": str(error.retry_after)})
        except UnsafeURL:
            status = 400
            return _error(request, status, "Use a public HTTP or HTTPS URL on port 80 or 443. Private destinations are blocked.")
        except RobotsDisallowed:
            status = 403
            return _error(request, status, "This page is disallowed by robots.txt for our audit User-Agent.")
        except FetchError:
            status = 502
            return _error(request, status, "The page or its robots.txt could not be fetched within the allowed HTML and size limits.")
        except TimeoutError:
            status = 504
            return _error(request, status, "The audit exceeded the request deadline. Please try again.")
        except ClientDisconnect:
            status = 499
            return _error(request, status, "The client disconnected; audit work was cancelled.")
        except asyncio.CancelledError:
            status = 499
            raise
        except Exception:
            status = 500
            return _error(request, status, "The audit could not be completed due to an unexpected error.")
        finally:
            _LOG.info("audit_request_finished", extra={
                "event": "audit_request_finished", "request_id": request.state.request_id,
                "route": "api_audit" if api else "html_audit", "status": status,
                "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
            })

    @application.post("/audit", response_class=HTMLResponse)
    async def audit_html(request: Request):
        return await handle(request, False)

    @application.post("/api/audit")
    async def audit_json(request: Request):
        return await handle(request, True)

    @application.exception_handler(RequestValidationError)
    async def invalid(request, error):
        return _error(request, 400, "Invalid request.")

    @application.exception_handler(HTTPException)
    async def http_error(request, error):
        message = "Page not found." if error.status_code == 404 else "This request is not supported."
        return _error(request, error.status_code, message)

    @application.exception_handler(Exception)
    async def unexpected(request, error):
        return _error(request, 500, "An unexpected server error occurred.")

    return application


# Configure logging during ASGI import, before Uvicorn emits startup messages.
# Settings and model resources are still deferred to lifespan initialization.
_INITIAL_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
configure_logging(_INITIAL_LEVEL if _INITIAL_LEVEL in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "INFO")
app = create_app()
