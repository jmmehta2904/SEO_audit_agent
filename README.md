# seo-audit-agent

Evidence-checked SEO review for agency reviewers. **Phases 0–5 are implemented:**
the repository contains pinned dependencies, offline ADK contract probes, safe
public-page fetching, bounded fact extraction, deterministic SEO checks, AI
evidence verification, escaped HTML/JSON reports, and the complete ADK workflow
with bounded retries and rules-only recovery. FastAPI now serves the form,
authenticated HTML/JSON audits, and health checks, with bounded request work,
rate limits, and safe logging. Tests use mocked websites/models. A real loopback
Uvicorn smoke test passed. Phase 5 adds ten saved evaluation pages, a tested
non-root Python 3.12 image, and GCP setup/deployment scripts. **No live Vertex
call, GCP resource provisioning, or cloud deployment was run.**

## Architecture

```text
URL + access code -> FastAPI -> fresh in-memory ADK session
  validate_url -> fetch_page -> extract_facts -> rule_findings
    -> auditor (LlmAgent) -> verify -> build_report -> HTML / JSON
                               |
              AI failure -> rules-only report with visible notice
```

## Local environment

Use Python 3.12. The initial environment was provisioned locally with uv 0.13.6:
CPython 3.12.16 lives in `.runtime/`, and `.venv/` uses that runtime. The existing
system Python 3.13.7 and its ADK 2.9.0 installation were left unchanged. uv is a
bootstrap tool under `.tools/`, not an application dependency.

For a fresh Windows checkout with Python 3.12 installed:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/inspect_adk.py
```

On Linux/macOS, use `python3.12 -m venv .venv` and `.venv/bin/python` in place of
the Windows executable. The Python 3.12 Linux image now builds and passes the
full offline suite. `requirements.lock` constrains all 58 resolved packages; direct
dependencies are listed in `requirements.txt`.

The initial Windows venv bootstrap could not complete `ensurepip` in the sandbox;
uv installed dependencies directly into the venv without needing pip. To add
dependencies to this existing environment, use:

```powershell
.\.tools\bin\uv.exe pip install --python .venv\Scripts\python.exe --cache-dir .cache\uv -r requirements.txt
```

Normal tests need no credentials or network. The test guard rejects DNS lookup
and socket connections, except the synchronous creation of an internal socket
pair used by asyncio. Windows implements this pair using local TCP. The desktop
sandbox stalled its accept operation, so the successful test run used an approved
execution outside that sandbox, with the application network guard active.

The web server is available now. For a local model-stub run in PowerShell:

```powershell
$auditAccessSecret = Read-Host "Choose a private access code" -AsSecureString
$env:ACCESS_CODE = [System.Net.NetworkCredential]::new("", $auditAccessSecret).Password
$env:MODEL_NAME = "local-stub"
$env:CONTACT_URL = "https://YOUR_AGENCY_DOMAIN/contact"
$env:DEV_STUB_MODEL = "true"
$env:LOG_LEVEL = "INFO"
.\.venv\Scripts\python.exe -m uvicorn app.server:app --host 127.0.0.1 --port 8080 --no-proxy-headers --no-access-log
```

Open `http://127.0.0.1:8080/` and enter the same access code. Stub mode still
fetches real public pages and honors robots; only the model is replaced. Reports
prominently identify stub mode and return `review_mode: "stub"` in JSON. It does
not validate real AI recommendations and is forbidden on Cloud Run. The above
MODEL_NAME is an explicit development label, not a production model default.

For a live Vertex model, follow [GCP_SETUP.md](docs/GCP_SETUP.md), set
DEV_STUB_MODEL=false and the verified model/project/location settings. For local
API-key development only, set GOOGLE_GENAI_USE_ENTERPRISE=0 and GOOGLE_API_KEY.
Leave GEMINI_API_KEY unset; ambiguous/unsupported key aliases are rejected. No
real key or model name is supplied by the application. The offline evaluator
is available in `eval/run_eval.py`; its default mode needs no credentials.

## Environment variables

See `.env.example`. Copy it to `.env` if useful, but explicitly load values into
your shell; the scaffold does not automatically load dotenv files.

| Variable | Intended use |
| --- | --- |
| `ACCESS_CODE` | Required shared access secret; no default |
| `MODEL_NAME` | Required model identifier; verify the current name |
| `GOOGLE_CLOUD_PROJECT` | Vertex project; no default |
| `GOOGLE_CLOUD_LOCATION` | Vertex location; no default |
| `CONTACT_URL` | Honest fetch User-Agent contact; no default |
| `GOOGLE_GENAI_USE_ENTERPRISE` | `1` for Vertex ADC; `0` for local API-key mode |
| `LOG_LEVEL` | Structured log verbosity; example `INFO` |
| `GOOGLE_API_KEY` | Optional local development alternative only |
| `DEV_STUB_MODEL` | Explicit offline local mode, default disabled; implemented in Phase 4 |
| `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS` | Enforced `false`; disables legacy span content |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | Enforced `NO_CONTENT` |
| `ADK_EXPERIMENTAL_TELEMETRY` | Enforced `false` |
| `ADK_EXPERIMENTAL_TELEMETRY_FEATURES` | Enforced empty |
| `PROJECT_ID` | Deploy helper; must match GOOGLE_CLOUD_PROJECT when both are set |
| `REGION` | Cloud Run deployment region, distinct from model location |
| `PYTHON_BIN` | Optional Python 3 executable for Bash deployment scripts; defaults to python3 |

