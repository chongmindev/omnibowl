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


SHORT_RE = re.compile(r"\(([A-Za-z][A-Za-z0-9-]{1,9}s?)\)")
LONG_PAREN_RE = re.compile(r"\b([A-Z][A-Za-z0-9-]{1,9}s?)\s+\(([^()]{3,80})\)")


def long_form(short: str, words_before: str) -> str | None:
    """Schwartz & Hearst (2003): the shortest span ending right before the parenthesis
    whose words contain the short form's letters in order, the first letter starting a
    word. "voice onset time (VOT)" -> "voice onset time"."""
    short = short[:-1] if short.endswith("s") and short[:-1].isupper() else short
    letters = [ch.lower() for ch in short if ch.isalnum()]
    words = words_before.split()[-min(len(letters) + 5, 2 * len(letters)):]
    text = " ".join(words)
    si, li = len(letters) - 1, len(text) - 1
    while si >= 0:
        c = letters[si]
        while li >= 0 and (text[li].lower() != c or (si == 0 and li > 0 and text[li - 1].isalnum())):
            li -= 1
        if li < 0:
            return None
        si, li = si - 1, li - 1
    start = text.rfind(" ", 0, li + 1) + 1
    long = text[start:].strip(" ,;:")
    return long if len(long.split()) >= 2 and long.lower() != short.lower() else None


def abbreviation_pairs(texts) -> Counter:
    """(short, long) pairs the book defines: "long form (SHORT)" or "SHORT (long form)"."""
    pairs = Counter()
    for t in texts:
        for m in SHORT_RE.finditer(t):
            lf = long_form(m.group(1), t[:m.start()])
            if lf:
                pairs[m.group(1).rstrip("s") if m.group(1)[:-1].isupper() else m.group(1), lf] += 1
        for m in LONG_PAREN_RE.finditer(t):
            short, inner = m.group(1), m.group(2)
            if short.isupper() or short[:-1].isupper():
                lf = long_form(short, inner + " ")
                if lf and inner.strip().lower().startswith(lf.lower().split()[0]):
                    pairs[short.rstrip("s") if short[:-1].isupper() else short, lf] += 1
    return pairs


def merge_abbreviations(found: dict, texts) -> dict:
    """Merge a short form and its long form into one term ("VOT" = "voice onset time"),
    and a plural acronym into its singular ("ERPs" = "ERP"). Only pairs the book
    itself defines are merged. The more mentioned form names the merged term."""
    def key_for(surface: str) -> tuple | None:
        low = surface.lower()
        cands = [k for k in found if k[0] == "concept" and (k[1] == low or k[1] == low.rstrip("s")
                 or k[1] == re.sub(r"s$", "", low))]
        return max(cands, key=lambda k: len(found[k])) if cands else None

    for (short, long), _ in abbreviation_pairs(texts).most_common():
        a, b = key_for(short), key_for(long)
        if not a or not b or a == b:
            continue
        keep, drop = (a, b) if len(found[a]) >= len(found[b]) else (b, a)
        found[keep] = found[keep] + found.pop(drop)
    for k in [k for k in found if k[0] == "concept" and re.fullmatch(r"[a-z0-9]+s", k[1])]:
        single = ("concept", k[1][:-1])
        surfaces = {m[3] for m in found[k]}
        if single in found and any(sf[:-1].isupper() for sf in surfaces):
            found[single] = found[single] + found.pop(k)
    return found


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
            if (j - i == 1 and span[0].pos_ == "PROPN" and j < len(doc)
                    and doc[j].tag_ == "POS"):
                continue  # "Broca" in "Broca's area": only the full phrase is a term
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


CITATION_PAREN_RE = re.compile(r"\([^()]*\b(?:19|20)\d{2}[a-z]?\b[^()]*\)")


