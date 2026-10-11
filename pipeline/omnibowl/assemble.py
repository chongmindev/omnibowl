"""Stage 5: assemble tossups (rough first version, decision 2026-10-09).

For each answer with a referent:
  1. Take its clues, skip the definition (it becomes the giveaway) and meta-text that
     addresses the reader or points at the book ("as you saw", "see Table 3.3").
  2. Render each clue (`render_clue`, decisions 2026-10-09, wording rules 1-4):
     - every mention of the answer, named or via its coreference chain, becomes the
       referent ("this ERP");
     - every other pronoun becomes the named phrase from its coreference chain ("they"
       -> "speakers"); a pronoun with no named antecedent drops the clue (rule 1);
     - the answer must be the clue's subject or a main argument, not a bystander in a
       prepositional phrase (rule 2, `min_topicality`);
     - the clue must contain one concrete item besides the answer: a name, a number, or
       another key term from the book (rule 4);
     - leading connectives and hedging frames ("But", "For instance", "The general idea
       is that") and parenthetical asides are trimmed, keeping a full clause (rule 3).
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
from .clues import clean, linked_chains
from .rank import chapter_of

EVENT_ANAPHOR = re.compile(r"^(This|That|These|Those)\s+(?:is|was|are|were|would|will|can|could|may|might|has|have|had|led|leads|means|meant|suggests|shows|showed|makes|made)\b")

WORD = re.compile(r"[A-Za-z][A-Za-z'’-]+")


def is_plural_noun(word: str) -> bool:
    w = word.split()[-1].lower()
    return w.endswith("s") and not w.endswith(("ss", "us", "is", "sis")) and len(w) > 3


def phrase(ref: str, many: bool, cap: bool) -> str:
    det = "these" if many or is_plural_noun(ref) else "this"
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
    toks = [t for t in doc if t.idx >= a and t.idx + len(t.text) <= b and t.tag_ != "POS"]
    if not toks:
        return None
    head = toks[-1]
    if doc.text[b:b + 1] in "-‐" and b < len(doc.text) or doc.text[a - 1:a] in ("-", "‐") and a > 0:
        return "hyphenated"  # "eye-tracking" would become "this organ-tracking"
    if any(c.dep_ in ("compound", "amod") and c.i < toks[0].i for c in head.children):
        return "modified"  # "job stress", "motor cortex neurons"
    if head.dep_ == "poss" and (any(c.dep_ == "conj" for c in head.children) or head.dep_ == "conj"):
        return "coordinated"  # "Broca's and Wernicke's attempts"
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



PERSONAL = {"he", "she", "it", "they", "him", "her", "them", "his", "its", "their", "hers",
            "theirs", "himself", "herself", "itself", "themselves"}
DEMONSTRATIVE = {"this", "that", "these", "those"}
POSSESSIVE = {"his", "her", "its", "their"}
DROPS: Counter = Counter()  # why clues were dropped in the last run (for diagnostics)


def topicality(doc, a: int, b: int, cfg: dict) -> float:
    """How central the answer is: subject 1.0, predicate noun 0.8, object 0.6, inside a
    prepositional phrase 0.3 (weights in config)."""
    toks = [t for t in doc if t.idx >= a and t.idx + len(t.text) <= b]
    if not toks:
        return 0.0
    head = next((t for t in toks if t.head not in toks), toks[-1])
    # Climb through noun-attached prepositional phrases: in "Damage to Broca's area
    # causes ...", the answer sits under "Damage", which is the subject.
    while head.dep_ == "pobj" and head.head.dep_ == "prep" and head.head.head.pos_ in ("NOUN", "PROPN", "PRON"):
        head = head.head.head
    w = cfg["topicality"]
    if head.dep_ in ("nsubj", "nsubjpass", "csubj"):
        return w["subject"]
    if head.dep_ in ("attr", "ROOT", "appos", "oprd"):
        return w["predicate"]
    if head.dep_ in ("dobj", "obj", "dative"):
        return w["object"]
    if head.dep_ == "pobj":
        return w["prepositional"]
    return w["other"]


def is_expletive(tok) -> bool:
    """Dummy "it": "it is helpful to ...", "it seems that ..."."""
    if tok.lower_ != "it":
        return False
    if tok.dep_ == "expl":
        return True
    h = tok.head
    return h.lemma_ in ("be", "seem", "appear", "turn") and any(
        c.dep_ in ("ccomp", "xcomp", "acomp") for c in h.children) and any(
        c.lower_ in ("to", "that") for c in h.subtree)


def trim(text: str, a: dict, nlp) -> str:
    """Rule 3: drop leading connectives and hedging frames and parenthetical asides;
    keep the result only if it is still a clause with a verb."""
    out = text
    for _ in range(3):
        before = out
        out = re.sub(rf"^(?:{'|'.join(a['leading_connectives'])}),?\s+", "", out)
        out = re.sub(rf"^(?:{'|'.join(a['frame_patterns'])})\s+", "", out, flags=re.I)
        if out == before:
            break
    if a["drop_parentheticals"]:
        if re.search(r"\b(?:in|of|to|for|with|from|by|at|on|as|than|the|a|an)\s*\([^()]*\)", out, re.I):
            return ""  # removing it would leave "the sentences in is remembered"
        out = re.sub(r"\s*\([^()]*\)", "", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;")
    if not out:
        return ""
    out = out[0].upper() + out[1:]
    if out != text and not any(t.pos_ in ("VERB", "AUX") for t in nlp(out)):
        return text
    return out


def render_clue(raw: str, doc, ref_spans, other_coref, chain_name, concrete_spans, ref, many,
                a: dict, nlp) -> str | None:
    """Rules 1-4 for one clause. `ref_spans`: answer mentions (named or coreference);
    `other_coref`: {(start, end): (unit, chain)} for other coreference mentions;
    `chain_name(unit, chain)`: the chain's named phrase or None; `concrete_spans`:
    mentions of other key terms. Offsets are inside `raw`. Returns text or None."""
    if not ref_spans:
        DROPS["no answer mention"] += 1
        return None
    quoted = sum(len(q) for q in re.findall(r"[“\"][^”\"]+[”\"]", raw))
    if quoted > a["max_quoted_share"] * len(raw):
        DROPS["quoted dialogue"] += 1
        return None
    # Rule 2: the answer must matter in the clause.
    if max(topicality(doc, x, y, a) for x, y in ref_spans) < a["min_topicality"]:
        DROPS["rule 2: answer not central"] += 1
        return None
    reps = [(x, y, "REF") for x, y in ref_spans]
    covered = lambda i: any(x <= i < y for x, y, _ in reps)
    # Rule 1: every other pronoun gets its name, or the clue goes.
    for tok in doc:
        low = tok.lower_
        is_pron = low in PERSONAL or (low in DEMONSTRATIVE and tok.pos_ == "PRON")
        if not is_pron or covered(tok.idx) or is_expletive(tok):
            continue
        key = next((v for (x, y), v in other_coref.items() if x <= tok.idx < y), None)
        name = chain_name(*key) if key else None
        if not name:
            if a.get("allow_unresolved_pronouns"):
                continue  # pool mode: the rewrite step sees the previous sentence
            DROPS["rule 1: unresolved pronoun"] += 1
            return None
        if low in POSSESSIVE:
            name = name + "’s"
        reps.append((tok.idx, tok.idx + len(tok.text), name))
    # Rule 4: one concrete item besides the answer.
    concrete = any(not covered(t.idx) and (t.pos_ == "PROPN" or t.like_num) for t in doc) or any(
        not covered(x) for x, _ in concrete_spans)
    if not concrete:
        DROPS["rule 4: nothing concrete"] += 1
        return None
    # Apply replacements left to right.
    out, pos = [], 0
    for x, y, rep in sorted(reps):
        if x < pos:
            continue
        seg = raw[pos:x]
        y = y
        if rep == "REF":
            lead = re.search(rf"(?:^|\s)((?:(?:the|The|a|an|An|A)\s+)?(?:[\w-]+\s+)?{re.escape(ref)}|the|The|a|an|An|A)\s+$", seg)
            if lead:
                seg = seg[:len(seg) - len(lead.group(0).lstrip())]
            cap = not "".join(out + [seg]).strip() or "".join(out + [seg]).rstrip().endswith((".", "!", "?"))
            word = phrase(ref, many, cap)
            if re.search(r"(?:\b(?:our|their|its|his|her|my|your)|['’]s?)\s+$", seg):
                word = ref  # "our conceptual representations", not "our these ..."
            orig = raw[x:y]
            nxt2 = raw[y:y + 2]
            if orig.lower() in POSSESSIVE or orig.endswith(("’s", "'s")):
                word += "’s"
            elif nxt2 in ("’s", "'s") and orig.lower() in PERSONAL:
                # "it's easy" is "it is easy", not a possessive
                word += " is" if not is_plural_noun(word) else " are"
                y += 2
        else:
            cap = not "".join(out + [seg]).strip()
            word = rep[0].upper() + rep[1:] if cap else (rep[0].lower() + rep[1:] if rep.split()[0].lower() in ("the", "a", "an") else rep)
        out += [seg, word]
        pos = y
    out.append(raw[pos:])
    text = trim(clean("".join(out)), a, nlp)
    if not text:
        DROPS["rule 3: parenthetical after a preposition"] += 1
        return None
    return text


def cited(conn, term) -> bool:
    """A person cited somewhere in the book: "Surname (1996)", "Surname et al., 1996"."""
    surname = term["norm"].split()[-1]
    for (text,) in conn.execute(
            "SELECT s.text FROM mentions m JOIN sentences s ON s.id = m.sentence_id WHERE m.term_id = ?",
            (term["id"],)):
        if re.search(rf"\b{re.escape(surname)}\b(?:\s+et al\.?|\s+(?:and|&)\s+[A-Z][\w-]+)?,?\s*\(?(?:19|20)\d\d",
                     text, re.I):
            return True
    return False


def run(book_id: int, conn, cfg: dict, mode: str = "strict") -> None:
    """mode "strict" applies the wording rules as set; "pool" applies the overrides in
    [assemble.pool] to draft more tossups for the v1 rewrite (docs/rewrite-v1.md)."""
    a = dict(cfg["assemble"])
    if mode == "pool":
        a.update(cfg["assemble"]["pool"])
    pver = a["pipeline_version"] + ("-pool" if mode == "pool" else "")
    nlp = spacy.load(cfg["sentences"]["spacy_model"], exclude=["ner", "lemmatizer"])
    source_re = re.compile("|".join(a["source_patterns"]))
    top = chapter_of(conn, book_id)

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
    coref_by_sentence, chain_rows = {}, {}
    for r in conn.execute("SELECT unit, chain, sentence_id, start_char, end_char, text FROM coref WHERE book_id = ?", (book_id,)):
        coref_by_sentence.setdefault(r["sentence_id"], []).append(r)
        chain_rows.setdefault((r["unit"], r["chain"]), []).append(r["text"])
    names_cache = {}

    def chain_name(unit, chain):
        """The named phrase of a coreference chain: its longest noun-phrase mention."""
        key = (unit, chain)
        if key not in names_cache:
            cands = [m for m in chain_rows.get(key, []) if m.lower() not in PERSONAL | DEMONSTRATIVE
                     and len(m.split()) <= a["max_name_words"] and is_np(m)]
            names_cache[key] = max(cands, key=len) if cands else None
        return names_cache[key]
    key_spans = {}
    for r in conn.execute(
            """SELECT m.term_id, m.sentence_id, m.start_char, m.end_char FROM mentions m
               JOIN terms t ON t.id = m.term_id WHERE m.book_id = ? AND t.class IN ('answer', 'broad_answer', 'clue_only', 'variant')""",
            (book_id,)):
        key_spans.setdefault(r["sentence_id"], []).append((r["term_id"], r["start_char"], r["end_char"]))

    variant_forms = {}  # head term id -> its variants' surface forms ("N400 effect")
    for r in conn.execute("SELECT v.head_id, t.forms FROM term_variants v JOIN terms t ON t.id = v.term_id "
                          "WHERE v.book_id = ?", (book_id,)):
        variant_forms.setdefault(r["head_id"], []).extend(f for f, _ in json.loads(r["forms"]))

    number = 0
    for t in conn.execute(
            """SELECT t.*, m.referent, m.plural, m.giveaway, m.main_chapter, m.referent_source FROM terms t
               JOIN answer_meta m ON m.term_id = t.id WHERE t.book_id = ? ORDER BY t.id""", (book_id,)):
        forms = [f for f, _ in json.loads(t["forms"])] + variant_forms.get(t["id"], [])
        ref, many = t["referent"], bool(t["plural"])
        if a["skip_generic_referents"] and t["referent_source"] == "generic":
            DROPS["answer: generic referent"] += 1
            continue
        if a["persons_need_citation_or_title"] and t["kind"] == "person" and t["referent_source"] != "book" \
                and not cited(conn, t):
            DROPS["answer: person never cited or titled"] += 1
            continue
        answer_chains = linked_chains(conn, book_id, t["id"], conn.execute(
            "SELECT sentence_id, start_char, end_char FROM mentions WHERE term_id = ?", (t["id"],)).fetchall())
        stems = answer_stems(forms, ref, cfg["rank"])
        clues = conn.execute(
            """SELECT c.*, s.text AS stext, cs.difficulty FROM clues c
               JOIN clue_scores cs ON cs.clue_id = c.id
               JOIN sentences s ON s.id = json_extract(c.sentence_ids, '$[0]')
               WHERE c.term_id = ? ORDER BY cs.difficulty DESC""", (t["id"],)).fetchall()
        picked, used_sent, per_section, per_chapter, length = [], set(), Counter(), Counter(), 0
        giveaway = t["giveaway"]
        tail_budget = len(giveaway or "") + 40
        for c in clues:
            sid = json.loads(c["sentence_ids"])[0]
            if sid in used_sent or c["kind"] == "definition" or meta_re.search(c["text"]):
                continue
            if per_section[c["section_id"]] >= a["max_per_section"]:
                continue
            # Broad answers ("eye") range over the book: one clue per chapter.
            if t["class"] == "broad_answer" and per_chapter[top(c["section_id"])] >= a["broad_max_per_chapter"]:
                continue
            c0, c1 = c["start_char"], c["end_char"]
            raw = c["stext"][c0:c1]
            if raw.rstrip().endswith(":") or source_re.search(raw) or EVENT_ANAPHOR.match(raw):
                continue
            doc = nlp(raw)
            named = [(x - c0, y - c0) for x, y, _ in named_spans.get((t["id"], sid), []) if c0 <= x and y <= c1]
            if any(bad_mention(doc, x, y) for x, y in named):
                continue
            chain_ref = [(r["start_char"] - c0, r["end_char"] - c0) for r in coref_by_sentence.get(sid, [])
                         if (r["unit"], r["chain"]) in answer_chains and c0 <= r["start_char"] and r["end_char"] <= c1
                         and not any(x <= r["start_char"] - c0 < y for x, y in named)]
            other = {(r["start_char"] - c0, r["end_char"] - c0): (r["unit"], r["chain"])
                     for r in coref_by_sentence.get(sid, [])
                     if (r["unit"], r["chain"]) not in answer_chains and c0 <= r["start_char"] and r["end_char"] <= c1}
            concrete = [(x - c0, y - c0) for tid, x, y in key_spans.get(sid, [])
                        if tid != t["id"] and c0 <= x and y <= c1]
            if any(bad_mention(doc, x, y) for x, y in chain_ref if raw[x:y].lower() not in PERSONAL):
                continue
            text = render_clue(raw, doc, named + chain_ref, other, chain_name, concrete, ref, many, a, nlp)
            if not text:
                continue
            if not text.endswith((".", "?", "!")):
                text += "."
            if leaks(text, stems) or len(text) > a["max_clue_chars"]:
                continue
            if length + len(text) + tail_budget > a["max_chars"]:
                continue
            picked.append((c, text))
            used_sent.add(sid)
            per_section[c["section_id"]] += 1
            per_chapter[top(c["section_id"])] += 1
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
             json.dumps({"name": (f"Chapter {ch['number']}: {ch['title']}" if ch["number"] else ch["title"]) if ch else "",
                        "number": int(ch["number"]) if ch and (ch["number"] or "").isdigit() else None}),
             json.dumps({"name": book["title"], "year": a["set_year"]}),
             "generated", book_id, t["id"],
             json.dumps(sorted({c["section_id"] for c, _ in picked})),
             json.dumps([c["id"] for c, _ in picked]), pver))
    conn.commit()
