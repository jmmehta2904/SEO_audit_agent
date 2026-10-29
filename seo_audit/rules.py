"""Deterministic first-pass checks; never produce a numeric SEO score."""

from seo_audit.models import Facts, Finding, Severity


RULE_SEVERITIES: dict[str, Severity] = {
    "title_missing": "high",
    "title_too_short": "medium",
    "title_too_long": "medium",
    "meta_description_missing": "medium",
    "meta_description_length": "low",
    "h1_count": "medium",
    "images_without_alt": "medium",
    "canonical_missing": "medium",
    "viewport_missing": "medium",
    "html_lang_missing": "low",
    "low_word_count": "low",
    "open_graph_missing": "low",
}


def rule_findings(facts: Facts) -> list[Finding]:
    findings: list[Finding] = []

    def add(id: str, title: str, explanation: str, evidence: str, fix: str):
        findings.append(Finding(id=id, source="code check", severity=RULE_SEVERITIES[id],
                                title=title, explanation=explanation, evidence=evidence, fix=fix))

    if facts.title_length == 0:
        add("title_missing", "Page title is missing", "No non-empty title was found in the first title element.",
            "title_length = 0", "Add a concise, descriptive title that identifies this page.")
    elif facts.title_length < 30:
        add("title_too_short", "Page title is shorter than the review range",
            "The title contains fewer than 30 characters; review whether it describes the page clearly.",
            f"title_length = {facts.title_length}; review range = 30–60",
            "Review the title and add useful context where appropriate.")
    elif facts.title_length > 60:
        add("title_too_long", "Page title is longer than the review range",
            "The title contains more than 60 characters; search result display length can vary.",
            f"title_length = {facts.title_length}; review range = 30–60",
            "Consider shortening the title while preserving its most useful page-specific information.")

    if facts.meta_description_length == 0:
        add("meta_description_missing", "Meta description is missing",
            "No non-empty content was found in the first description meta element.",
            "meta_description_length = 0", "Add a useful description of this page's content.")
    elif not 70 <= facts.meta_description_length <= 160:
        add("meta_description_length", "Meta description is outside the review range",
            "The description length falls outside the configured 70–160 character range.",
            f"meta_description_length = {facts.meta_description_length}; review range = 70–160",
            "Review the description for a concise, accurate summary; search engines may choose different text.")

    if facts.h1_count != 1:
        add("h1_count", "H1 count differs from one", "The fetched HTML does not contain exactly one H1 element.",
            f"h1_count = {facts.h1_count}", "Review the heading structure and use a clear primary page heading.")
    if facts.images_without_alt:
        add("images_without_alt", "Images are missing alt attributes",
            "Some img elements have no alt attribute. An empty alt attribute is accepted for decorative images.",
            f"images_without_alt = {facts.images_without_alt}; image_count = {facts.image_count}",
            "Add descriptive alt text to informative images and alt=\"\" to decorative images.")
    if not facts.canonical_url:
        add("canonical_missing", "Canonical link is missing", "No canonical link with a non-empty href was found.",
            "canonical_url = (empty)", "Review the preferred URL and add a canonical link where appropriate.")
    if not facts.viewport:
        add("viewport_missing", "Viewport meta is missing", "No non-empty viewport meta content was found.",
            "viewport = (empty)", "Review mobile layout and provide an appropriate viewport meta element.")
    if not facts.html_lang:
        add("html_lang_missing", "HTML language is missing", "The html element has no non-empty lang attribute.",
            "html_lang = (empty)", "Set the html lang attribute to the page's primary language.")
    if facts.word_count < 300:
        add("low_word_count", "Fetched body has fewer than 300 words",
            "The extracted body falls below this first-pass review threshold; appropriate length depends on page purpose.",
            f"word_count = {facts.word_count}; review threshold = 300",
            "Review whether the page provides enough useful content for its intended audience.")
    if facts.open_graph_count == 0:
        add("open_graph_missing", "Open Graph tags are missing", "No meta element with an og: property was found.",
            "open_graph_count = 0", "Add appropriate Open Graph metadata if this page is intended for social sharing.")
    return findings
