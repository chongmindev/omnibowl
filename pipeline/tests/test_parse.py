import pytest

from omnibowl import parse
from conftest import BOOK


def test_repair_mojibake():
    assert parse.repair_mojibake("Speakersâ€™ Intentions") == "Speakers’ Intentions"
    assert parse.repair_mojibake("plain ascii") == "plain ascii"
    assert parse.repair_mojibake("already ’ fine") == "already ’ fine"


def run_tiny(tiny_pdf, conn, cfg):
    book_id = parse.run(tiny_pdf, conn, cfg)
    return book_id


def test_sections_from_outline(tiny_pdf, conn, cfg):
    run_tiny(tiny_pdf, conn, cfg)
    rows = conn.execute("SELECT kind, number, title, parent_id FROM sections ORDER BY ord").fetchall()
    assert [(r["kind"], r["number"], r["title"]) for r in rows] == [
        ("front_matter", None, "Preface"),
        ("chapter", "1", "Speaker’s Chapter"),
        ("section", "1.1", "First Section"),
        ("chapter", "2", "Second Chapter"),
        ("back_matter", None, "Glossary"),
    ]
    assert rows[2]["parent_id"] is not None


def test_lines_assigned_to_section_mid_page(tiny_pdf, conn, cfg):
    run_tiny(tiny_pdf, conn, cfg)
    q = """SELECT s.number FROM lines l JOIN sections s ON s.id = l.section_id WHERE l.text = ?"""
    assert conn.execute(q, ("Body line two",)).fetchone()[0] == "1"
    assert conn.execute(q, ("Section body",)).fetchone()[0] == "1.1"


def test_rel_size_is_per_chapter(tiny_pdf, conn, cfg):
    run_tiny(tiny_pdf, conn, cfg)
    rel = dict(conn.execute("SELECT text, rel_size FROM lines").fetchall())
    assert rel["Body line one"] == 1.0
    assert rel["Bigger body 0"] == 1.0
    assert rel["Panel text"] == pytest.approx(0.75)


def test_panels_and_links(tiny_pdf, conn, cfg):
    run_tiny(tiny_pdf, conn, cfg)
    assert conn.execute("SELECT count(*) FROM panels").fetchone()[0] == 1
    in_panel = {r[0] for r in conn.execute("SELECT text FROM lines WHERE panel_id IS NOT NULL")}
    assert in_panel == {"Panel text", "glossterm"}
    link = conn.execute("SELECT text, target_page, line_id FROM links").fetchone()
    assert link["text"] == "glossterm"
    assert link["target_page"] == 4
    assert link["line_id"] is not None


def test_rerun_is_idempotent(tiny_pdf, conn, cfg):
    run_tiny(tiny_pdf, conn, cfg)
    first = conn.execute("SELECT count(*) FROM lines").fetchone()[0]
    run_tiny(tiny_pdf, conn, cfg)
    assert conn.execute("SELECT count(*) FROM lines").fetchone()[0] == first
    assert conn.execute("SELECT count(*) FROM books").fetchone()[0] == 1


@pytest.mark.skipif(not BOOK.exists(), reason="v0 book not present")
def test_v0_book_structure(conn, cfg):
    """Structural facts from CLAUDE.md. Counts only, no book text."""
    parse.run(BOOK, conn, cfg)
    kinds = dict(conn.execute("SELECT kind, count(*) FROM sections GROUP BY kind").fetchall())
    assert kinds == {"front_matter": 8, "chapter": 13, "section": 61, "back_matter": 4}
    assert conn.execute("SELECT page_count FROM books").fetchone()[0] == 1279
    # Every chapter and section begins at its heading: its number is among its first
    # three lines (multi-line headings center the number between title lines).
    bad = conn.execute("""
        SELECT s.number FROM sections s
        WHERE s.kind IN ('chapter', 'section') AND s.number NOT IN (
            SELECT l.text FROM lines l WHERE l.section_id = s.id ORDER BY l.seq LIMIT 3)
    """).fetchall()
    assert bad == []
    # No outline mojibake survives.
    assert conn.execute("SELECT count(*) FROM sections WHERE title LIKE '%â€%'").fetchone()[0] == 0
    # Body text normalizes to 1.0 in every chapter.
    off = conn.execute("""
        SELECT s.number FROM sections s WHERE s.kind = 'chapter' AND 1.0 != (
            SELECT l.rel_size FROM lines l WHERE l.section_id IN
              (SELECT id FROM sections WHERE id = s.id OR parent_id = s.id)
            GROUP BY l.rel_size ORDER BY sum(length(l.text)) DESC LIMIT 1)
    """).fetchall()
    assert off == []
