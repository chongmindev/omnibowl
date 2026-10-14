"""Player names: refuse ones containing a blocked word (decision "room names: blocked
words", 2026-10-14). Same rule as nameOk in tools/judge.js; the list and the edge length
come from tossups.json ("names"), written by tools/play.py from pipeline/config.toml.
"""

import re
import unicodedata
from dataclasses import dataclass

LEET = str.maketrans("013457@$!", "oieastasi")


def words(text: str) -> list[str]:
    """Lowercase letter runs, accents dropped and look-alike digits/symbols read as letters."""
    t = unicodedata.normalize("NFKD", text.lower().translate(LEET))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.findall(r"[a-z]+", t)


@dataclass(frozen=True)
class NameRules:
    phrases: tuple[tuple[str, ...], ...] = ()   # each blocked entry, as words
    edges: frozenset[str] = frozenset()         # entries (joined) long enough for the edge rule

    @classmethod
    def from_json(cls, j: dict) -> "NameRules":
        phrases = tuple(p for p in (tuple(words(e)) for e in j.get("blocked", [])) if p)
        n = j.get("edge_min_letters", 0)
        return cls(phrases, frozenset("".join(p) for p in phrases if n and len("".join(p)) >= n))


def name_ok(name: str, r: NameRules) -> bool:
    ws = words(name)
    if not ws:
        return True
    padded = " " + " ".join(ws) + " "
    whole = "".join(ws)  # "f.u.c.k" -> "fuck"
    for p in r.phrases:
        if " " + " ".join(p) + " " in padded or "".join(p) == whole:
            return False
    return not any(w.startswith(e) or w.endswith(e) for w in ws for e in r.edges)
