import base64
import json
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from pydantic import ValidationError

from seo_audit.extract import extract_facts
from seo_audit.models import Finding, Report
from seo_audit.report import build_report, render_html, render_json
from seo_audit.rules import rule_findings
from seo_audit.verify import verify_audit


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)
ATTACK = '</script><script>alert("XSS")</script><img src=x onerror=alert(1)>'


def make_report(*, fixture="representative.html", unavailable=False):
    page = extract_facts((FIXTURES / fixture).read_text(), url="https://example.com/review")
    proposals = {"issues": [{
        "category": "heading_quality", "severity": "low", "title": "Review the heading context",
        "explanation": "Consider whether the heading gives readers sufficient context.",
        "fix": "Review it alongside the page purpose.", "evidence_type": "metric",
        "fact_key": "h1_count", "fact_value": page.facts.h1_count,
    }, {
        "category": "cta_quality", "severity": "medium", "title": "Fabricated proposal",
        "explanation": "This must be dropped.", "fix": "Never show this fix.",
        "evidence_type": "quote", "evidence_quote": "A nonexistent sign-up button",
    }]}
    verification = None if unavailable else verify_audit(
        proposals, facts=page.facts, evidence_text=page.evidence_text,
    )
    return build_report(page.facts, rule_findings(page.facts), verification,
                        model_name="offline-test-model", ai_review_available=not unavailable,
                        generated_at=NOW)


def test_report_combines_code_and_verified_ai_with_correct_counts():
    report = make_report()
    assert report.verification.proposed == 2
    assert report.verification.verified == 1
    assert report.verification.dropped == 1
    assert sum(report.severity_counts.values()) == len(report.findings)
    for severity in ("high", "medium", "low"):
        assert report.severity_counts[severity] == sum(f.severity == severity for f in report.findings)
    assert report.findings[-1].source == "AI, verified"
    assert report.ai_review_available
    assert report.ai_notice is None
    assert report.model_name == "offline-test-model"
    assert report.adk_version == version("google-adk")
    assert report.generated_at == NOW
    assert report.url == report.facts.url
    assert "Fabricated proposal" not in render_html(report)
    assert "Never show this fix" not in render_json(report)


def test_html_contains_report_sections_counts_metadata_and_limitations():
    report = make_report()
    html = render_html(report)
    soup = BeautifulSoup(html, "html.parser")
    assert soup.find(id="findings")
    assert soup.find(id="facts")
    assert soup.find(id="limitations")
    text = soup.get_text(" ", strip=True)
    for value in ("code check", "AI, verified", "2 proposed, 1 verified, 1 dropped",
                  "offline-test-model", version("google-adk"), "2026-10-28T10:00:00+00:00",
                  "automated first-pass review", "not an authoritative SEO audit",
                  "one page only", "HTML as fetched", "no JavaScript rendering"):
        assert value.lower() in text.lower()
    assert "@media print" in html
    assert soup.find_all("table") and len(soup.find_all("table")) == 2


def test_hostile_fixture_title_is_escaped_with_no_live_script():
    html = render_html(make_report(fixture="xss_title.html"))
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script" not in html.lower()
    assert BeautifulSoup(html, "html.parser").find("script") is None


def test_all_page_and_model_strings_are_escaped_and_never_become_links():
    report = make_report()
    facts = report.facts.model_copy(update={
        "url": ATTACK, "title": ATTACK, "meta_description": ATTACK,
        "canonical_url": "javascript:alert(1)", "headings": {"h1": [ATTACK]},
        "link_texts": [ATTACK], "alt_texts": [ATTACK], "viewport": ATTACK, "html_lang": ATTACK,
    })
    hostile = Finding(id="test", source="AI, verified", category="cta_quality", severity="low",
                      title=ATTACK, explanation=ATTACK, evidence=ATTACK, fix=ATTACK)
    findings = [*report.findings[:-1], hostile]
    report = report.model_copy(update={"url": ATTACK, "facts": facts, "findings": findings,
                                       "model_name": ATTACK, "adk_version": ATTACK,
                                       "limitations": [ATTACK]})
    html = render_html(report)
    soup = BeautifulSoup(html, "html.parser")
    assert "&lt;script&gt;" in html
    assert not soup.find_all(["script", "img", "iframe", "svg"])
    for tag in soup.find_all():
        assert not any(key.lower().startswith("on") for key in tag.attrs)
    assert all(not str(link.get("href", "")).startswith("javascript:") for link in soup.find_all("a"))
    assert ATTACK in soup.get_text()


