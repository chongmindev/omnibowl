"""Parse step 4: stitch tagged lines into text units and split them into sentences.

Reads the tagged lines (tag.py) and writes the `sentences` table. Each sentence keeps
the ids of the lines it came from, so every later clue can point back to the page.

Stitching (see docs/decisions.md, "parse: sentences"):
  - A unit is one stream of text: body prose in a section, one box or exercise
    region, one definition, one caption. Body text continues across interruptions
    (a margin definition, a caption, a box placed mid-paragraph) and breaks only at a
    plain heading or a new section. A region's text breaks at headings that belong
    to it (its marker, in-box headings).
  - A drop cap is joined to the start of the next line of its stream.
  - Lines are joined with a space, except after a line-final dash. A line-final
    hyphen is kept ("well-" + "known") unless the book itself uses the joined word
    more often than the hyphenated form ("informa-" + "tion").
Splitting uses spaCy's dependency parser (en_core_web_sm).
"""

import json
import re
from collections import Counter

import spacy

from . import db

BOLD = 16
WORD_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")
DASHES = "—–"


def load_nlp(cfg: dict):
    scfg = cfg["sentences"]
    nlp = spacy.load(scfg["spacy_model"], exclude=scfg["spacy_exclude"])
    nlp.max_length = scfg["spacy_max_length"]
    return nlp


def word_counts(lines) -> Counter:
    counts = Counter()
    for ln in lines:
        counts.update(w.lower() for w in WORD_RE.findall(ln["text"]))
    return counts


def join(prev: str, nxt: str, counts: Counter) -> str:
    """Joint between two consecutive lines of one unit."""
    if prev.endswith("-") and nxt[:1].isalpha():
        head = re.search(r"([A-Za-z]+)-$", prev)
        tail = re.match(r"[A-Za-z]+", nxt)
        if head and tail:
            closed = (head.group(1) + tail.group(0)).lower()
            hyphened = f"{head.group(1)}-{tail.group(0)}".lower()
            if counts[closed] > counts[hyphened]:
                return prev[:-1] + nxt
        return prev + nxt
    if prev[-1:] in DASHES or nxt[:1] in DASHES:
        return prev + nxt
    return prev + " " + nxt


def build_units(lines, cfg: dict) -> list[dict]:
    """Group tagged lines into text units in reading order."""
    keep = set(cfg["sentences"]["kinds"])
    units: list[dict] = []
    open_units: dict[tuple, dict] = {}
    pending_dropcap: dict[tuple, object] = {}
    item_no: dict[tuple, int] = {}
    section = None

    def close(pred=lambda key: True):
        for key in [k for k in open_units if pred(k)]:
            del open_units[key]

    for ln in lines:
        kind, feature = ln["kind"], ln["feature"]
        if ln["section_id"] != section:
            section = ln["section_id"]
            close()
            pending_dropcap.clear()
        if kind == "heading":
            # A plain heading ends everything in the section; a region's own heading
            # (its marker or an in-box heading) ends that region's units.
            close(lambda k: feature is None or k[1] == feature)
            continue
        if kind == "dropcap":
            pending_dropcap[("region", feature) if feature else ("body", None)] = ln
            continue
        if kind not in keep:
            continue
        # Definitions and captions: each bold-start line starts a new item, so page
        # furniture inside a panel cannot split one.
        if kind in ("definition", "caption"):
            if ln["bold_start"] or (kind, feature) not in item_no:
                item_no[(kind, feature)] = ln["id"]
            key = (kind, feature, item_no[(kind, feature)])
        else:
            key = (kind, feature)
        unit = open_units.get(key)
        if unit is None:
            unit = {"kind": kind, "feature": feature, "section_id": section, "lines": []}
            open_units[key] = unit
            units.append(unit)
            dc = pending_dropcap.pop(("region", feature) if feature else ("body", None), None)
            if dc is not None:
                unit["dropcap"] = dc
        unit["lines"].append(ln)
    return [u for u in units if u["lines"]]


def unit_text(unit: dict, counts: Counter) -> tuple[str, list[tuple[int, int, int]]]:
    """Text of a unit and (start, end, line_id) character spans for each line."""
    text, spans = "", []
    for i, ln in enumerate(unit["lines"]):
        piece = ln["text"]
        if i == 0:
            text = (unit["dropcap"]["text"] if "dropcap" in unit else "") + piece
            start = 0
        else:
            text = join(text, piece, counts)
            start = len(text) - len(piece)
        spans.append((start, len(text), ln["id"]))
    return text, spans


