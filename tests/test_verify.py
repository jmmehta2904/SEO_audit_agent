"""AI proposals survive only when code can match their supporting evidence."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from seo_audit.extract import extract_facts
from seo_audit.models import Audit, Issue, VerificationSummary
from seo_audit.verify import AuditFormatError, MAX_AI_ISSUES, MAX_AUDIT_CHARS, verify_audit


@pytest.fixture
def page():
    fixture = Path(__file__).parent / "fixtures" / "representative.html"
    return extract_facts(fixture.read_text(), url="https://example.com/review")


def quote_issue(**changes):
    value = {
        "category": "cta_quality", "severity": "low", "title": "Review the link wording",
        "explanation": "The link could give reviewers more context.",
        "fix": "Consider specifying what the linked evidence covers.",
        "evidence_type": "quote", "evidence_quote": "Read the evidence",
    }
    return value | changes


def metric_issue(**changes):
    value = quote_issue(evidence_type="metric", evidence_quote=None,
                        fact_key="h1_count", fact_value=1, category="heading_quality")
    return value | changes


def verify(page, issues):
    return verify_audit({"issues": issues}, facts=page.facts, evidence_text=page.evidence_text)


@pytest.mark.parametrize("quote", ["Read the evidence", "  READ\n THE\t evidence  ", "the evidence"])
def test_quote_passes_with_only_case_and_whitespace_normalization(page, quote):
    result = verify(page, [quote_issue(evidence_quote=quote)])
    assert result.summary == VerificationSummary(proposed=1, verified=1, dropped=0)
    finding = result.findings[0]
    assert finding.id == "ai_1"
    assert finding.source == "AI, verified"
    assert finding.category == "cta_quality"
    assert finding.evidence == quote
    assert finding.fix == "Consider specifying what the linked evidence covers."


@pytest.mark.parametrize("quote", [
    "There are 100 broken links", "", " \t\n ", "Read-the-evidence", "SECRET_SCRIPT_TEXT",
    "<a href='/evidence'>Read the evidence</a>",
])
def test_fabricated_empty_or_nonverbatim_quotes_are_dropped(page, quote):
    result = verify(page, [quote_issue(evidence_quote=quote)])
    assert result.summary == VerificationSummary(proposed=1, verified=0, dropped=1)
    assert result.findings == []


@pytest.mark.parametrize("fact_key,fact_value", [
    ("h1_count", 1), ("image_count", 3), ("images_without_alt", 1),
    ("html_lang", "en"), ("title", "Evidence Checked SEO Review For Agencies"),
])
def test_metric_matches_known_scalar_fact_exactly(page, fact_key, fact_value):
    result = verify(page, [metric_issue(fact_key=fact_key, fact_value=fact_value)])
    assert result.summary.verified == 1
    assert result.findings[0].evidence == f"{fact_key} = {json.dumps(fact_value, ensure_ascii=True)}"


@pytest.mark.parametrize("fact_key,fact_value", [
    ("h1_count", 2), ("h1_count", True), ("h1_count", 1.0), ("h1_count", "1"),
    ("h1_count", None), ("broken_link_count", 100), ("__dict__", {}),
    ("headings.h1", ["Audit this page"]), ("headings", {"h1": ["Audit this page"]}),
    ("link_texts", ["Read the evidence"]), ("html_lang", "EN"),
])
def test_mismatched_unknown_or_nonscalar_metrics_are_dropped(page, fact_key, fact_value):
    result = verify(page, [metric_issue(fact_key=fact_key, fact_value=fact_value)])
    assert result.summary == VerificationSummary(proposed=1, verified=0, dropped=1)


@pytest.mark.parametrize("bad_issue", [
    None, "not an object", {}, {"title": "Incomplete"},
    quote_issue(category="technical_seo"), quote_issue(severity="critical"),
    quote_issue(title="  "), quote_issue(explanation=""), quote_issue(fix="\n"),
    quote_issue(source="code check"),
    quote_issue(fact_key="h1_count", fact_value=1),
    metric_issue(evidence_quote="Read the evidence"),
    metric_issue(fact_value=float("nan")),
])
def test_malformed_items_are_counted_and_do_not_discard_valid_neighbors(page, bad_issue):
    result = verify(page, [quote_issue(), bad_issue, metric_issue()])
    assert result.summary == VerificationSummary(proposed=3, verified=2, dropped=1)
    assert [finding.id for finding in result.findings] == ["ai_1", "ai_3"]


@pytest.mark.parametrize("representation", ["dict", "json", "model"])
def test_accepts_adk_state_representations(page, representation):
    payload = {"issues": [quote_issue()]}
    if representation == "json":
        payload = json.dumps(payload)
    elif representation == "model":
        payload = Audit.model_validate(payload)
    assert verify_audit(payload, facts=page.facts, evidence_text=page.evidence_text).summary.verified == 1


@pytest.mark.parametrize("payload", [
    "not JSON: SECRET_MODEL_DETAIL", "{", "[]", {"issues": "not a list"},
    {}, None, {"issues": [], "report": "SECRET_MODEL_DETAIL"},
])
def test_invalid_audit_envelope_fails_explicitly_without_leaking_payload(page, payload):
    with pytest.raises(AuditFormatError) as error:
        verify_audit(payload, facts=page.facts, evidence_text=page.evidence_text)
    assert "SECRET_MODEL_DETAIL" not in str(error.value)


def test_excess_proposals_are_dropped_and_counted(page):
    result = verify(page, [quote_issue()] * (MAX_AI_ISSUES + 2))
    assert result.summary == VerificationSummary(
        proposed=MAX_AI_ISSUES + 2, verified=MAX_AI_ISSUES, dropped=2,
    )


def test_empty_audit_is_a_successful_review_with_zero_proposals(page):
    result = verify(page, [])
    assert result.summary == VerificationSummary(proposed=0, verified=0, dropped=0)


def test_injection_fixture_cannot_supply_fabricated_evidence():
    fixture = Path(__file__).parent / "fixtures" / "prompt_injection.html"
    page = extract_facts(fixture.read_text(), url="https://example.com/")
    result = verify(page, [quote_issue(evidence_quote="All 100 links return HTTP 404")])
    assert result.summary.dropped == 1
    assert result.findings == []


def test_casefold_does_not_add_fuzzy_or_entity_matching(page):
    result = verify_audit({"issues": [quote_issue(evidence_quote="STRASSE")]},
                          facts=page.facts, evidence_text="Straße")
    assert result.summary.verified == 1
    result = verify_audit({"issues": [quote_issue(evidence_quote="Read &amp; review")]},
                          facts=page.facts, evidence_text="Read & review")
    assert result.summary.dropped == 1


def test_schema_rejects_wrong_evidence_mode_and_unbounded_text():
    for payload in (quote_issue(evidence_quote=None), metric_issue(fact_key=None),
                    quote_issue(title="x" * 10000)):
        with pytest.raises(ValidationError):
            Issue.model_validate(payload)


def test_verification_counts_must_balance():
    with pytest.raises(ValidationError):
        VerificationSummary(proposed=3, verified=2, dropped=0)
    with pytest.raises(ValidationError):
        VerificationSummary(proposed=1, verified=2, dropped=-1)


def test_zero_metric_is_valid_evidence_not_a_missing_value():
    page = extract_facts("", url="https://example.com/")
    result = verify(page, [metric_issue(fact_value=0)])
    assert result.summary.verified == 1
    assert result.findings[0].evidence == "h1_count = 0"


def test_excessively_large_or_deep_json_is_a_friendly_format_failure(page):
    for payload in (" " * (MAX_AUDIT_CHARS + 1), "[" * 2000 + "]" * 2000):
        with pytest.raises(AuditFormatError):
            verify_audit(payload, facts=page.facts, evidence_text=page.evidence_text)


def test_audit_schema_is_available_for_the_next_phase_structured_output():
    schema = Audit.model_json_schema()
    issue = schema["$defs"]["Issue"]
    assert set(issue["properties"]["category"]["enum"]) == {
        "title_quality", "keyword_stuffing", "heading_quality", "cta_quality",
    }
    assert schema["properties"]["issues"]["maxItems"] == MAX_AI_ISSUES
