"""SDK contract probes, not the production audit workflow (Phase 3)."""

import asyncio
import json
import sys
from importlib.metadata import version

import pytest
from pydantic import BaseModel
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.workflow import START, RetryConfig, Workflow, node
from google.genai import types


class ProbeAudit(BaseModel):
    issues: list[str]


class StubLlm(BaseLlm):
    model: str = "offline-contract-probe"
    fail: bool = False
    seen_instructions: list[str] = []

    async def generate_content_async(self, llm_request, stream=False):
        self.seen_instructions.append(str(llm_request.config.system_instruction))
        if self.fail:
            raise ConnectionError("synthetic model failure")
        yield LlmResponse(content=types.Content(
            role="model", parts=[types.Part(text=json.dumps({"issues": ["probe"]}))]
        ))


def test_pinned_runtime():
    assert sys.version_info[:2] == (3, 12)
    assert version("google-adk") == "2.13.0"
    assert version("google-genai") == "2.33.0"


@pytest.mark.asyncio
@pytest.mark.parametrize("transient_failures,model_fails", [(0, False), (1, False), (0, True)])
async def test_workflow_contract(transient_failures, model_fails):
    attempts = 0

    @node(retry_config=RetryConfig(
        max_attempts=3, initial_delay=0.001, jitter=0,
        exceptions=[ConnectionError],
    ), timeout=2)
    def fetch_probe(ctx, url: str):
        nonlocal attempts
        attempts += 1
        if attempts <= transient_failures:
            raise ConnectionError("synthetic transient fetch failure")
        assert url == "https://example.com/"
        ctx.state["facts_json"] = json.dumps({"title": "Probe title"})
        ctx.state["page_excerpt"] = "Offline page text"
        ctx.state["rule_findings"] = [{"id": "probe"}]

    stub = StubLlm(fail=model_fails)
    auditor = LlmAgent(
        name="auditor_probe", model=stub, output_schema=ProbeAudit,
        output_key="audit", instruction="Facts: {facts_json}\nText: {page_excerpt}",
        retry_config=RetryConfig(max_attempts=2, initial_delay=0.001,
                                 jitter=0, exceptions=[ConnectionError]),
    )

    @node
    def finish_probe(ctx):
        audit = ctx.state["audit"]
        audit = json.loads(audit) if isinstance(audit, str) else audit
        assert audit == {"issues": ["probe"]}
        ctx.state["report"] = {"verified": len(audit["issues"])}

    workflow = Workflow(name="contract_probe", edges=[
        (START, fetch_probe, auditor, finish_probe)
    ])
    sessions = InMemorySessionService()
    runner = Runner(node=workflow, app_name="contract_probe", session_service=sessions)
    identifiers = dict(app_name="contract_probe", user_id="offline", session_id="probe")
    await sessions.create_session(**identifiers, state={"url": "https://example.com/"})

    async def consume():
        async for _ in runner.run_async(
            user_id="offline", session_id="probe",
            new_message=types.Content(role="user", parts=[types.Part(text="audit")]),
        ):
            pass

    try:
        if model_fails:
            with pytest.raises(ConnectionError, match="synthetic model failure"):
                await asyncio.wait_for(consume(), timeout=10)
        else:
            await asyncio.wait_for(consume(), timeout=10)
        session = await sessions.get_session(**identifiers)
        assert session is not None
        assert session.state["rule_findings"] == [{"id": "probe"}]
        assert attempts == transient_failures + 1
        assert len(stub.seen_instructions) == (2 if model_fails else 1)
        assert "Probe title" in stub.seen_instructions[0]
        assert "Offline page text" in stub.seen_instructions[0]
        if model_fails:
            assert "report" not in session.state
        else:
            assert session.state["report"] == {"verified": 1}
    finally:
        await sessions.delete_session(**identifiers)
        await runner.close()
    assert await sessions.get_session(**identifiers) is None


def test_network_guard_is_active():
    import socket

    with pytest.raises(AssertionError, match="Unexpected network"):
        socket.create_connection(("example.com", 443))