`K_SERVICE` is inspected as Cloud Run's platform marker. Cloud Run rejects stub
mode, local API-key mode, and a GOOGLE_APPLICATION_CREDENTIALS file override; it
uses its attached service account through ADC. Health checks never initialize a
model client or validate cloud permissions. Startup fails with sanitized setting
names if required configuration is missing; secret values are never echoed.

Never commit access codes, keys, ADC files, or service-account credentials. Cloud
Run will use service-account ADC and Secret Manager, with no deployed API key.

## Decisions

1. Work sequentially through Phases 0–5. Each phase ends with `pytest -q`, a
   summary, and a stop until the user continues. Phase 0 was committed before
   the user took over Git operations. From Phase 1 onward, staging and commits
   are handled by the user; the agent leaves changes ready for review.
2. Target Python 3.12; use the isolated project runtime rather than modifying
   system Python. Ignore runtime, venv, bootstrap tools, caches, and secrets.
3. Pin `google-adk==2.13.0`. Its resolved SDK is `google-genai==2.33.0`, with
   `pydantic==2.14.1`. Constrain transitive versions to retain inspected behavior.
4. Source inspection confirms **`GOOGLE_GENAI_USE_ENTERPRISE=1`** selects Vertex.
   ADK `utils/env_utils.py` checks it first; the deprecated
   `GOOGLE_GENAI_USE_VERTEXAI` alias is used only when the newer variable is
   absent. GenAI `_api_client.py` likewise gives ENTERPRISE precedence when both
   environment values conflict and warns. Use only the newer variable. GenAI
   recognizes `true` and `1` (case-insensitive) as enabled. This is a source
   verification, not a live Vertex authentication test.
5. Inspected Workflow supports `edges=[(START, step, auditor, finish)]`.
   `@node` defaults to `parameter_binding='state'`, accepts `timeout` and
   `retry_config`, and writes state via `ctx.state`. The imported `START` is a
   sentinel node, not an assumed literal string. Use `Runner(node=workflow, ...)`.
6. `RetryConfig` fields are `max_attempts`, `initial_delay`, `max_delay`,
   `backoff_factor`, `jitter`, and `exceptions`. Attempts include the initial
   call; exception classes are normalized to names. Set explicit bounds rather
   than relying on the five-attempt default. LlmAgent also accepts `retry_config`.
7. The smoke tests confirm `BaseLlm.generate_content_async` can yield
   `LlmResponse` with `types.Content`/`types.Part`; `output_schema` plus
   `output_key` stores a parsed dictionary. Production verification will accept
   a dictionary or JSON string as requested. Prompt placeholders bind from state.
8. Completed node state survives exhausted LLM retries; the original
   `ConnectionError` propagates from Runner in the tested case. This enables
   rules-only recovery later. Session create/get/delete and Runner close are
   asynchronous and exercised in the smoke tests.
9. Evidence checks establish that quoted text or metrics exist, not that an AI
   judgment is correct. Reports will explain this and contain no numeric SEO score.
10. Planned safe defaults: fail closed for unavailable robots rules except
    404/410; recheck redirect destinations; no environment HTTP proxies;
    ephemeral reports with browser-generated JSON downloads; no arbitrary
    forwarded-IP trust; no automatic live cloud deployment.
11. Fetch limits are 2 MiB for HTML and 512 KiB for robots.txt, with three
    redirects per resource chain. A total ten-second deadline covers DNS,
    robots, redirects, and reading the page; connect timeout is five seconds.
    DNS runs off the event loop. Cancellation stops waiting, but cannot kill an
    operating-system resolver thread that is already running.
12. Request identity encoding and reject responses that remain compressed, to
    avoid allocating a decompression bomb before the streamed limit is checked.
    This deliberately refuses servers that ignore the identity request. HTML
    uses the HTTP charset with UTF-8 fallback; robots requires valid UTF-8 and
    text/plain. Missing or unexpected media types fail closed.
13. Robots policies are fetched via the same URL, DNS, redirect, timeout, and
    size controls, then cached per origin only for the current audit. Every
    page redirect checks the destination's policy. Robots resource requests are
    exempt from recursive robots checks. Only 404/410 permits a missing file;
    other failures refuse the page. Transient failures are distinguishable for
    bounded workflow retries implemented in Phase 3.
14. Use a small local matcher for robots, since the inspected Python 3.12
    `urllib.robotparser` implementation lacks wildcard and longest-match
    behavior. The matcher covers exact product-token groups, merged groups,
    wildcard fallback, case-sensitive paths and queries, percent normalization,
    `*`, terminal `$`, and allow on equal specificity, using literal-segment
    matching instead of untrusted regular expressions. These matching rules
    follow [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html); our three-hop
    cap and fail-closed HTTP error policy are deliberate stricter service limits.
