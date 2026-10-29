"""Smoke-test a built image locally, with no external network or cloud calls."""

import argparse
import json
import os
import secrets
import subprocess
import sys
from uuid import uuid4


PROBE = r'''
import json, os, pathlib, sys, time, urllib.error, urllib.request
base = "http://127.0.0.1:" + os.environ["PORT"]
def request(path, body=None, content_type="application/json"):
    headers = {"content-type": content_type} if body is not None else {}
    req = urllib.request.Request(base + path, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=2) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers
deadline = time.monotonic() + 30
while True:
    try:
        status, body, headers = request("/healthz")
        assert status == 200 and json.loads(body) == {"status": "ok"}
        break
    except (OSError, AssertionError):
        if time.monotonic() >= deadline:
            raise RuntimeError("Container health check failed") from None
        time.sleep(.25)
status, body, headers = request("/")
assert status == 200 and b"access_code" in body
assert headers["cache-control"] == "no-store"
status, _, _ = request("/api/audit", json.dumps({"url": "https://example.com/", "access_code": "wrong"}).encode())
assert status == 401
payload = {"url": "ftp://invalid.example/", "access_code": os.environ["ACCESS_CODE"]}
status, _, _ = request("/api/audit", json.dumps(payload).encode())
assert status == 400
import urllib.parse
status, body, _ = request("/audit", urllib.parse.urlencode(payload).encode(), "application/x-www-form-urlencoded")
assert status == 400 and b"<!doctype html>" in body.lower()
assert os.getuid() == 10001 and os.geteuid() != 0
assert sys.version_info[:2] == (3, 12)
assert not pathlib.Path("/app/.env").exists()
from importlib.metadata import version
assert version("google-adk") == "2.13.0"
print(json.dumps({"health": 200, "form": 200, "wrong_code": 401, "invalid_url_json": 400,
                  "invalid_url_html": 400, "uid": os.getuid(), "port": os.environ["PORT"],
                  "python": sys.version.split()[0], "adk": version("google-adk")}))
'''


def run(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True, timeout=60, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="seo-audit-agent:phase5")
    args = parser.parse_args()
    name = "seo-audit-smoke-" + uuid4().hex[:12]
    env = os.environ | {"ACCESS_CODE": secrets.token_urlsafe(32)}
    started = False
    try:
        run("run", "--detach", "--name", name, "--network", "none", "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--env", "ACCESS_CODE",
            "--env", "MODEL_NAME=container-smoke-stub", "--env", "DEV_STUB_MODEL=true",
            "--env", "CONTACT_URL=https://example.com/contact", "--env", "PORT=18081", args.image, env=env)
        started = True
        result = run("exec", "-i", name, "python", "-", input=PROBE)
        print(result.stdout.strip())
        # Cloud Run's marker must reject the local stub before serving requests.
        rejected = subprocess.run(["docker", "run", "--rm", "--network", "none", "--env", "ACCESS_CODE",
            "--env", "MODEL_NAME=container-smoke-stub", "--env", "DEV_STUB_MODEL=true",
            "--env", "CONTACT_URL=https://example.com/contact", "--env", "K_SERVICE=smoke",
            args.image], env=env, capture_output=True, text=True, timeout=30)
        if rejected.returncode == 0 or "configuration_error" not in rejected.stdout:
            raise RuntimeError("Cloud Run stub rejection check failed")
        print("Cloud Run stub rejection: passed")
        # Only application-controlled stdout is inspected; no model/fetch runs.
        for line in run("logs", name).stdout.splitlines():
            event = json.loads(line)
            if env["ACCESS_CODE"] in line or "access_code" in event:
                raise RuntimeError("Container log privacy check failed")
        print("Structured stdout and secret exclusion: passed")
    except (subprocess.SubprocessError, OSError, RuntimeError, ValueError):
        # Never echo Docker arguments, environment values or request payloads.
        print("Container smoke failed; inspect Docker availability and image configuration.", file=sys.stderr)
        return 1
    finally:
        if started:
            run("rm", "--force", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
