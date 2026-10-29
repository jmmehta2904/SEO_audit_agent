# Deferred plan: modern public-page auditing

Status: **PLANNED ONLY — NOT IMPLEMENTED OR ENABLED.**
Recorded: 2026-10-29. Implement only when the user explicitly requests this work.
This document does not authorize dependency installation, browser downloads,
cloud changes, or deployment. Phase 5 remains a separate pending checkpoint.

## Intended behavior

Accept a public page URL, determine whether its content can be audited, and
return an evidence-checked report for the content actually observed. Support
traditional HTML, server-rendered applications, and JavaScript-rendered pages.

"Any public URL" is the product ambition, not a promise to bypass website access
controls or understand every file format. The first extension covers HTML and
JavaScript applications. A PDF, image, video, archive, or other unsupported type
gets an explicit unsupported-content result until a dedicated parser is planned
and tested. Full-site crawling, rankings, backlinks, and authenticated browsing
are separate capabilities, outside this extension.

## Fetch and render strategy

1. Preserve the existing safe HTTP fetch as the initial step: URL validation,
   all-address DNS checks, robots permission, bounded redirects/body, MIME check,
   and response/final-URL metadata. Save the initial facts as a separate baseline.
2. Introduce a browser-rendering adapter using pinned Playwright and its matching
   Chromium build. Verify the installed APIs before relying on them. It takes a
   validated public URL and returns a bounded rendered snapshot and diagnostics.
3. Keep browser rendering disabled by default until the security and deployment
   gates below pass. Proposed setting: ENABLE_BROWSER_RENDERING=false.
4. Add an optional render_mode request field: html, auto, or browser. Omitted
   fields retain HTML behavior while the feature is disabled. Once enabled,
   omitted/auto attempts browser rendering after the safe initial fetch. Do not
   rely only on low word count or framework signatures: substantial initial HTML
   can still change after hydration. Explicit browser mode fails clearly if a
   usable rendered result cannot be obtained; it never silently becomes HTML.
5. Auto mode may fall back to fetched HTML on renderer unavailability, crash, or
   timeout, with a prominent warning and failed/partial status. An unsafe target,
   disallowed navigation, or missing security boundary is a refusal, not a reason
   to retry with a less restrictive path.
6. Use a fresh non-persistent context per audit, a fixed desktop viewport, no
   saved login state, and no browser profile shared between customers. Preserve
   ephemeral same-audit state only where necessary for a public page to work.
7. Wait for DOMContentLoaded, then a bounded stabilization interval and bounded
   scroll attempts for lazy text. Start with a 20-second total renderer budget,
   up to three scroll steps, and a hard stop. Do not wait indefinitely for network
   idle: polling applications may never become idle. Include observed readiness
   and partial-load diagnostics, without claiming a complete interactive session.
8. No logins, form submissions, purchases, arbitrary button clicking, CAPTCHA
   bypass, or automatic consent choices. A consent/login/challenge wall becomes
   a clear limitation. No LLM-generated browser code or autonomous browsing tools.

## Browser security is a release gate

Running website JavaScript adds network paths beyond the initial URL. The current
HTTPX validation alone is insufficient, and Playwright routing is not a complete
network isolation boundary.

- Isolate the browser in a credential-free rendering worker. Do not expose the
  main application's ADC, access code, API keys, environment, filesystem, browser
  connection endpoint, or cloud-management permissions to that worker. Do not
  give its service identity model, secret-access, or build roles.
- Require enforced outbound mediation: resolve and validate every destination,
  connect to the validated public IP, retain correct TLS/SNI/Host semantics, and
  block direct connections that bypass the mediator. A check followed by an
  independent browser DNS lookup does not solve DNS rebinding.
- Validate document/subframe navigation, scripts, styles, fetch/XHR, redirects,
  and worker traffic; block metadata, private/loopback/link-local/reserved
  addresses, IPv4-mapped IPv6, unsafe schemes, URL credentials, and forbidden
  ports. Apply the existing redirect bounds and robots policy to fetched paths.
- Prove coverage of redirect hops rather than assuming a route callback sees all
  of them. Select and test the necessary browser interception/network-mediation
  mechanism against the pinned version. Do not use a browser request API that
  secretly follows unchecked redirects as a safety shortcut.
- Disable service workers and block WebSockets, WebRTC/direct UDP, QUIC, popups,
  downloads, file access, and unnecessary background networking. Initially block
  image/font/media downloads; allow only the public scripts/styles/data needed
  for rendering, within the enforced network policy. Record limitations caused
  by intentionally blocked resources.
- Start with caps of 100 resource requests, 15 MiB total transferred content,
  2 MiB per document/script/data response, and a 2 MiB serialized DOM snapshot.
  Enforce these during processing, not just after an unbounded download. A fixed,
  application-owned snapshot routine must stop at its output limit.
- Treat renderer console output, page errors, URLs/query strings, DOM, screenshots,
  traces, and HAR files as untrusted/sensitive. Keep them out of logs; no automatic
  screenshot/trace/profile persistence. Close contexts and cancel jobs on timeout
  or disconnect. Recycle crashed workers and enforce CPU/memory/job limits.
