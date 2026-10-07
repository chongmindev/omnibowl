"""Glossary and index extraction on a synthetic back matter (no book text)."""

import json

from omnibowl import backmatter

BOLD = 16


def add_line(conn, seq, text, x0=77.0, bold_run=None, links=()):
    spans = ([[bold_run, 11, BOLD], [text[len(bold_run):], 11, 0]] if bold_run else [[text, 11, 0]])
    cur = conn.execute(
        """INSERT INTO lines (book_id, seq, page, x0, y0, x1, y1, size, rel_size, bold_start,
           bold_frac, italic_frac, text, spans_json, lkey)
           VALUES (1, ?, 1, ?, ?, 500, ?, 11, 1.0, ?, 0, 0, ?, ?, ?)""",
        (seq, x0, seq * 15.0, seq * 15.0 + 12, bool(bold_run), text, json.dumps(spans), f"k{seq}"))
    for page in links:
        conn.execute("INSERT INTO links (book_id, page, line_id, target_page, text) VALUES (1, 1, ?, ?, '')",
                     (cur.lastrowid, page))
    return conn.execute("SELECT * FROM lines WHERE id = ?", (cur.lastrowid,)).fetchone()


def setup(conn):
    conn.execute("INSERT INTO books (id, slug, path, sha256, page_count) VALUES (1, 'b', 'b.pdf', 'x', 1)")


def test_glossary_entries(conn):
    setup(conn)
    lines = [add_line(conn, 1, "phoneme A unit of sound", bold_run="phoneme", links=[12]),
             add_line(conn, 2, "that distinguishes meaning."),
             add_line(conn, 3, "N400 An ERP component.", bold_run="N400", links=[30])]
    got = backmatter.glossary(lines, conn)
    assert [(e["term"], e["definition"], e["pages"]) for e in got] == [
        ("phoneme", "A unit of sound that distinguishes meaning.", [12]),
        ("N400", "An ERP component.", [30])]


def test_index_subentries_and_wraps(conn, cfg):
    setup(conn)
    lines = [add_line(conn, 1, "aging"),
             add_line(conn, 2, "effects on comprehension, 38", x0=92, links=[714]),
             add_line(conn, 3, "word recall and, 40, 41,", x0=92, links=[720, 721]),
             add_line(conn, 4, "52", x0=107, links=[730]),
             add_line(conn, 5, "affixes, 191–196", links=[375, 384])] + \
            [add_line(conn, 6 + i, f"term{i}, {i}", links=[i]) for i in range(5)]
    got = backmatter.index(lines, conn, cfg)
    assert [(e["term"], e["pages"], e["parent"]["term"] if e["parent"] else None) for e in got[:4]] == [
        ("aging", [], None),
        ("effects on comprehension", [714], "aging"),
        ("word recall and", [720, 721, 730], "aging"),
        ("affixes", [375, 384], None)]


def test_author_in_text_uses_surname():
    assert backmatter._in_text("Goldin-Meadow, S.", "author_index", "as goldin-meadow showed")
    assert not backmatter._in_text("acción potencial", "glossary", "action potential")
