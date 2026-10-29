"""Validate deployment inputs and serialize a non-secret gcloud env-vars file."""

import ipaddress
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


def required(env, name, limit=2048):
    value = env.get(name, "")
    if (not value or value != value.strip() or len(value) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError(f"Invalid or missing {name}.")
    return value


def validate_setup(env):
    project = required(env, "PROJECT_ID", 30)
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project):
        raise ValueError("Invalid PROJECT_ID.")
    region = required(env, "REGION", 64)
    if not re.fullmatch(r"[a-z]+-[a-z]+[0-9]", region):
        raise ValueError("Invalid REGION; use a Cloud Run region, not global.")
    if env.get("GOOGLE_CLOUD_PROJECT") and env["GOOGLE_CLOUD_PROJECT"] != project:
        raise ValueError("PROJECT_ID and GOOGLE_CLOUD_PROJECT must match.")


def runtime_environment(env):
    validate_setup(env)
    model = required(env, "MODEL_NAME", 512)
    location = required(env, "GOOGLE_CLOUD_LOCATION", 64)
    if location != "global" and not re.fullmatch(r"[a-z]+-[a-z]+[0-9]", location):
        raise ValueError("Invalid GOOGLE_CLOUD_LOCATION.")
    contact = required(env, "CONTACT_URL")
    try:
        parsed = urlsplit(contact)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.port not in {None, 80, 443} or "\\" in contact
                or any(char.isspace() for char in contact) or parsed.hostname.lower() in {
                    "localhost", "metadata.google.internal"}):
            raise ValueError
        try:
            literal = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            literal = None
        if literal is not None and not literal.is_global:
            raise ValueError
    except ValueError:
        raise ValueError("Invalid CONTACT_URL; use a public HTTP(S) contact URL.") from None
    if env.get("DEV_STUB_MODEL", "false").lower() not in {"false", "0"}:
        raise ValueError("Disable DEV_STUB_MODEL before deployment.")
    level = env.get("LOG_LEVEL", "INFO")
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("Invalid LOG_LEVEL.")
    return {
        "MODEL_NAME": model, "CONTACT_URL": contact,
        "GOOGLE_CLOUD_PROJECT": env["PROJECT_ID"], "GOOGLE_CLOUD_LOCATION": location,
        "GOOGLE_GENAI_USE_ENTERPRISE": "1", "DEV_STUB_MODEL": "false", "LOG_LEVEL": level,
        "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS": "false",
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "NO_CONTENT",
        "ADK_EXPERIMENTAL_TELEMETRY": "false", "ADK_EXPERIMENTAL_TELEMETRY_FEATURES": "",
    }


def main():
    try:
        if sys.argv[1:] == ["validate-setup"]:
            validate_setup(os.environ)
        elif len(sys.argv) == 3 and sys.argv[1] == "write-runtime":
            # JSON is a YAML subset accepted by --env-vars-file. Values cannot
            # inject comma-separated flags or shell code; secrets aren't copied.
            values = runtime_environment(os.environ)
            Path(sys.argv[2]).write_text(json.dumps(values, ensure_ascii=True), encoding="utf-8")
        else:
            raise ValueError("Use validate-setup or write-runtime PATH.")
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
