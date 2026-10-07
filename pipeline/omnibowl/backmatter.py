"""Parse step 5: read the glossary and indexes into the `labels` table.

The profile says which back-matter part is a glossary, subject index or author index
(guessed from contents; corrections in profiles/<book>.toml). Labels are for tuning
and evaluating `candidates`; the method there must still work on a book without them.

Glossary: a bold-start line begins an entry; the bold run is the term, the rest of the
line and the following lines are the definition; links on the term line give the page
where the term is introduced.
Index: one entry per line, "term, 12, 30-31", with a link per page reference. A line
indented past the part's usual left edge is a sub-entry of the last top-level entry; a
line that starts with a digit continues the previous entry's pages.
"""

import json
import re
from collections import Counter

from . import db
from .sentences import bold_term

PAGE_TAIL_RE = re.compile(r",\s*(?:\d|see\b|See\b).*$")


def _links(conn, line_id: int) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT target_page FROM links WHERE line_id = ? AND target_page IS NOT NULL ORDER BY x0",
        (line_id,))]


def glossary(lines, conn) -> list[dict]:
    entries = []
    for ln in lines:
        term = bold_term(ln) if ln["bold_start"] else ""
        if term:
            entries.append({"term": term.rstrip(":"), "definition": ln["text"][len(term):].strip(),
                            "pages": _links(conn, ln["id"]), "line_id": ln["id"], "parent": None})
        elif entries:
            entries[-1]["definition"] = (entries[-1]["definition"] + " " + ln["text"]).strip()
    return entries


def index(lines, conn, cfg: dict) -> list[dict]:
    xs = Counter(round(ln["x0"]) for ln in lines)
    margin = min(x for x, c in xs.items() if c >= cfg["backmatter"]["margin_min_share"] * len(lines))
    indent = cfg["backmatter"]["subentry_min_indent"]
    entries, top = [], None
    for ln in lines:
        text, pages = ln["text"].strip(), _links(conn, ln["id"])
        if text[:1].isdigit() and entries:
            entries[-1]["pages"] += pages  # wrapped page list
            continue
        term = PAGE_TAIL_RE.sub("", text).strip().rstrip(",")
        if not term:
            continue
        sub = ln["x0"] - margin >= indent and top is not None
        e = {"term": term, "definition": None, "pages": pages, "line_id": ln["id"],
             "parent": top if sub else None}
        entries.append(e)
        if not sub:
            top = e
    return entries


def _in_text(term: str, role: str, text: str) -> bool:
    """Whether the term occurs in the chapters; for authors, the surname."""
    key = term.split(",")[0] if role == "author_index" else term
    return key.lower().strip() in text


def run(book_id: int, conn, cfg: dict, profile: dict) -> None:
    db.clear(conn, book_id, db.LABEL_TABLES)
    roles = {b["title"]: b["role"] for b in profile.get("back_matter", [])}
    text = " ".join(r[0] for r in conn.execute(
        "SELECT text FROM sentences WHERE book_id = ?", (book_id,))).lower()
    secs = conn.execute("SELECT id, title FROM sections WHERE book_id = ? AND kind = 'back_matter'",
                        (book_id,)).fetchall()
    for sec in secs:
        role = roles.get(sec["title"])
        if role not in ("glossary", "subject_index", "author_index"):
            continue
        lines = conn.execute("SELECT * FROM lines WHERE section_id = ? ORDER BY seq", (sec["id"],)).fetchall()
        # The section's own title line is not an entry.
        lines = [ln for ln in lines if ln["text"].strip() != sec["title"]]
        entries = glossary(lines, conn) if role == "glossary" else index(lines, conn, cfg)
        ids = {}
        for e in entries:
            cur = conn.execute(
                """INSERT INTO labels (book_id, role, term, parent_id, definition, target_pages,
                   in_text, line_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (book_id, role, e["term"], ids.get(id(e["parent"])) if e["parent"] else None,
                 e["definition"], json.dumps(e["pages"]), int(_in_text(e["term"], role, text)),
                 e["line_id"]))
            ids[id(e)] = cur.lastrowid
    conn.commit()
