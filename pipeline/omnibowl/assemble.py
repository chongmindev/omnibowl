"""Stage 5: assemble tossups (rough first version, decision 2026-10-09).

For each answer with a referent:
  1. Take its clues, skip the definition (it becomes the giveaway) and meta-text that
     addresses the reader or points at the book ("as you saw", "see Table 3.3").
  2. In each clue, replace the first mention of the answer with the referent ("this
     ERP"), and later named mentions too; pronouns after that stay.
  3. Drop a clue that still leaks the answer: any word sharing a stem with a
     distinctive answer word ("Broca" -> "Brocan").
  4. Pick clues hardest first, one per sentence, at most `max_per_section` per
     section, until the length budget is reached; the order stays hard to easy, so
     difficulty never rises.
  5. End with "For 10 points, name this <referent> <definition>." when the answer has
     a definition, else "For 10 points, name this <referent>." after the easiest clue.
  6. Keep the tossup only if it has `min_sentences` to `max_sentences` sentences.
The answerline is the display name plus merged forms as accepted alternates. Tossups
are stored in the QBReader shape (`tossups`), source = 'generated'.
"""

import json
import re
from collections import Counter

import spacy

from . import db
from .clues import clean

PRONOUN_SUBJ = re.compile(r"^(He|She|They|It|His|Her|Their|Its)\b")
EVENT_ANAPHOR = re.compile(r"^(This|That|These|Those)\s+(?:is|was|are|were|would|will|can|could|may|might|has|have|had|led|leads|means|meant|suggests|shows|showed|makes|made)\b")

WORD = re.compile(r"[A-Za-z][A-Za-z'’-]+")
PRONOUN = re.compile(r"^(he|she|it|they|this|these|that|those)$", re.I)


def phrase(ref: str, many: bool, cap: bool) -> str:
    det = "these" if many else "this"
    p = f"{det} {ref}"
    return p[0].upper() + p[1:] if cap else p


def mask(text: str, spans: list[tuple[int, int, str]], ref: str, many: bool) -> str:
    """Replace mentions (offsets inside `text`) with the referent: the first mention
    always, later ones when they are named. A leading "the"/"The" goes with it."""
    out, pos, first = [], 0, True
    for a, b, via in sorted(spans):
        if a < pos:
            continue
        if not first and via != "named":
            continue
        lead = re.search(rf"(?:^|\s)((?:(?:the|The|a|an|An|A)\s+)?(?:[\w-]+\s+)?{re.escape(ref)}|the|The|a|an|An|A)\s+$",
                         text[pos:a])
        a2 = a - len(lead.group(0).lstrip()) if lead else a
        cap = a2 == 0 or text[:a2].rstrip().endswith((".", "!", "?"))
        out.append(text[pos:a2])
        out.append(phrase(ref, many, cap))
        pos, first = b, False
    out.append(text[pos:])
    return "".join(out)


def leaks(text: str, stems: set[str]) -> bool:
    return any(w.lower()[:len(s)] == s for w in WORD.findall(text) for s in stems)


def answer_stems(forms: list[str], ref: str, cfg: dict) -> set[str]:
    stop = set(cfg["specificity_stopwords"]) if "specificity_stopwords" in cfg else set()
    ref_words = {w.lower() for w in WORD.findall(ref)}
    stems = set()
    for f in forms:
        for w in WORD.findall(f):
            lw = w.lower()
            if len(lw) >= 4 and lw not in stop and lw not in ref_words:
                stems.add(lw[:max(4, len(lw) - 2)])
    return stems


def answerline(display: str, forms: list[str]) -> tuple[str, str]:
    alts = []
    for f in forms:
        if f.lower() != display.lower() and f.lower().rstrip("s") != display.lower().rstrip("s") \
                and f.lower() not in [a.lower() for a in alts]:
            alts.append(f)
    acc = f" [accept {' or '.join(alts[:3])}]" if alts else ""
    return f"<b><u>{display}</u></b>{acc}", f"{display}{acc}"