15. Extraction removes script, style, noscript, and template content and never
    fetches linked assets. Body text excludes metadata. The first title and
    description are measured after whitespace normalization. Counts cover the
    full parsed HTML, even where evidence samples are truncated. Store at most
    1,000 title characters, 2,000 description characters, 20 headings total,
    20 link texts, and 20 alt texts (300 characters per item), plus a 6,000
    character body excerpt. Canonical, viewport, and language samples are capped
    at 2,048, 512, and 128 characters. Word count uses Unicode word tokens and
    is not language-aware segmentation.
16. Only absent alt attributes are flagged: `alt=""` is valid for decorative
    images. Canonical and viewport checks require non-empty attribute values;
    they do not validate URL preferences or responsive layout. Open Graph
    presence means a meta element with a property beginning `og:`. All stored
    page text remains untrusted and is escaped when rendered.
17. AI categories are `title_quality`, `keyword_stuffing`, `heading_quality`, and
    `cta_quality`. Proposals must have non-blank title, explanation, and fix;
    quote evidence requires only a non-blank quote, and metric evidence requires
    only a known scalar fact key and value. Null unused evidence fields are
    permitted for structured model output. Extra fields and mixed evidence modes
    are rejected. The schema limits output to 30 proposals and bounds text fields.
18. Quote checks collapse whitespace and casefold, then require an exact
    substring of retained evidence. There is no fuzzy, punctuation, or HTML-entity
    matching. Metric checks require identical scalar types and values: `true`,
    `1.0`, and `"1"` cannot substitute for an integer `1`. Lists, dictionaries,
    unknown fact names, and dotted paths are not valid metric evidence.
19. The verifier accepts an Audit model, state dictionary, or JSON string. It
    drops malformed list items individually, retains valid neighbors, and counts
    overflow beyond 30 proposals as dropped. Raw JSON is capped at 128 Ki
    characters. An unreadable envelope raises a sanitized `AuditFormatError`;
    it is not silently counted as a successful zero-proposal review. The Phase 3
    structured-output step may reject malformed schemas before verification;
    that case uses the rules-only failure path after bounded retries.
20. Reports combine code checks with verified AI findings, compute severity
    counts, require an explicit model identity, and record installed ADK version
    and a timezone-aware UTC timestamp. AI-unavailable reports have a visible
    notice and `verification: null`, because failed proposal counts are unknown.
    A successful empty AI review instead reports zero proposed/verified/dropped.
21. Jinja autoescape is enabled unconditionally and undefined values are errors.
    Page/model content is never trusted markup. Page and canonical URLs are
    displayed as text. The JSON download is a fixed application/json base64
    data link with a fixed filename: the browser downloads it without scripts,
    additional fetches, or server-side report persistence. This is the simplest
    self-contained form of the planned browser-handled download. No `safe`
    template filter, executable scripts, external styles, or external assets are
    needed for the report.
22. The production graph is one Workflow in the requested order. Six code nodes
    surround the tool-free auditor LlmAgent. State contains JSON-compatible
    values only, and completed nodes reassign state entries rather than mutating
    nested lists. Raw HTML is cleared from current state after extraction; the
    request's ephemeral event history may still hold it until session deletion.
23. The prompt contains only bounded Facts JSON and a JSON-encoded body excerpt
    of at most 6,000 original characters. Angle brackets are escaped to prevent
    forged delimiters. Facts JSON has a 32,000-character encoded budget; text
    samples shrink if necessary, while numeric measurements remain unchanged.
    Session history is excluded from model input. The auditor has no tools or
    agent transfers, uses temperature 0, and requests at most 4,096 output tokens.
24. Validation/fetch retry only TransientFetchError, at most three attempts.
    Model retries cover transient provider/connection failures, malformed schema
    output, and model-node timeouts, also at most three attempts. Initial delay
    is 0.5 seconds, doubling to at most one second. Each model attempt has a
    15-second node timeout; SDK HTTP attempts are set to one to avoid multiplying
    retries. The full audit has a 90-second deadline. Permanent provider/auth
    failures degrade immediately; security and permanent fetch errors propagate.
25. Recovery checks ADK's failing node-path metadata and exception type, then
    reads the committed facts/rule checkpoint. Event author alone is unreliable
    for this graph. Unrelated extraction, verification, and report programming
    failures are not converted into AI-unavailable reports. The copied session
    returned by get_session is updated through append_event for recovered state.
26. AuditService reuses its graph/model but creates a unique session per request.
    It deletes sessions and closes each Runner after success, failure, timeout,
    or cancellation. An overall deadline/cancellation propagates, rather than
    being presented as a completed AI review. Worker-thread operations already
    running may finish after cancellation; no database or report storage is used.
27. ADK/GenAI/HTTP and asyncio log messages are replaced by allowlisted dependency
    metadata, with message arguments and exception/stack content removed by an
    idempotent process-wide log-record guard. ADK 2.13.0 defaults to legacy span
    content capture, so this service enforces the privacy env controls above and
    explicit per-request NO_CONTENT telemetry. No tracing exporter is installed
    by the application. Phase 4 wires application stdout JSON logging and includes
    Uvicorn logs in the dependency privacy guard.
