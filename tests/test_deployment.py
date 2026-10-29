import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from deploy.config_env import runtime_environment, validate_setup


ROOT = Path(__file__).resolve().parents[1]
ENV = {"PROJECT_ID": "seo-audit-test", "REGION": "us-central1", "GOOGLE_CLOUD_LOCATION": "global",
       "MODEL_NAME": "explicit-test-model", "CONTACT_URL": "https://agency.example/contact"}


@pytest.mark.parametrize("key,value", [
    ("PROJECT_ID", ""), ("PROJECT_ID", "bad;project"), ("REGION", "global"),
    ("REGION", "us-central1\n--cpu=9"), ("GOOGLE_CLOUD_PROJECT", "different-project"),
    ("MODEL_NAME", ""), ("CONTACT_URL", "https://user:pass@example.com"),
    ("CONTACT_URL", "https://127.0.0.1/"), ("CONTACT_URL", "https://metadata.google.internal/"),
    ("CONTACT_URL", "file:///etc/passwd"), ("GOOGLE_CLOUD_LOCATION", "--location=x"),
    ("DEV_STUB_MODEL", "true"), ("LOG_LEVEL", "LOUD"),
])
def test_invalid_deploy_settings_are_rejected_without_values(key, value):
    with pytest.raises(ValueError) as error:
        runtime_environment(ENV | {key: value})
    assert key in str(error.value) or key == "GOOGLE_CLOUD_PROJECT"
    if len(value) > 8:
        assert value not in str(error.value)


def test_setup_does_not_require_model_or_secret():
    validate_setup({"PROJECT_ID": ENV["PROJECT_ID"], "REGION": ENV["REGION"]})


def test_runtime_file_is_explicit_and_preserves_delimiters_without_secrets():
    contact = "https://agency.example/contact?team=seo,engineering&note=$(touch%20oops)"
    values = runtime_environment(ENV | {"CONTACT_URL": contact, "ACCESS_CODE": "never-export-me",
                                       "GOOGLE_API_KEY": "never-export-this-either"})
    assert json.loads(json.dumps(values))["CONTACT_URL"] == contact
    assert "ACCESS_CODE" not in values and "GOOGLE_API_KEY" not in values
    assert values["GOOGLE_GENAI_USE_ENTERPRISE"] == "1"
    assert values["DEV_STUB_MODEL"] == "false"


def bash_path():
    candidates = [Path("C:/Program Files/Git/bin/bash.exe")]
    resolved = shutil.which("bash")
    if resolved:
        candidates.append(Path(resolved))
    for candidate in candidates:
        if candidate.is_file():
            # Avoid Windows' WSL launcher when no Linux distro is configured.
            if os.name == "nt" and "system32" in str(candidate).lower():
                continue
            return str(candidate)
    pytest.skip("Bash unavailable; shell execution checks need Git Bash or Linux Bash.")


@pytest.fixture
def shell_harness(tmp_path):
    bash = bash_path()
    binary = tmp_path / "bin"
    binary.mkdir()
    helper = tmp_path / "fake_gcloud.py"
    helper.write_text('''import json, os, pathlib, sys
args = sys.argv[1:]
path = pathlib.Path(os.environ["FAKE_GCLOUD_STATE"])
state = json.loads(path.read_text()) if path.exists() else {"calls": [], "accounts": [], "secret": False}
state["calls"].append(args)
failure = os.environ.get("FAKE_GCLOUD_FAILURE")
if failure and args[:3] == ["iam", "service-accounts", "list"]:
    path.write_text(json.dumps(state)); sys.exit(1)
if args[:3] == ["iam", "service-accounts", "list"]:
    email = args[args.index("--filter") + 1].split("=", 1)[1]
    if email.split("@")[0] in state["accounts"]: print(email)
elif args[:3] == ["iam", "service-accounts", "create"]:
    state["accounts"].append(args[3])
elif args[:2] == ["secrets", "list"]:
    if state["secret"]: print("access-code")
elif args[:2] == ["secrets", "create"]:
    state["secret"] = True
elif args[:3] == ["secrets", "versions", "describe"]:
    if os.environ.get("FAKE_SECRET_VERSION", "yes") == "yes": print("ENABLED")
    else:
        path.write_text(json.dumps(state)); sys.exit(1)
elif args[:2] == ["run", "deploy"]:
    state["runtime_env"] = json.loads(pathlib.Path(args[args.index("--env-vars-file") + 1]).read_text())
path.write_text(json.dumps(state))
''', encoding="utf-8")
    (binary / "gcloud").write_text('#!/usr/bin/env bash\nexec "$PYTHON_BIN" "$FAKE_GCLOUD_HELPER" "$@"\n', encoding="utf-8")
    (binary / "gcloud").chmod(0o755)
    state = tmp_path / "state.json"
    env = os.environ | ENV | {"PYTHON_BIN": Path(sys.executable).as_posix(),
        "FAKE_GCLOUD_BIN": binary.as_posix(), "FAKE_GCLOUD_HELPER": helper.as_posix(),
        "FAKE_GCLOUD_STATE": state.as_posix(), "DEV_STUB_MODEL": "false"}
    env.pop("GOOGLE_CLOUD_PROJECT", None)
    env.pop("BASH_ENV", None)
    def run(script, *args, **changes):
        # Verify the executable is the fake before allowing ANY script to run.
        launcher = '''if command -v cygpath >/dev/null; then fakebin="$(cygpath -u "$FAKE_GCLOUD_BIN")"; else fakebin="$FAKE_GCLOUD_BIN"; fi
export PATH="$fakebin:$PATH"
[[ "$(command -v gcloud)" == "$fakebin/gcloud" ]] || exit 99
exec bash "$DEPLOY_TEST_SCRIPT" "$@"
'''
        return subprocess.run([bash, "-c", launcher, "test", *args], cwd=ROOT,
            env=env | {"DEPLOY_TEST_SCRIPT": (ROOT / "deploy" / script).as_posix()} | changes,
            capture_output=True, text=True, timeout=40)
    return run, state


