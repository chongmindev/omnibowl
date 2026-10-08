"""How many glossary and index terms appear among the candidates (any class).

uv run python eval/candidate_recall.py [--book language_in_mind] [--class answer]

Labels are normalized like candidates (lowercase, last word lemmatized), with a
trailing parenthetical dropped: "noun phrase (NP)" -> "noun phrase". Authors match a
person candidate by surname.
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

import spacy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from omnibowl.candidates import concept_key  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "omnibowl.db")
    ap.add_argument("--book", default=None, help="book slug; default all")
    ap.add_argument("--cls", default=None, help="only count candidates of this class")
    ap.add_argument("--misses", type=int, default=0, help="print this many missed glossary terms")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    nlp = spacy.load("en_core_web_sm", exclude=["ner", "parser"])
    books = [r[0] for r in conn.execute("SELECT slug FROM books")] if not args.book else [args.book]
    for slug in books:
        bid = conn.execute("SELECT id FROM books WHERE slug = ?", (slug,)).fetchone()[0]
        where = "AND class = ?" if args.cls else ""
        params = (bid, args.cls) if args.cls else (bid,)
        concepts = {r[0] for r in conn.execute(f"SELECT norm FROM terms WHERE book_id = ? AND kind = 'concept' {where}", params)}
        people = {r[0] for r in conn.execute(f"SELECT norm FROM terms WHERE book_id = ? AND kind = 'person' {where}", params)}
        if not concepts and not people:
            continue
        print(f"== {slug}" + (f"  (class = {args.cls})" if args.cls else ""))
        for role in ("glossary", "subject_index", "author_index"):
            terms = [r[0] for r in conn.execute(
                "SELECT term FROM labels WHERE book_id = ? AND role = ? AND in_text = 1 AND parent_id IS NULL",
                (bid, role))]
            if not terms:
                continue
            if role == "author_index":
                hits = [t for t in terms if t.split(",")[0].strip().lower().split()[-1] in people]
            else:
                clean = [re.sub(r"\s*\([^)]*\)\s*$", "", t) for t in terms]
                keys = [concept_key(d[:]) if len(d) else "" for d in nlp.pipe(clean)]
                hits = [t for t, k in zip(terms, keys) if k in concepts]
                missed = [t for t, k in zip(terms, keys) if k not in concepts]
            print(f"  {role:<14} {len(hits):>5}/{len(terms):<5} = {len(hits) / len(terms):.1%}")
            if role == "glossary" and args.misses:
                print("    missed:", ", ".join(missed[:args.misses]))


if __name__ == "__main__":
    main()