28. Settings use Pydantic BaseModel and SecretStr, read explicit environment
    variables, and never automatically load `.env`. ACCESS_CODE has no default,
    must be non-blank valid Unicode, and is capped at 512 characters. Model and
    contact are always explicit. Vertex requires project/location and no API key;
    local API-key mode is an explicit development alternative. Secret-containing
    provider connection kwargs are excluded from model repr/serialization.
29. Both audit endpoints authenticate using UTF-8 bytes and hmac.compare_digest
    before URL validation, rate admission, fetching, or model work. Wrong/missing
    codes return 401. Only URL and access code fields are accepted. Request bodies
    are capped at 16 KiB, including chunked input, and parsing is inside the
    90-second deadline. Duplicate fields, malformed payloads, and file uploads
    are rejected without reflecting the submitted body or access code.
30. The in-memory sliding window admits five authenticated attempts per peer IP
    per hour across both endpoints. Failed/queued authenticated attempts count;
    unauthenticated requests do not consume the audit quota. Entries expire at
    one hour; at most 4,096 active IP buckets are retained, refusing new buckets
    at capacity rather than resetting existing limits. 429 includes Retry-After.
31. Two audits can hold the process semaphore, including report rendering. Queue
    wait and body parsing count against the 90-second deadline. Disconnects and
    cancellation cancel the worker and drain cleanup; permits and ephemeral ADK
    sessions are released. Known AI failures still return successful rules-only
    reports. Unexpected errors return a generic 500 without stack traces.
32. The app uses only the ASGI peer address, never arbitrary X-Forwarded-For or
    Forwarded headers. Run one Uvicorn worker with proxy header rewriting disabled
    by default. Behind a reverse proxy, multiple reviewers may share a bucket
    until trusted proxy handling is explicitly configured and validated. Limits
    and semaphores are per process/instance; workers/replicas multiply them.
33. JSON logs use an allowlist of event/status/duration/request-ID/route fields,
    not arbitrary messages, exceptions, URLs, IPs, headers, or bodies. Request
    IDs are generated locally. Responses have no-store, nosniff, no-referrer,
    and a restrictive script-free CSP. Browser and API errors are sanitized.
34. Model clients authenticate lazily. Lifespan owns and closes async and sync
    model transports without initializing unused clients at shutdown. Stub mode
    always returns an empty AI proposal list, is visibly labelled, and preserves
    real deterministic checks. It is never represented as a real model review.

Reproduce signatures and source excerpts with `scripts/inspect_adk.py`; the
Phase 0 output is saved in `docs/adk-introspection.txt`. No deprecated
SequentialAgent is used.

## Security model and limitations

Implemented in Phase 1: only HTTP/HTTPS on ports 80/443, no userinfo, at most
2,048 characters, no whitespace/control/backslash parser ambiguities, explicit
metadata-host blocking, and checks on every DNS answer, including mapped IPv6.
Private, loopback, link-local, multicast, reserved, unspecified, and other
non-global destinations are blocked. Numeric IP spellings are checked through
their resolved addresses. Every HTTP hop is manually checked, TLS verification
stays enabled, environment proxies are disabled, and cookies are cleared before
each request. Robots are checked before requesting a page, including redirect
destinations. Errors contain no page bodies or underlying transport details.

**Residual DNS rebinding:** validation and HTTPX connection establishment perform
separate resolutions; an attacker may change DNS between them. This is not IP
pinning or a complete SSRF isolation boundary. Cloud Run deployment will use the
specified least-privilege runtime service account. Narrow IAM reduces potential
impact but does not eliminate this risk. No live-network or cloud security test
has been performed at this checkpoint.

The final service is an automated first-pass review, not an authoritative SEO
audit. It covers one page based on fetched HTML without JavaScript rendering.
Rate limiting is per-instance and best-effort; deployment scripts cap Cloud Run
instances at two. Reports are not persisted.

Report HTML is escaped, evidence verification and the production LLM workflow
are implemented, and the FastAPI access controls are covered by offline tests.
Evidence matching alone does not establish whether an AI recommendation is
correct. No live model has been used to validate prompt-injection behavior.

## Phase 1 interfaces and rules

- `await validate_url(url)` returns a normalized URL after checking DNS.
- `await safe_fetch(url, contact_url=...)` returns `FetchedPage(url, html,
  content_type)` after robots and bounded fetching. Its optional transport
  argument is for trusted test injection, not an HTTP API input.
- `extract_facts(html, url=...)` returns `ExtractedPage(facts, body_excerpt,
  evidence_text)`. Facts and Finding models round-trip through JSON for future
  ADK session state.
- `rule_findings(facts)` returns an ordered list of deterministic findings.
  `UnsafeURL`, `RobotsDisallowed`, and `FetchError` are intended for 400, 403,
  and 502 responses. `TransientFetchError` is retryable. The web layer will map
  errors in Phase 4; there is no running API yet.

