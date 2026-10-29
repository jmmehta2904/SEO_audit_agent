"""Small, bounded robots policy matcher; network access lives in security.py.

RFC 9309 matching: grouped agents, wildcard fallback, merged exact-agent groups,
case-sensitive paths, '*'/'$', longest octet match, allow on ties, and normalized
percent escapes. A literal-segment matcher avoids untrusted regex backtracking.
"""

import re
from dataclasses import dataclass
from urllib.parse import quote, urlsplit


_UNRESERVED = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
_ESCAPE = re.compile(r"%([0-9a-fA-F]{2})")


def _octets(value: str, *, pattern: bool) -> str:
    value = quote(value, safe="/:;?@!&=+,%" + ("*" if pattern else ""))

    def normalize(match):
        char = chr(int(match[1], 16))
        return char if char in _UNRESERVED else match[0].upper()

    return _ESCAPE.sub(normalize, value)


@dataclass(frozen=True)
class _Rule:
    pattern: str
    end: bool
    allow: bool
    specificity: int

    def matches(self, path: str) -> bool:
        parts = self.pattern.split("*")
        if len(parts) == 1:
            return path == self.pattern if self.end else path.startswith(self.pattern)
        if not path.startswith(parts[0]):
            return False
        position = len(parts[0])
        for part in parts[1:-1]:
            found = path.find(part, position)
            if found < 0:
                return False
            position = found + len(part)
        last = parts[-1]
        if self.end:
            return path.endswith(last) and len(path) - len(last) >= position
        return path.find(last, position) >= 0


@dataclass(frozen=True)
class RobotsPolicy:
    rules: tuple[_Rule, ...]

    @classmethod
    def from_text(cls, text: str, product_token: str) -> "RobotsPolicy":
        groups: list[tuple[list[str], list[_Rule]]] = []
        agents: list[str] = []
        rules: list[_Rule] = []
        started_rules = False
        for raw_line in text.splitlines():
            line = raw_line.partition("#")[0].strip()
            name, separator, value = line.partition(":")
            if not separator:
                continue
            name, value = name.strip().lower(), value.strip()
            if name == "user-agent" and value:
                if started_rules:
                    groups.append((agents, rules))
                    agents, rules, started_rules = [], [], False
                agents.append(value.lower())
            elif name in {"allow", "disallow"} and agents:
                started_rules = True
                if not value or not value.startswith(("/", "*")):
                    continue
                end = value.endswith("$")
                pattern = _octets(value[:-1] if end else value, pattern=True)
                specificity = len(_ESCAPE.sub("x", pattern.replace("*", "")))
                rules.append(_Rule(pattern, end, name == "allow", specificity))
        groups.append((agents, rules))
        exact = [group for agents, group in groups if product_token.lower() in agents]
        selected = exact if exact else [group for agents, group in groups if "*" in agents]
        return cls(tuple(rule for group in selected for rule in group))

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.path == "/robots.txt":
            return True
        path = _octets((parts.path or "/") + ("?" + parts.query if parts.query else ""), pattern=False)
        applicable = [(rule.specificity, rule.allow) for rule in self.rules if rule.matches(path)]
        return max(applicable)[1] if applicable else True
