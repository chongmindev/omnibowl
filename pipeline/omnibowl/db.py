"""SQLite schema and connection. One file, data/omnibowl.db.

Each stage owns a set of tables and clears its own rows for a book before
rewriting them, so any stage can be rerun alone.
"""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    title       TEXT,
    path        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    page_count  INTEGER NOT NULL,
    page_width  REAL,
    page_height REAL
);

-- Outline entries. kind: chapter | section | front_matter | back_matter.
-- Start is (page, y) from the outline destination; end is the next entry's start.
CREATE TABLE IF NOT EXISTS sections (
    id          INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(id),
    parent_id   INTEGER REFERENCES sections(id),
    level       INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    number      TEXT,
    title       TEXT NOT NULL,
    page_start  INTEGER NOT NULL,
    y_start     REAL NOT NULL,
    ord         INTEGER NOT NULL
);

-- Measurement layer: every text line with its layout features. No classification.
-- page is the 1-based PDF page. seq is reading order within the book.
-- rel_size is size / body size of the enclosing level-1 outline entry.
CREATE TABLE IF NOT EXISTS lines (
    id          INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(id),
    section_id  INTEGER REFERENCES sections(id),
    seq         INTEGER NOT NULL,
    page        INTEGER NOT NULL,
    x0 REAL, y0 REAL, x1 REAL, y1 REAL,
    size        REAL NOT NULL,
    rel_size    REAL NOT NULL,
    bold_start  INTEGER NOT NULL,  -- first span is bold
    bold_frac   REAL NOT NULL,     -- share of characters in bold
    italic_frac REAL NOT NULL,
    panel_id    INTEGER,           -- light-filled drawing containing the line, if any
    text        TEXT NOT NULL,
    spans_json  TEXT NOT NULL,     -- [[text, size, flags], ...] for later tagging
    lkey        TEXT NOT NULL,     -- stable key "page:y0:x0" (rounded) for eval files
    -- Written by tagging (parse step 2).
    kind        TEXT,              -- see blocks.kind
    feature     TEXT,              -- e.g. "BOX 3.1", "DIGGING DEEPER", "Questions to Contemplate"
    block_id    INTEGER
);
CREATE INDEX IF NOT EXISTS lines_lkey ON lines(book_id, lkey);
CREATE INDEX IF NOT EXISTS lines_book_seq ON lines(book_id, seq);

-- Light-filled drawings (box backgrounds). Coordinates may run off the page:
-- the ebook was sliced from long scrolls, so one panel spans several pages.
CREATE TABLE IF NOT EXISTS panels (
    id          INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(id),
    page        INTEGER NOT NULL,
    x0 REAL, y0 REAL, x1 REAL, y1 REAL,
    fill        TEXT NOT NULL,
    stroke      TEXT
);

-- Internal links. target_page is 1-based. line_id is the line the link sits on.
CREATE TABLE IF NOT EXISTS links (
    id          INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(id),
    page        INTEGER NOT NULL,
    line_id     INTEGER REFERENCES lines(id),
    x0 REAL, y0 REAL, x1 REAL, y1 REAL,
    target_page INTEGER,
    uri         TEXT,
    text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS links_book_page ON links(book_id, page);

-- Maximal runs of lines with the same kind, feature, and section.
-- kind: heading | body | box | table | caption | definition | exercise | example |
--       dropcap | furniture | front_matter | back_matter | other
CREATE TABLE IF NOT EXISTS blocks (
    id          INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(id),
    section_id  INTEGER REFERENCES sections(id),
    kind        TEXT NOT NULL,
    feature     TEXT,
    seq_start   INTEGER NOT NULL,
    seq_end     INTEGER NOT NULL,
    page_start  INTEGER NOT NULL,
    page_end    INTEGER NOT NULL
);

-- Sentences in reading order. unit groups sentences from one stitched stream of text
-- (body prose of a section, one box, one definition, ...). line_ids is a JSON list
-- of the lines the sentence came from, for grounding.
CREATE TABLE IF NOT EXISTS sentences (
    id          INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(id),
    section_id  INTEGER REFERENCES sections(id),
    kind        TEXT NOT NULL,
    feature     TEXT,
    unit        INTEGER NOT NULL,
    ord         INTEGER NOT NULL,
    text        TEXT NOT NULL,
    line_ids    TEXT NOT NULL,
    start_char  INTEGER NOT NULL,  -- offset of the first character in the first line
    end_char    INTEGER NOT NULL,  -- offset just past the last character in the last line
    page_start  INTEGER NOT NULL,
    page_end    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sentences_book ON sentences(book_id, unit, ord);

-- Glossary and index entries from the back matter: labels for evaluating candidates,
-- never a source of clues. role: glossary | subject_index | author_index.
-- target_pages is a JSON list of 1-based PDF pages from the entry's links. in_text is
-- 1 when the term occurs in the chapters' sentences (filters e.g. a bilingual
-- glossary's other language).
CREATE TABLE IF NOT EXISTS labels (
    id          INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(id),
    role        TEXT NOT NULL,
    term        TEXT NOT NULL,
    parent_id   INTEGER REFERENCES labels(id),
    definition  TEXT,
    target_pages TEXT NOT NULL,
    in_text     INTEGER NOT NULL,
    line_id     INTEGER NOT NULL REFERENCES lines(id)
);
CREATE INDEX IF NOT EXISTS labels_book ON labels(book_id, role);

-- Candidate answers (stage: candidates). kind: concept | person. norm groups surface
-- forms; forms is a JSON list of [surface, count]. Scores and class are filled by the
-- scoring step.
CREATE TABLE IF NOT EXISTS terms (
    id          INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(id),
    kind        TEXT NOT NULL,
    norm        TEXT NOT NULL,
    display     TEXT NOT NULL,
    n_mentions  INTEGER NOT NULL,
    n_sentences INTEGER NOT NULL,
    n_sections  INTEGER NOT NULL,
    forms       TEXT NOT NULL,
    keyness     REAL,
    keyness_kind TEXT,             -- log_ratio (one word, people) | local_mi (multi-word)
    coverage    REAL,              -- distinct sentences mentioning the term
    n_chapters  INTEGER,           -- distinct chapters mentioning it
    spread      REAL,              -- n_chapters / chapters in the book
    class       TEXT,
    UNIQUE (book_id, kind, norm)
);

-- Every place a term appears: sentence and character offsets within its text.
CREATE TABLE IF NOT EXISTS mentions (
    id          INTEGER PRIMARY KEY,
    book_id     INTEGER NOT NULL REFERENCES books(id),
    term_id     INTEGER NOT NULL REFERENCES terms(id),
    sentence_id INTEGER NOT NULL REFERENCES sentences(id),
    start_char  INTEGER NOT NULL,
    end_char    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS mentions_term ON mentions(term_id);
CREATE INDEX IF NOT EXISTS mentions_sentence ON mentions(sentence_id);
"""

PARSE_TABLES = ["mentions", "terms", "labels", "sentences", "blocks", "links", "lines", "panels", "sections"]
TAG_TABLES = ["blocks"]
SENTENCE_TABLES = ["sentences"]
LABEL_TABLES = ["labels"]
CANDIDATE_TABLES = ["mentions", "terms"]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def clear(conn: sqlite3.Connection, book_id: int, tables: list[str]) -> None:
    for t in tables:
        conn.execute(f"DELETE FROM {t} WHERE book_id = ?", (book_id,))