| Finding identifier | Trigger | Severity |
| --- | --- | --- |
| `title_missing` | No non-empty first title | high |
| `title_too_short` | Title length below 30 | medium |
| `title_too_long` | Title length above 60 | medium |
| `meta_description_missing` | No non-empty first description | medium |
| `meta_description_length` | Non-empty length outside 70–160 | low |
| `h1_count` | Number of H1 elements differs from one | medium |
| `images_without_alt` | One or more img elements lack alt | medium |
| `canonical_missing` | No canonical link with non-empty href | medium |
| `viewport_missing` | No viewport meta with non-empty content | medium |
| `html_lang_missing` | No non-empty html lang | low |
| `low_word_count` | Fewer than 300 body word tokens | low |
| `open_graph_missing` | No Open Graph meta properties | low |

Missing title/description findings suppress redundant length findings. These
ranges are review heuristics, not universal search-engine limits or SEO scores.

## Phase 2 interfaces and report contract

- `verify_audit(payload, facts=..., evidence_text=...)` returns
  `VerificationResult(findings, summary)`. Its summary contains `proposed`,
  `verified`, and `dropped`, with `proposed == verified + dropped`. Returned
  findings always have source `AI, verified` and a subjective category.
- `build_report(facts, code_findings, verification, model_name=...)` combines
  saved deterministic checks with those verified findings. For a rules-only
  report, pass `verification=None, ai_review_available=False`. A missing
  verification result cannot silently become a successful AI review.
- `render_html(report)` produces the standalone Jinja report, with severity
  counts, source-labelled findings/evidence/fixes, facts, model/version/time,
  verification counts, limitations, print CSS, and JSON download.
- `render_json(report)` serializes the same Report model. Its top-level keys
  are `url`, `facts`, `findings`, `severity_counts`, `verification`,
  `ai_review_available`, `ai_notice`, `generated_at`, `model_name`, `adk_version`,
  `limitations`, and the additive `review_mode` field (`live` or `stub`). Finding
  adds optional `category` to the Phase 1 contract. Report rendering itself does
  not call a model, fetch a website, or write a report file.

## Phase 3 workflow and GCP setup

`build_workflow(model, contact_url=...)` creates the single ADK graph. The model
must be an explicit configured name or a BaseLlm instance. Its fetcher injection
point is trusted application/test configuration, never an HTTP request field.

The future server calls:

```python
service = AuditService(model=configured_model, contact_url=configured_contact)
report = await service.audit(public_url)
```

AuditService is defined in `seo_audit/service.py`; nodes, graph, and prompt live
in `nodes.py`, `workflow.py`, and `prompts.py`. Settings loading, authentication,
rate limits, global concurrency control, and server lifecycle wiring are now
implemented in Phase 4. The service can also be called directly from Python.

See [GCP_SETUP.md](docs/GCP_SETUP.md) for the current setup checklist and a manual
live-call example. Nothing in GCP is needed to run offline tests. A live call
requires a billing-enabled intended project, model API and developer IAM,
verified model/location, ADC, and a contact URL. The guide distinguishes developer,
runtime, and build identities and includes future deployment permissions.

gcloud/Docker executables, a configured CLI project, and an ADC file were detected
locally; credential validity and cloud permissions have not been tested. MODEL_NAME
and GOOGLE_CLOUD_LOCATION were unset in the agent's environment. No credentials
were read or printed, and no cloud resources were created or modified.

## Phase 4 HTTP interfaces

| Endpoint | Input | Response |
| --- | --- | --- |
| `GET /` | None | URL/access-code form, including a visible local-stub warning where applicable |
| `GET /healthz` | None; no access code | `{"status":"ok"}`; no network/model initialization |
| `POST /audit` | URL-encoded or multipart `url` and `access_code`; no files | Escaped, printable HTML report |
| `POST /api/audit` | JSON object containing `url` and `access_code` | JSON from the same Report contract |

Example JSON request shape (use your private access code, never paste it in logs):

```json
{"url":"https://YOUR_PUBLIC_PAGE_URL/","access_code":"YOUR_PRIVATE_ACCESS_CODE"}
```

Both audit routes apply the same authentication, sliding-window quota, semaphore,
deadline, and error mappings. Error JSON contains `detail` and `request_id`;
HTML errors link back to the form. Reports and JSON downloads are not persisted.
Wrong input/unsafe URLs return 400, authentication 401, robots disallow 403,
oversized request 413, wrong request media type 415, rate limiting 429, unexpected
errors 500, fetch failure 502, and overall deadline 504. A disconnected client is
recorded as 499 after cancelling work; the client may already have closed its
connection. Missing/malformed AI review alone yields a 200 rules-only report.

`app/server.py` exposes `app` and a testable `create_app` factory. Configuration
and runtime initialization occur in lifespan; model clients are lazy and closed
at shutdown. `app/runtime.py` manages the live provider/development stub;
`app/limits.py` owns the rate limiter. Test injection hooks are trusted Python
configuration and are not HTTP parameters. Standard Swagger/OpenAPI UI routes
are disabled; these four interfaces are documented here.

The deployment must verify how its proxy supplies ASGI peer addresses. The app
deliberately does not trust client-supplied forwarding headers. With the default
proxy-disabled configuration, Cloud Run/proxy addresses can produce coarser
shared quotas; accurately separating reviewers behind a trusted proxy remains
deployment-specific validation. This is a best-effort abuse control, not a
distributed client identity or durable usage accounting system.

