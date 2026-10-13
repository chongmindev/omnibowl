"""Answer handles ("this organ", "these cells") and how many answers in a pool each fits
(decision "lead-in handles: pool check", A, 2026-10-12).

In a closed pool (players see only these answers), a lead-in handle that fits one answer
gives it away on the first word. A handle "fits" an answer when that answer's tossups use
it anywhere, or it is the answer's referent. The lead-in (first sentence) handle must fit
at least `lead_handle_min_answers` answers; later sentences may sharpen (docs/rewrite-v1.md).
Handles compare by lemma of their head noun ("these cells" = "this cell").
Limit: the head comes from spaCy's parse, which sometimes tags a verb as a noun ("this type
treats ..." -> "treats"). Such a head fits no other answer, so the error shows up as a
flagged lead-in for a person to fix; a misparse that lands on a generic noun could let one through, which is rare.
"""

from collections import defaultdict
from dataclasses import dataclass

DETERMINERS = {"this", "these"}


@dataclass
class Handle:
    start: int        # character offsets of "this ... noun" in the sentence
    end: int
    det: str          # "this" or "these"
    head: str         # lemma of the head noun, lowercase
    text: str


def handles(text: str, nlp) -> list[Handle]:
    """Every "this/these <noun phrase>" in the text, in order."""
    out = []
    for tok in nlp(text):
        if tok.lower_ in DETERMINERS and tok.dep_ == "det" and tok.head.pos_ in ("NOUN", "PROPN") \
                and tok.head.i > tok.i:
            head = tok.head
            out.append(Handle(tok.idx, head.idx + len(head.text), tok.lower_, (head.lemma_ or head.text).lower(),
                              text[tok.idx:head.idx + len(head.text)]))
    return out


def lead_handle(first_sentence: str, nlp) -> Handle | None:
    hs = handles(first_sentence, nlp)
    return hs[0] if hs else None


def fits(pool: dict[str, list[str]], nlp) -> dict[str, set[str]]:
    """{head lemma: answers it fits}, from {answer key: [texts that describe it]} (its
    tossups and its referent)."""
    out = defaultdict(set)
    for answer, texts in pool.items():
        for t in texts:
            for h in handles(t, nlp):
                out[h.head].add(answer)
    return out
