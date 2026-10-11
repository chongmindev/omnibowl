"""Clue uniqueness: could a player guess a different answer from this clue?
(decision "clue uniqueness", option C, 2026-10-10.)

A retrieval test. Each answer is a document: the sentences the book says about it (its
clue sentences). Each clue, with the answer's own words removed, is a query, and every
answer is ranked against it, by query likelihood with Dirichlet smoothing (`model =
"lm"`, the default) or BM25 (`"bm25"`). BM25 favors the very long documents of broad
answers ("language", "words"), which contain every query word; query likelihood scores
the share of a document's words, so length stops winning by itself. The clue's own sentence is left out of every
document while it is the query, so a clue cannot find its answer just by matching
itself. A clue is unique when its answer ranks first; `margin` says by how much (above 0:
its answer wins; below 0: another answer fits the clue better). For "lm" it is the
log-likelihood gap per query word; for "bm25", (true - best other) / true.

Writes `clue_uniqueness` (rank, margin) for every clue of every answer. Runs at the end
of `rank`. What it cannot see: facts the book never states about the other answer
(a clue can look unique in this book and still fit an answer from outside it).
"""

import json
import math
import re
from collections import Counter, defaultdict

from spacy.lang.en.stop_words import STOP_WORDS

from .candidates import citation_parens

WORD = re.compile(r"[a-z][a-z'’-]*[a-z]|\d+")


def stem(w: str) -> str:
    w = re.sub(r"['’]s$", "", w)
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def tokens(text: str, min_chars: int) -> list[str]:
    return [stem(w) for w in WORD.findall(text.lower()) if w not in STOP_WORDS and len(w) >= min_chars]


def run(book_id: int, conn, cfg: dict) -> None:
    u = cfg["uniqueness"]
    classes = cfg["clues"]["term_classes"]
    conn.execute("DELETE FROM clue_uniqueness WHERE book_id = ?", (book_id,))
    terms = conn.execute(
        f"SELECT id, norm, forms FROM terms WHERE book_id = ? AND class IN ({','.join('?' * len(classes))})",
        (book_id, *classes)).fetchall()
    own = {t["id"]: {stem(w) for f in [t["norm"]] + [f for f, _ in json.loads(t["forms"])]
                     for w in WORD.findall(f.lower())} for t in terms}

    # Documents: per answer, token counts of each of its distinct clue sentences.
    sent_tokens: dict[int, Counter] = {}
    doc_sents = defaultdict(set)
    clues = conn.execute(
        """SELECT c.id, c.term_id, c.text, json_extract(c.sentence_ids, '$[0]') AS sid, s.text AS stext
           FROM clues c JOIN sentences s ON s.id = json_extract(c.sentence_ids, '$[0]')
           WHERE c.book_id = ?""", (book_id,)).fetchall()
    for c in clues:
        if c["term_id"] not in own:
            continue
        doc_sents[c["term_id"]].add(c["sid"])
        if c["sid"] not in sent_tokens:
            sent_tokens[c["sid"]] = Counter(tokens(c["stext"], u["min_chars"]))
    tf = {d: sum((sent_tokens[s] for s in ss), Counter()) for d, ss in doc_sents.items()}
    dlen = {d: sum(v.values()) for d, v in tf.items()}
    n_docs = len(tf)
    avg = sum(dlen.values()) / n_docs if n_docs else 1.0
    postings = defaultdict(list)
    for d, v in tf.items():
        for w in v:
            postings[w].append(d)
    idf = {w: math.log(1 + (n_docs - len(ds) + 0.5) / (len(ds) + 0.5)) for w, ds in postings.items()}
    in_docs = defaultdict(set)
    for d, ss in doc_sents.items():
        for s in ss:
            in_docs[s].add(d)
    k1, b = u["bm25_k1"], u["bm25_b"]
    mu = u["lm_mu"]
    total = sum(dlen.values()) or 1
    coll = Counter()
    for v in tf.values():
        coll.update(v)
    coll = {w: n / total for w, n in coll.items()}

    rows = []
    for c in clues:
        d_true = c["term_id"]
        if d_true not in own:
            continue
        text = c["text"]
        for a, z in reversed(citation_parens(text)):
            text = text[:a] + text[z:]
        query = set(tokens(text, u["min_chars"])) - own[d_true]
        held = sent_tokens[c["sid"]]
        scores = Counter()
        held_len = sum(held.values())
        if u["model"] == "bm25":
            for w in query:
                for d in postings.get(w, ()):
                    f, length = tf[d][w], dlen[d]
                    if d in in_docs[c["sid"]] and (u["leave_out"] == "all" or d != d_true):  # leave the sentence out
                        f, length = f - held[w], length - held_len
                    if f > 0:
                        scores[d] += idf[w] * f * (k1 + 1) / (f + k1 * (1 - b + b * length / avg))
            s_true = scores.get(d_true, 0.0)
            others = [v for d, v in scores.items() if d != d_true]
            rank = 1 + sum(v > s_true for v in others)
            best_other = max(others, default=0.0)
            margin = (s_true - best_other) / s_true if s_true > 0 else -1.0
        else:
            # Query likelihood, Dirichlet smoothing: log P(query | answer) = sum over query
            # words of log((tf + mu p(w|book)) / (len + mu)); written as a match part
            # (only documents containing a query word) plus a length part every document
            # has, so documents without any match still compete through their length.
            q = [w for w in query if w in coll]
            here = in_docs[c["sid"]] if u["leave_out"] == "all" else in_docs[c["sid"]] - {d_true}
            lengths = {d: dlen[d] - (held_len if d in here else 0) for d in tf}
            touched = {d for w in q for d in postings[w]}
            for d in touched:
                sc = 0.0
                for w in q:
                    f = tf[d].get(w, 0) - (held[w] if d in here else 0)
                    if f > 0:
                        sc += math.log(1 + f / (mu * coll[w]))
                scores[d] = sc
            full = {d: scores.get(d, 0.0) + len(q) * math.log(mu / (lengths[d] + mu)) for d in tf}
            s_true = full.get(d_true, float("-inf"))
            others = [v for d, v in full.items() if d != d_true]
            rank = 1 + sum(v > s_true for v in others)
            # Margin: log-likelihood gap to the best other answer, per query word.
            margin = (s_true - max(others)) / len(q) if q and others else -1.0
        rows.append((book_id, c["id"], rank, round(margin, 4), len(query)))
    conn.executemany(
        "INSERT INTO clue_uniqueness (book_id, clue_id, rank, margin, query_words) VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()
