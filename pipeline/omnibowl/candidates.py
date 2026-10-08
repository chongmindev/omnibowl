"""Stage 2: candidate answers.

Step 1: extract candidate terms and every mention of them.
Step 2: keyness. Coverage and the answer / clue_only / too_broad class come next.

Extraction (decision "candidates: extraction", option C):
  - Concepts: part-of-speech patterns over spaCy's tags, after Justeson & Katz (1995):
    a run of adjectives and nouns ending in a noun, optionally "noun of noun", with
    hyphens and possessive 's allowed inside. Every pattern match up to
    `max_words` is a candidate, including nested ones ("garden path sentence" and
    "sentence"), and single nouns count (decision 2026-10-07).
  - People: spaCy's PERSON entities, grouped by surname ("Paul Broca" and "Broca").
Terms are grouped by a normalized key: lowercase, last word lemmatized
("garden path sentences" -> "garden path sentence").
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


def concept_spans(doc, max_words: int):
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
            words = sum(1 for t in span if classes[t.i] in "ANP"
                        and not (t.i > 0 and classes[t.i - 1] == "H"))
            if words > max_words or (i, j) in seen:
                continue
            if not any(c.isalpha() for c in span.text):
                continue
            seen.add((i, j))
            yield span


def person_spans(doc):
    for ent in doc.ents:
        if ent.label_ != "PERSON":
            continue
        toks = [t for t in ent if t.tag_ != "POS" and t.text not in ("'s", "’s")]
        if not toks or not toks[-1].text[:1].isupper():
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

    found = defaultdict(list)          # (kind, key) -> [(sentence_id, start, end, surface)]
    for row, doc in zip(rows, nlp.pipe((r["text"] for r in rows), batch_size=ccfg["spacy_batch_size"])):
        for span in concept_spans(doc, ccfg["max_words"]):
            found["concept", concept_key(span)].append((row["id"], span.start_char, span.end_char, surface(span)))
        for ent, key in person_spans(doc):
            found["person", key].append((row["id"], ent.start_char, ent.end_char, ent.text.strip()))

    section_of = {r["id"]: r["section_id"] for r in rows}
    stop = [p.lower() for p in ccfg["phrase_stoplist"]]
    for (kind, key), ms in sorted(found.items()):
        if len(ms) < ccfg["min_mentions"]:
            continue
        if kind == "concept" and any(re.search(rf"(?<![a-z]){re.escape(p)}(?![a-z])", key) for p in stop):
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
