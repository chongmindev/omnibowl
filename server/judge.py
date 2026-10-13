"""Answer judging (decision "answer judging", P1 + S1, 2026-10-11). Same rules as
tools/judge.js (the single-player page); server/tests/judge_cases.json holds the cases
both must pass.

accept: the guess equals an accepted form, or is within the typo limit of one and no
        other book term is as close (a different real term is not a typo).
prompt: the same for the tossup's prompt forms, once per buzz.
Comparison ignores case, punctuation, `ignore_words` and word order. Inflections are
forgiven (decision "answer judging: leniency", 2026-10-12): words also match by a light
stem ("testing" = "test"), unless the guess is itself another book term ("priming" is
not "prime"). After a prompt, the new answer is also tried together with the prompted
one ("aphasia", then "Broca's").
"""

import re
from dataclasses import dataclass, field


def norm(s: str) -> str:
    """Same normalization as tools/play.py and tools/judge.js."""
    s = re.sub(r"<[^>]+>", "", s).lower().replace("’", "").replace("'", "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


@dataclass(frozen=True)
class Rules:
    ignore_words: frozenset[str] = frozenset()
    typo_free_below: int = 0
    one_typo_up_to: int = 0
    long_typos: int = 0      # all zero: exact matching only
    stem_from: int = 0       # words this long or longer are stemmed; 0: no stemming
    terms: frozenset[str] = field(default=frozenset(), repr=False)  # keys of guard terms

    @classmethod
    def from_json(cls, j: dict) -> "Rules":
        ignore = frozenset(j.get("ignore_words", []))
        r = cls(ignore, j.get("typo_free_below", 0), j.get("one_typo_up_to", 0), j.get("long_typos", 0),
                j.get("stem_from", 0))
        return cls(ignore, r.typo_free_below, r.one_typo_up_to, r.long_typos, r.stem_from,
                   frozenset(k for k in (r.key(t) for t in j.get("terms", [])) if k))

    def key(self, s: str) -> str:
        return " ".join(sorted(w for w in norm(s).split() if w not in self.ignore_words))

    def stem_key(self, key: str) -> str:
        return " ".join(sorted(stem(w, self.stem_from) for w in key.split())) if self.stem_from else key

    def allowed(self, n: int) -> int:
        if n < self.typo_free_below or not self.one_typo_up_to:
            return 0
        return 1 if n <= self.one_typo_up_to else self.long_typos


def stem(w: str, min_len: int) -> str:
    """Light suffix stripping, repeated until nothing changes: -ies, -ing, -ed, -es, -s, then
    a doubled final consonant and a final e ("planning" -> "plan", "coding" = "code" -> "cod").
    It only has to map a word and its inflections to the same string, not to a real word."""
    if len(w) < min_len:
        return w
    while True:
        old = w
        if w.endswith("ies") and len(w) > 4:
            w = w[:-3] + "y"
        elif w.endswith("ing") and len(w) >= 6:
            w = w[:-3]
        elif w.endswith(("ed", "es")) and len(w) >= 5:
            w = w[:-2]
        elif w.endswith("s") and not w.endswith(("ss", "us", "is")) and len(w) >= 4:
            w = w[:-1]
        if len(w) >= 4 and w[-1] == w[-2] and w[-1] not in "aeiouls":
            w = w[:-1]
        if len(w) >= 4 and w.endswith("e"):
            w = w[:-1]
        if w == old:
            return w


def distance(a: str, b: str) -> int:
    """Edit distance with adjacent transpositions (optimal string alignment)."""
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def close(g: str, forms: set[str], others: set[str], rules: Rules) -> bool:
    """g is within the typo limit of a form, and no other term is as close."""
    best = None
    for f in forms:
        k = rules.allowed(len(f))
        if k and abs(len(f) - len(g)) <= k:
            d = distance(g, f)
            if d <= k and (best is None or d < best):
                best = d
    if best is None:
        return False
    return not any(abs(len(t) - len(g)) <= best and distance(g, t) <= best for t in others)


def judge(given: str, accept, prompt, rules: Rules, prompted: bool, previous: str = "") -> str:
    """'accept', 'prompt' or 'reject'. A second prompt in a row is a reject. After a
    prompt, `previous` is the prompted answer, also tried joined to the new one."""
    if prompted and previous and judge_one(f"{given} {previous}", accept, prompt, rules, True) == "accept":
        return "accept"
    return judge_one(given, accept, prompt, rules, prompted)


def judge_one(given: str, accept, prompt, rules: Rules, prompted: bool) -> str:
    g = rules.key(given)
    if not g:
        return "reject"
    acc, pr = {rules.key(a) for a in accept}, {rules.key(p) for p in prompt}
    if g in acc:
        return "accept"
    if not prompted and g in pr:
        return "prompt"
    others = rules.terms - acc - pr
    if g not in others:  # a different book term is never an inflection of the answer
        gs = rules.stem_key(g)
        if gs in {rules.stem_key(a) for a in acc}:
            return "accept"
        if not prompted and gs in {rules.stem_key(p) for p in pr}:
            return "prompt"
    if close(g, acc, others, rules):
        return "accept"
    if not prompted and close(g, pr, others, rules):
        return "prompt"
    return "reject"