- Validate a non-root browser sandbox and enforced egress in the actual target
  runtime. Do not assume Cloud Run provides every required browser/network
  isolation primitive. If it cannot satisfy the controls, keep browser mode off
  and select a separate sandbox-capable rendering deployment. Never silently use
  --no-sandbox or weaken SSRF controls to make Chromium launch.

The deployment feasibility test is required before enabling browser mode. Its
result and selected isolation mechanism must be documented before implementation
is considered complete. This plan intentionally does not assert that an untested
Cloud Run configuration meets this boundary.

## Evidence, rules, and report changes

- Preserve separate initial-HTML and rendered-DOM snapshots, facts, and evidence.
  Use rendered DOM as the primary audit input after successful rendering; use
  initial HTML in explicit HTML mode or a clearly identified fallback.
- Run the existing deterministic rules on the selected primary snapshot. Present
  initial/rendered differences as computed observations rather than duplicate
  findings or automatic claims that JavaScript dependence is an SEO defect.
- Feed the model only primary facts and the bounded text excerpt, never raw HTML
  or browser-control APIs. Verify quotes/metrics against that same snapshot.
  Evidence from scripts, unrelated resources, or another frame must not prove a
  finding about the main document.
- Make the evidence scope explicit for findings. If both scopes are later allowed
  in model proposals, require an explicit scope and validate against the matching
  evidence bundle; never merge sources into an ambiguous global evidence string.
- Add report metadata for requested/used rendering mode, rendering outcome,
  original/final URL, snapshot source, rendering time, and bounded diagnostic
  counts/reasons. Keep review_mode=live/stub separate from rendering mode.
- Clearly distinguish "missing in fetched HTML" from "missing in rendered DOM".
  A failed browser attempt must not turn the initial loading shell into a claim
  about the final user-visible page. Keep evidence verification limits explicit.
- Retain Jinja autoescape, script-free report UI, source labels, severity counts,
  JSON download, print support, and the no-numeric-SEO-score rule. Update the
  current no-JavaScript limitation text according to the actual audit mode.

## Integration and resource changes

- Keep one ADK Workflow: validate_url -> fetch_page -> extract_facts ->
  rule_findings -> auditor -> verify -> build_report. The fetch/render adapter
  supplies the selected snapshot and diagnostics without giving the model tools.
- Extend the fetch result/state contracts, renderer interface, report metadata,
  and the authenticated form/API with the optional rendering mode. Preserve old
  request bodies and the existing report fields; document additive metadata.
- Implement rendering as an optional dependency/runtime profile. Pin the Python
  Playwright package and matching browser/system dependencies together. The
  existing minimal HTML-only installation must remain usable without Chromium.
- Start with one active browser render job per worker and a bounded queue; retain
  the application's two-audit semaphore and overall 90-second request deadline.
  Browser waits, retries, and inter-service calls must fit that deadline. Lower
  admission limits or fail promptly if measured memory/latency requires it.
- If a rendering service is used, make it private, authenticate calls with
  service identity, scope the caller to invocation only, and validate worker
  responses as untrusted. Never expose it as an unauthenticated arbitrary proxy.
- Verify container startup, browser sandbox, resource cleanup, image size, memory,
  outbound policy, cold starts, and latency in the deployment target. Existing
  Cloud Run memory/timeout settings are baselines, not proof Chromium fits them.

## Tests and completion criteria

1. Add deterministic fixtures for HTML, SSR, hydrated content, a minimal React-like
   shell, delayed headings/text, lazy content, polling, client-side redirects,
   rendering errors, consent walls, and an unsupported MIME type. No third-party
   network is required for normal tests.
2. Test that initial/rendered facts differ correctly and that quotes/metrics never
   verify against the wrong snapshot. Cover fabricated issues, injection text,
   XSS, stub/live labels, fallback warnings, and existing HTML-only behavior.
3. Add malicious pages attempting private/metadata access through all supported
   network paths, redirects, alternative IP forms, DNS rebinding, frames, workers,
   and direct-proxy bypass. Assert denial at the actual outbound boundary.
4. Test timeouts, resource/DOM limits, crashes, disconnects, queue saturation,
   cleanup, context/cookie isolation, and absence of credentials or content in
   logs/artifacts. A simulated route denial alone is not proof of isolation.
5. Keep existing pytest tests passing. Add an explicitly invoked browser test
   group with controlled local fixtures. Any local-fixture test exception must
   be injected by trusted tests and must never be enabled in deployed code.
6. Extend the Phase 5 evaluation corpus and report accuracy separately for initial
   HTML and rendered pages. Do not use live-web variability as the offline oracle.
7. Run a deployment feasibility smoke test and an explicitly authorized small
   set of real public-page audits. Document what worked, what was blocked, and
   what remains unsupported. Browsing/scrolling cannot guarantee all content.

Suggested checkpoints: contracts/fixtures; isolated network boundary; renderer;
snapshot-aware workflow/reports/API; evaluation and deployment. Report tests and
stop at each agreed checkpoint. The user continues to handle Git operations.

## Documentation consulted

- [Playwright network controls](https://playwright.dev/python/docs/network)
- [Browser contexts and service-worker controls](https://playwright.dev/python/docs/api/class-browsercontext)
- [Container and untrusted-site sandbox guidance](https://playwright.dev/python/docs/docker)

Recheck these against the installed version when implementation is authorized.
