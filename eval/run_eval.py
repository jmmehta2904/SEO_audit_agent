"""Evaluate saved HTML, with opt-in Vertex review of the same bounded evidence."""

import argparse
import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys

# Support `python eval/run_eval.py` from any working directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seo_audit.extract import ExtractedPage, extract_facts
from seo_audit.rules import RULE_SEVERITIES, rule_findings


@dataclass(frozen=True)
class CaseResult:
    name: str
    page: ExtractedPage
    expected: frozenset[str]
    actual: frozenset[str]

    @property
    def tp(self):
        return len(self.expected & self.actual)

    @property
    def fp(self):
        return len(self.actual - self.expected)

    @property
    def fn(self):
        return len(self.expected - self.actual)


def evaluate(directory: Path = ROOT / "eval") -> list[CaseResult]:
    expected = json.loads((directory / "expected.json").read_text(encoding="utf-8"))
    files = {path.name for path in (directory / "cases").glob("*.html")}
    if not isinstance(expected, dict) or set(expected) != files or len(files) != 10:
        raise ValueError("Evaluation requires exactly ten HTML files with matching expectations.")
    results = []
    for name, identifiers in sorted(expected.items()):
        if (not isinstance(identifiers, list) or any(not isinstance(item, str) for item in identifiers)
                or len(set(identifiers)) != len(identifiers)
                or not set(identifiers) <= RULE_SEVERITIES.keys()):
            raise ValueError("Expected findings must be unique known rule identifiers.")
        page = extract_facts((directory / "cases" / name).read_text(encoding="utf-8"),
                             url=f"https://example.com/evaluation/{name}")
        actual = frozenset(finding.id for finding in rule_findings(page.facts))
        results.append(CaseResult(name, page, frozenset(identifiers), actual))
    return results


def ratio(numerator: int, denominator: int) -> str:
    # Empty/empty is undefined, rather than assigning a misleading perfect score.
    return f"{numerator / denominator:.1%}" if denominator else "N/A"


async def review_saved_cases(results, model):
    """Exercise ADK's model adapter, production prompt/schema and verifier.

    This deliberately doesn't fetch synthetic URLs or run the HTTP workflow:
    only the saved facts/text reach Vertex. Workflow orchestration has its own
    offline tests. Each page has one model attempt and a 45-second deadline.
    """
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types
    from google.genai.errors import APIError
    from google.auth.exceptions import GoogleAuthError
    import httpx
    from pydantic import ValidationError
    from seo_audit.models import Audit
    from seo_audit.prompts import AUDITOR_INSTRUCTION, prepare_inputs
    from seo_audit.verify import AuditFormatError, verify_audit

    counts = {}
    for result in results:
        facts_json, excerpt = prepare_inputs(result.page.facts, result.page.body_excerpt)
        request = LlmRequest(
            model=model.model,
            contents=[types.Content(role="user", parts=[types.Part(text="Review this saved page.")])],
            config=types.GenerateContentConfig(
                system_instruction=AUDITOR_INSTRUCTION.format(facts_json=facts_json, page_excerpt=excerpt),
                response_mime_type="application/json", response_schema=Audit,
                temperature=0, max_output_tokens=4096,
                http_options=types.HttpOptions(timeout=30000,
                    retry_options=types.HttpRetryOptions(attempts=1)),
            ),
        )
        try:
            async with asyncio.timeout(45):
                output = None
                async for response in model.generate_content_async(request, stream=False):
                    if response.error_code:
                        raise AuditFormatError("Model review unavailable.")
                    if not response.partial and response.content and response.content.parts:
                        parts = response.content.parts
                        if any(part.function_call or part.function_response or part.executable_code
                               or part.code_execution_result or part.inline_data or part.file_data for part in parts):
                            raise AuditFormatError("Unsupported model response.")
                        output = "".join(part.text or "" for part in parts if not part.thought)
                counts[result.name] = verify_audit(output, facts=result.page.facts,
                                                  evidence_text=result.page.evidence_text).summary
        except (APIError, GoogleAuthError, httpx.TransportError, TimeoutError,
                ConnectionError, ValidationError, AuditFormatError):
            counts[result.name] = None
    return counts


