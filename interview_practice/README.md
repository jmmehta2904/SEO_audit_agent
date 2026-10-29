# Python practical preparation: E2M Web AI Engineer

Your round: **31 October 2026**. This is a small learning reference, not a universal
app builder or production-ready deployment. Your goal is to rebuild and change it.

Role verified from the exact employer posting on 29 October 2026:
https://e2msolutions.keka.com/careers/jobdetails/91473
It emphasizes agent loops, tools, MCP, context, structured outputs, orchestration,
REST APIs, debugging, and end-to-end delivery. These exercises are preparation
suggestions, not knowledge of E2M's actual interview questions or scoring.

## Run now

From the repository root in PowerShell, use the existing Python environment:

```powershell
.\.venv\Scripts\python.exe -m interview_practice.agent "What does an SEO audit cover?"
.\.venv\Scripts\python.exe -m unittest interview_practice.test_agent -v
.\.venv\Scripts\python.exe -m uvicorn interview_practice.api:app --host 127.0.0.1 --port 8091
```

Open http://127.0.0.1:8091/docs and try POST /chat:

```json
{"message": "What does an SEO audit cover?"}
```

Default `mode: mock` runs a fixed search-then-finish script. It demonstrates the
plumbing and is not intelligent. It does not understand quote requests. Tests
exercise quote execution separately. Live mode lets an LLM select tools.

For a real Gemini call, set your own key and a function-calling model available
in your account. Do not paste keys into chat or commit them. These variable names
are separate from the existing SEO application's configuration.

```powershell
$env:PRACTICE_GEMINI_API_KEY = Read-Host "Gemini API key" -MaskInput
$env:PRACTICE_GEMINI_MODEL = Read-Host "Available Gemini model ID"
.\.venv\Scripts\python.exe -m interview_practice.agent "Estimate 4 hours at INR 1500 per hour" --live
```

For the API, set `$env:PRACTICE_MODE = "live"` before starting Uvicorn.
The live adapter is based on Google's documented generateContent REST interface.
Offline checks mock HTTP responses; a successful paid model call has not been
verified. CLI live mode requires network access and your own credentials.

## Understand the flow

```text
Request -> input validation -> model + tool definitions
                               |
                               v
                     proposed function call
                               |
                     allowlist + argument validation
                               |
                     Python tool -> result -> model
                               |
                     finish -> validate answer + sources
                               |
                     JSON response + execution trace
```

The model proposes a call. Python executes it. A tool result is added to context
before the next model call. Each request has fresh state. Six model turns and
eight tool calls bound work. Provider calls have a 20-second timeout; there is no
global request deadline. Traces show execution metadata, not private reasoning.

Read `agent.py` in this order: argument models, tool implementations, declarations,
provider adapter, `run_agent`. Then read `api.py` and the failure tests.

## 29 October: build the foundations (about 5 focused hours)

1. **45 minutes: Python tools.** In a blank file write `search_knowledge(query)`
   and `estimate_quote(hours, hourly_rate)`. Try empty input, negative numbers,
   strings instead of numbers, and an unknown service. Explain each return value.
2. **45 minutes: API.** Build POST /chat and GET /health yourself. Validate message
   length and return useful errors. Use /docs to demonstrate a request.
3. **90 minutes: agent loop.** Read this reference once, close it, implement a tool
   registry and a bounded model -> tool -> model loop. Begin with scripted model
   turns. Add real API integration only after the loop works.
4. **45 minutes: structured results.** Validate the finish payload. Reject a source
   ID that retrieval did not return. Be able to explain why valid JSON alone is
   insufficient to prove that an answer is correct.
5. **60 minutes: debugging.** Reproduce an invalid argument, unknown tool, model
   outage, and repeated call. Explain what the user sees in each case.

Completion check: you can code a tool and an API without copying, draw the loop,
and explain every line of your implementation.

## 30 October: adapt and rehearse (about 5 focused hours)

1. **60 minutes: MCP.** Expose the same retrieval and quote functions through the
   optional `mcp_server.py`. Learn how a client discovers tools and calls them.
   Explain that an MCP server exposes capabilities; the application still owns
   model invocation, policy enforcement and the agent loop. This reference's
   agent calls tools locally; connecting an MCP client is an extension exercise.
2. **60 minutes: unseen change.** Implement `score_lead(budget, urgency, service)`.
   Define a deterministic rubric before coding; validate inputs, expose a schema,
   add it to the registry, and test boundary values. Do not let the model invent
   the scoring formula.