def bad_mention(doc, a: int, b: int) -> str | None:
    """Why a mention cannot be replaced by the referent, or None. The answer must be a
    noun phrase of its own: not a modifier ("N400 activity", "Kuhl and Miller's VOT
    experiment"), not joined to another name ("Broca and Wernicke"), not inside
    parentheses, not a value ("a VOT of 0 ms")."""
    toks = [t for t in doc if t.idx >= a and t.idx + len(t.text) <= b]
    if not toks:
        return None
    head = toks[-1]
    if head.dep_ in ("compound", "amod", "nmod", "poss") and head.head.i > head.i:
        return "modifier"
    nxt = doc[head.i + 1] if head.i + 1 < len(doc) else None
    if nxt is not None and nxt.pos_ in ("NOUN", "PROPN") and nxt.dep_ != "appos" and not nxt.is_punct:
        return "modifier"
    prev = doc[toks[0].i - 1] if toks[0].i > 0 else None
    if prev is not None and (prev.pos_ == "ADJ" or prev.lower_ in ("a", "an", "another", "each", "every", "no")):
        return "modified"  # "a bigger N400" would become "a bigger this effect"
    if head.dep_ == "conj" or any(c.dep_ == "conj" for c in head.children):
        return "coordinated"
    if doc.text[:a].count("(") > doc.text[:a].count(")"):
        return "parenthetical"
    if nxt is not None and nxt.lower_ == "of" and head.i + 2 < len(doc) and doc[head.i + 2].like_num:
        return "value"
    return None


def resolve_pronoun(text: str, start: int, chain_rows) -> str | None:
    """Replace a clue-initial pronoun with the fullest earlier mention in its chain
    ("They found" -> "Kuperberg and colleagues found"); None if the chain has no name."""
    m = PRONOUN_SUBJ.match(text)
    if not m:
        return text
    for row in chain_rows:
        if row["start_char"] - start == 0:
            names = [r["text"] for r in row["chain_mentions"] if not PRONOUN_SUBJ.match(r["text"])
                     and r["text"].lower() not in ("they", "he", "she", "it") and len(r["text"]) > 3
                     and len(r["text"].split()) <= 8 and row["is_np"](r["text"])]
            if names:
                name = max(names, key=len) if len(max(names, key=len)) < 60 else names[0]
                name = re.sub(r"^(the|a|an)\s+", lambda x: x.group(1).capitalize() + " ", name, flags=re.I)
                return name[0].upper() + name[1:] + text[m.end():]
    return None


