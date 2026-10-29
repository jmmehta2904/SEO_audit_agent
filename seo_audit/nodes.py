"""Six code nodes around the Workflow's tool-free LlmAgent step."""

import asyncio
from collections.abc import Awaitable, Callable

from google.adk.workflow import RetryConfig, node

from seo_audit import extract, report, rules, security
from seo_audit.models import Facts, Finding, VerificationResult
from seo_audit.prompts import prepare_inputs
from seo_audit.verify import AuditFormatError, verify_audit


Fetcher = Callable[..., Awaitable[security.FetchedPage]]


def make_nodes(*, contact_url: str, fetcher: Fetcher, retry_delay_seconds: float):
    retry = RetryConfig(max_attempts=3, initial_delay=retry_delay_seconds,
                        max_delay=1, backoff_factor=2, jitter=0,
                        exceptions=[security.TransientFetchError])

    def completed(ctx, *names):
        # Reassign rather than mutating a nested list so ADK emits a state delta.
        ctx.state["completed_steps"] = [*ctx.state.get("completed_steps", []), *names]

    @node(retry_config=retry, timeout=6)
    async def validate_url(ctx, url: str):
        try:
            async with asyncio.timeout(5):
                ctx.state["url"] = await security.validate_url(url)
        except TimeoutError:
            raise security.TransientFetchError("URL validation timed out.") from None
        completed(ctx, "validate_url")

    @node(retry_config=retry, timeout=12)
    async def fetch_page(ctx, url: str):
        page = await fetcher(url, contact_url=contact_url)
        ctx.state["page_html"] = page.html
        ctx.state["fetched_url"] = page.url
        completed(ctx, "fetch_page")

    @node(timeout=10)
    async def extract_facts(ctx, page_html: str, fetched_url: str):
        extracted = await asyncio.to_thread(extract.extract_facts, page_html, url=fetched_url)
        facts_json, page_excerpt = prepare_inputs(extracted.facts, extracted.body_excerpt)
        ctx.state["facts"] = extracted.facts.model_dump(mode="json")
        ctx.state["facts_json"] = facts_json
        ctx.state["page_excerpt"] = page_excerpt
        ctx.state["evidence_text"] = extracted.evidence_text
        ctx.state["page_html"] = None  # Raw HTML is no longer needed in current state.
        completed(ctx, "extract_facts")

    @node
    def rule_findings(ctx):
        facts = Facts.model_validate(ctx.state["facts"])
        ctx.state["rule_findings"] = [finding.model_dump(mode="json") for finding in rules.rule_findings(facts)]
        completed(ctx, "rule_findings")

    @node
    def verify(ctx):
        if "audit" not in ctx.state:
            raise AuditFormatError("The AI review did not produce a structured audit.")
        result = verify_audit(ctx.state["audit"], facts=Facts.model_validate(ctx.state["facts"]),
                              evidence_text=ctx.state["evidence_text"])
        ctx.state["verification"] = result.model_dump(mode="json")
        completed(ctx, "auditor", "verify")

    @node
    def build_report(ctx, model_name: str):
        result = report.build_report(
            Facts.model_validate(ctx.state["facts"]),
            [Finding.model_validate(value) for value in ctx.state["rule_findings"]],
            VerificationResult.model_validate(ctx.state["verification"]), model_name=model_name,
        )
        ctx.state["report"] = result.model_dump(mode="json")
        completed(ctx, "build_report")

    return validate_url, fetch_page, extract_facts, rule_findings, verify, build_report
