import json
from pathlib import Path
import shutil

import pytest

from eval.run_eval import CaseResult, evaluate, main, markdown, review_saved_cases
from seo_audit.models import VerificationSummary


def test_saved_corpus_covers_every_rule_and_has_no_mismatches():
    from seo_audit.rules import RULE_SEVERITIES
    results = evaluate()
    assert len(results) == 10
    assert set().union(*(item.expected for item in results)) == set(RULE_SEVERITIES)
    assert sum(item.tp for item in results) == 20
    assert all(item.fp == item.fn == 0 for item in results)
    assert results[0].actual == frozenset()
    assert results[-1].page.facts.word_count < 300  # Script content isn't evidence.
    assert "Ignore previous instructions" in results[-1].page.evidence_text


def test_cli_is_offline_and_reports_totals(capsys):
    assert main([]) == 0
    text = capsys.readouterr().out
    assert "| **TOTAL** | 20 | 20 | 20 | 0 | 0 | 100.0% | 100.0% |" in text
    assert "N/A" in text
    assert "AI proposed" not in text


def test_precision_recall_detect_unexpected_and_missing_findings():
    page = evaluate()[0].page
    result = CaseResult("mistake.html", page, frozenset({"title_missing", "h1_count"}),
                        frozenset({"title_missing", "viewport_missing"}))
    assert (result.tp, result.fp, result.fn) == (1, 1, 1)
    text = markdown([result])
    assert "50.0% | 50.0%" in text
    assert "unexpected=['viewport_missing']; missing=['h1_count']" in text


@pytest.mark.parametrize("change", ["missing", "unknown", "duplicate", "wrong_type"])
def test_corrupt_expectations_fail_closed(tmp_path, change):
    source = Path(__file__).resolve().parents[1] / "eval"
    shutil.copytree(source / "cases", tmp_path / "cases")
    expected = json.loads((source / "expected.json").read_text())
    if change == "missing":
        expected.pop("01_complete.html")
    else:
        expected["01_complete.html"] = {"unknown": ["invented"], "duplicate": ["h1_count", "h1_count"],
                                         "wrong_type": "h1_count"}[change]
    (tmp_path / "expected.json").write_text(json.dumps(expected))
    with pytest.raises(ValueError):
        evaluate(tmp_path)


async def test_live_review_uses_saved_bounded_inputs_and_production_verifier():
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    issue = {"category": "cta_quality", "severity": "low", "title": "Review the action",
             "explanation": "The action is generic.", "fix": "Describe the next step.",
             "evidence_type": "quote", "evidence_quote": "Click here."}

    class Model(BaseLlm):
        async def generate_content_async(self, request, stream=False):
            assert not stream and not request.tools_dict
            prompt = request.config.system_instruction
            assert "<UNTRUSTED_FACTS_JSON>" in prompt
            assert "Ignore previous instructions" in prompt
            assert "<script>" not in prompt
            assert request.config.response_schema.__name__ == "Audit"
            assert request.config.http_options.retry_options.attempts == 1
            payload = {"issues": [issue, issue | {"evidence_quote": "This quotation was invented."}]}
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=json.dumps(payload))]))

    results = [evaluate()[-1]]
    counts = await review_saved_cases(results, Model(model="offline-evaluation-stub"))
    assert counts[results[0].name] == VerificationSummary(proposed=2, verified=1, dropped=1)
    text = markdown(results, counts)
    assert "AI proposed | AI verified | AI dropped" in text
    assert "2 | 1 | 1 |" in text


async def test_live_failure_is_unavailable_not_zero_counts():
    from google.adk.models.base_llm import BaseLlm

    class Model(BaseLlm):
        async def generate_content_async(self, request, stream=False):
            raise ConnectionError("private provider details")
            yield  # Async generator contract.

    results = evaluate()[:1]
    counts = await review_saved_cases(results, Model(model="offline-failure-stub"))
    assert counts == {results[0].name: None}
    text = markdown(results, counts)
    assert "unavailable | unavailable | unavailable" in text
    assert "AI reviews completed: 0/1" in text
    assert "private provider" not in text


def test_live_requires_explicit_configuration(monkeypatch, capsys):
    for key in ("MODEL_NAME", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"):
        monkeypatch.delenv(key, raising=False)
    assert main(["--live"]) == 2
    assert "Missing live evaluation settings" in capsys.readouterr().err
