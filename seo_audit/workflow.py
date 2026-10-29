"""The single, inspected ADK Workflow used by the audit service."""

import httpx
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.registry import LLMRegistry
from google.adk.workflow import START, NodeTimeoutError, RetryConfig, Workflow
from google.auth.exceptions import GoogleAuthError
from google.genai import types
from google.genai.errors import APIError
from pydantic import ValidationError

from seo_audit.logging_utils import protect_dependency_logs
from seo_audit.models import Audit
from seo_audit.nodes import Fetcher, make_nodes
from seo_audit.prompts import AUDITOR_INSTRUCTION
from seo_audit.security import safe_fetch


class AIReviewError(Exception):
    """A known provider/authentication/response failure, without response details."""


class TransientAIError(AIReviewError):
    """Retryable model transport or service failure."""


def _model_error(callback_context, llm_request, error):
    if isinstance(error, APIError):
        if error.code in {408, 429} or error.code >= 500:
            raise TransientAIError("AI provider temporarily unavailable.") from None
        raise AIReviewError("AI provider rejected the request.") from None
    if isinstance(error, (ConnectionError, TimeoutError, httpx.TransportError)):
        raise TransientAIError("AI provider connection failed.") from None
    if isinstance(error, GoogleAuthError):
        raise AIReviewError("AI provider authentication unavailable.") from None
    # Returning None lets ADK propagate unrelated programming errors unchanged.
    return None


def _model_response(callback_context, llm_response):
    if llm_response.error_code:
        raise AIReviewError("AI provider returned an unusable response.")
    if llm_response.partial:
        return None
    content = llm_response.content
    if not content or content.role != "model" or not content.parts:
        raise AIReviewError("AI provider returned no review.")
    for part in content.parts:
        if any((part.function_call, part.function_response, part.executable_code,
                part.code_execution_result, part.inline_data, part.file_data)):
            raise AIReviewError("AI provider returned unsupported review content.")
    return None


def build_workflow(
    model: str | BaseLlm, *, contact_url: str, fetcher: Fetcher = safe_fetch,
    retry_delay_seconds: float = 0.5, model_timeout_seconds: float = 15,
) -> Workflow:
    protect_dependency_logs()
    if retry_delay_seconds < 0 or model_timeout_seconds <= 0:
        raise ValueError("Retry delay must be nonnegative and model timeout positive.")
    if isinstance(model, str):
        if not model.strip():
            raise ValueError("MODEL_NAME must be configured explicitly.")
        # Resolve the installed provider class now, without initializing a client
        # or making a model request. Unsupported model wiring fails at startup.
        model = LLMRegistry.new_llm(model)
    elif not isinstance(model, BaseLlm) or not model.model.strip():
        raise ValueError("Provide a configured model name or BaseLlm instance.")
    validate, fetch, extract, checks, verify, finish = make_nodes(
        contact_url=contact_url, fetcher=fetcher, retry_delay_seconds=retry_delay_seconds,
    )
    auditor = LlmAgent(
        name="auditor", model=model, instruction=AUDITOR_INSTRUCTION,
        output_schema=Audit, output_key="audit", tools=[], include_contents="none",
        disallow_transfer_to_parent=True, disallow_transfer_to_peers=True,
        on_model_error_callback=_model_error, after_model_callback=_model_response,
        retry_config=RetryConfig(max_attempts=3, initial_delay=retry_delay_seconds,
                                 max_delay=1, backoff_factor=2, jitter=0,
                                 exceptions=[TransientAIError, ValidationError, NodeTimeoutError]),
        timeout=model_timeout_seconds,
        generate_content_config=types.GenerateContentConfig(
            temperature=0, max_output_tokens=4096,
            http_options=types.HttpOptions(
                timeout=max(1, int(model_timeout_seconds * 1000)),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        ),
    )
    return Workflow(name="seo_audit", edges=[(START, validate, fetch, extract, checks, auditor, verify, finish)])
