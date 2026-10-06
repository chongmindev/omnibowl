from pathlib import Path

import pymupdf
import pytest

from omnibowl import config, db

BOOK = config.ROOT / "data" / "books" / "language_in_mind.pdf"


@pytest.fixture
def cfg():
    return config.load()


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture
def tiny_pdf(tmp_path) -> Path:
    """Four pages: front matter, a chapter with one section and a panel, a glossary.

    Chapter 1 uses a smaller body font than chapter 2 to exercise per-chapter scaling.
    """
    doc = pymupdf.open()
    for _ in range(4):
        doc.new_page()
    doc[0].insert_text((72, 100), "Front matter", fontsize=12)

    p = doc[1]
    p.insert_text((72, 80), "1", fontsize=20)
    p.insert_text((72, 120), "Body line one", fontsize=12)
    p.insert_text((72, 140), "Body line two", fontsize=12)
    p.insert_text((72, 300), "1.1", fontsize=20)
    p.insert_text((72, 340), "Section body", fontsize=12)
    p.draw_rect(pymupdf.Rect(60, 400, 500, 500), fill=(0.97, 0.9, 0.86), color=None)
    p.insert_text((72, 430), "Panel text", fontsize=9)
    p.insert_text((72, 460), "glossterm", fontsize=9)
    p.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(70, 448, 140, 464),
                   "page": 3, "to": pymupdf.Point(72, 80)})

    p = doc[2]
    p.insert_text((72, 80), "2", fontsize=20)
    for i in range(5):
        p.insert_text((72, 120 + 20 * i), f"Bigger body {i}", fontsize=15)

    doc[3].insert_text((72, 80), "Glossary", fontsize=20)

    doc.set_toc([
        [1, "Preface", 1, {"kind": pymupdf.LINK_GOTO, "page": 0, "to": pymupdf.Point(72, 80)}],
        [1, "1 Speakerâ€™s Chapter", 2, {"kind": pymupdf.LINK_GOTO, "page": 1, "to": pymupdf.Point(72, 60)}],
        [2, "1.1 First Section", 2, {"kind": pymupdf.LINK_GOTO, "page": 1, "to": pymupdf.Point(72, 280)}],
        [1, "2 Second Chapter", 3, {"kind": pymupdf.LINK_GOTO, "page": 2, "to": pymupdf.Point(72, 60)}],
        [1, "Glossary", 4, {"kind": pymupdf.LINK_GOTO, "page": 3, "to": pymupdf.Point(72, 60)}],
    ])
    path = tmp_path / "tiny.pdf"
    doc.save(path)
    return path
