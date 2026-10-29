# GCP preparation for seo-audit-agent

Checked against Google's documentation on 2026-10-29. No resources have been
created, IAM policies changed, credentials validated, or live model calls made
by the agent. Phases 0–5 are implemented. The Docker image builds, its local
smoke test passes, and the full offline suite passes on Windows and Linux.
The setup/deployment scripts have been exercised only with a fake gcloud.

## What is needed now

Offline tests and local model-stub mode require nothing from GCP. Stub mode still
fetches real public URLs when used interactively. For the first real model call, prepare:

1. An intended Google Cloud **project ID** with billing enabled. Use the project
   ID, not its display name or project number. Have a project administrator enable
   `aiplatform.googleapis.com` and grant the developer identity
   `roles/aiplatform.user`. The quota project also needs
   `serviceusage.services.use`, normally through `roles/serviceusage.serviceUsageConsumer`.
2. A currently supported Gemini model identifier and the location where that
   model is available. Confirm structured JSON output support and usable quota.
   Set `MODEL_NAME` only after verifying the current identifier in your project.
   The application supplies no default model, project, or location.
3. Local Application Default Credentials (ADC) for that developer identity.
   gcloud CLI login and Python ADC are separate credential configurations.
4. A public contact URL for the fetch User-Agent.

Sources: [model setup and permissions](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/start),
[local ADC](https://docs.cloud.google.com/docs/authentication/set-up-adc-local-dev-environment),
[model locations](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/locations).
Google's current documentation uses Gemini Enterprise Agent Platform branding;
the SDK backend and IAM/API identifiers used here remain the aiplatform ones.

On this machine, gcloud and Docker executables, a configured CLI project, and a
local ADC file were detected. Their presence does not prove the intended account,
project, token validity, billing, model access, quota, or IAM permissions. In the
agent's process, MODEL_NAME and GOOGLE_CLOUD_LOCATION were not configured.
Credentials were not read or printed.

## Local PowerShell setup

Run these yourself, using your intended project/account. If the existing ADC is
already correct, keep it; otherwise authenticate it for the intended identity.

```powershell
$env:GOOGLE_CLOUD_PROJECT = "YOUR_PROJECT_ID"
$env:GOOGLE_CLOUD_LOCATION = "YOUR_VERIFIED_MODEL_LOCATION"
$env:MODEL_NAME = "YOUR_VERIFIED_MODEL_ID"
$env:CONTACT_URL = "https://YOUR_AGENCY_DOMAIN/contact"
$env:GOOGLE_GENAI_USE_ENTERPRISE = "1"

gcloud config set project $env:GOOGLE_CLOUD_PROJECT
gcloud services enable aiplatform.googleapis.com --project $env:GOOGLE_CLOUD_PROJECT
gcloud auth application-default login
gcloud auth application-default set-quota-project $env:GOOGLE_CLOUD_PROJECT
```

API enablement requires administrative permission. Granting roles also needs a
project administrator; these commands do not grant those roles. For ADC mode,
leave API-key environment variables unset. No service-account JSON key or API key
needs to be shared with this project or pasted into the chat.

The repository does not automatically load `.env`. Either set the variables in
the shell as above, or explicitly load your private `.env` locally. Never commit
credentials, ADC files, access codes, or keys.

Cloud Run's region and the model's location are separate settings. Cloud Run
needs a real supported region (`REGION` in the deployment scripts). A model may
use a supported regional endpoint or `global` where that model supports it.
Choose according to availability and data residency requirements; do not use
`global` as the Cloud Run deployment region.

## First application smoke test, after authentication

From the project root, open the project interpreter:

```powershell
.\.venv\Scripts\python.exe
```

Then enter the following, replacing the public page URL. This is an explicit
live fetch and potentially billable model call; it has not been executed here.

```python
import asyncio
import os
from seo_audit.service import AuditService

service = AuditService(
    model=os.environ["MODEL_NAME"],
    contact_url=os.environ["CONTACT_URL"],
)
report = asyncio.run(service.audit("https://YOUR_PUBLIC_PAGE_URL/"))
print(report.ai_review_available)
print(report.verification)
print(report.severity_counts)
```

A report alone does not prove a successful model call: check that
`ai_review_available` is true. Known provider/authentication failures yield a
rules-only report with a visible notice. Robots, unsafe URL, and fetch failures
remain errors. A successful empty AI review has a zero-count verification summary.

## Prepare for deployment later

The setup script enables these APIs (including IAM/Resource Manager for its
own service-account and role-binding operations):

```text
run.googleapis.com
cloudbuild.googleapis.com
artifactregistry.googleapis.com
aiplatform.googleapis.com
secretmanager.googleapis.com
logging.googleapis.com
iam.googleapis.com
cloudresourcemanager.googleapis.com
```

Keep these three identities distinct:

| Identity | Required application/deployment permissions |
| --- | --- |
| Runtime `seo-audit-sa` | `roles/aiplatform.user`, requested `roles/logging.logWriter`, and `roles/secretmanager.secretAccessor` on the single `access-code` secret |
| Human/source deployer | `roles/run.sourceDeveloper` and `roles/serviceusage.serviceUsageConsumer` on the project; `roles/iam.serviceAccountUser` on the runtime service account |
| Build `seo-audit-build` | `roles/run.builder` on the project; selected explicitly by deploy.sh |

This project overrides the default build identity with its own build account.
The human needs `iam.serviceAccounts.actAs` (normally the scoped
`roles/iam.serviceAccountUser` role) on the chosen build account as well as the
runtime account. Do not grant builder or administrator roles to the runtime
account to fix a build error. Public access additionally
requires permission to change Cloud Run IAM, and organization policies may
restrict public services. Have the administrator arrange that for deployment.
Sources: [source deployment permissions](https://docs.cloud.google.com/run/docs/deploying-source-code),
[custom build identity](https://docs.cloud.google.com/run/docs/configuring/services/build-service-account),
[runtime secret access](https://docs.cloud.google.com/run/docs/configuring/services/secrets).

Generate a strong ACCESS_CODE privately and store it as a version of the
`access-code` Secret Manager secret when preparing deployment. Runtime access
is granted on that secret, not every secret in the project. Cloud Run will use
its attached service account and ADC for model authentication.

Create billing budget alerts before live usage. An alerts-only budget does not
cap spending. Google also documents service-specific spend cap budgets for
eligible services, including Vertex and Cloud Run; configure them where your
account supports them. They use gross estimated costs, are scoped to one
project/service per cap, and enforcement is not instantaneous. Monitor remaining
credits as well as gross usage. The deployment uses at most two instances and zero
minimum instances by default; request rate limits are implemented per process.
Verify ASGI peer/proxy behavior before relying on per-reviewer IP separation. Source:
[budget behavior](https://docs.cloud.google.com/billing/docs/how-to/budgets),
[spend caps](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps).

## Execute later: setup and deployment

These commands change cloud resources. They are provided for the next checkpoint;
none have been executed against Google Cloud as part of Phase 5. Use an
administrator-approved project with billing enabled. The scripts do not create
a project or attach billing, and never change the gcloud configured project.

Use Google Cloud Shell, Linux/macOS Bash, or Git Bash on Windows. They are Bash
scripts, not PowerShell scripts. From the project root in Bash:

```bash
export PROJECT_ID="YOUR_PROJECT_ID"
export REGION="YOUR_CLOUD_RUN_REGION"
export GOOGLE_CLOUD_PROJECT="$PROJECT_ID"
export GOOGLE_CLOUD_LOCATION="YOUR_VERIFIED_MODEL_LOCATION"
export MODEL_NAME="YOUR_VERIFIED_MODEL_ID"
export CONTACT_URL="https://YOUR_PUBLIC_DOMAIN/contact"
export DEV_STUB_MODEL=false
# In Git Bash, if python3 isn't available, use the existing project Python:
# export PYTHON_BIN="$PWD/.venv/Scripts/python.exe"

bash deploy/setup_gcp.sh
```

Setup requires permissions to enable APIs, create service accounts/secrets,
and bind IAM policies. It creates missing resources and reapplies only its
required bindings, so rerunning it does not create duplicate accounts/secrets
or secret versions. It does not remove pre-existing IAM grants. Use dedicated
accounts/a dedicated project and review existing grants if these names already
exist. Permissions/network failures abort; they aren't treated as absence.

The runtime account receives only the two documented project roles and access
to the single access-code secret. The build account receives run.builder.
The script does not grant the human deployment permissions automatically; an
administrator must arrange the documented project and service-account roles.
For public access, the deployer additionally needs permission to set the Cloud
Run service IAM policy. Organization policies may disallow public invocation.

Add your private access code as a secret version in the Secret Manager console,
or use stdin in Bash so the secret value is not a command-line argument/history:

```bash
read -r -s -p "Choose a strong private access code: " audit_code
printf '\n'
printf '%s' "$audit_code" | gcloud secrets versions add access-code --data-file=- --project "$PROJECT_ID"
unset audit_code
```

Use a strong randomly generated value, save it privately, and do not paste it
into chat or commit it. No secret value is created by setup. Deployment checks
that the `latest` secret version exists and is enabled without reading its value.
The requested `access-code:latest` binding means a new instance reads the latest
version; coordinate rotation with a new revision to avoid mixed instance values.

After verifying the model, quotas, budgets and developer permissions:

```bash
bash deploy/deploy.sh
# Optional demo day: keep one instance warm (can incur idle charges).
bash deploy/deploy.sh --demo-day
# After the demo, redeploy normally to restore zero minimum instances.
```

Deployment builds the checked-in Dockerfile with the separate build account,
attaches the runtime account, exposes the public form behind application-level
audit access-code authentication, and configures:

| Setting | Value |
| --- | --- |
| Minimum instances | 0; 1 only with --demo-day |
| Maximum instances | 2 |
| Cloud Run concurrency | 4 per instance |
| Application active audits | 2 per process |
| CPU / memory | 1 CPU / 1 GiB |
| Platform / application deadline | 120 / 90 seconds |
| Model authentication | Vertex ADC through the runtime service account |
| Access code | ACCESS_CODE=access-code:latest |

Cloud Run region, model location, model ID and contact URL are read from the
environment. Project consistency and basic input shape are validated locally;
this does not prove regional model availability. A temporary JSON env-vars file
serializes shell-sensitive values and is deleted even when a cloud command fails.
No API key, ADC file, or local ACCESS_CODE value is copied to Cloud Run. Scripts
force Vertex mode, disable the local stub and disable content telemetry.

## Validate after deployment

Obtain the service URL from the deployment output. Verify the form and health
endpoint, then submit a known public HTML page using your access code. Check:

1. `/healthz` returns `{"status":"ok"}` and the form loads.
2. Wrong/missing access codes return 401 before fetch/model work.
3. A real audit returns `review_mode="live"`, `ai_review_available=true`, the
   configured model identity and verified/dropped counts. A rules-only report
   is useful degradation but does not validate successful model access.
4. Unsafe destinations are rejected; HTML/JSON contain the expected findings.
5. Cloud logs contain allowlisted structured events without codes, page bodies,
   model payloads, or audited query strings. Google-managed request/build logs
   are separate from application logging and should also be reviewed.
6. Proxy/client-IP behavior, latency, concurrent requests and actual costs are
   measured before depending on the service for multiple external reviewers.

Record actual results; the repository does not claim these cloud checks passed.
Residual check-then-connect DNS rebinding remains documented in README. Least
privilege reduces impact but isn't connection pinning or a network isolation
boundary. Keep the pilot restricted and evaluate this before unrestricted use.

Your reported credits expire **17 January 2027**. Confirm their remaining balance
and eligible charges in the billing console. After expiry/exhaustion, paid usage
can be billed according to your account status. Nothing here schedules spending,
deployment or a future shutdown.

This design does not need a database, vector store, Kubernetes cluster, or Agent
Engine deployment. Reports and sessions are ephemeral in the Cloud Run process.

## Non-secret values to share when ready

- Target project ID.
- Cloud Run region and model location, identified separately.
- Verified model ID.
- Public contact URL.
- Whether billing, model API enablement, and developer IAM are ready.

Keep access codes, API keys, ADC files, private keys, and tokens private. The
agent does not need them in chat. Initial IAM/billing setup is an administrator
task; the user remains in control of changes to the cloud project.
