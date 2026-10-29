"""Verify supporting evidence, not the truth of an AI's subjective judgment."""

import json

from pydantic import ValidationError

from seo_audit.models import (
    MAX_AI_ISSUES, Audit, Facts, Finding, Issue, VerificationResult, VerificationSummary,
)


MAX_AUDIT_CHARS = 128 * 1024


class AuditFormatError(ValueError):
    """The AI envelope is unreadable; do not pretend it was a successful review."""


def _proposals(payload: Audit | dict | str) -> list:
    if isinstance(payload, Audit):
        payload = payload.model_dump()
    elif isinstance(payload, str):
        if len(payload) > MAX_AUDIT_CHARS:
            raise AuditFormatError("The AI review exceeded the supported output limit.")
        try:
            payload = json.loads(payload)
        except (ValueError, RecursionError):
            raise AuditFormatError("The AI review could not be read.") from None
    if not isinstance(payload, dict) or set(payload) != {"issues"} or not isinstance(payload["issues"], list):
        raise AuditFormatError("The AI review must contain an issues list.")
    return payload["issues"]


def normalize_quote(value: str) -> str:
    return " ".join(value.split()).casefold()


def verify_audit(
    payload: Audit | dict | str, *, facts: Facts, evidence_text: str,
) -> VerificationResult:
    """Drop invalid proposals individually and count all drops.

    Unreadable top-level output raises AuditFormatError for the orchestration
    layer to mark AI unavailable. A readable list can retain valid neighbors of
    malformed items. Quotes never use fuzzy, HTML, or punctuation normalization;
    metrics require the exact scalar field, Python type, and value.
    """
    proposals = _proposals(payload)
    evidence = normalize_quote(evidence_text)
    fact_values = facts.model_dump()
    findings = []
    for index, proposal in enumerate(proposals[:MAX_AI_ISSUES], start=1):
        try:
            issue = Issue.model_validate(proposal, strict=True)
        except ValidationError:
            continue
        if issue.evidence_type == "quote":
            if normalize_quote(issue.evidence_quote) not in evidence:
                continue
            displayed_evidence = issue.evidence_quote
        else:
            if issue.fact_key not in fact_values:
                continue
            expected = fact_values[issue.fact_key]
            if type(expected) not in (str, int, float, bool):
                continue
            if type(expected) is not type(issue.fact_value) or expected != issue.fact_value:
                continue
            displayed_evidence = f"{issue.fact_key} = {json.dumps(expected, ensure_ascii=True)}"
        findings.append(Finding(
            id=f"ai_{index}", source="AI, verified", category=issue.category,
            severity=issue.severity, title=issue.title, explanation=issue.explanation,
            fix=issue.fix, evidence=displayed_evidence,
        ))
    return VerificationResult(findings=findings, summary=VerificationSummary(
        proposed=len(proposals), verified=len(findings), dropped=len(proposals) - len(findings),
    ))
