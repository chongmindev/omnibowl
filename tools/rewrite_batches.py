"""Export the draft pool as compact batch files for the v1 hand-assisted rewrite.

uv run python -m omnibowl.run --stage assemble --pool --book data/books/<book>.pdf
uv run python tools/rewrite_batches.py [--book language_in_mind] [--size 20]

Appends data/rewrites/batch_NN.json after the existing batches, for tossups not yet in
any batch (gitignored: it holds book text). Each tossup has its
draft, answer, accepted forms, referent, definition, and each clue's source sentence with
the sentence before it, which is everything the rewrite needs and nothing more.
See docs/rewrite-v1.md.
"""

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="language_in_mind")
    ap.add_argument("--size", type=int, default=20)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "omnibowl.db")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    book = conn.execute("SELECT id FROM books WHERE slug = ?", (args.book,)).fetchone()
    out_dir = ROOT / "data" / "rewrites"
    out_dir.mkdir(parents=True, exist_ok=True)

    items = []
    for t in conn.execute("SELECT * FROM tossups WHERE book_id = ? ORDER BY number", (book["id"],)):
        term = conn.execute(
            """SELECT t.display, t.kind, t.class, t.forms, m.referent, m.referent_source, m.giveaway
               FROM terms t JOIN answer_meta m ON m.term_id = t.id WHERE t.id = ?""", (t["term_id"],)).fetchone()
        clues = []
        for cid in json.loads(t["clue_ids"]):
            c = conn.execute(
                """SELECT c.text, s.text AS sentence, s.unit, s.ord, s.page_start FROM clues c
                   JOIN sentences s ON s.id = json_extract(c.sentence_ids, '$[0]') WHERE c.id = ?""", (cid,)).fetchone()
            prev = conn.execute("SELECT text FROM sentences WHERE book_id = ? AND unit = ? AND ord = ?",
                                (book["id"], c["unit"], c["ord"] - 1)).fetchone()
            clues.append({"source": c["sentence"], "previous": prev[0] if prev else "", "page": c["page_start"]})
        items.append({
            "id": t["id"],
            "answer": term["display"],
            "accept": sorted({f for f, _ in json.loads(term["forms"])} - {term["display"]}),
            "kind": term["kind"],
            "referent": term["referent"],
            "referent_is_generic": term["referent_source"] == "generic",
            "definition": term["giveaway"],
            # Broad answers ("eye") take one clue per chapter; the rewrite treats them as
            # a tour of the book rather than one topic.
            "broad": term["class"] == "broad_answer",
            "draft": t["question"],
            "clues": clues,
        })
    # Append only: existing batches (and their out_NN.json rewrites) are never renumbered.
    done = {i["id"] for f in out_dir.glob("batch_*.json") for i in json.loads(f.read_text())}
    items = [i for i in items if i["id"] not in done]
    start = max([int(p.stem.split("_")[1]) for p in out_dir.glob("batch_*.json")] or [0]) + 1
    n = 0
    for i in range(0, len(items), args.size):
        (out_dir / f"batch_{start + n:02d}.json").write_text(json.dumps(items[i:i + args.size], ensure_ascii=False, indent=1))
        n += 1
    print(f"{len(items)} new tossups -> " + (f"batches {start:02d}-{start + n - 1:02d}" if n else "no new batches"))


if __name__ == "__main__":
    main()
