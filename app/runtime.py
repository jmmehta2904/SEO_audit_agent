"""Owned model resources and an explicit, labelled local-development stub."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_response import LlmResponse
from google.genai import Client, types
from pydantic import Field, PrivateAttr

from seo_audit.config import Settings
from seo_audit.models import Report
from seo_audit.service import AuditService


class AuditBackend(Protocol):
    async def audit(self, url: str) -> Report: ...


@dataclass
class AppRuntime:
    service: AuditBackend
    close_callback: Callable[[], Awaitable[None]] | None = None

    async def aclose(self):
        if self.close_callback is not None:
            await self.close_callback()


class DevelopmentStub(BaseLlm):
    async def generate_content_async(self, llm_request, stream=False):
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text='{"issues": []}')]))


class ManagedGemini(Gemini):
    """One model/client per app lifespan and event loop, lazily authenticated."""

    _owned_client: Client | None = PrivateAttr(default=None)
    client_kwargs: dict | None = Field(default=None, repr=False, exclude=True)

    @property
    def api_client(self):
        if self._owned_client is None:
            self._owned_client = super().api_client
        return self._owned_client

    async def aclose(self):
        client = self._owned_client
        self._owned_client = None
        if client is not None:
            try:
                await client.aio.aclose()
            finally:
                client.close()


def create_runtime(settings: Settings) -> AppRuntime:
    if settings.dev_stub_model:
        model = DevelopmentStub(model=settings.model_name)
        close = None
    else:
        kwargs = {"enterprise": settings.use_vertex}
        if settings.use_vertex:
            kwargs.update(project=settings.google_cloud_project, location=settings.google_cloud_location)
        else:
            kwargs["api_key"] = settings.google_api_key.get_secret_value()
        model = ManagedGemini(model=settings.model_name, client_kwargs=kwargs)
        close = model.aclose
    return AppRuntime(AuditService(model=model, contact_url=settings.contact_url), close)