## Validation record

Phase 0, 2026-10-28:

- Python 3.12.16 / ADK 2.13.0 / GenAI 2.33.0 installed in isolated environment.
- Installed-source introspection script completed without model network calls.
- Final `python -m pytest -q`: **5 passed in 1.14s**.
- Offline contract probes: state binding, chained nodes, prompt substitution,
  structured stub output, transient node retry, model retry exhaustion,
  preserved earlier state, and session deletion. Network guard self-test passed.
- Initial test run: 2 passed / 3 setup errors because the network guard blocked
  Windows asyncio socketpair creation. After narrowing the guard, the sandbox
  still stalled local socketpair accept; approved unsandboxed execution passed.
- Not tested: production security/extraction/report/server behavior, live model
  or Vertex ADC, evaluation corpus, Docker build, Linux runtime, GCP scripts, or
  Cloud Run deployment. These belong to later checkpoints.

Phase 1, 2026-10-28:

- Tests were written before security implementation; the first run failed at
  collection because the fetch API did not exist. Extraction/rule tests likewise
  failed at collection before those modules were implemented.
- Full `python -m pytest -q`: **176 passed in 2.51s**, offline, including the
  five original ADK smoke tests. Windows asyncio still required approved
  execution outside the desktop sandbox; all application DNS and HTTP traffic
  in security tests was mocked.
- Covered: forbidden and mixed DNS answers, metadata, mapped IPv6, numeric IP
  spellings, IDNs, URL ambiguity/userinfo/ports, revalidated redirects and limits,
  robots wildcard/precedence/group/encoding behavior, refusal on robots errors,
  stream size enforcement and closure, MIME and charset handling, identity
  encoding, no cookie replay, proxy settings, slow DNS and body deadlines,
  bounded extraction, malformed HTML, all deterministic rules and thresholds,
  and JSON round-trips. Fixtures include injection text and a hostile title.
- One initial slow-stream timeout assertion expired during Windows setup before
  reaching the stream; the test now allows setup time and delays the first body
  chunk past the deadline. The full suite then passed.
- Not tested: real Internet fetching or DNS rebinding prevention, AI evidence
  verification/injection defense, report XSS escaping, production workflow,
  server/API/auth/rate limits, live Vertex, evaluation, Docker/Linux, or GCP.
  The injection and hostile-title fixtures currently test extraction only.
- No Git staging or commit was performed for Phase 1; the user handles Git.

Phase 2, 2026-10-28:

- Added tests first; the initial run had two collection errors because the new
  Audit/Report APIs were still absent.
- Full `python -m pytest -q`: **247 passed in 2.75s**, including 71 new Phase 2
  cases and all 176 existing cases. The suite remains offline; Windows asyncio
  required the same approved execution outside the desktop sandbox.
- Covered: quote pass/fail/normalization, scalar metric type/value matching,
  zero values, unknown or nested fact keys, malformed proposals/envelopes,
  overflow and output limits, balanced counts, schema generation, all supported
  state representations, retained code checks, available/unavailable/empty AI
  review reports, timestamp normalization, report JSON round-trips, and XSS in
  page/model strings. Downloaded JSON was decoded and compared to the exact
  serialized report, including the hostile-title fixture.
- HTML DOM tests confirm no script/image/iframe/SVG injection, event-handler
  attributes, or attacker-controlled executable links. Print CSS is present.
- Not tested: browser interaction with the download, visual browser/PDF print
  layout, live website fetches, actual LLM prompt-injection resistance, live
  Vertex/ADC, production workflow recovery, FastAPI, evaluation, Docker/Linux,
  or deployment. Rules-only report construction is tested here; catching workflow
  exceptions and recovering session state belongs to Phase 3.
- No Git staging or commit was performed; the user handles Git.

Phase 3, 2026-10-28:

- Tests were added first; the initial run failed at collection while production
  workflow APIs were absent. Inspected installed ADK/GenAI signatures and source
  before using new callbacks, event metadata, telemetry, and SDK retry settings;
  `scripts/inspect_adk.py` and its saved output were extended accordingly.
- Final full `python -m pytest -q`: **287 passed in 7.41s**, including 40 new
  Phase 3 cases. No real DNS, website, credential refresh, or model HTTP call was
  required. Windows asyncio used the same approved execution outside the sandbox.
- Tested the actual seven-step Workflow, verified/fabricated proposals, injection
  fixtures and forged prompt delimiters, bounded prompt projection, transient
  fetch/model retries, provider 403/503, malformed/empty output, model timeouts,
  strict metric types after ADK serialization, rejection of model tool attempts,
  rules-only recovery from committed state, independent concurrent sessions,
  cleanup on success/error/cancellation, and the overall deadline.
- Privacy tests exercise dependency message/exception redaction and enforced
  telemetry settings, including inherited content-capture overrides. A separate
  test uses the real installed Gemini/GenAI adapter with inert test credentials
  and mocked Vertex HTTP to verify structured-schema request serialization.
- Integration testing identified SDK schema objects that cannot be blindly JSON
  dumped in test spies, node-path versus author metadata for recovery, and a test
  credential refresh requirement. These were corrected without weakening the
  fallback boundary or making any live model call.