def bold_term(ln) -> str:
    """The bold run that opens a line (a definition's term)."""
    term = []
    for text, _size, flags in json.loads(ln["spans_json"]):
        if not (flags & BOLD or not text.strip()):
            break
        term.append(text)
    return "".join(term).strip()


def keep_term_attached(bounds, text: str, term: str):
    """spaCy reads "phonetics The study of..." as two sentences; a definition's bold
    term always belongs to the sentence after it."""
    if not term or not text.startswith(term):
        return bounds
    # Merge leading sentences until one extends past the term, then one more so the
    # term is never a sentence by itself.
    i = 0
    while i < len(bounds) - 1 and bounds[i][1] <= len(term) + 1:
        i += 1
    return [(bounds[0][0], bounds[i][1])] + bounds[i + 1:]


def merge_open_parens(bounds, text: str):
    """Never end a sentence inside unclosed parentheses or brackets: spaCy splits
    citations such as "(CDC, 2016; Twenge et al., 2016)" after the semicolon."""
    out = []
    for a, b in bounds:
        if out:
            pa, pb = out[-1]
            seg = text[pa:pb]
            if seg.count("(") > seg.count(")") or seg.count("[") > seg.count("]"):
                out[-1] = (pa, b)
                continue
        out.append((a, b))
    return out


def drop_runin_label(bounds, text: str, label: str):
    """A caption's bold run-in label ("Figure 2.1", "Culture and normality") names the
    caption; it is not part of its first sentence. A bold opening that is itself a
    sentence (ends in . ? !) is kept."""
    if not label or label[-1] in ".?!" or not text.startswith(label):
        return bounds
    cut = len(label)
    while cut < len(text) and text[cut].isspace():
        cut += 1
    out = [(max(a, cut), b) for a, b in bounds if b > cut]
    return out


def run(book_id: int, conn, cfg: dict, nlp=None) -> None:
    nlp = nlp or load_nlp(cfg)
    db.clear(conn, book_id, db.SENTENCE_TABLES)
    lines = conn.execute(
        """SELECT id, seq, page, section_id, kind, feature, block_id, bold_start, spans_json, text FROM lines
           WHERE book_id = ? AND kind NOT IN ('front_matter', 'back_matter') ORDER BY seq""",
        (book_id,)).fetchall()
    counts = word_counts(lines)
    units = build_units(lines, cfg)
    texts, spans = zip(*(unit_text(u, counts) for u in units)) if units else ((), ())
    page_of = {ln["id"]: ln["page"] for ln in lines}

    rows = []
    batch = cfg["sentences"]["spacy_batch_size"]
    for u_ord, (unit, doc, sp) in enumerate(zip(units, nlp.pipe(texts, batch_size=batch), spans)):
        bounds = merge_open_parens([(sent.start_char, sent.end_char) for sent in doc.sents], doc.text)
        if unit["kind"] == "definition":
            bounds = keep_term_attached(bounds, doc.text, bold_term(unit["lines"][0]))
        elif unit["kind"] == "caption" and "dropcap" not in unit:
            bounds = drop_runin_label(bounds, doc.text, bold_term(unit["lines"][0]))
        for s_ord, (a, b) in enumerate(bounds):
            raw = doc.text[a:b]
            text = raw.strip()
            if not any(ch.isalnum() for ch in text):
                continue
            a += len(raw) - len(raw.lstrip())
            b -= len(raw) - len(raw.rstrip())
            hits = [(s, e, lid) for (s, e, lid) in sp if s < b and e > a]
            line_ids = [lid for _, _, lid in hits]
            pages = [page_of[i] for i in line_ids]
            rows.append((book_id, unit["section_id"], unit["kind"], unit["feature"], u_ord, s_ord,
                         text, json.dumps(line_ids), max(0, a - hits[0][0]), b - hits[-1][0],
                         min(pages), max(pages)))
    conn.executemany(
        """INSERT INTO sentences (book_id, section_id, kind, feature, unit, ord, text, line_ids,
           start_char, end_char, page_start, page_end)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
    conn.commit()
