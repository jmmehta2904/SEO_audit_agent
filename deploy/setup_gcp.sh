#!/usr/bin/env bash
# Administrator-run, idempotent provisioning. Does not deploy the service.
set -euo pipefail
if (($#)); then printf '%s\n' 'Usage: bash deploy/setup_gcp.sh (PROJECT_ID and REGION required)' >&2; exit 2; fi
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com aiplatform.googleapis.com \
  secretmanager.googleapis.com logging.googleapis.com iam.googleapis.com \
  cloudresourcemanager.googleapis.com --project "$PROJECT_ID" --quiet

# Successful list calls distinguish absence from permission/network failures.
for account in seo-audit-sa seo-audit-build; do
  email="${account}@${PROJECT_ID}.iam.gserviceaccount.com"
  found="$(gcloud iam service-accounts list --project "$PROJECT_ID" \
    --filter "email=${email}" --format 'value(email)')"
  if [[ -z "$found" ]]; then
    gcloud iam service-accounts create "$account" --project "$PROJECT_ID" \
      --display-name "$account" --quiet
  fi
done

# The runtime identity gets only these project roles and one secret binding.
for role in roles/aiplatform.user roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:${RUNTIME_SA}" --role "$role" --condition=None --quiet >/dev/null
done
# Build permissions never go to the runtime identity.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:${BUILD_SA}" --role roles/run.builder --condition=None --quiet >/dev/null

found="$(gcloud secrets list --project "$PROJECT_ID" --filter 'name:access-code' --format 'value(name.basename())')"
if ! printf '%s\n' "$found" | grep -Fxq 'access-code'; then
  gcloud secrets create access-code --replication-policy automatic --project "$PROJECT_ID" --quiet
fi
gcloud secrets add-iam-policy-binding access-code --project "$PROJECT_ID" \
  --member "serviceAccount:${RUNTIME_SA}" --role roles/secretmanager.secretAccessor \
  --condition=None --quiet >/dev/null

printf '%s\n' \
  'Setup complete. No service was deployed and no secret value was generated.' \
  'Next: add a private access-code secret version via the console or stdin.' \
  'Arrange deployer permissions documented in docs/GCP_SETUP.md.' \
  'Create billing budget alerts and eligible service spend caps in the console.' \
  'Validate the model, quota and remaining credit balance before deployment.'
