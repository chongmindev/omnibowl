"""Clue difficulty features (decision "clue difficulty: A + B2", 2026-10-12). Used by `rank`.

  A1 repetition: a fact the book restates in other sentences about the same answer is
     easier than one stated once.
  A2 emphasis (placement): computed in rank.py, as before (definitions and summaries easy,
     boxes and captions hard).
  A3 detail: years, other numbers, citations, people named and quoted stimuli make a clue
     harder.
  B2 familiarity: how often real quizbowl tossups use the clue's book terms, and how late
     in the question (stage `qbreader`).
difficulty = weighted mean of the features present (weights in [rank]).
What would break on another book: nothing book-specific; a book without citations or
QBReader coverage just leans on placement and repetition.
"""

import math
import re
from collections import Counter, defaultdict

WORD = re.compile(r"[A-Za-z][A-Za-z'’-]+|\d+")
NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?%?(?![\w])")
CITATION = re.compile(r"\bet al\.|\([A-Z][A-Za-z’'-]+(?: (?:and|&) [A-Z][A-Za-z’'-]+)?,? \d{4}[a-z]?\)")
QUOTED = re.compile(r"“[^”]{1,80}”|\"[^\"]{1,80}\"")


def words(text: str, stop: set[str], drop: set[str], prefix: int) -> set[str]:
    return {w.lower()[:prefix] for w in WORD.findall(text)
            if len(w) > 2 and w.lower() not in stop and w.lower() not in drop}


def repetition(clue_words: list[set[str]], sentence_ids: list[int], min_shared: int) -> list[float]:
    """For each clue of one answer, 1 / (1 + other clue sentences sharing at least
    `min_shared` of its words). Clues from the same sentence do not count as restatements."""
    index = defaultdict(set)
    for i, ws in enumerate(clue_words):
        for w in ws:
            index[w].add(i)
    out = []
    for i, ws in enumerate(clue_words):
        shared = Counter(j for w in ws for j in index[w] if sentence_ids[j] != sentence_ids[i])
        restated = {sentence_ids[j] for j, n in shared.items() if n >= min_shared}
        out.append(1.0 / (1 + len(restated)))
    return out


def detail(text: str, people_named: int, r: dict) -> float:
    """min(1, markers / saturation): numbers (years included), citations, people, quotes."""
    cites = CITATION.findall(text)
    rest = CITATION.sub(" ", text)  # a citation's year is not a second marker
    markers = len(NUMBER.findall(rest)) + len(cites) + people_named + len(QUOTED.findall(rest))
    return min(1.0, markers / r["detail_saturation"])


def phrase_hardness(n_tossups: int, mean_pos: float | None, r: dict) -> float:
    """1 - f * position: a phrase quizbowl uses often, late in questions, is easy."""
    f = min(1.0, math.log1p(n_tossups) / math.log1p(r["familiar_tossups"]))
    pos = r["unknown_pos"] if mean_pos is None else mean_pos
    return 1.0 - f * pos


def familiarity(phrases: list[tuple[int, float | None]], r: dict) -> float | None:
    """The clue's most familiar phrase decides; None when it has no looked-up phrase."""
    return min((phrase_hardness(n, p, r) for n, p in phrases), default=None)


def combine(features: dict[str, float | None], r: dict) -> float:
    """Weighted mean over the features that are present (not None) and weighted."""
    num = den = 0.0
    for name, value in features.items():
        w = r.get(f"w_{name}", 0.0)
        if value is not None and w:
            num += w * value
            den += w
    return num / den if den else 0.0