- Not tested: real Vertex/ADC, actual model quality or live prompt-injection
  behavior, real websites, browser/PDF printing, production FastAPI access
  controls, evaluation corpus, container build/Linux runtime, or GCP deployment.
  Mocked provider serialization does not establish real endpoint/model acceptance.
- No Git staging or commit was performed; the user handles Git.

Phase 4, 2026-10-28:

- Added configuration/API tests first; the initial run failed at collection while
  Settings and app runtime/server modules were absent. No dependencies were added.
- Final full `python -m pytest -q`: **358 passed, 1 warning in 11.39s**,
  including 71 new Phase 4 cases and all 287 existing cases. Compilation checks
  also passed. The warning is the upstream TestClient notice described below.
- Covered form/health/report routes, both auth formats, malformed Unicode codes,
  sanitized errors, bounded JSON/form/chunked bodies, duplicate inputs, multipart
  file refusal, shared and per-IP quotas, spoofed forwarding headers, two-work
  concurrency, queued deadlines, disconnect cancellation, failure permit release,
  real ADK session deletion through HTTP timeout, rules-only recovery, lazy model
  ownership, transport shutdown, visible stub metadata, and stdout log privacy.
- A real Uvicorn process was launched only on 127.0.0.1 with a generated private
  code and local model stub, then stopped. Health/form returned 200, wrong code
  returned 401, authenticated malformed URL returned 400, and stdout logs were
  JSON without the access code. No external website or model was contacted.
- One known upstream Starlette TestClient deprecation warning recommends httpx2;
  the pinned httpx compatibility path passed. Kept the requested minimal dependencies.
- Not tested: real Vertex/ADC, real website fetching, browser download/visual PDF
  interaction, Cloud Run proxy behavior, distributed rate limits, production
  deployment, Docker/Linux, or the Phase 5 evaluation corpus.
- No Git staging or commit was performed; the user handles Git.

## Phase 5: evaluation, container and deployment

Run the saved-page evaluation locally, without credentials or network:

```powershell
.\.venv\Scripts\python.exe eval/run_eval.py
```

The [recorded Markdown table](eval/RESULTS.md) reports **10 cases, 20 expected
findings, 20 true positives, 0 false positives, 0 false negatives, 100.0% total
precision and 100.0% total recall**. These are synthetic regression cases with
manually assigned identifiers covering all 12 rules, not measured effectiveness
on arbitrary websites or search rankings. The no-findings control reports N/A
for zero denominators; totals are micro-averaged. See [evaluation details](eval/README.md).

Once you have configured Vertex ADC, a verified model name, project and model
location, this opt-in command makes potentially billable model calls:

```powershell
.\.venv\Scripts\python.exe eval/run_eval.py --live
```

Live evaluation sends saved facts/text through the inspected ADK Gemini adapter,
production prompt, Audit schema and evidence verifier; it does not fetch fixture
URLs or exercise the HTTP workflow. It uses one model attempt per page and a
45-second per-page deadline. It adds proposed/verified/dropped columns, marks
failures unavailable, and prints totals with a completed-review denominator.
It requires no server access code. Disable stub mode and unset API keys first.
Exit codes: 0 exact rule agreement and all requested reviews complete; 1 rule
mismatches; 2 bad configuration or an unavailable live review. Live results have
not been recorded. Stub tests verify fabricated-evidence drops in this path.

Build and smoke-test the image with Docker Desktop's Linux engine running:

```powershell
docker build --tag seo-audit-agent:phase5 .
.\.venv\Scripts\python.exe scripts/smoke_container.py
```

The smoke script starts an ephemeral container with **external networking disabled**,
a read-only filesystem, dropped capabilities, and a generated ephemeral access
code passed through the child environment. It tests health, form, wrong-code 401,
authenticated invalid-URL HTML/JSON 400, non-root UID 10001, Python/ADK versions,
and `${PORT:-8080}` expansion using port 18081. It checks JSON stdout and Cloud
Run stub rejection, then removes its test container. It makes no website/model
requests. Full successful audit reports are exercised by the offline API tests.

For an interactive local container, set your own private ACCESS_CODE environment
variable and explicit model/project/contact settings first. Vertex mode uses
ADC; the local stub alternative can be started as follows:

```powershell
docker run --rm -p 127.0.0.1:8080:8080 --env ACCESS_CODE --env MODEL_NAME=local-stub --env DEV_STUB_MODEL=true --env CONTACT_URL seo-audit-agent:phase5
```

Open `http://127.0.0.1:8080/`. Stub mode performs real public-page fetches but
does no real AI review and labels reports accordingly. It is forbidden on Cloud
Run. Do not mount your whole home folder or bake ADC/API keys into the image.

The Dockerfile installs the pinned dependencies into `python:3.12-slim`, copies
only application code/templates, runs one worker as UID/GID 10001, uses `exec`
for signal handling, expands PORT at startup, and disables arbitrary proxy
header rewriting and Uvicorn access logs. Docker/source upload allowlists exclude
`.env`, credentials, Git history, local runtimes/caches, evaluation and tests.
No secret defaults or application dependency changes were introduced.

