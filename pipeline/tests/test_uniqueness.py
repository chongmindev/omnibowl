from pathlib import Path

from omnibowl import config, db, uniqueness


def test_tokens_drop_stopwords_and_plurals():
    assert uniqueness.tokens("The babies' babbling of syllables", 3) == ["babie", "babbling", "syllable"]


def _book(tmp_path: Path):
    conn = db.connect(tmp_path / "t.db")
    conn.execute("INSERT INTO books (id, slug, path, sha256, page_count) VALUES (1, 'b', 'b.pdf', 'x', 1)")
    sents = {1: "The N400 peaks for semantically anomalous words.",
             2: "The N400 is smaller for predictable words.",
             3: "The P600 follows syntactic violations in sentences.",
             4: "The P600 appears for garden path sentences.",
             5: "Both components were recorded with electrodes on the scalp."}
    for i, text in sents.items():
        conn.execute("""INSERT INTO sentences (id, book_id, kind, unit, ord, text, line_ids, start_char, end_char,
                        page_start, page_end) VALUES (?, 1, 'body', 1, ?, ?, '[]', 0, 0, 1, 1)""", (i, i, text))
    for tid, norm in [(1, "n400"), (2, "p600")]:
        conn.execute("""INSERT INTO terms (id, book_id, kind, norm, display, n_mentions, n_sentences, n_sections,
                        forms, class) VALUES (?, 1, 'concept', ?, ?, 1, 1, 1, '[]', 'answer')""",
                     (tid, norm, norm.upper()))
    clues = [(1, 1, 1), (2, 1, 2), (3, 2, 3), (4, 2, 4), (5, 1, 5), (6, 2, 5)]
    for cid, tid, sid in clues:
        conn.execute("""INSERT INTO clues (id, book_id, term_id, sentence_ids, kind, via, shared, start_char,
                        end_char, text) VALUES (?, 1, ?, ?, 'body', 'named', 0, 0, 0, ?)""",
                     (cid, tid, f"[{sid}]", sents[sid]))
    conn.commit()
    return conn


def test_specific_clue_wins_shared_clue_does_not(tmp_path):
    conn = _book(tmp_path)
    uniqueness.run(1, conn, config.load())
    got = {r["clue_id"]: (r["rank"], r["margin"]) for r in conn.execute("SELECT * FROM clue_uniqueness")}
    assert got[1][0] == 1 and got[1][1] > 0      # "semantically anomalous words": the N400
    assert got[3][0] == 1 and got[3][1] > 0      # "syntactic violations": the P600
    assert got[5][1] <= got[1][1]                # the shared electrodes sentence is less unique