def citation_parens(text: str) -> list[tuple[int, int]]:
    """Character ranges of parentheticals that contain a year: citations and source
    notes ("(Saffran et al., 1996)", "(Adapted from ..., 2001, J. Mem. Lang.)"). Their
    words are references, not facts, so concepts and spaCy names there are skipped."""
    return [m.span() for m in CITATION_PAREN_RE.finditer(text)]


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


def capitalized_share(mentions) -> float:
    """Share of mentions written with a capital first letter (surface is the 4th field)."""
    return sum(m[3][:1].isupper() for m in mentions) / len(mentions) if mentions else 0.0


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
        refs = citation_parens(row["text"])
        in_ref = lambda a, b: any(r0 <= a and b <= r1 for r0, r1 in refs)
        for span in concept_spans(doc, ccfg["max_words"], skip_first):
            if not in_ref(span.start_char, span.end_char):
                found["concept", concept_key(span)].append((row["id"], span.start_char, span.end_char, surface(span)))
        taken = set()
        for ent, key in person_spans(doc):
            if in_ref(ent.start_char, ent.end_char):
                continue
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
    found = merge_abbreviations(merged, [r["text"] for r in rows])
    found = drop_subsumed(found, ccfg["subsumed_share"])
    # A name inside an established possessive term ("Broca" in "Broca's area") is not a
    # mention of the person; a one-off phrase ("Tomasello's terminology") still is.
    possessive = defaultdict(list)
    for (kind, key), ms in found.items():
        if kind == "concept" and len(ms) >= ccfg["min_mentions"] and re.search(r"['’]s\b", key):
            for sid, a, b, _ in ms:
                possessive[sid].append((a, b))
    for (kind, key) in [k for k in found if k[0] == "person"]:
        found[kind, key] = [m for m in found[kind, key]
                            if not any(a <= m[1] and m[2] <= b for a, b in possessive[m[0]])]
    # A one-word proper noun that is also a person's surname is that person ("Broca"),
    # but not a common noun the NER once misread as a name ("Memory", "Brain"): the
    # concept must be written capitalized most of the time.
    people = {k for (kind, k) in found if kind == "person"}
    for (kind, key) in [k for k in found if k[0] == "concept" and k[1] in people
                        and capitalized_share(found[k]) >= ccfg["surname_min_capitalized"]]:
        found["person", key] = found["person", key] + found.pop((kind, key))
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
            # One-word surface forms only: a merged long form ("American Sign Language")
            # would add wordfreq's inflated phrase estimate to an acronym.
            surfaces = {f.lower() for f, _ in json.loads(t["forms"]) if " " not in f} | {t["norm"]}
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

def topic_names(text: str) -> list[str]:
    """Lowercased names a heading or label puts forward as topics: "The Eye" -> ["eye"],
    "Sleep and Dreams" -> ["sleep and dreams", "sleep", "dreams"], "Eye, structure of"
    -> ["eye"], "event-related potentials (ERPs)" -> ["event-related potentials", "erps"]."""
    text = re.sub(r"^\s*(?:(?i:module|chapter|unit|section)\s+)?[A-Z]?\d[\d.]*\s*[:.]?\s*", "", text)
    inner = re.findall(r"\(([^)]+)\)", text)
    text = re.sub(r"\([^)]*\)", "", text).split(",")[0]
    out = []
    # "The Role of Sleep": the part before "of" is a generic head; keep what follows.
    parts = re.split(r",|\b(?:and|or|in)\b|&", text) + re.split(r"\bof\b", text)[1:]
    for name in [text, *inner, *[q for p in parts for q in re.split(r"\bof\b", p)[-1:]]]:
        name = re.sub(r"^(?:the|a|an)\s+", "", name.strip().lower()).strip(" :.?")
        if name and name not in out:
            out.append(name)
    return out


