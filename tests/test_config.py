import traceback

import pytest

from seo_audit.config import ConfigurationError, Settings


def environment(**changes):
    return {"ACCESS_CODE": "a-private-test-access-code", "MODEL_NAME": "offline-test-model",
            "CONTACT_URL": "https://agency.example/contact", "DEV_STUB_MODEL": "true"} | changes


def test_stub_requires_no_cloud_credentials_and_masks_secrets():
    settings = Settings.from_env(environment())
    assert settings.dev_stub_model
    assert settings.google_cloud_project is None
    assert "a-private-test-access-code" not in repr(settings)
    assert "a-private-test-access-code" not in settings.model_dump_json()


@pytest.mark.parametrize("field", ["ACCESS_CODE", "MODEL_NAME", "CONTACT_URL"])
def test_missing_required_setting_fails_without_echoing_values(field):
    values = environment()
    del values[field]
    with pytest.raises(ConfigurationError) as error:
        Settings.from_env(values)
    assert "a-private-test-access-code" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("changes", [
    {"ACCESS_CODE": " "}, {"MODEL_NAME": ""}, {"CONTACT_URL": "file:///contact"},
    {"CONTACT_URL": "https://agency.example/\r\nInjected: secret"},
    {"DEV_STUB_MODEL": "not-a-bool"}, {"LOG_LEVEL": "anything"},
    {"K_SERVICE": "cloud-run-service"},
    {"ACCESS_CODE": "\ud800"},
])
def test_invalid_settings_and_cloud_stub_mode_are_rejected(changes):
    with pytest.raises(ConfigurationError):
        Settings.from_env(environment(**changes))


def test_vertex_mode_requires_project_and_location_and_uses_no_api_key():
    values = environment(DEV_STUB_MODEL="false", GOOGLE_GENAI_USE_ENTERPRISE="1")
    with pytest.raises(ConfigurationError):
        Settings.from_env(values)
    values |= {"GOOGLE_CLOUD_PROJECT": "test-project", "GOOGLE_CLOUD_LOCATION": "test-location"}
    assert Settings.from_env(values).use_vertex
    with pytest.raises(ConfigurationError):
        Settings.from_env(values | {"GOOGLE_API_KEY": "private-api-key"})


def test_local_api_key_mode_and_cloud_run_restrictions():
    values = environment(DEV_STUB_MODEL="false", GOOGLE_GENAI_USE_ENTERPRISE="0", GOOGLE_API_KEY="private-api-key")
    settings = Settings.from_env(values)
    assert not settings.use_vertex
    assert settings.google_api_key.get_secret_value() == "private-api-key"
    assert "private-api-key" not in settings.model_dump_json()
    with pytest.raises(ConfigurationError):
        Settings.from_env(values | {"K_SERVICE": "cloud-run-service"})


def test_cloud_run_uses_platform_adc_without_key_file_override():
    values = environment(DEV_STUB_MODEL="false", K_SERVICE="cloud-run-service",
                         GOOGLE_CLOUD_PROJECT="test-project", GOOGLE_CLOUD_LOCATION="test-location")
    assert Settings.from_env(values).cloud_run
    with pytest.raises(ConfigurationError):
        Settings.from_env(values | {"GOOGLE_APPLICATION_CREDENTIALS": "private-file.json"})


def test_ambient_alias_key_is_rejected_instead_of_implicitly_used():
    with pytest.raises(ConfigurationError):
        Settings.from_env(environment(GEMINI_API_KEY="unintended-private-key"))