3. **90 minutes: mock practical.** Start a blank folder. Build an agency FAQ
   assistant with retrieval, an estimate tool, one API endpoint, structured
   results, and three meaningful failure checks. Keep a timer; use the schedule
   below. Use AI help only if interview rules allow it.
4. **45 minutes: requirement switch.** Turn the FAQ assistant into a support-ticket
   triager. Reuse the provider, loop and API; replace the tools, schemas and task
   instructions. Explain which parts should be deterministic.
5. **45 minutes: explain and repair.** Record a five-minute explanation. Then break
   one schema and fix it from the error. Review the questions below.

On 31 October, spend 30-45 minutes running your environment and explaining the
architecture. Avoid adding major new frameworks immediately before the round.

## Optional MCP server

Requires the official Python MCP SDK v1 (`mcp>=1.0,<2`), which is not installed by
this kit. Use a separate virtual environment if you install it, so the existing
SEO application's locked dependencies stay stable. Install FastAPI, Uvicorn,
HTTPX and Pydantic there too if you run the rest of this kit from that environment.

Run `python -m interview_practice.mcp_server` from the repository root. This is a
stdio server, so use an MCP client or Inspector to discover and call its tools;
it is not an HTTP URL. It must not print application logs to stdout.
The optional server has not been runtime-tested here because the SDK is absent.

Official references:
- https://github.com/modelcontextprotocol/python-sdk/tree/v1.x
- https://ai.google.dev/gemini-api/docs/function-calling
- https://ai.google.dev/api/generate-content

## A 90-minute practical schedule

| Minutes | Deliverable |
| --- | --- |
| 0-10 | Confirm input/output, sample cases, allowed tools, credentials and scope |
| 10-20 | Draw architecture; define request, result and tool schemas |
| 20-45 | Make one complete happy path work |
| 45-60 | Add tool validation, bounded loop and visible failures |
| 60-75 | Check three failure cases and one requirement change |
| 75-90 | Demo, explain tradeoffs and identify remaining production work |

Suggested opening: "I will first build the smallest complete workflow with a
validated input and output. Then I will add tools and failure handling. What is
the expected input, desired output, and definition of a successful result?"

## Questions you should answer with code or an example

- Why choose a deterministic workflow instead of an agent for a fixed sequence?
- What happens after the model returns a function name and arguments?
- How does the next model call learn the tool result?
- How do you stop an infinite loop and measure API usage?
- Why do you validate tool arguments even with a model-supplied schema?
- What is the difference between direct tool calling and MCP?
- What belongs in the system instructions, user message and retrieved context?
- What is RAG? How would embeddings improve this tiny lexical search?
- What happens on a 429, timeout, invalid key or malformed model response?
- How would you add persistence, authentication and deployment?
- How would you evaluate correctness across ten saved examples?

## Honest boundaries and next improvements

This kit has no durable history, vector database, streaming, authentication,
rate limiting, deployment, or real CRM/email writes. It is intended for loopback
practice. Add those only when the task needs them. The parent repository already
contains a larger SEO project you can study for deployment and evaluation.

The prompt asks for grounded answers and explicit rates, but only argument types,
numeric bounds and retrieved citation IDs are enforced by code. The model can
still make an unsupported prose claim or fabricate a rate; a cited ID does not
prove entailment. For production, extract/confirm critical inputs and verify
claims against evidence. Prompt injection resistance is not guaranteed.

Good extension tasks: add an overall deadline; bounded retries for transient
read-only API failures; a response model on the API; a ten-case evaluation set;
or SQLite persistence with session ownership. For external writes, add explicit
approval and idempotency. Never retry a write blindly.

## Start coding now

Set a 20-minute timer. Without reading `agent.py`, write:

```python
def score_lead(budget: int, urgency: str, service: str) -> dict:
    ...
```

Rules: budget below INR 10,000 = 0 points; INR 10,000-49,999 = 1;
INR 50,000+ = 2. Urgency `this_week` = 2, `this_month` = 1, `later` = 0.
Supported services are `seo`, `web`, `automation`; supported service = 1.
Reject negative budget, unknown urgency and unsupported service.
Return `score` and `priority`: 4-5 = hot, 2-3 = warm, 0-1 = cold.
Test INR 9,999, 10,000, 49,999 and 50,000 and an invalid urgency.

Send your code back for review. Next round: expose it as a validated tool, then
add it to the agent loop. Rebuilding and adapting this pattern is the objective.