[GCP setup instructions](docs/GCP_SETUP.md) describe the administrator-run
`deploy/setup_gcp.sh` and subsequent `deploy/deploy.sh [--demo-day]`. They read
project, region, model location/name and contact from environment variables;
runtime environment values travel through a temporary JSON env file so commas,
ampersands and shell metacharacters cannot become flags. Secret contents are
never passed as deployment arguments. The scripts use an explicit project on
every cloud command and do not change the CLI's configured project.

Decisions added in Phase 5:

- Setup creates separate `seo-audit-sa` runtime and `seo-audit-build` build
  identities. Runtime grants are only the requested aiplatform user, logging
  writer and access-code-secret accessor. The build identity receives run.builder;
  setup does not grant broad administrator roles or change the human's IAM.
- Secret creation is idempotent and creates no value. You supply a secret version
  privately later. Deployment checks that the latest version is enabled before building.
- Deployment uses minimum 0, maximum 2 instances, concurrency 4, one CPU, 1 GiB,
  and a 120-second platform timeout around the app's 90-second deadline. The
  `--demo-day` switch changes minimum instances to 1; normal redeployment restores 0.
- Shell scripts are Bash (Cloud Shell/Linux/macOS/Git Bash), with LF enforced by
  `.gitattributes`. PYTHON_BIN supports the project interpreter on Windows. Setup
  also enables IAM and Resource Manager APIs for its service-account/IAM operations.
- The base-image tag follows the requested Python 3.12 line; package versions
  are pinned. The recorded local build used base digest
  `sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159`.
  Rebuilds can receive patched base layers; an immutable release digest should
  be recorded when deploying.

### Phase 5 tested / not tested (2026-10-29)

- Final Windows full offline suite: **388 passed, 1 warning in 16.79s**; 30 Phase 5
  tests added. Tests reject unexpected socket/DNS access. Windows test execution
  used the approved process outside the sandbox because asyncio's internal
  socketpair stalls inside it. A preliminary sandbox attempt was interrupted.
- Linux full offline suite in the built image, `--network none`, read-only
  mounts of tests/eval/deploy/config: **388 passed, 1 warning in 12.84s**.
  An initial Linux invocation stopped at argument parsing; rerunning with the
  checked-in pytest configuration passed. The warning in both runs is the
  previously recorded upstream Starlette TestClient deprecation.
- Offline evaluator: exit 0; actual output saved in `eval/RESULTS.md`.
- Docker 28.5.1: image build passed; image smoke passed with Python 3.12.16 and
  ADK 2.13.0. External networking was disabled for smoke/tests. Image dependency
  installation required network access during the build.
- Bash syntax check passed. Hermetic fake-gcloud tests execute both scripts,
  repeat setup to verify idempotence, check role scoping and normal/demo scaling,
  exercise failed IAM lookup/missing-secret/invalid-input handling, and verify
  shell-sensitive contact values, secret exclusion and temporary-file cleanup.
- Inspected the installed ADK `LlmRequest` signature and
  `Gemini.generate_content_async` source before adding the optional evaluator
  adapter call; signatures are recorded in `docs/adk-introspection.txt`.
- `gcloud meta list-files-for-upload` inspected the local source allowlist;
  only Docker inputs/application files were selected. It did not contact GCP.
- Not tested: live Vertex/ADC/model quality, `--live` evaluation, public website
  fetching, real IAM/API provisioning, Cloud Build, Secret Manager access,
  Cloud Run deployment/proxy behavior, browser download/print interaction, or
  load testing. Fake-gcloud tests prove command construction, not cloud permissions.
- Residual DNS rebinding and per-instance quotas remain as documented above.
  These artifacts complete the original Phase 5 scope; they are not evidence of
  a cloud security audit or production readiness for unrestricted public use.
- No Git staging/commit was performed; the user owns Git. No GCP setup or
  deployment script was run against Google Cloud. Stop at this checkpoint.

## Checkpoints and deferred work

Deferred extension requested on 2026-10-29: [Modern public-page support plan](docs/MODERN_PAGE_SUPPORT_PLAN.md).
It covers JavaScript/browser rendering, isolated outbound networking,
snapshot-specific evidence, API/report changes, tests, and deployment gates.
**Planning only: no browser dependencies or runtime behavior have changed.
Implement only when the user explicitly says to proceed.** This extension does
not imply full-site crawling, every file format, or bypassing site restrictions.

| Phase | Deliverable | Status |
| --- | --- | --- |
| 0 | Scaffold, SDK inspection, offline smoke probes | Complete |
| 1 | Security tests first, fetch, extraction, deterministic rules | Complete; checkpoint |
| 2 | Evidence schemas, verification, escaped reports | Complete; checkpoint |
| 3 | Production ADK workflow, retries, degradation | Complete; checkpoint |
| 4 | FastAPI, access code, limits, API tests | Complete; checkpoint |
| 5 | Evaluation, container, deployment scripts, final documentation | Complete; checkpoint; cloud setup not run |
| Deferred extension | Modern public-page/browser rendering | Planned only; awaiting explicit implementation request |