def run(book_id: int, conn, cfg: dict) -> None:
    a = cfg["assemble"]
    nlp = spacy.load(cfg["sentences"]["spacy_model"], exclude=["ner", "lemmatizer"])
    source_re = re.compile("|".join(a["source_patterns"]))

    def is_np(text: str) -> bool:
        d = nlp(text)
        root = [t for t in d if t.head == t]
        return bool(root) and root[0].pos_ in ("NOUN", "PROPN")
    db.clear(conn, book_id, db.ASSEMBLE_TABLES)
    book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    meta_re = re.compile("|".join(a["meta_patterns"]), re.I)
    chapters = {s["id"]: s for s in conn.execute(
        "SELECT id, number, title FROM sections WHERE book_id = ? AND kind = 'chapter'", (book_id,))}
    named_spans = {}
    for r in conn.execute("SELECT term_id, sentence_id, start_char, end_char FROM mentions WHERE book_id = ?", (book_id,)):
        named_spans.setdefault((r["term_id"], r["sentence_id"]), []).append((r["start_char"], r["end_char"], "named"))
    coref_by_sentence = {}
    for r in conn.execute("SELECT unit, chain, sentence_id, start_char, end_char FROM coref WHERE book_id = ?", (book_id,)):
        coref_by_sentence.setdefault(r["sentence_id"], []).append(r)

    number = 0
    for t in conn.execute(
            """SELECT t.*, m.referent, m.plural, m.giveaway, m.main_chapter FROM terms t
               JOIN answer_meta m ON m.term_id = t.id WHERE t.book_id = ? ORDER BY t.id""", (book_id,)):
        forms = [f for f, _ in json.loads(t["forms"])]
        ref, many = t["referent"], bool(t["plural"])
        stems = answer_stems(forms, ref, cfg["rank"])
        clues = conn.execute(
            """SELECT c.*, s.text AS stext, cs.difficulty FROM clues c
               JOIN clue_scores cs ON cs.clue_id = c.id
               JOIN sentences s ON s.id = json_extract(c.sentence_ids, '$[0]')
               WHERE c.term_id = ? ORDER BY cs.difficulty DESC""", (t["id"],)).fetchall()
        picked, used_sent, per_section, length = [], set(), Counter(), 0
        giveaway = t["giveaway"]
        tail_budget = len(giveaway or "") + 40
        for c in clues:
            sid = json.loads(c["sentence_ids"])[0]
            if sid in used_sent or c["kind"] == "definition" or meta_re.search(c["text"]):
                continue
            if per_section[c["section_id"]] >= a["max_per_section"]:
                continue
            raw = c["stext"][c["start_char"]:c["end_char"]]
            spans = [(x - c["start_char"], y - c["start_char"], v) for x, y, v in named_spans.get((t["id"], sid), [])
                     if c["start_char"] <= x and y <= c["end_char"]]
            if not spans:  # reached through coreference: use the linked chain's mentions
                spans = [(r["start_char"] - c["start_char"], r["end_char"] - c["start_char"], "coref")
                         for r in coref_by_sentence.get(sid, [])
                         if c["start_char"] <= r["start_char"] and r["end_char"] <= c["end_char"]
                         and PRONOUN.match(c["stext"][r["start_char"]:r["end_char"]] or "x")][:1]
            if not spans:
                continue
            doc = nlp(raw)
            if any(bad_mention(doc, x, y) for x, y, v in spans if v == "named"):
                continue
            if raw.rstrip().endswith(":") or source_re.search(raw):
                continue
            # "This would reduce..." points back at a whole event: no name can replace it.
            if EVENT_ANAPHOR.match(raw):
                continue
            # A clue opening with a pronoun that is not the answer: give it its name.
            if PRONOUN_SUBJ.match(raw) and not any(x == 0 for x, _, _ in spans):
                chain_rows = []
                for r in coref_by_sentence.get(sid, []):
                    if r["start_char"] == c["start_char"]:
                        chain_rows.append({"start_char": r["start_char"], "is_np": is_np, "chain_mentions": conn.execute(
                            "SELECT text FROM coref WHERE book_id = ? AND unit = ? AND chain = ? ORDER BY id",
                            (book_id, r["unit"], r["chain"])).fetchall()})
                fixed = resolve_pronoun(raw, c["start_char"], chain_rows)
                if fixed is None:
                    continue
                shift = len(fixed) - len(raw)
                raw, spans = fixed, [(x + shift, y + shift, v) for x, y, v in spans]
            text = clean(mask(raw, spans, ref, many))
            if not text.endswith((".", "?", "!")):
                text += "."
            if leaks(text, stems) or len(text) > a["max_clue_chars"]:
                continue
            if length + len(text) + tail_budget > a["max_chars"]:
                continue
            picked.append((c, text))
            used_sent.add(sid)
            per_section[c["section_id"]] += 1
            length += len(text) + 1
            if len(picked) >= a["max_sentences"] - 1:
                break
        lead = phrase(ref, many, True) + " "
        if giveaway and not leaks(giveaway, stems):
            last = f"For 10 points, name {phrase(ref, many, False)} {giveaway.rstrip('.')}."
        else:
            # The easiest clue whose subject is the answer becomes the giveaway:
            # "This gene regulates ..." -> "For 10 points, name this gene that regulates ...".
            subj = [k for k, (_, txt) in enumerate(picked) if txt.startswith(lead)]
            if subj:
                k = subj[-1]
                _, txt = picked.pop(k)
                rel = "who" if t["kind"] == "person" else "that"
                last = f"For 10 points, name {phrase(ref, many, False)} {rel} {txt[len(lead):].rstrip('.')}."
            elif picked:
                last = f"For 10 points, name {phrase(ref, many, False)}."
            else:
                continue
        sentences = len(picked) + 1
        if sentences < a["min_sentences"]:
            continue
        question = " ".join(text for _, text in picked) + " " + last
        if len(question) < a["min_chars"]:
            continue
        number += 1
        ans_html, ans_plain = answerline(t["display"], forms)
        ch = chapters.get(t["main_chapter"])
        diffs = [c["difficulty"] for c, _ in picked]
        conn.execute(
            """INSERT INTO tossups (id, question, question_sanitized, answer, answer_sanitized, category,
               subcategory, difficulty, number, packet_json, set_json, source, book_id, term_id,
               section_ids, clue_ids, pipeline_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"{book['slug']}-{t['id']}", question, question, ans_html, ans_plain,
             a["category"], a["subcategory"], round(10 * sum(diffs) / len(diffs)) if diffs else 0, number,
             json.dumps({"name": f"Chapter {ch['number']}: {ch['title']}" if ch else "", "number": int(ch["number"]) if ch and ch["number"].isdigit() else None}),
             json.dumps({"name": book["title"], "year": a["set_year"]}),
             "generated", book_id, t["id"],
             json.dumps(sorted({c["section_id"] for c, _ in picked})),
             json.dumps([c["id"] for c, _ in picked]), a["pipeline_version"]))
    conn.commit()
