#!/usr/bin/env bash
# Shared validation only; sourcing this file never calls Google Cloud.
set -euo pipefail
DEPLOY_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
command -v "$PYTHON_BIN" >/dev/null || { printf '%s\n' 'Python 3 is required (set PYTHON_BIN if needed).' >&2; exit 2; }
command -v gcloud >/dev/null || { printf '%s\n' 'Install the Google Cloud CLI first.' >&2; exit 2; }
export PROJECT_ID REGION
"$PYTHON_BIN" "$DEPLOY_ROOT/deploy/config_env.py" validate-setup
RUNTIME_SA="seo-audit-sa@${PROJECT_ID}.iam.gserviceaccount.com"
BUILD_SA="seo-audit-build@${PROJECT_ID}.iam.gserviceaccount.com"
