"""Local practice API; open /docs for a ready-to-use request form."""
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from interview_practice.agent import GeminiProvider, MockProvider, ProviderError, run_agent

app = FastAPI(title="Web AI Engineer Practice")


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
def chat(request: Request):
    # Synchronous route uses FastAPI's worker pool, not the event loop.
    mode = os.environ.get("PRACTICE_MODE", "mock")
    if mode not in {"mock", "live"}:
        raise HTTPException(503, "PRACTICE_MODE must be mock or live")
    try:
        provider = GeminiProvider() if mode == "live" else MockProvider()
        return {"mode": mode, **run_agent(request.message, provider)}
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