async def live_review(results):
    from app.runtime import ManagedGemini
    from seo_audit.logging_utils import protect_dependency_logs

    required = ("MODEL_NAME", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION")
    missing = [key for key in required if not os.environ.get(key, "").strip()]
    if missing:
        raise ValueError("Missing live evaluation settings: " + ", ".join(missing))
    if any(os.environ.get(key) for key in ("GOOGLE_API_KEY", "GEMINI_API_KEY")):
        raise ValueError("Live evaluation requires Vertex ADC; unset API keys.")
    if os.environ.get("DEV_STUB_MODEL", "false").lower() not in {"false", "0"}:
        raise ValueError("Disable DEV_STUB_MODEL for live evaluation.")
    protect_dependency_logs()
    model = ManagedGemini(model=os.environ["MODEL_NAME"], client_kwargs={
        "enterprise": True, "project": os.environ["GOOGLE_CLOUD_PROJECT"],
        "location": os.environ["GOOGLE_CLOUD_LOCATION"],
    })
    try:
        return await review_saved_cases(results, model)
    finally:
        await model.aclose()


def markdown(results, live=None):
    lines = ["# Saved-page SEO evaluation", "",
             "Synthetic fixtures; rule precision/recall measure the configured rules, not SEO effectiveness.",
             "TP/FP/FN count finding identifiers per page. N/A means a zero denominator.", ""]
    header = "| Case | Expected | Found | TP | FP | FN | Precision | Recall |"
    divider = "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    if live is not None:
        header += " AI proposed | AI verified | AI dropped |"
        divider += " ---: | ---: | ---: |"
    lines += [header, divider]
    for item in results:
        row = (f"| {item.name} | {len(item.expected)} | {len(item.actual)} | {item.tp} | {item.fp} | {item.fn}"
               f" | {ratio(item.tp, item.tp + item.fp)} | {ratio(item.tp, item.tp + item.fn)} |")
        if live is not None:
            count = live[item.name]
            row += (f" {count.proposed} | {count.verified} | {count.dropped} |" if count is not None
                    else " unavailable | unavailable | unavailable |")
        lines.append(row)
    tp, fp, fn = (sum(getattr(item, field) for item in results) for field in ("tp", "fp", "fn"))
    total = (f"| **TOTAL** | {sum(len(item.expected) for item in results)} | {sum(len(item.actual) for item in results)}"
             f" | {tp} | {fp} | {fn} | {ratio(tp, tp + fp)} | {ratio(tp, tp + fn)} |")
    if live is not None:
        completed = [count for count in live.values() if count is not None]
        total += " " + " | ".join(str(sum(getattr(count, field) for count in completed))
                                     for field in ("proposed", "verified", "dropped")) + " |"
        lines += [total, "", f"AI reviews completed: {len(completed)}/{len(results)}. Totals exclude unavailable reviews.",
                  "Evidence verification does not establish the correctness of subjective judgments."]
    else:
        lines.append(total)
    for item in results:
        if item.fp or item.fn:
            lines += ["", f"{item.name}: unexpected={sorted(item.actual - item.expected)}; missing={sorted(item.expected - item.actual)}"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Billable Vertex calls on saved pages; requires ADC.")
    args = parser.parse_args(argv)
    try:
        results = evaluate()
        live = asyncio.run(live_review(results)) if args.live else None
    except ValueError as error:
        # Configuration errors contain only setting names, never their values.
        print(str(error), file=sys.stderr)
        return 2
    print(markdown(results, live), end="")
    return 2 if live is not None and any(value is None for value in live.values()) else int(any(item.fp or item.fn for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
