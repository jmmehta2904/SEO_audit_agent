"""Create consistent reports and render all untrusted strings with autoescape."""

import base64
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from seo_audit.models import Facts, Finding, Report, VerificationResult


LIMITATIONS = (
    "Automated first-pass review, not an authoritative SEO audit.",
    "One page only, based on the HTML as fetched (no JavaScript rendering).",
    "AI, verified means supporting evidence matched page-derived text or facts; subjective conclusions can still be wrong.",
    "Text samples are bounded, so quotes outside the retained evidence are dropped. Counts and lengths use the full parsed HTML.",
    "Length and word-count ranges are review heuristics, not universal search-engine requirements.",
)

_TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
_ENVIRONMENT = Environment(
    loader=FileSystemLoader(_TEMPLATES), autoescape=True,
    undefined=StrictUndefined, keep_trailing_newline=True,
)


def build_report(
    facts: Facts,
    code_findings: list[Finding],
    verification: VerificationResult | None,
    *,
    model_name: str,
    ai_review_available: bool = True,
    generated_at: datetime | None = None,
) -> Report:
    """Combine saved code checks with verified AI proposals, or mark AI absent."""
    if not model_name.strip():
        raise ValueError("A non-empty configured model name is required.")
    if any(finding.source != "code check" for finding in code_findings):
        raise ValueError("Code findings must come from deterministic checks.")
    if ai_review_available != (verification is not None):
        raise ValueError("AI availability must match the presence of verification results.")
    generated_at = generated_at or datetime.now(timezone.utc)
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("The report timestamp must include a timezone.")
    findings = [*code_findings, *(verification.findings if verification is not None else [])]
    return Report(
        url=facts.url, facts=facts, findings=findings,
        severity_counts={severity: sum(f.severity == severity for f in findings)
                         for severity in ("high", "medium", "low")},
        verification=verification.summary if verification is not None else None,
        ai_review_available=ai_review_available,
        ai_notice=None if ai_review_available else "AI review unavailable",
        generated_at=generated_at.astimezone(timezone.utc), model_name=model_name,
        adk_version=version("google-adk"), limitations=list(LIMITATIONS),
    )


def render_json(report: Report) -> str:
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=True, indent=2, allow_nan=False)


def render_html(report: Report) -> str:
    # Base64 carries JSON as a typed download, never executable HTML or JS.
    # Its alphabet cannot introduce HTML attribute delimiters; autoescape is
    # still applied. The browser handles the download without server storage.
    download = base64.b64encode(render_json(report).encode("utf-8")).decode("ascii")
    fact_rows = []
    for key, value in report.facts.model_dump().items():
        label = "H1 count" if key == "h1_count" else key.replace("_", " ").capitalize()
        if isinstance(value, (list, dict)):
            displayed = json.dumps(value, ensure_ascii=False, indent=2)
        else:
            displayed = str(value) if value != "" else "(empty)"
        fact_rows.append((label, key, displayed))
    return _ENVIRONMENT.get_template("report.html").render(
        report=report, fact_rows=fact_rows, download=download,
        timestamp=report.generated_at.isoformat(),
    )
