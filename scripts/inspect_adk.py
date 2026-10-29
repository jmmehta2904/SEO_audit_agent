"""Print the installed SDK contracts; does not construct a live model client."""

import inspect
import platform
from importlib.metadata import version
from pathlib import Path

import google.adk
import google.genai
from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.events import Event, EventActions
from google.adk.events.event import NodeInfo
from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_response import LlmResponse
from google.adk.models.registry import LLMRegistry
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.telemetry.context import ContentCapturingMode, TelemetryConfig
from google.adk.workflow import START, NodeTimeoutError, RetryConfig, Workflow, node
from google.genai import Client
from google.genai import types


def main():
    print("Python:", platform.python_version())
    for package in ("google-adk", "google-genai", "pydantic"):
        print(f"{package}: {version(package)}")
    for symbol in (
        Workflow, node, RetryConfig, LlmAgent, Runner, Runner.run_async,
        Runner.close, InMemorySessionService,
        InMemorySessionService.create_session,
        InMemorySessionService.get_session,
        InMemorySessionService.delete_session,
        InMemorySessionService.append_event,
        BaseLlm, BaseLlm.generate_content_async, LlmResponse,
        types.Content, types.Part, Event, EventActions, NodeInfo, NodeInfo.name.fget,
        RunConfig, TelemetryConfig, NodeTimeoutError, LLMRegistry.new_llm, Gemini,
        Client, Client.close, types.GenerateContentConfig, types.HttpOptions, types.HttpRetryOptions,
    ):
        print(f"\n{symbol.__module__}.{symbol.__qualname__}{inspect.signature(symbol)}")
    print("\nSTART:", repr(START))
    print("\nRetryConfig source:\n", inspect.getsource(RetryConfig))
    print("\nContentCapturingMode:", [mode.value for mode in ContentCapturingMode])
    for symbol in (types.GenerateContentConfig, types.HttpOptions, types.HttpRetryOptions):
        print(symbol.__name__, "fields:", list(symbol.model_fields))
    print("\nVertex-switch source excerpts:")
    roots = (Path(google.adk.__file__).parent, Path(google.genai.__file__).parent)
    for root, relative in zip(roots, ("utils/env_utils.py", "_api_client.py")):
        source = root / relative
        lines = source.read_text(encoding="utf-8").splitlines()
        matches = [i for i, line in enumerate(lines)
                   if "GOOGLE_GENAI_USE_" in line or "self.vertexai = env_" in line]
        included = set()
        for match in matches:
            for i in range(max(0, match - 2), min(len(lines), match + 4)):
                if i not in included:
                    print(f"{root.name}/{relative}:{i + 1}: {lines[i]}")
                    included.add(i)
    telemetry = roots[0] / "telemetry/context.py"
    print("\nTelemetry privacy source excerpts:")
    lines = telemetry.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS" in line or "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT" in line:
            print(f"adk/telemetry/context.py:{i + 1}: {line}")


if __name__ == "__main__":
    main()
