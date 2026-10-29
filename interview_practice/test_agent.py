"""Offline checks; run with unittest to avoid the parent project's pytest hooks."""
import unittest
from unittest.mock import patch

import httpx

from interview_practice.agent import GeminiProvider, MockProvider, ProviderError, run_agent


def call(name, args, **extra):
    return {"role": "model", "parts": [{"functionCall": {"name": name, "args": args, **extra}}]}


class Scripted:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.history = None

    def generate(self, history):
        self.history = history
        return next(self.turns)


class AgentTests(unittest.TestCase):
    def test_mock_and_sources(self):
        result = run_agent("SEO audit", MockProvider())
        self.assertEqual(result["status"], "completed")
        self.assertIn("seo", result["result"]["source_ids"])

    def test_invalid_call_is_returned_to_model(self):
        provider = Scripted([call("estimate_quote", {"hours": -1, "hourly_rate": 20}),
                             call("finish", {"answer": "Need valid hours.", "source_ids": [], "needs_human": True})])
        result = run_agent("Estimate", provider)
        self.assertFalse(result["trace"][0]["ok"])
        self.assertIn("error", provider.history[2]["parts"][0]["functionResponse"]["response"])

    def test_unknown_tool_cannot_execute(self):
        result = run_agent("Execute code", Scripted([call("exec", {"code": "print(1)"})]), max_steps=1)
        self.assertEqual(result["status"], "limit_reached")
        self.assertFalse(result["trace"][0]["ok"])

    def test_unretrieved_citations_rejected(self):
        provider = Scripted([call("finish", {"answer": "Fact", "source_ids": ["seo"], "needs_human": False})])
        result = run_agent("Question", provider, max_steps=1)
        self.assertFalse(result["trace"][0]["ok"])

    def test_quote_and_call_id(self):
        provider = Scripted([call("estimate_quote", {"hours": 4, "hourly_rate": 1500}, id="call-1"),
                             call("finish", {"answer": "Estimate INR 6000.", "source_ids": [], "needs_human": False})])
        run_agent("4 hours at INR 1500", provider)
        response = provider.history[2]["parts"][0]["functionResponse"]
        self.assertEqual(response["id"], "call-1")
        self.assertEqual(response["response"]["total"], 6000)

    def test_whitespace_rejected(self):
        with self.assertRaises(ValueError):
            run_agent("   ", MockProvider())

    def test_gemini_adapter_preserves_opaque_parts(self):
        content = call("search_knowledge", {"query": "SEO"})
        content["parts"][0]["thoughtSignature"] = "opaque-signature"
        response = httpx.Response(200, json={"candidates": [{"content": content}]},
                                  request=httpx.Request("POST", "https://example.com"))
        with patch.dict("os.environ", {"PRACTICE_GEMINI_API_KEY": "fake", "PRACTICE_GEMINI_MODEL": "test-model"}):
            with patch("interview_practice.agent.httpx.post", return_value=response) as post:
                self.assertEqual(GeminiProvider().generate([]), content)
                self.assertEqual(post.call_args.kwargs["timeout"], 20)

    def test_provider_failure_is_clear(self):
        with patch.dict("os.environ", {"PRACTICE_GEMINI_API_KEY": "fake", "PRACTICE_GEMINI_MODEL": "test-model"}):
            with patch("interview_practice.agent.httpx.post", side_effect=httpx.ConnectError("private details")):
                with self.assertRaisesRegex(ProviderError, "Model request failed"):
                    GeminiProvider().generate([])


if __name__ == "__main__":
    unittest.main()
