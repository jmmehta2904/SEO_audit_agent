import json
import logging
from types import SimpleNamespace

from google.adk.models.google_llm import Gemini

from app.runtime import DevelopmentStub, ManagedGemini, create_runtime
from seo_audit.config import Settings
from seo_audit.logging_utils import JsonFormatter


def config(**changes):
    return Settings.from_env({"ACCESS_CODE": "private-test-code", "MODEL_NAME": "offline-test-model",
                              "CONTACT_URL": "https://agency.example/contact", "DEV_STUB_MODEL": "true"} | changes)


async def test_managed_client_is_lazy_and_closes_both_transports_once(monkeypatch):
    counts = {"created": 0, "async_closed": 0, "sync_closed": 0}

    async def async_close():
        counts["async_closed"] += 1

    def sync_close():
        counts["sync_closed"] += 1

    client = SimpleNamespace(aio=SimpleNamespace(aclose=async_close), close=sync_close)

    def create(model):
        counts["created"] += 1
        return client

    monkeypatch.setattr(Gemini, "api_client", property(create))
    model = ManagedGemini(model="offline-test-model")
    assert counts["created"] == 0
    assert model.api_client is model.api_client
    assert counts["created"] == 1
    await model.aclose()
    await model.aclose()
    assert counts == {"created": 1, "async_closed": 1, "sync_closed": 1}


async def test_unused_client_is_not_initialized_at_shutdown(monkeypatch):
    def forbidden(model):
        raise AssertionError("Unused model must not initialize ADC or a client")

    monkeypatch.setattr(Gemini, "api_client", property(forbidden))
    runtime = create_runtime(config(DEV_STUB_MODEL="false", GOOGLE_CLOUD_PROJECT="test-project",
                                    GOOGLE_CLOUD_LOCATION="test-location"))
    await runtime.aclose()


def test_local_stub_has_an_explicit_configured_identity():
    runtime = create_runtime(config())
    assert runtime.service.model_name == "offline-test-model"
    assert runtime.close_callback is None


def test_live_model_connection_secret_is_excluded_from_repr_and_dump():
    model = ManagedGemini(model="offline-test-model", client_kwargs={"api_key": "PRIVATE_CONNECTION_KEY"})
    assert "PRIVATE_CONNECTION_KEY" not in repr(model)
    assert "PRIVATE_CONNECTION_KEY" not in json.dumps(model.model_dump())


def test_json_formatter_ignores_arbitrary_messages_exceptions_and_extras():
    record = logging.LogRecord("seo_audit.server", logging.ERROR, __file__, 1,
                               "PRIVATE_MESSAGE %s", ("PRIVATE_ARGUMENT",), None)
    record.event = "PRIVATE_EVENT"
    record.request_id = "PRIVATE_ID"
    record.route = "/api/audit?PRIVATE_QUERY"
    record.body = "PRIVATE_BODY"
    record.setting_names = ["ACCESS_CODE", "PRIVATE_SETTING"]
    text = JsonFormatter().format(record)
    assert "PRIVATE" not in text
    assert json.loads(text)["setting_names"] == ["ACCESS_CODE"]


def test_json_formatter_does_not_crash_on_unexpected_extra_types():
    record = logging.LogRecord("seo_audit.server", logging.ERROR, __file__, 1,
                               "PRIVATE_MESSAGE", (), None)
    record.event = {"private": "PRIVATE_DATA"}
    record.route = ["PRIVATE_DATA"]
    assert "PRIVATE" not in JsonFormatter().format(record)
