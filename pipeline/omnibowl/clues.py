"""Stage 3: clues. Every fact the book states about each answer, one fact per clue,
each pointing to the sentence it came from.

Sentences about an answer (decision "clues: coreference", 2026-10-08):
  - named: the sentence contains a mention of the answer (from `candidates`);
  - coref: the sentence contains a mention from a coreference chain (`coref`) that is
    linked to the answer. A chain is linked only through a named mention of that exact
    answer: a chain mention equal to the answer, allowing a leading "the"/"this"/"a".
    So "Broca's" (in "Broca's area") never links a chain to Broca the man, and "this
    distinction" (between N400 and P600) never links to the N400.
  - shared: the sentence also names another answer, so the clue is weaker for
    uniqueness; `rank` decides how much weaker.
One fact per clue (decision "clues: atomicity", option B with A as fallback): split a
sentence at "; ", at ", and"/", but" joining two clauses that each have their own
subject; keep the parts that mention the answer. (", which" clauses are not split off:
the part would lose what it describes, "Which provided phonological priming...".) A part without its own subject is not split off (fallback: the whole sentence).
Citation parentheticals are removed from clue text.
"""

import json
import re
from collections import defaultdict

import spacy

from . import db
from .candidates import citation_parens

LEADING = re.compile(r"^(?:the|this|that|these|those|a|an)\s+", re.I)


def answer_terms(conn, book_id: int, classes: list[str]):
    return conn.execute(
        f"SELECT id, kind, norm, display FROM terms WHERE book_id = ? AND class IN ({','.join('?' * len(classes))})",
        (book_id, *classes)).fetchall()


def linked_chains(conn, book_id: int, term_id: int, mentions) -> set[tuple[int, int]]:
    """(unit, chain) pairs linked to the term through a named mention."""
    out = set()
    for m in mentions:
        for c in conn.execute(
                "SELECT unit, chain, start_char, end_char, text FROM coref WHERE sentence_id = ?",
                (m["sentence_id"],)):
            if c["start_char"] <= m["start_char"] and m["end_char"] <= c["end_char"]:
                extra = (c["text"][:m["start_char"] - c["start_char"]] +
                         c["text"][m["end_char"] - c["start_char"]:]).strip()
                if extra == "" or LEADING.fullmatch(extra + " "):
                    out.add((c["unit"], c["chain"]))
    return out


def clause_parts(doc) -> list[tuple[int, int]]:
    """Character ranges of the clauses a sentence splits into (option B), or the whole
    sentence when no split is clean (option A)."""
    cuts = []
    for t in doc:
        # "; " between clauses
        if t.text == ";" and 0 < t.i < len(doc) - 1:
            cuts.append((t.idx, t.idx + 1))
        # ", and/but" + a clause with its own subject, attached to the root
        if (t.dep_ == "conj" and t.pos_ in ("VERB", "AUX") and t.head.dep_ == "ROOT"
                and any(ch.dep_ in ("nsubj", "nsubjpass") for ch in t.children)):
            cc = [ch for ch in t.children if ch.dep_ == "cc"] or \
                 [ch for ch in t.head.children if ch.dep_ == "cc" and ch.i < t.i]
            if cc:
                k = cc[-1]
                start = doc[k.i - 1].idx if k.i > 0 and doc[k.i - 1].text == "," else k.idx
                cuts.append((start, k.idx + len(k.text)))
    if not cuts:
        return [(0, len(doc.text))]
    parts, pos = [], 0
    for a, b in sorted(cuts):
        if a > pos:
            parts.append((pos, a))
        pos = b
    parts.append((pos, len(doc.text)))
    return [(a, b) for a, b in parts if doc.text[a:b].strip()]


def clean(text: str) -> str:
    for a, b in reversed(citation_parens(text)):
        text = text[:a] + text[b:]
    text = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s{2,}", " ", text)).strip(" ,;")
    return text[:1].upper() + text[1:] if text else text


def run(book_id: int, conn, cfg: dict, nlp=None) -> None:
    c = cfg["clues"]
    nlp = nlp or spacy.load(cfg["sentences"]["spacy_model"], exclude=["ner", "lemmatizer"])
    db.clear(conn, book_id, db.CLUE_TABLES)
    kinds = set(c["sentence_kinds"])
    sent = {r["id"]: r for r in conn.execute(
        "SELECT id, kind, section_id, text FROM sentences WHERE book_id = ?", (book_id,))}
    terms = answer_terms(conn, book_id, c["term_classes"])
    term_ids = {t["id"] for t in terms}
    named_by_sentence = defaultdict(set)
    for r in conn.execute("SELECT term_id, sentence_id FROM mentions WHERE book_id = ?", (book_id,)):
        if r["term_id"] in term_ids:
            named_by_sentence[r["sentence_id"]].add(r["term_id"])
    chain_mentions = defaultdict(list)
    for r in conn.execute("SELECT unit, chain, sentence_id, start_char, end_char FROM coref WHERE book_id = ?",
                          (book_id,)):
        chain_mentions[r["unit"], r["chain"]].append(r)

    parse_cache: dict[int, object] = {}
    rows = []
    for t in terms:
        mentions = conn.execute(
            "SELECT sentence_id, start_char, end_char FROM mentions WHERE term_id = ?", (t["id"],)).fetchall()
        spots = defaultdict(list)  # sentence -> [(start, end, via)]
        for m in mentions:
            spots[m["sentence_id"]].append((m["start_char"], m["end_char"], "named"))
        for key in linked_chains(conn, book_id, t["id"], mentions):
            for cm in chain_mentions[key]:
                if not any(a <= cm["start_char"] < b for a, b, _ in spots[cm["sentence_id"]]):
                    spots[cm["sentence_id"]].append((cm["start_char"], cm["end_char"], "coref"))
        for sid, marks in sorted(spots.items()):
            s = sent.get(sid)
            if s is None or s["kind"] not in kinds:
                continue
            doc = parse_cache.get(sid) or parse_cache.setdefault(sid, nlp(s["text"]))
            shared = len(named_by_sentence[sid] - {t["id"]}) > 0
            for a, b in clause_parts(doc):
                inside = [m for m in marks if a <= m[0] < b]
                if not inside:
                    continue
                text = clean(s["text"][a:b])
                if len(text.split()) < c["min_words"]:
                    continue
                via = "named" if any(m[2] == "named" for m in inside) else "coref"
                rows.append((book_id, t["id"], json.dumps([sid]), s["section_id"], s["kind"], via,
                             int(shared), a, b, text))
    conn.executemany(
        """INSERT INTO clues (book_id, term_id, sentence_ids, section_id, kind, via, shared,
           start_char, end_char, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
    conn.commit()
