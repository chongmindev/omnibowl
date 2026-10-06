"""Block-tagging accuracy against eval/parse_tags.tsv, per book and overall.

uv run python eval/tag_accuracy.py [--db data/omnibowl.db]
"""

import argparse
import csv
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_gold(path: Path) -> list[dict]:
    with open(path) as f:
        return list(csv.DictReader((l for l in f if not l.startswith("#")), delimiter="\t"))


def predicted(conn, book: str, lkey: str) -> str | None:
    row = conn.execute("""SELECT l.kind FROM lines l JOIN books b ON b.id = l.book_id
                          WHERE b.slug = ? AND l.lkey = ?""", (book, lkey)).fetchone()
    return row[0] if row else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "omnibowl.db")
    ap.add_argument("--gold", type=Path, default=ROOT / "eval" / "parse_tags.tsv")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)

    by = defaultdict(Counter)  # (book, sample) -> right/total
    errors = Counter()
    missing = 0
    for g in load_gold(args.gold):
        p = predicted(conn, g["book"], g["lkey"])
        if p is None:
            missing += 1
            continue
        for key in ((g["book"], g["sample"]), (g["book"], "all"), ("ALL", "all")):
            by[key]["n"] += 1
            by[key]["right"] += p == g["gold"]
        if p != g["gold"]:
            errors[(g["book"], g["gold"], p)] += 1
    for (book, sample), c in sorted(by.items()):
        print(f"{book:<20} {sample:<4} {c['right']:>3}/{c['n']:<3} = {c['right'] / c['n']:.1%}")
    if missing:
        print(f"missing keys: {missing}")
    for (book, g, p), v in sorted(errors.items()):
        print(f"  {book:<20} gold={g:<11} pred={p:<11} x{v}")


if __name__ == "__main__":
    main()