def test_setup_is_idempotent_and_runtime_roles_are_scoped(shell_harness):
    run, state_file = shell_harness
    first = run("setup_gcp.sh")
    assert first.returncode == 0, first.stderr
    second = run("setup_gcp.sh")
    assert second.returncode == 0, second.stderr
    state = json.loads(state_file.read_text())
    assert state["accounts"] == ["seo-audit-sa", "seo-audit-build"]
    calls = state["calls"]
    assert len([args for args in calls if args[:2] == ["secrets", "create"]]) == 1
    runtime = "serviceAccount:seo-audit-sa@seo-audit-test.iam.gserviceaccount.com"
    grants = [args[args.index("--role") + 1] for args in calls
              if args[:2] == ["projects", "add-iam-policy-binding"] and runtime in args]
    assert set(grants) == {"roles/aiplatform.user", "roles/logging.logWriter"}
    secret_grants = [args for args in calls if args[:2] == ["secrets", "add-iam-policy-binding"]]
    assert all(args[2] == "access-code" and "roles/secretmanager.secretAccessor" in args for args in secret_grants)
    assert not any(args[:2] == ["run", "deploy"] for args in calls)


def test_setup_permission_failure_does_not_attempt_create(shell_harness):
    run, state_file = shell_harness
    result = run("setup_gcp.sh", FAKE_GCLOUD_FAILURE="yes")
    assert result.returncode != 0
    assert not any(args[:3] == ["iam", "service-accounts", "create"]
                   for args in json.loads(state_file.read_text())["calls"])


@pytest.mark.parametrize("arguments,minimum", [((), "0"), (("--demo-day",), "1")])
def test_deploy_flags_safe_serialization_and_temp_cleanup(shell_harness, arguments, minimum):
    run, state_file = shell_harness
    contact = "https://agency.example/contact?team=seo,engineering&note=$(touch%20oops)"
    result = run("deploy.sh", *arguments, CONTACT_URL=contact, ACCESS_CODE="must-not-leak")
    assert result.returncode == 0, result.stderr
    state = json.loads(state_file.read_text())
    args = next(args for args in state["calls"] if args[:2] == ["run", "deploy"])
    flags = {flag: args[args.index(flag) + 1] for flag in (
        "--min-instances", "--max-instances", "--concurrency", "--cpu", "--memory", "--timeout")}
    assert flags == {"--min-instances": minimum, "--max-instances": "2", "--concurrency": "4",
                     "--cpu": "1", "--memory": "1Gi", "--timeout": "120"}
    assert "--allow-unauthenticated" in args
    assert "ACCESS_CODE=access-code:latest" in args
    assert state["runtime_env"]["CONTACT_URL"] == contact
    assert "must-not-leak" not in json.dumps(state) + result.stdout + result.stderr
    env_path = args[args.index("--env-vars-file") + 1]
    # Git Bash /tmp is different from Windows /tmp; ask the same shell.
    check = subprocess.run([bash_path(), "-c", 'test ! -e "$1"', "test", env_path], capture_output=True)
    assert check.returncode == 0


def test_missing_secret_or_invalid_model_prevents_deployment(shell_harness):
    run, state_file = shell_harness
    assert run("deploy.sh", FAKE_SECRET_VERSION="no").returncode != 0
    assert run("deploy.sh", MODEL_NAME="").returncode != 0
    assert not any(args[:2] == ["run", "deploy"] for args in json.loads(state_file.read_text())["calls"])
