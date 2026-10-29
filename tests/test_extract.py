from pathlib import Path

from seo_audit.extract import MAX_BODY_EXCERPT, MAX_EVIDENCE_ITEMS, extract_facts
from seo_audit.models import Facts


FIXTURES = Path(__file__).parent / "fixtures"


def test_extracts_page_facts_and_evidence():
    result = extract_facts((FIXTURES / "representative.html").read_text(), url="https://example.com/review")
    facts = result.facts
    assert facts.title == "Evidence Checked SEO Review For Agencies"
    assert facts.title_length == 40
    assert facts.meta_description.startswith("A practical review")
    assert facts.h1_count == 1
    assert facts.headings["h1"] == ["Audit this page"]
    assert facts.headings["h2"] == ["Evidence and trust"]
    assert facts.image_count == 3
    assert facts.images_without_alt == 1  # Empty alt is valid for decorative images.
    assert facts.alt_texts == ["Team reviewing an audit"]
    assert facts.link_texts == ["Read the evidence"]
    assert facts.canonical_url == "https://example.com/review"
    assert facts.viewport == "width=device-width, initial-scale=1"
    assert facts.html_lang == "en"
    assert facts.open_graph_count == 1
    assert facts.url == "https://example.com/review"
    assert "Team reviewing an audit" in result.evidence_text
    assert facts.meta_description in result.evidence_text
    assert "SECRET_SCRIPT_TEXT" not in result.evidence_text
    assert "NOT_IN_EXCERPT" not in result.evidence_text
    assert "not-body-text" not in result.evidence_text
    assert "<html" not in result.body_excerpt


def test_whitespace_is_collapsed_before_measuring():
    result = extract_facts("<title> One\n  clear\t title </title><meta name='description' content=' Helpful\n text '><h1>A\n B</h1>", url="https://example.com/")
    assert result.facts.title == "One clear title"
    assert result.facts.title_length == 15
    assert result.facts.meta_description == "Helpful text"
    assert result.facts.meta_description_length == 12
    assert result.facts.headings["h1"] == ["A B"]


def test_word_count_uses_full_body_without_head_or_executable_text():
    result = extract_facts("<head><title>Never count this title</title></head><body><p>One two three.</p><script>four five six</script></body>", url="https://example.com/")
    assert result.facts.word_count == 3
    assert result.body_excerpt == "One two three."


def test_missing_body_and_head_are_supported():
    result = extract_facts("<h1>Fragment</h1><p>Three more words</p>", url="https://example.com/")
    assert result.facts.word_count == 4
    assert result.facts.title == ""
    assert result.facts.title_length == 0
    assert result.facts.meta_description == ""
    assert result.facts.h1_count == 1


def test_empty_html_and_empty_attributes_are_supported():
    result = extract_facts("", url="https://example.com/")
    assert result.facts.word_count == 0
    assert result.body_excerpt == ""
    result = extract_facts("<html lang='  '><meta name=description><meta name=viewport content=''><link rel=canonical><img alt><img alt='  '><img></html>", url="https://example.com/")
    assert result.facts.meta_description == ""
    assert result.facts.viewport == ""
    assert result.facts.canonical_url == ""
    assert result.facts.html_lang == ""
    assert result.facts.images_without_alt == 1


def test_large_text_is_bounded_but_metrics_are_not_truncated():
    html = ("<html><head><title>" + "t" * 1500 + "</title>"
            + "<meta name=description content='" + "d" * 3000 + "'></head><body>"
            + "<h1>Heading</h1>" * 40
            + "<a href='/'>" + "link " * 100 + "</a>"
            + "<a href='/'>Link</a>" * 40
            + "<img alt='Picture'>" * 40 + "<p>" + "word " * 3000 + "</p></body></html>")
    result = extract_facts(html, url="https://example.com/")
    facts = result.facts
    assert facts.title_length == 1500
    assert len(facts.title) == 1000
    assert facts.meta_description_length == 3000
    assert len(facts.meta_description) == 2000
    assert facts.h1_count == 40
    assert len(facts.headings["h1"]) == MAX_EVIDENCE_ITEMS
    assert len(facts.link_texts) == MAX_EVIDENCE_ITEMS
    assert len(facts.alt_texts) == MAX_EVIDENCE_ITEMS
    assert facts.image_count == 40
    assert facts.word_count > 3000
    assert len(result.body_excerpt) <= MAX_BODY_EXCERPT == 6000
    assert len(result.evidence_text) < 30000


def test_extraction_and_facts_roundtrip_are_json_serializable():
    result = extract_facts((FIXTURES / "representative.html").read_text(), url="https://example.com/")
    assert Facts.model_validate_json(result.facts.model_dump_json()) == result.facts


def test_injection_content_remains_untrusted_evidence():
    result = extract_facts((FIXTURES / "prompt_injection.html").read_text(), url="https://example.com/")
    assert "Ignore previous instructions" in result.body_excerpt
    assert "DO_NOT_INCLUDE_SCRIPT_TEXT" not in result.evidence_text


def test_hostile_title_is_preserved_as_text_for_later_escaping():
    result = extract_facts((FIXTURES / "xss_title.html").read_text(), url="https://example.com/")
    assert result.facts.title == "<script>alert(1)</script>"
    assert result.facts.title in result.evidence_text


def test_malformed_head_contents_do_not_break_extraction():
    result = extract_facts("<head><h1>Misplaced heading</h1><img src='/x'></head><body>Actual body</body>", url="https://example.com/")
    assert result.facts.h1_count == 1
    assert result.facts.image_count == 1
    assert result.facts.images_without_alt == 1
    assert result.body_excerpt == "Actual body"


def test_metadata_outside_head_is_not_counted_as_body_text():
    result = extract_facts("<title>Metadata only title</title><p>Actual body</p>", url="https://example.com/")
    assert result.facts.title == "Metadata only title"
    assert result.body_excerpt == "Actual body"
    assert result.facts.word_count == 2


def test_nested_inert_elements_are_removed_without_errors():
    result = extract_facts("<template><template><h1>Inert</h1></template></template><p>Actual body</p>", url="https://example.com/")
    assert result.facts.h1_count == 0
    assert result.body_excerpt == "Actual body"
