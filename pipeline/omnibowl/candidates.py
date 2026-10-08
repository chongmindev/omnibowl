"""Stage 2: candidate answers.

Step 1: extract candidate terms and every mention of them.
Step 2: keyness. Step 3: coverage. Step 4: class (answer / clue_only / too_broad / not_term).

Extraction (decision "candidates: extraction", option C):
  - Concepts: part-of-speech patterns over spaCy's tags, after Justeson & Katz (1995):
    a run of adjectives and nouns ending in a noun, optionally "noun of noun", with
    hyphens and possessive 's allowed inside. Every pattern match up to
    `max_words` is a candidate, including nested ones ("garden path sentence" and
    "sentence"), and single nouns count (decision 2026-10-07).
  - People: spaCy's PERSON entities, grouped by surname ("Paul Broca" and "Broca"),
    plus surnames from citations ("Saffran et al., 1996", "Aslin & Newport, 2012",
    "Kellman and Spelke (1983)"), which spaCy mostly misses.
Terms are grouped by a normalized key: lowercase, last word lemmatized
("garden path sentences" -> "garden path sentence"), then variants are merged
(decision "candidates: variant grouping", A + B): the same content words in any order
or form ("word order" ~ "order of words"), and synonym heads from config ("color terms"
~ "color words" ~ "color names"). Phrases opening with a determiner-like adjective
("other languages", "same word") are dropped; the phrase without it is kept.
"""

import json
import math
import re
from collections import Counter, defaultdict

import spacy
from wordfreq import word_frequency

from . import db

WORD_RE = re.compile(r"[a-z0-9]+(?:['’][a-z]+)?")

# One letter per token for pattern matching.
#   A adjective (or participle used as one)   N noun / proper noun
#   P "of"   H hyphen joining two words   S possessive 's   - anything else
PATTERN = re.compile(r"(?:[AN][HS]?)*N(?:P(?:[AN][HS]?)*N)?")


def token_class(tok) -> str:
    if tok.pos_ in ("NOUN", "PROPN"):
        return "N"
    if tok.pos_ == "ADJ" or tok.tag_ in ("VBN", "VBG") and tok.dep_ == "amod":
        return "A"
    if tok.lower_ == "of" and tok.pos_ == "ADP":
        return "P"
    if tok.text in ("-", "‐", "–") and not tok.whitespace_ and tok.i > 0 and not tok.nbor(-1).whitespace_:
        return "H"
    if tok.tag_ == "POS":
        return "S"
    return "-"


def surface(span) -> str:
    """Span text with hyphens and possessives joined as written."""
    return span.text.strip()


def concept_key(span) -> str:
    """Lowercase surface with the last word lemmatized."""
    toks = list(span)
    head = toks[-1]
    lemma = head.lemma_.lower() if head.pos_ == "NOUN" else head.lower_
    text = "".join(t.text_with_ws for t in toks[:-1]).lower() + lemma
    return re.sub(r"\s+", " ", text).strip()


def classify(doc) -> str:
    """Token classes, with every word glued into a hyphenated compound ("top-down",
    "tip-of-the-tongue") counted as an adjective unless it is already a noun."""
    cls = [token_class(t) for t in doc]
    for i, t in enumerate(doc):
        if cls[i] in "AN" or not t.is_alpha:
            continue
        if (i > 0 and cls[i - 1] == "H") or (i + 1 < len(doc) and cls[i + 1] == "H"):
            cls[i] = "A"
    return "".join(cls)


def drop_subsumed(found: dict, share: float) -> dict:
    """Drop a concept when at least `share` of its mentions sit inside mentions of a
    longer concept ("turn preference paradigm" inside "head-turn preference paradigm"),
    so a fragment does not compete with the term it belongs to. Single words are kept:
    "paradigm" on its own is judged by keyness instead."""
    spans = defaultdict(list)  # sentence -> [(start, end, key)]
    for (kind, key), ms in found.items():
        if kind == "concept":
            for sid, a, b, _ in ms:
                spans[sid].append((a, b, key))
    out = {}
    for (kind, key), ms in found.items():
        if kind == "concept" and " " in key:
            inside = sum(1 for sid, a, b, _ in ms
                         if any(a2 <= a and b <= b2 and (b2 - a2) > (b - a) for a2, b2, k2 in spans[sid] if k2 != key))
            if inside >= share * len(ms):
                continue
        out[kind, key] = ms
    return out