def book_topics(conn, book_id: int, c: dict) -> set[str]:
    """Names the book itself treats as topics (decision "broad answers", option B): a
    glossary entry, a subject-index head with at least `broad_min_index_subentries`
    subentries, or a chapter or section heading. Works without any of them: a book with
    no glossary or index still has headings."""
    topics = set()
    for (term,) in conn.execute("SELECT term FROM labels WHERE book_id = ? AND role = 'glossary'", (book_id,)):
        topics.update(topic_names(term))
    for (term,) in conn.execute(
            """SELECT p.term FROM labels p WHERE p.book_id = ? AND p.role = 'subject_index' AND p.parent_id IS NULL
               AND (SELECT COUNT(*) FROM labels k WHERE k.parent_id = p.id) >= ?""",
            (book_id, c["broad_min_index_subentries"])):
        topics.update(topic_names(term))
    # A heading that recurs ("Module Review", "Check Your Understanding") is furniture.
    titles = Counter(" ".join(topic_names(t)[:1]) for (t,) in conn.execute(
        "SELECT title FROM sections WHERE book_id = ? AND kind IN ('chapter', 'section')", (book_id,)))
    for title, n in titles.items():
        if n <= c["broad_max_heading_repeats"]:
            topics.update(topic_names(title))
    # Index letter dividers ("A", "B") and fragments are not topics.
    return {t for t in topics if len(t) >= c["broad_min_name_chars"] and re.search(r"[a-z]", t)}


def classify_terms(book_id: int, conn, cfg: dict) -> None:
    """Class from hand-set lines in config (decision "candidates: class lines", option X):
    too_broad   in at least `too_broad_min_chapters` chapters;
    answer      not too broad, at least `answer_min_sentences` sentences (a tossup needs
                four to seven clues), keyness at or above the answer line for its kind;
    clue_only   keyness at or above the floor for its kind, but not an answer;
    not_term    below the floor (everyday words and incidental phrases).
    broad_answer (decision "broad answers", options B + A, 2026-10-10): a too_broad term
                the book itself treats as a topic (`book_topics`), or, if
                `broad_by_keyness`, one that also clears the answer keyness line. Broad
                answers ("eye", "sleep") draw their clues from many chapters."""
    c = cfg["candidates"]["classes"]
    topics = book_topics(conn, book_id, c)
    rows = conn.execute("SELECT id, keyness, keyness_kind, coverage, n_chapters, norm, forms FROM terms WHERE book_id = ?",
                        (book_id,)).fetchall()
    out = []
    for row in rows:
        t = dict(row)
        t["forms"] = [t["norm"]] + [f for f, _ in json.loads(t["forms"])]
        lr = t["keyness_kind"] == "log_ratio"
        answer_line = c["answer_log_ratio"] if lr else c["answer_local_mi"]
        floor = c["floor_log_ratio"] if lr else c["floor_local_mi"]
        if t["n_chapters"] >= c["too_broad_min_chapters"]:
            cls = "too_broad"
            if t["coverage"] >= c["answer_min_sentences"] and (
                    (c["broad_by_keyness"] and t["keyness"] >= answer_line)
                    or topics & {f.lower() for f in t["forms"]}):
                cls = "broad_answer"
        elif t["coverage"] >= c["answer_min_sentences"] and t["keyness"] >= answer_line:
            cls = "answer"
        elif t["keyness"] >= floor:
            cls = "clue_only"
        else:
            cls = "not_term"
        out.append([cls, t["id"], t["norm"]])
    # Generic-answer filter (decision "generic answers", F3, 2026-10-10): an answer made
    # only of words that are themselves everyday or broad words in this book ("individual
    # words", "language comprehension") is a phrase, not a topic; it stays a clue.
    if c["generic_answer_filter"]:
        broad = {norm for cls, _, norm in out if " " not in norm and cls in ("too_broad", "broad_answer", "not_term")}
        for o in out:
            words = re.findall(r"[a-z0-9]+", o[2])
            if o[0] == "answer" and len(words) > 1 and all(w in broad or w.rstrip("s") in broad for w in words):
                o[0] = "clue_only"
    conn.executemany("UPDATE terms SET class = ? WHERE id = ?", [(cls, tid) for cls, tid, _ in out])
    conn.commit()
