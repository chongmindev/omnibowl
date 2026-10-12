"""Stage 4: rank. For each answer, the referent ("this ERP") and the giveaway; for each
clue, a difficulty from 0 (easy) to 1 (hard).

Difficulty (decision "clue difficulty: A + B2", 2026-10-12; difficulty.py) is the weighted
mean of placement (below), repetition, detail and QBReader familiarity. It replaced
(decision "rank: difficulty", option C = A + B, rough first version):
  - emphasis (A): how the textbook presents the fact. Definitions and summaries are
    easy; body text where the term is introduced in bold is easy; ordinary body text
    is middling; boxes and captions are harder; clues found only through coreference
    and clues outside the answer's main chapter are harder still. Values in config.
  - specificity (B): how rare the clue's most distinctive words are in the book
    (inverse sentence frequency of its top words), so names, numbers and study details
    read as harder than general statements.
  difficulty = w_emphasis * emphasis + w_specificity * specificity.
Referent (decision "rank: referent", A then B then C):
  A. the book's own words: the head noun of the answer's definition ("N400: An ERP in
     which ..." -> "this ERP"), or for a person the title the book puts before the
     name ("neurologist Carl Wernicke" -> "this neurologist");
  B. the answer's own head word ("garden path sentences" -> "these sentences");
  C. a generic word by kind ("this term", "this researcher").
Giveaway: "For 10 points, name this <definition>" when the answer has a definition.
"""

import json
import math
import re
from collections import Counter, defaultdict

import spacy

from . import db, difficulty, uniqueness

WORD = re.compile(r"[A-Za-z][A-Za-z'’-]+|\d+")


def chapter_of(conn, book_id):
    secs = conn.execute("SELECT id, parent_id FROM sections WHERE book_id = ?", (book_id,)).fetchall()
    parent = {s["id"]: s["parent_id"] for s in secs}

    def top(sid):
        while sid is not None and parent.get(sid) is not None:
            sid = parent[sid]
        return sid
    return top


def definition_of(conn, term) -> str | None:
    """The answer's own definition text (after the term), from a definition sentence
    that starts with the term."""
    for (text,) in conn.execute(
            """SELECT s.text FROM clues c JOIN sentences s ON s.id = json_extract(c.sentence_ids, '$[0]')
               WHERE c.term_id = ? AND s.kind = 'definition' AND s.ord = 0 ORDER BY s.id""", (term["id"],)):
        for form in sorted({term["display"], *[f for f in _forms(term)]}, key=len, reverse=True):
            if text.lower().startswith(form.lower()):
                rest = re.sub(r"^\s*\([^)]*\)", "", text[len(form):]).strip(" :,")
                if len(rest.split()) >= 3:
                    return rest
    return None


def _forms(term) -> list[str]:
    import json
    return [f for f, _ in json.loads(term["forms"])]


def referent_from_definition(defn: str, nlp) -> tuple[str, str] | None:
    """('ERP', 'in which a waveform ...'): the head noun of the definition's first noun
    phrase, and the rest of the definition after that phrase."""
    doc = nlp(defn)
    chunks = list(doc.noun_chunks)
    if not chunks or chunks[0].start > 1:
        return None
    head = chunks[0].root
    if head.pos_ not in ("NOUN", "PROPN"):
        return None
    rest = doc[head.i + 1:].text.strip()
    mods = [t.text for t in chunks[0] if t.i < head.i and t.dep_ in ("amod", "compound") and t.pos_ != "DET"]
    noun = " ".join(mods[-1:] + [head.text]) if mods and len(mods[-1]) < 14 else head.text
    return noun, rest


