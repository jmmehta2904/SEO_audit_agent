import json
import logging
import os

import pytest
from google.adk.telemetry.context import TelemetryConfig

from seo_audit.logging_utils import protect_dependency_logs


@pytest.mark.parametrize("name", [
    "google_adk.google.adk.workflow._node_runner", "google.adk.test",
    "google_genai._api_client", "google.genai.test", "httpx", "httpcore.connection", "asyncio",
])
def test_dependency_messages_arguments_and_exceptions_cannot_log_content(name, caplog):
    caplog.set_level(logging.DEBUG)
    protect_dependency_logs()
    secret = "PRIVATE_CONTENT_MUST_NOT_APPEAR"
    try:
        raise ValueError(secret)
    except ValueError:
        logging.getLogger(name).error("Untrusted request %s", secret, exc_info=True, stack_info=True)
    assert secret not in caplog.text
    record = caplog.records[-1]
    assert record.exc_info is record.exc_text is record.stack_info is None
    assert json.loads(record.getMessage()) == {"event": "dependency_event", "component": name, "level": "ERROR"}


def test_guard_is_idempotent_and_does_not_rewrite_application_events(caplog):
    caplog.set_level(logging.INFO)
    protect_dependency_logs()
    factory = logging.getLogRecordFactory()
    protect_dependency_logs()
    assert logging.getLogRecordFactory() is factory
    logging.getLogger("seo_audit.test").info('{"event":"audit_completed"}')
    assert caplog.records[-1].getMessage() == '{"event":"audit_completed"}'


def test_content_capture_is_disabled_even_with_inherited_telemetry_overrides(monkeypatch):
    monkeypatch.setenv("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "true")
    monkeypatch.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "SPAN_AND_EVENT")
    monkeypatch.setenv("ADK_EXPERIMENTAL_TELEMETRY", "true")
    monkeypatch.setenv("ADK_EXPERIMENTAL_TELEMETRY_FEATURES", "logs")
    monkeypatch.setenv("ADK_TELEMETRY_IGNORE_RUN_CONFIG", "true")
    protect_dependency_logs()
    config = TelemetryConfig()
    assert not config.should_add_content_to_legacy_spans
    assert not config.should_add_content_to_experimental_spans
    assert not config.should_emit_experimental_telemetry
    assert os.environ["ADK_EXPERIMENTAL_TELEMETRY_FEATURES"] == ""
