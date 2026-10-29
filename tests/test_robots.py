"""Robots matching cases beyond the stdlib parser's prefix-only behavior."""

import pytest

from seo_audit.robots import RobotsPolicy


@pytest.mark.parametrize("rules,path,allowed", [
    ("Disallow: /private", "/private", False),
    ("Disallow: /private", "/Private", True),
    ("Disallow: /private\nAllow: /private/public", "/private/public/file", True),
    ("Allow: /private\nDisallow: /private/secret", "/private/secret", False),
    ("Disallow: /same\nAllow: /same", "/same", True),
    ("Disallow: /*.pdf$", "/download/report.pdf", False),
    ("Disallow: /*.pdf$", "/download/report.pdf.html", True),
    ("Disallow: /*?token=", "/download?token=abc", False),
    ("Disallow: /a*b*c$", "/axbyc", False),
    ("Disallow: /a*b*c$", "/axbycd", True),
    ("Disallow: /a*b*b$", "/ab", True),
    ("Disallow: /a*b*b$", "/abb", False),
    ("Disallow: /a**b", "/ab/c", False),
    ("Disallow: /café", "/caf%C3%A9/menu", False),
    ("Disallow: /a%62c", "/abc", False),
    ("Disallow: /abc", "/a%62c", False),
    ("Disallow: /a%2fb", "/a%2Fb", False),
    ("Disallow: /a%2fb", "/a/b", True),
    ("Disallow: /literal%2A", "/literal*", False),
    ("Disallow: /literal%24", "/literal$", False),
    ("Disallow:", "/anything", True),
    ("Disallow: / # keep robots itself fetchable", "/robots.txt", True),
])
def test_matching(rules, path, allowed):
    policy = RobotsPolicy.from_text("User-agent: *\n" + rules, "SEO-Audit-Demo")
    assert policy.allows("https://example.com" + path) is allowed


def test_named_group_overrides_wildcard_and_matching_groups_merge():
    policy = RobotsPolicy.from_text("""
User-agent: *
Disallow: /
User-agent: seo-audit-demo
Disallow: /one
Sitemap: https://example.com/sitemap.xml
Disallow: /two
User-agent: OtherBot
User-agent: SEO-AUDIT-DEMO
Disallow: /three
""", "SEO-Audit-Demo")
    for path in ("/one", "/two", "/three"):
        assert not policy.allows("https://example.com" + path)
    assert policy.allows("https://example.com/public")


def test_unrelated_agents_and_rules_outside_groups_do_not_apply():
    policy = RobotsPolicy.from_text("Disallow: /\nUser-agent: OtherBot\nDisallow: /", "SEO-Audit-Demo")
    assert policy.allows("https://example.com/anything")


def test_empty_specific_group_overrides_wildcard():
    policy = RobotsPolicy.from_text("User-agent: *\nDisallow: /\nUser-agent: SEO-Audit-Demo\n", "SEO-Audit-Demo")
    assert policy.allows("https://example.com/")


def test_many_wildcards_do_not_use_exponential_regex_backtracking():
    pattern = "/" + "*a" * 500 + "b$"
    policy = RobotsPolicy.from_text("User-agent: *\nDisallow: " + pattern, "SEO-Audit-Demo")
    assert policy.allows("https://example.com/" + "a" * 1000 + "c")
