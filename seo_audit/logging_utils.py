"""Prevent SDK debug messages and exceptions from exposing untrusted content.

The FastAPI entry point will configure stdout and application JSON logging in
Phase 4. This process-wide, idempotent record guard also covers lazy SDK loggers
and logs captured by other handlers. Install it before constructing a workflow.
"""

import json
import logging
import os
import re
import sys
import threading
from datetime import datetime, timezone


_LOCK = threading.Lock()
_DEPENDENCIES = ("google_adk", "google.adk", "google_genai", "google.genai", "httpx", "httpcore", "asyncio", "uvicorn")


def protect_dependency_logs() -> None:
    with _LOCK:
        # ADK 2.13.0 otherwise defaults to capturing content in legacy spans.
        # Enforce the service's no-content policy even if an inherited shell
        # enables capture or locks telemetry to environment configuration.
        os.environ["ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"] = "false"
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "NO_CONTENT"
        os.environ["ADK_EXPERIMENTAL_TELEMETRY"] = "false"
        os.environ["ADK_EXPERIMENTAL_TELEMETRY_FEATURES"] = ""
        previous = logging.getLogRecordFactory()
        if getattr(previous, "_seo_audit_privacy_guard", False):
            return

        def factory(*args, **kwargs):
            record = previous(*args, **kwargs)
            if any(record.name == name or record.name.startswith(name + ".") for name in _DEPENDENCIES):
                record.msg = json.dumps({"event": "dependency_event", "component": record.name,
                                         "level": record.levelname}, separators=(",", ":"))
                record.args = ()
                record.exc_info = record.exc_text = record.stack_info = None
            return record

        factory._seo_audit_privacy_guard = True
        logging.setLogRecordFactory(factory)


class JsonFormatter(logging.Formatter):
    """Never serialize arbitrary messages, exception strings, or extra fields."""

    def format(self, record):
        allowed_events = {"application_started", "application_stopped", "configuration_error", "audit_request_finished"}
        event = getattr(record, "event", None)
        if not isinstance(event, str) or event not in allowed_events:
            event = "dependency_event" if any(record.name == name or record.name.startswith(name + ".") for name in _DEPENDENCIES) else "application_event"
        result = {"timestamp": datetime.now(timezone.utc).isoformat(), "level": record.levelname, "event": event}
        request_id = getattr(record, "request_id", "")
        if isinstance(request_id, str) and re.fullmatch(r"[0-9a-f]{32}", request_id):
            result["request_id"] = request_id
        route = getattr(record, "route", "")
        if isinstance(route, str) and route in {"html_audit", "api_audit", "other"}:
            result["route"] = route
        for name in ("status", "duration_ms"):
            value = getattr(record, name, None)
            if type(value) is int and value >= 0:
                result[name] = value
        names = getattr(record, "setting_names", [])
        known = {"ACCESS_CODE", "MODEL_NAME", "CONTACT_URL", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION",
                 "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_ENTERPRISE", "DEV_STUB_MODEL", "LOG_LEVEL", "authentication_mode"}
        if isinstance(names, (list, tuple)):
            clean = [name for name in names if isinstance(name, str) and name in known]
            if clean:
                result["setting_names"] = clean
        return json.dumps(result, separators=(",", ":"))


class _Stdout:
    # Resolve stdout at emission time, including when pytest temporarily captures it.
    def write(self, value):
        return sys.stdout.write(value)

    def flush(self):
        sys.stdout.flush()


def configure_logging(level="INFO"):
    protect_dependency_logs()
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_seo_audit_stdout", False):
            root.removeHandler(handler)
            handler.close()
    handler = logging.StreamHandler(_Stdout())
    handler._seo_audit_stdout = True
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        dependency = logging.getLogger(name)
        dependency.handlers.clear()
        dependency.propagate = True
        dependency.setLevel(level)