def group_key(key: str, synonyms: dict[str, str]) -> str:
    """Key shared by variants of one concept: content words in any order, the head
    word replaced by its synonym group ("terms for colors" and "color names" both ->
    "color term"). The head is the word before "of"/"for", else the last word."""
    words = WORD_RE.findall(key)
    if len(words) <= 1:
        return key
    joins = [i for i, w in enumerate(words) if w in ("of", "for")]
    head = joins[0] - 1 if joins and joins[0] > 0 else len(words) - 1
    content = [synonyms.get(w, w) if i == head else w for i, w in enumerate(words) if w not in ("of", "for")]
    return " ".join(sorted(content))


def concept_spans(doc, max_words: int, skip_first: frozenset = frozenset()):
    classes = classify(doc)
    seen = set()
    for i in range(len(doc)):
        if classes[i] not in "AN":
            continue
        for j in range(i + 1, min(len(doc), i + 2 * max_words) + 1):
            if classes[j - 1] != "N":
                continue
            if not PATTERN.fullmatch(classes[i:j]):
                continue
            span = doc[i:j]
            if span[0].lower_ in skip_first and j - i > 1:
                continue
            if any(re.fullmatch(r"[A-Z]\.", t.text) for t in span):
                continue  # initials belong to names and citations ("J. Mem."), not terms
            words = sum(1 for t in span if classes[t.i] in "ANP"
                        and not (t.i > 0 and classes[t.i - 1] == "H"))
            if words > max_words or (i, j) in seen:
                continue
            if not any(c.isalpha() for c in span.text):
                continue
            seen.add((i, j))
            yield span


NAME = r"(?:(?:van|von|de|der|den|di|da|du|le|la)\s+)*[A-Z][\w'’\-]+"
CITATION_RE = re.compile(
    rf"(?P<a>{NAME})(?:\s*(?:,\s*(?P<c>{NAME}))?\s*(?:,\s*)?(?:&|and)\s+(?P<b>{NAME}))?"
    r"(?:\s+et\s+al\.?|\s+and\s+(?:colleagues|coworkers))?"
    r"(?:'s|’s)?,?\s+\(?(?:19|20)\d{2}[a-z]?\b")


def citation_people(text: str, stopwords: set[str]):
    """(start, end, surname) for each author named in a citation."""
    for m in CITATION_RE.finditer(text):
        for g in ("a", "c", "b"):
            name = m.group(g)
            if not name:
                continue
            last = name.split()[-1]
            if last.lower() in stopwords or not any(ch.islower() for ch in last):
                continue
            yield m.start(g), m.end(g), last.lower(), name


def person_spans(doc):
    for ent in doc.ents:
        if ent.label_ != "PERSON":
            continue
        toks = [t for t in ent if t.tag_ != "POS" and t.text not in ("'s", "’s")]
        if not toks or not toks[-1].text[:1].isupper():
            continue
        # Journal abbreviations in source lines ("J. Mem. Lang.") are not people.
        nxt = doc[ent.end] if ent.end < len(doc) else None
        if nxt is not None and nxt.text == "." and len(toks[-1].text) <= 4 and re.match(r"^[A-Z]\.", ent.text):
            continue
        yield ent, toks[-1].text.lower()