def person_title(conn, book_id, term, nlp) -> str | None:
    """The noun the book puts right before a person's name, checked with the parser:
    "linguist Charles Hockett", "neurologist Carl Wernicke" (not "after Paul Broca")."""
    surname = term["norm"].split()[-1]
    titles = Counter()
    texts = [r[0] for r in conn.execute(
        """SELECT DISTINCT s.text FROM mentions m JOIN sentences s ON s.id = m.sentence_id
           WHERE m.term_id = ? LIMIT 80""", (term["id"],))]
    for doc in nlp.pipe(texts):
        for t in doc:
            if t.lower_ != surname or t.pos_ != "PROPN":
                continue
            i = t.i
            while i > 0 and doc[i - 1].pos_ == "PROPN":  # first token of the full name
                i -= 1
            # Only before a full name ("linguist Charles Hockett"), so a noun that merely
            # precedes the surname ("region Broca") does not count.
            if (t.i - i >= 1 and i > 0 and doc[i - 1].pos_ == "NOUN" and doc[i - 1].is_lower
                    and doc[i - 1].is_alpha):
                titles[doc[i - 1].lower_] += 1
    return titles.most_common(1)[0][0] if titles else None


def concept_category(conn, term, nlp, generic: set[str]) -> str | None:
    """The kind of thing the book calls the answer: "X is a gene", "the FOXP2 gene"."""
    forms = sorted({term["display"], *_forms(term)}, key=len, reverse=True)
    cats = Counter()
    texts = [r[0] for r in conn.execute(
        """SELECT DISTINCT s.text FROM mentions m JOIN sentences s ON s.id = m.sentence_id
           WHERE m.term_id = ? LIMIT 80""", (term["id"],))]
    answer_words = {w.lower() for f in forms for w in WORD.findall(f)}
    for text in texts:
        for f in forms:
            m = re.search(rf"\b{re.escape(f)}\s+(?:is|was|are|refers to)\s+(?:a|an|the)\s+((?:[a-z-]+\s+){{0,2}}?([a-z-]+))\b(?:\s+(?:that|which|in|of|for|where|whose|used)\b|[,.;])",
                          text, re.I)
            if m:
                cats[m.group(2).lower()] += 2
            m = re.search(rf"\bthe\s+{re.escape(f)}\s+([a-z]+)\b", text)
            if m:
                cats[m.group(1).lower()] += 1
    for word, _ in cats.most_common():
        if word not in answer_words and word not in generic and len(word) > 2:
            doc = nlp(word)
            if doc and doc[0].pos_ == "NOUN":
                return word
    return None


def plural(term) -> bool:
    disp = term["display"]
    return disp.endswith("s") and not disp.isupper() and term["norm"] != disp.lower()


