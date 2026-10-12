"""Pyramidality: is a tossup's clue difficulty non-increasing from first sentence to last?

Used by eval/pyramid.py and eval/review_difficulty.py. A rewritten tossup (docs/rewrite-v1.md)
does not record which source sentence each of its sentences came from, so `link` matches
each question sentence to the source sharing the largest share of its content words.
Words are compared by their first `prefix` letters, so "categorized" meets "categories".
"""

import re

WORD = re.compile(r"[A-Za-z][A-Za-z'’-]+|\d+")
# A sentence ends at . ! ? (optionally followed by a closing quote or bracket) before a
# capital letter, a digit or an opening quote; "et al." and "e.g." are not ends.
BOUNDARY = re.compile(r"[.!?][”\"')\]]?\s+(?=[A-Z0-9“\"(])")
NOT_END = ("et al.", "e.g.", "i.e.", "vs.", "cf.")
GIVEAWAY = re.compile(r"^For 10 points\b", re.I)


def split_sentences(question: str) -> list[str]:
    text, out, start = question.strip(), [], 0
    for m in BOUNDARY.finditer(text):
        head = text[start:m.end()].rstrip()
        if not head.lower().endswith(NOT_END):
            out.append(head)
            start = m.end()
    out.append(text[start:].strip())
    return [x for x in out if x]


def clue_sentences(question: str) -> tuple[list[str], str | None]:
    """The question's sentences before the giveaway, and the giveaway (None if missing)."""
    sents = split_sentences(question)
    if sents and GIVEAWAY.match(sents[-1]):
        return sents[:-1], sents[-1]
    return sents, None


def content_words(text: str, stop: set[str], prefix: int) -> set[str]:
    return {w.lower()[:prefix] for w in WORD.findall(text) if len(w) > 2 and w.lower() not in stop}


def link(sentences: list[str], sources: list[str], stop: set[str], prefix: int,
         min_share: float) -> list[int | None]:
    """For each sentence, the index of the source it came from, or None when no source
    shares at least `min_share` of the sentence's content words."""
    src = [content_words(s, stop, prefix) for s in sources]
    out = []
    for s in sentences:
        words = content_words(s, stop, prefix)
        best, best_share = None, 0.0
        for i, sw in enumerate(src):
            share = len(words & sw) / len(words) if words else 0.0
            if share > best_share:
                best, best_share = i, share
        out.append(best if best_share >= min_share else None)
    return out


def pair_agreement(hard_first: list[float]) -> float | None:
    """Share of sentence pairs (earlier, later) where the earlier one scores harder; a tie
    counts half. 1.0 = perfectly pyramidal, 0.5 = no relation, 0.0 = reversed."""
    n = len(hard_first)
    if n < 2:
        return None
    pairs = [(hard_first[i], hard_first[j]) for i in range(n) for j in range(i + 1, n)]
    return sum(1.0 if a > b else 0.5 if a == b else 0.0 for a, b in pairs) / len(pairs)


def dropbacks(hard_first: list[float], eps: float) -> int:
    """Adjacent pairs where difficulty rises again by more than `eps`."""
    return sum(1 for a, b in zip(hard_first, hard_first[1:]) if b - a > eps)


def order_agreement(scores: dict[int, float], gold_hard_first: list[int]) -> float | None:
    """Share of pairs in a hand-made order (sentence indices, hardest first) that the
    scores put the same way round; a tie counts half."""
    idx = [i for i in gold_hard_first if i in scores]
    return pair_agreement([scores[i] for i in idx])
