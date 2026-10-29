from pathlib import Path

import pytest

from seo_audit.extract import extract_facts
from seo_audit.models import Finding
from seo_audit.rules import RULE_SEVERITIES, rule_findings


def page(*, title_length=40, description_length=100, h1_count=1, words=300,
         canonical=True, viewport=True, lang=True, og=True, image=""):
    return (f"<html{' lang=en' if lang else ''}><head><title>{'T' * title_length}</title>"
            f"<meta name=description content='{'D' * description_length}'>"
            + ("<link rel=canonical href='https://example.com/'>" if canonical else "")
            + ("<meta name=viewport content='width=device-width'>" if viewport else "")
            + ("<meta property='og:title' content='Page'>" if og else "")
            + "</head><body>" + "<h1></h1>" * h1_count
            + image + "<p>" + "word " * words + "</p></body></html>")


def findings(html):
    return rule_findings(extract_facts(html, url="https://example.com/").facts)


def ids(html):
    return {finding.id for finding in findings(html)}


def test_healthy_page_has_no_deterministic_findings():
    assert findings(page()) == []


@pytest.mark.parametrize("length,expected", [
    (0, {"title_missing"}), (29, {"title_too_short"}), (30, set()),
    (60, set()), (61, {"title_too_long"}),
])
def test_title_thresholds(length, expected):
    assert ids(page(title_length=length)) == expected


@pytest.mark.parametrize("length,expected", [
    (0, {"meta_description_missing"}), (69, {"meta_description_length"}),
    (70, set()), (160, set()), (161, {"meta_description_length"}),
])
def test_description_thresholds(length, expected):
    assert ids(page(description_length=length)) == expected


@pytest.mark.parametrize("count,expected", [(0, {"h1_count"}), (1, set()), (2, {"h1_count"})])
def test_h1_count(count, expected):
    assert ids(page(h1_count=count)) == expected


@pytest.mark.parametrize("words,expected", [(0, {"low_word_count"}), (299, {"low_word_count"}), (300, set())])
def test_word_count_threshold(words, expected):
    assert ids(page(words=words)) == expected


@pytest.mark.parametrize("options,expected", [
    ({"canonical": False}, {"canonical_missing"}),
    ({"viewport": False}, {"viewport_missing"}),
    ({"lang": False}, {"html_lang_missing"}),
    ({"og": False}, {"open_graph_missing"}),
    ({"image": "<img src='/missing.jpg'>"}, {"images_without_alt"}),
    ({"image": "<img src='/decorative.jpg' alt=''>"}, set()),
])
def test_presence_rules(options, expected):
    assert ids(page(**options)) == expected


def test_missing_basics_have_no_redundant_length_findings():
    fixture = Path(__file__).parent / "fixtures" / "missing_basics.html"
    result = findings(fixture.read_text())
    assert {finding.id for finding in result} == {
        "title_missing", "meta_description_missing", "h1_count", "images_without_alt",
        "canonical_missing", "viewport_missing", "html_lang_missing", "low_word_count", "open_graph_missing",
    }
    for finding in result:
        assert finding.source == "code check"
        assert finding.severity == RULE_SEVERITIES[finding.id]
        assert finding.title and finding.explanation and finding.evidence and finding.fix
        assert Finding.model_validate_json(finding.model_dump_json()) == finding


def test_metrics_in_evidence_use_untruncated_lengths():
    result = findings(page(title_length=1500, description_length=3000))
    assert "1500" in next(finding.evidence for finding in result if finding.id == "title_too_long")
    assert "3000" in next(finding.evidence for finding in result if finding.id == "meta_description_length")


def test_rules_are_deterministic_and_do_not_mutate_facts():
    facts = extract_facts(page(title_length=15, canonical=False), url="https://example.com/").facts
    before = facts.model_dump_json()
    assert rule_findings(facts) == rule_findings(facts)
    assert facts.model_dump_json() == before
