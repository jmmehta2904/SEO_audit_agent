"""Run fresh ephemeral ADK sessions and recover only recognized AI failures."""

import asyncio
from uuid import uuid4

from google.adk.agents.run_config import RunConfig
from google.adk.events import Event, EventActions
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.telemetry.context import ContentCapturingMode, TelemetryConfig
from google.adk.workflow import NodeTimeoutError
from google.genai import types
from pydantic import ValidationError

from seo_audit.models import Facts, Finding, Report
from seo_audit.nodes import Fetcher
from seo_audit.report import build_report
from seo_audit.security import safe_fetch
from seo_audit.verify import AuditFormatError
from seo_audit.workflow import AIReviewError, build_workflow


class AuditService:
    """Reusable graph/model, isolated request sessions; no persistent reports.

    Does not perform HTTP authentication or rate limiting; the Phase 4 server
    must apply those controls before calling audit().
    """

    def __init__(
        self, *, model: str | BaseLlm, contact_url: str, fetcher: Fetcher = safe_fetch,
        session_service: InMemorySessionService | None = None,
        retry_delay_seconds: float = 0.5, model_timeout_seconds: float = 15,
        timeout_seconds: float = 90,
    ):
        if timeout_seconds <= 0:
            raise ValueError("Overall audit timeout must be positive.")
        self.workflow = build_workflow(model, contact_url=contact_url, fetcher=fetcher,
                                       retry_delay_seconds=retry_delay_seconds,
                                       model_timeout_seconds=model_timeout_seconds)
        self.model_name = model if isinstance(model, str) else model.model
        self.sessions = session_service if session_service is not None else InMemorySessionService()
        self.timeout_seconds = timeout_seconds

    async def audit(self, url: str) -> Report:
        identifiers = {"app_name": "seo_audit", "user_id": "reviewer", "session_id": uuid4().hex}
        runner = Runner(node=self.workflow, app_name=identifiers["app_name"], session_service=self.sessions)
        created = False
        failed_node = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                await self.sessions.create_session(**identifiers, state={"url": url, "model_name": self.model_name})
                created = True
                try:
                    async for event in runner.run_async(
                        user_id=identifiers["user_id"], session_id=identifiers["session_id"],
                        new_message=types.Content(role="user", parts=[types.Part(text="Review this page.")]),
                        run_config=RunConfig(max_llm_calls=3, telemetry=TelemetryConfig(
                            capture_message_content=ContentCapturingMode.NO_CONTENT,
                            adk_experimental_telemetry_opt_in=False,
                            experimental_features_opt_in=frozenset(),
                        )),
                    ):
                        if event.error_code:
                            failed_node = event.node_info.name
                except (AIReviewError, ValidationError, NodeTimeoutError, AuditFormatError) as error:
                    # An error after AI starts is not automatically an AI error.
                    # Inspect the error's source as well as its class, so extract,
                    # verification, and report programming failures stay visible.
                    recognized = failed_node == "auditor" or (
                        isinstance(error, AuditFormatError) and failed_node == "verify"
                    )
                    if not recognized:
                        raise
                    session = await self.sessions.get_session(**identifiers)
                    if session is None or "facts" not in session.state or "rule_findings" not in session.state:
                        raise RuntimeError("AI recovery checkpoint is missing.") from None
                    partial = build_report(
                        Facts.model_validate(session.state["facts"]),
                        [Finding.model_validate(value) for value in session.state["rule_findings"]],
                        None, model_name=self.model_name, ai_review_available=False,
                    )
                    # get_session returns a copy in ADK 2.13.0: persist the final
                    # ephemeral result through append_event, not direct mutation.
                    await self.sessions.append_event(session, Event(
                        author="build_report", actions=EventActions(state_delta={
                            "report": partial.model_dump(mode="json"),
                        }),
                    ))
                    return partial
                session = await self.sessions.get_session(**identifiers)
                if session is None or "report" not in session.state:
                    raise RuntimeError("Audit workflow completed without a report.")
                return Report.model_validate(session.state["report"])
        finally:
            async def cleanup():
                try:
                    if created:
                        await self.sessions.delete_session(**identifiers)
                finally:
                    await runner.close()

            # A timeout or client disconnect must not leave a page in session RAM.
            task = asyncio.create_task(cleanup())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
