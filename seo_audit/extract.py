"""Extract full-page measurements and bounded evidence, without executing HTML."""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from seo_audit.models import Facts


MAX_BODY_EXCERPT = 6000
MAX_EVIDENCE_ITEMS = 20
MAX_ITEM_CHARS = 300
MAX_TITLE_CHARS = 1000
MAX_DESCRIPTION_CHARS = 2000
_WORD = re.compile(r"\b\w+(?:['’\-]\w+)*\b", re.UNICODE)


@dataclass(frozen=True)
class ExtractedPage:
    facts: Facts
    body_excerpt: str
    evidence_text: str


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _attribute(tag: Tag, name: str) -> str:
    value = tag.get(name)
    return _normalize(value) if isinstance(value, str) else ""


def _samples(values: Iterable[str]) -> list[str]:
    result = []
    for value in values:
        normalized = _normalize(value)
        if normalized:
            result.append(normalized[:MAX_ITEM_CHARS].rstrip())
        if len(result) == MAX_EVIDENCE_ITEMS:
            break
    return result


def extract_facts(html: str, *, url: str) -> ExtractedPage:
    """Parse safely fetched HTML. Does not fetch links, images, or other assets.

    Body word count is a Unicode word-token approximation, not language-aware
    segmentation. Metadata uses its first matching element. Empty alt attributes
    are permitted; only absent attributes count as images without alt.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "noscript", "template"]):
        tag.decompose()

    title = _normalize(soup.title.get_text(" ", strip=True)) if soup.title else ""
    description = ""
    viewport = ""
    seen_description = seen_viewport = False
    open_graph_count = 0
    for tag in soup.find_all("meta"):
        name = _attribute(tag, "name").lower()
        if name == "description" and not seen_description:
            description, seen_description = _attribute(tag, "content"), True
        elif name == "viewport" and not seen_viewport:
            viewport, seen_viewport = _attribute(tag, "content"), True
        if _attribute(tag, "property").lower().startswith("og:"):
            open_graph_count += 1

    canonical = ""
    for tag in soup.find_all("link"):
        rel = tag.get("rel", [])
        rel = rel.split() if isinstance(rel, str) else rel
        if any(str(token).lower() == "canonical" for token in rel):
            canonical = _attribute(tag, "href")
            if canonical:
                break

    heading_tags = soup.find_all(re.compile(r"^h[1-6]$"))
    headings = {f"h{level}": [] for level in range(1, 7)}
    for tag in heading_tags[:MAX_EVIDENCE_ITEMS]:
        text = _normalize(tag.get_text(" ", strip=True))[:MAX_ITEM_CHARS].rstrip()
        if text:
            headings[tag.name].append(text)
    images = soup.find_all("img")
    h1_count = sum(tag.name == "h1" for tag in heading_tags)
    image_count = len(images)
    images_without_alt = sum(not tag.has_attr("alt") for tag in images)
    alt_texts = _samples(_attribute(tag, "alt") for tag in images)
    link_texts = _samples(tag.get_text(" ", strip=True) for tag in soup.find_all("a", href=True))
    html_lang = _attribute(soup.html, "lang")[:128] if soup.html else ""

    # Remove head metadata from body text, even for fragments without a body tag.
    for tag in soup.find_all("head"):
        tag.decompose()
    for tag in soup.find_all(["title", "meta", "link", "base"]):
        tag.decompose()
    body = soup.body if soup.body is not None else soup
    body_text = _normalize(body.get_text(" ", strip=True))
    body_excerpt = body_text[:MAX_BODY_EXCERPT].rstrip()
    facts = Facts(
        url=url, title=title[:MAX_TITLE_CHARS].rstrip(), title_length=len(title),
        meta_description=description[:MAX_DESCRIPTION_CHARS].rstrip(),
        meta_description_length=len(description),
        h1_count=h1_count, headings=headings,
        image_count=image_count, images_without_alt=images_without_alt,
        alt_texts=alt_texts, link_texts=link_texts, canonical_url=canonical[:2048],
        viewport=viewport[:512], html_lang=html_lang,
        word_count=sum(1 for _ in _WORD.finditer(body_text)), open_graph_count=open_graph_count,
    )
    evidence_parts = [facts.title, facts.meta_description]
    evidence_parts.extend(text for group in facts.headings.values() for text in group)
    evidence_parts.extend(facts.link_texts)
    evidence_parts.extend(facts.alt_texts)
    evidence_parts.append(body_excerpt)
    return ExtractedPage(facts, body_excerpt, "\n".join(text for text in evidence_parts if text))