def run(book_id: int, conn, cfg: dict, nlp=None) -> None:
    ccfg = cfg["candidates"]
    nlp = nlp or spacy.load(cfg["sentences"]["spacy_model"])
    db.clear(conn, book_id, db.CANDIDATE_TABLES)
    rows = conn.execute(
        f"""SELECT id, section_id, text FROM sentences WHERE book_id = ?
            AND kind IN ({','.join('?' * len(ccfg['sentence_kinds']))}) ORDER BY id""",
        (book_id, *ccfg["sentence_kinds"])).fetchall()

    stop_names = {w.lower() for w in ccfg["citation_name_stopwords"]}
    skip_first = frozenset(w.lower() for w in ccfg["leading_modifiers"])
    synonyms = {w: grp[0] for grp in ccfg["synonym_heads"] for w in grp}
    found = defaultdict(list)          # (kind, key) -> [(sentence_id, start, end, surface)]
    for row, doc in zip(rows, nlp.pipe((r["text"] for r in rows), batch_size=ccfg["spacy_batch_size"])):
        for span in concept_spans(doc, ccfg["max_words"], skip_first):
            found["concept", concept_key(span)].append((row["id"], span.start_char, span.end_char, surface(span)))
        taken = set()
        for ent, key in person_spans(doc):
            found["person", key].append((row["id"], ent.start_char, ent.end_char, ent.text.strip()))
            taken.add(ent.start_char)
        for a, b, key, name in citation_people(row["text"], stop_names):
            if a not in taken:
                found["person", key].append((row["id"], a, b, name))

    section_of = {r["id"]: r["section_id"] for r in rows}
    stop = [p.lower() for p in ccfg["phrase_stoplist"]]
    found = {k: v for k, v in found.items() if not (k[0] == "concept" and any(
        re.search(rf"(?<![a-z]){re.escape(p)}(?![a-z])", k[1]) for p in stop))}
    # Merge variants: the most mentioned key in a group names it.
    groups = defaultdict(list)
    for (kind, key), ms in found.items():
        groups[kind, group_key(key, synonyms) if kind == "concept" else key].append((key, ms))
    merged = {}
    for (kind, _g), members in groups.items():
        members.sort(key=lambda km: -len(km[1]))
        merged[kind, members[0][0]] = [m for _, ms in members for m in ms]
    found = drop_subsumed(merged, ccfg["subsumed_share"])
    for (kind, key), ms in sorted(found.items()):
        if len(ms) < ccfg["min_mentions"]:
            continue
        forms = Counter(m[3] for m in ms)
        # People: prefer the fullest common form ("Paul Broca" over "Broca").
        display = (max(forms, key=lambda f: (len(f.split()) > 1 and forms[f] >= 2, forms[f]))
                   if kind == "person" else forms.most_common(1)[0][0])
        cur = conn.execute(
            """INSERT INTO terms (book_id, kind, norm, display, n_mentions, n_sentences, n_sections, forms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (book_id, kind, key, display, len(ms), len({m[0] for m in ms}),
             len({section_of[m[0]] for m in ms}), json.dumps(forms.most_common(10), ensure_ascii=False)))
        conn.executemany(
            "INSERT INTO mentions (book_id, term_id, sentence_id, start_char, end_char) VALUES (?, ?, ?, ?, ?)",
            [(book_id, cur.lastrowid, s, a, b) for s, a, b, _ in ms])
    conn.commit()
    keyness(book_id, conn, cfg)
    coverage(book_id, conn)
    classify_terms(book_id, conn, cfg)


# --------------------------------------------------------------------------- keyness

def keyness(book_id: int, conn, cfg: dict) -> None:
    """Keyness (decision "candidates: keyness", option A with phrase option 3).

    One-word terms and people: smoothed log ratio of per-million frequency in the book
    against everyday English (wordfreq), log2((book + k) / (english + k)).
    Multi-word terms: wordfreq has no phrase data, so they are scored inside the book by
    local mutual information (Evert 2005): mentions x PMI, where PMI = log2(P(phrase) /
    product of P(word)) says how much more often the words occur together than their
    separate frequencies predict. Plain PMI rewards phrases seen twice; weighting by
    mentions fixes that (eval/candidate_ranking.py).
    The two scores are on different scales; `keyness_kind` says which one a term has.
    """
    ccfg = cfg["candidates"]
    texts = [r[0].lower() for r in conn.execute(
        f"""SELECT text FROM sentences WHERE book_id = ?
            AND kind IN ({','.join('?' * len(ccfg['sentence_kinds']))})""",
        (book_id, *ccfg["sentence_kinds"]))]
    counts = Counter(w for t in texts for w in WORD_RE.findall(t))
    total = sum(counts.values())
    k = ccfg["log_ratio_smoothing"]
    updates = []
    for t in conn.execute("SELECT id, kind, norm, n_mentions, forms FROM terms WHERE book_id = ?", (book_id,)):
        words = WORD_RE.findall(t["norm"])
        if t["kind"] == "person" or len(words) <= 1:
            surfaces = {f.lower() for f, _ in json.loads(t["forms"])} | {t["norm"]}
            if t["kind"] == "person":
                surfaces = {t["norm"]}
            english = sum(word_frequency(f, "en") for f in surfaces) * 1e6
            book = t["n_mentions"] / total * 1e6
            updates.append((math.log2((book + k) / (english + k)), "log_ratio", t["id"]))
        else:
            p_phrase = t["n_mentions"] / total
            p_words = math.prod(max(counts[w], 1) / total for w in words)
            pmi = math.log2(p_phrase / p_words)
            updates.append((t["n_mentions"] * pmi, "local_mi", t["id"]))
    conn.executemany("UPDATE terms SET keyness = ?, keyness_kind = ? WHERE id = ?", updates)
    conn.commit()


# -------------------------------------------------------------------------- coverage

def coverage(book_id: int, conn) -> None:
    """Coverage (decision "candidates: coverage", option B): how much the book says about
    a term, as distinct sentences (`coverage`), plus how widely it is spread: distinct
    chapters (`n_chapters`) and that count as a share of all chapters (`spread`). A term
    in every chapter ("language") is too broad to be an answer; one in two chapters
    ("N400") is specific."""
    secs = conn.execute("SELECT id, parent_id, kind FROM sections WHERE book_id = ?", (book_id,)).fetchall()
    parent = {s["id"]: s["parent_id"] for s in secs}

    def top(sid):
        while sid is not None and parent.get(sid) is not None:
            sid = parent[sid]
        return sid

    n_chapters = sum(1 for s in secs if s["kind"] == "chapter")
    chap_of = {r["id"]: top(r["section_id"]) for r in conn.execute(
        "SELECT id, section_id FROM sentences WHERE book_id = ?", (book_id,))}
    per_term = defaultdict(set)
    for term_id, sentence_id in conn.execute(
            "SELECT term_id, sentence_id FROM mentions WHERE book_id = ?", (book_id,)):
        per_term[term_id].add(chap_of[sentence_id])
    conn.executemany(
        "UPDATE terms SET coverage = n_sentences, n_chapters = ?, spread = ? WHERE id = ?",
        [(len(ch), len(ch) / n_chapters, tid) for tid, ch in per_term.items()])
    conn.commit()


# ----------------------------------------------------------------------------- class

def classify_terms(book_id: int, conn, cfg: dict) -> None:
    """Class from hand-set lines in config (decision "candidates: class lines", option X):
    too_broad   in at least `too_broad_min_chapters` chapters;
    answer      not too broad, at least `answer_min_sentences` sentences (a tossup needs
                four to seven clues), keyness at or above the answer line for its kind;
    clue_only   keyness at or above the floor for its kind, but not an answer;
    not_term    below the floor (everyday words and incidental phrases)."""
    c = cfg["candidates"]["classes"]
    rows = conn.execute("SELECT id, keyness, keyness_kind, coverage, n_chapters FROM terms WHERE book_id = ?",
                        (book_id,)).fetchall()
    out = []
    for t in rows:
        lr = t["keyness_kind"] == "log_ratio"
        answer_line = c["answer_log_ratio"] if lr else c["answer_local_mi"]
        floor = c["floor_log_ratio"] if lr else c["floor_local_mi"]
        if t["n_chapters"] >= c["too_broad_min_chapters"]:
            cls = "too_broad"
        elif t["coverage"] >= c["answer_min_sentences"] and t["keyness"] >= answer_line:
            cls = "answer"
        elif t["keyness"] >= floor:
            cls = "clue_only"
        else:
            cls = "not_term"
        out.append((cls, t["id"]))
    conn.executemany("UPDATE terms SET class = ? WHERE id = ?", out)
    conn.commit()