def test_json_download_decodes_to_exact_report_without_persistence():
    report = make_report(fixture="xss_title.html")
    soup = BeautifulSoup(render_html(report), "html.parser")
    link = soup.find("a", attrs={"download": "seo-audit-report.json"})
    assert link is not None
    prefix = "data:application/json;base64,"
    assert link["href"].startswith(prefix)
    downloaded = base64.b64decode(link["href"][len(prefix):], validate=True).decode("utf-8")
    assert downloaded == render_json(report)
    assert json.loads(downloaded)["facts"]["title"] == "<script>alert(1)</script>"
    assert Report.model_validate_json(downloaded) == report


def test_unavailable_ai_has_code_findings_and_unknown_counts_not_fabricated_zeros():
    report = make_report(unavailable=True)
    assert not report.ai_review_available
    assert report.ai_notice == "AI review unavailable"
    assert report.verification is None
    assert all(finding.source == "code check" for finding in report.findings)
    assert "AI review unavailable" in render_html(report)
    assert "proposal counts unavailable" in render_html(report).lower()
    assert json.loads(render_json(report))["verification"] is None


def test_empty_findings_report_is_printable_and_honest():
    page = extract_facts("", url="https://example.com/")
    verification = verify_audit({"issues": []}, facts=page.facts, evidence_text="")
    report = build_report(page.facts, [], verification, model_name="offline-test-model", generated_at=NOW)
    text = BeautifulSoup(render_html(report), "html.parser").get_text(" ", strip=True)
    assert "No findings" in text
    assert "0 proposed, 0 verified, 0 dropped" in text
    assert report.severity_counts == {"high": 0, "medium": 0, "low": 0}


def test_report_build_does_not_mutate_its_inputs():
    page = extract_facts("", url="https://example.com/")
    code = rule_findings(page.facts)
    verification = verify_audit({"issues": []}, facts=page.facts, evidence_text="")
    before = [finding.model_dump() for finding in code]
    build_report(page.facts, code, verification, model_name="offline-test-model", generated_at=NOW)
    assert before == [finding.model_dump() for finding in code]
    assert verification.findings == []


def test_timestamp_is_aware_and_normalized_to_utc():
    report = make_report()
    local_time = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
    result = build_report(report.facts, [], None, model_name="offline-test-model",
                          ai_review_available=False, generated_at=local_time)
    assert result.generated_at.utcoffset() == timedelta(0)
    assert result.generated_at == NOW
    with pytest.raises(ValueError):
        build_report(report.facts, [], None, model_name="offline-test-model",
                     ai_review_available=False, generated_at=datetime(2026, 10, 28))


def test_model_identity_must_be_explicit_and_nonempty():
    report = make_report()
    with pytest.raises(ValueError):
        build_report(report.facts, [], None, model_name=" ", ai_review_available=False)


def test_report_rejects_inconsistent_counts_or_availability():
    payload = json.loads(render_json(make_report()))
    payload["severity_counts"]["high"] += 1
    with pytest.raises(ValidationError):
        Report.model_validate(payload)
    payload = json.loads(render_json(make_report()))
    payload["ai_review_available"] = False
    with pytest.raises(ValidationError):
        Report.model_validate(payload)


def test_missing_verification_is_not_silently_a_successful_ai_review():
    page = extract_facts("", url="https://example.com/")
    with pytest.raises(ValueError):
        build_report(page.facts, [], None, model_name="offline-test-model")


def test_report_count_fields_do_not_coerce_booleans_or_strings():
    for value in (True, "1", 1.0):
        payload = json.loads(render_json(make_report()))
        payload["severity_counts"]["low"] = value
        with pytest.raises(ValidationError):
            Report.model_validate(payload)


def test_unavailable_review_cannot_include_verified_ai_results():
    report = make_report()
    verification = verify_audit({"issues": []}, facts=report.facts, evidence_text="")
    with pytest.raises(ValueError):
        build_report(report.facts, [], verification, model_name="offline-test-model",
                     ai_review_available=False)
