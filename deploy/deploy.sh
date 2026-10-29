#!/usr/bin/env bash
# Run only after setup, secret provisioning and explicit cloud deployment intent.
set -euo pipefail
minimum=0
case "${1:-}" in
  '') ;;
  --demo-day) minimum=1; shift ;;
  *) printf '%s\n' 'Usage: bash deploy/deploy.sh [--demo-day]' >&2; exit 2 ;;
esac
if (($#)); then printf '%s\n' 'Unexpected argument.' >&2; exit 2; fi
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
umask 077
env_file="$(mktemp "${TMPDIR:-/tmp}/seo-audit-env.XXXXXX")"
trap 'rm -f -- "$env_file"' EXIT
"$PYTHON_BIN" "$DEPLOY_ROOT/deploy/config_env.py" write-runtime "$env_file"

if ! secret_state="$(gcloud secrets versions describe latest --secret access-code \
  --project "$PROJECT_ID" --format 'value(state)')" || [[ "$secret_state" != 'ENABLED' ]]; then
  printf '%s\n' 'The latest access-code secret version must exist and be enabled before deploying.' >&2
  exit 2
fi

cd -- "$DEPLOY_ROOT"
gcloud run deploy seo-audit-agent --source . --project "$PROJECT_ID" --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --build-service-account "projects/${PROJECT_ID}/serviceAccounts/${BUILD_SA}" \
  --allow-unauthenticated --min-instances "$minimum" --max-instances 2 \
  --concurrency 4 --cpu 1 --memory 1Gi --timeout 120 \
  --env-vars-file "$env_file" --set-secrets ACCESS_CODE=access-code:latest --quiet
printf '%s\n' \
  'Deployment command completed. Validate /healthz, authentication and a live audit.' \
  'Check ai_review_available=true; a rules-only response does not prove Vertex access.' \
  'Use the normal deploy command after demo day to restore zero minimum instances.'