def run(book_id: int, conn, cfg: dict, nlp=None) -> None:
    r = cfg["rank"]
    nlp = nlp or spacy.load(cfg["sentences"]["spacy_model"], exclude=["ner", "lemmatizer"])
    db.clear(conn, book_id, db.RANK_TABLES)
    top = chapter_of(conn, book_id)

    # Sentence frequency of words, for specificity.
    sents = conn.execute("SELECT id, text FROM sentences WHERE book_id = ?", (book_id,)).fetchall()
    df = Counter()
    for s in sents:
        df.update({w.lower() for w in WORD.findall(s["text"])})
    n = len(sents)
    max_idf = math.log(n)
    stop = set(r["specificity_stopwords"])

    classes = cfg["clues"]["term_classes"]
    terms = conn.execute(f"SELECT * FROM terms WHERE book_id = ? AND class IN ({','.join('?' * len(classes))})",
                         (book_id, *classes)).fetchall()
    for t in terms:
        clues = conn.execute(
            """SELECT c.*, s.text AS stext FROM clues c JOIN sentences s ON s.id = json_extract(c.sentence_ids, '$[0]')
               WHERE c.term_id = ?""", (t["id"],)).fetchall()
        if not clues:
            continue
        chapters = Counter(top(c["section_id"]) for c in clues)
        main_ch = chapters.most_common(1)[0][0]
        answer_words = {w.lower() for f in _forms(t) for w in WORD.findall(f)}

        # Referent and giveaway.
        defn = definition_of(conn, t)
        ref, rest, source = None, None, None
        if t["kind"] == "person":
            title = person_title(conn, book_id, t, nlp)
            ref, source = (title, "book") if title else (r["generic_person"], "generic")
        elif (cat := concept_category(conn, t, nlp, set(r["generic_categories"]))):
            ref, source = cat, "book"
            if defn and (got := referent_from_definition(defn, nlp)):
                rest = got[1]
        elif (defn and (got := referent_from_definition(defn, nlp))
              and got[0].lower().split()[-1] not in set(r["generic_categories"])):
            ref, rest, source = got[0].lower() if not got[0].isupper() else got[0], got[1], "definition"
        elif len(t["norm"].split()) > 1:
            ref, source = t["norm"].split()[-1], "head"
        else:
            ref, source = r["generic_concept"], "generic"
        many = plural(t) and source in ("head", "definition") and ref.endswith("s")
        conn.execute(
            """INSERT INTO answer_meta (book_id, term_id, referent, plural, referent_source, giveaway,
               main_chapter) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (book_id, t["id"], ref, int(many), source, rest, main_ch))

        # Clue difficulty.
        texts = [c["stext"][c["start_char"]:c["end_char"]] for c in clues]
        first_sid = [json.loads(c["sentence_ids"])[0] for c in clues]
        cw = [difficulty.words(x, stop, answer_words, r["repetition_word_prefix"]) for x in texts]
        reps = difficulty.repetition(cw, first_sid, r["repetition_min_shared"])
        people, phrases = defaultdict(set), defaultdict(list)
        for cid, mid, kind, n_tossups, mean_pos in conn.execute(
                """SELECT c.id, m.term_id, mt.kind, q.n_tossups, q.mean_pos FROM clues c
                   JOIN mentions m ON m.sentence_id = json_extract(c.sentence_ids, '$[0]')
                        AND m.start_char >= c.start_char AND m.end_char <= c.end_char AND m.term_id != c.term_id
                   JOIN terms mt ON mt.id = m.term_id
                   LEFT JOIN qb_phrases q ON q.term_id = m.term_id
                   WHERE c.term_id = ?""", (t["id"],)):
            if kind == "person":
                people[cid].add(mid)
            if n_tossups is not None:
                phrases[cid].append((n_tossups, mean_pos))
        rows = []
        for c, text, rep in zip(clues, texts, reps):
            emph = r["emphasis"].get(c["kind"], r["emphasis"]["body"])
            if c["kind"] == "body" and _bold_intro(conn, c, t):
                emph = r["emphasis"]["bold_intro"]
            if c["via"] == "coref":
                emph += r["coref_penalty"]
            if top(c["section_id"]) != main_ch:
                emph += r["off_chapter_penalty"]
            emph = min(1.0, emph)
            idfs = sorted((math.log(n / df[w.lower()]) for w in WORD.findall(text)
                           if w.lower() not in stop and w.lower() not in answer_words and df[w.lower()]),
                          reverse=True)[:r["specificity_top_words"]]
            spec = (sum(idfs) / len(idfs) / max_idf) if idfs else 0.0
            det = difficulty.detail(text, len(people[c["id"]]), r)
            fam = difficulty.familiarity(phrases[c["id"]], r)
            diff = difficulty.combine({"emphasis": emph, "specificity": spec, "repetition": rep,
                                       "detail": det, "familiarity": fam}, r)
            rows.append((book_id, c["id"], round(diff, 4), round(emph, 4), round(spec, 4), round(rep, 4),
                         round(det, 4), None if fam is None else round(fam, 4)))
        conn.executemany(
            """INSERT INTO clue_scores (book_id, clue_id, difficulty, emphasis, specificity, repetition, detail,
               familiarity) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", rows)
    conn.commit()
    uniqueness.run(book_id, conn, cfg)


def _bold_intro(conn, clue, term) -> bool:
    """The clue's sentence has the term in bold (the book introducing it)."""
    import json
    sid = json.loads(clue["sentence_ids"])[0]
    lids = json.loads(conn.execute("SELECT line_ids FROM sentences WHERE id = ?", (sid,)).fetchone()[0])
    forms = [f.lower() for f in _forms(term)]
    for lid in lids:
        for text, _size, flags in json.loads(conn.execute("SELECT spans_json FROM lines WHERE id = ?", (lid,)).fetchone()[0]):
            if flags & 16 and any(f in text.lower() for f in forms):
                return True
    return False
