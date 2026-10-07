"""Sentence-split accuracy against eval/sentences.tsv.

uv run python eval/sentence_accuracy.py

A judged-correct sentence counts as right if the splitter still produces exactly it; a
judged-wrong sentence counts as right once the splitter no longer produces it.
"""

import argparse
import csv
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def exists(conn, g) -> bool:
    return conn.execute(
        """SELECT 1 FROM sentences s JOIN books b ON b.id = s.book_id
           JOIN lines f ON f.id = json_extract(s.line_ids, '$[0]')
           JOIN lines l ON l.id = json_extract(s.line_ids, '$[#-1]')
           WHERE b.slug = ? AND f.lkey = ? AND s.start_char = ? AND l.lkey = ? AND s.end_char = ?""",
        (g["book"], g["start_lkey"], int(g["start_char"]), g["end_lkey"], int(g["end_char"]))).fetchone() is not None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "omnibowl.db")
    ap.add_argument("--gold", type=Path, default=ROOT / "eval" / "sentences.tsv")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    with open(args.gold) as f:
        gold = list(csv.DictReader((l for l in f if not l.startswith("#")), delimiter="\t"))
    score = Counter()
    misses = []
    for g in gold:
        ok = exists(conn, g) == (g["correct"] == "yes")
        for key in (g["book"], "ALL"):
            score[key, "n"] += 1
            score[key, "right"] += ok
        if not ok:
            misses.append(f"  {g['book']:<18} {g['start_lkey']}+{g['start_char']}  "
                          f"{'still wrong' if g['correct'] == 'no' else 'no longer produced'}  {g['note']}")
    for key in sorted({k for k, _ in score}):
        n, r = score[key, "n"], score[key, "right"]
        print(f"{key:<18} {r:>3}/{n:<3} = {r / n:.1%}")
    print("\n".join(misses))


if __name__ == "__main__":
    main()
