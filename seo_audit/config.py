"""Environment-only settings with secret-safe startup failures."""

import os
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

from seo_audit.security import validate_contact_url


class ConfigurationError(ValueError):
    def __init__(self, fields: tuple[str, ...]):
        self.fields = fields
        super().__init__("Invalid configuration. Check: " + ", ".join(fields) + ".")


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access_code: SecretStr
    model_name: str
    contact_url: str
    google_cloud_project: str | None = None
    google_cloud_location: str | None = None
    google_api_key: SecretStr | None = None
    use_vertex: bool = True
    dev_stub_model: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    cloud_run: bool = Field(default=False, exclude=True)
    alias_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    credentials_file_set: bool = Field(default=False, exclude=True, repr=False)

    @field_validator("use_vertex", "dev_stub_model", mode="before")
    @classmethod
    def boolean(cls, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false", "1", "0"}:
            return value.lower() in {"true", "1"}
        raise ValueError("Use true/false or 1/0.")

    @field_validator("access_code")
    @classmethod
    def access_secret(cls, value):
        raw = value.get_secret_value()
        if not raw.strip() or len(raw) > 512:
            raise ValueError("An access code of 1–512 non-blank characters is required.")
        try:
            raw.encode("utf-8")
        except UnicodeError:
            raise ValueError("Access code must be valid Unicode text.") from None
        return value

    @field_validator("model_name")
    @classmethod
    def model_required(cls, value):
        if not value.strip():
            raise ValueError("An explicit model name is required.")
        return value.strip()

    @field_validator("contact_url")
    @classmethod
    def contact(cls, value):
        return validate_contact_url(value)

    @model_validator(mode="after")
    def authentication_mode(self):
        if self.alias_api_key is not None:
            raise ValueError("Use only the documented GOOGLE_API_KEY alternative.")
        if self.cloud_run and (self.dev_stub_model or not self.use_vertex or self.google_api_key or self.credentials_file_set):
            raise ValueError("Cloud Run requires Vertex platform ADC and forbids stub/key modes.")
        if not self.dev_stub_model:
            if self.use_vertex:
                if not self.google_cloud_project or not self.google_cloud_project.strip():
                    raise ValueError("Vertex requires a project.")
                if not self.google_cloud_location or not self.google_cloud_location.strip():
                    raise ValueError("Vertex requires a model location.")
                if self.google_api_key:
                    raise ValueError("Vertex mode uses ADC, not an API key.")
            elif self.google_api_key is None:
                raise ValueError("Local API-key mode requires GOOGLE_API_KEY.")
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        source = os.environ if env is None else env
        names = {
            "access_code": "ACCESS_CODE", "model_name": "MODEL_NAME", "contact_url": "CONTACT_URL",
            "google_cloud_project": "GOOGLE_CLOUD_PROJECT", "google_cloud_location": "GOOGLE_CLOUD_LOCATION",
            "google_api_key": "GOOGLE_API_KEY", "use_vertex": "GOOGLE_GENAI_USE_ENTERPRISE",
            "dev_stub_model": "DEV_STUB_MODEL", "log_level": "LOG_LEVEL", "alias_api_key": "GEMINI_API_KEY",
        }
        values = {key: source[name] for key, name in names.items() if name in source}
        for name in ("google_cloud_project", "google_cloud_location", "google_api_key", "alias_api_key"):
            if not values.get(name):
                values[name] = None
        values["cloud_run"] = bool(source.get("K_SERVICE"))
        values["credentials_file_set"] = bool(source.get("GOOGLE_APPLICATION_CREDENTIALS"))
        try:
            return cls.model_validate(values)
        except ValidationError as error:
            fields = tuple(sorted({names.get(item["loc"][0], "authentication_mode")
                                   if item["loc"] else "authentication_mode"
                                   for item in error.errors(include_input=False)}))
            raise ConfigurationError(fields) from None
