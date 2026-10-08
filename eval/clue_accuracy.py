"""Clue precision and recall against eval/clues.tsv (sentence level).

uv run python eval/clue_accuracy.py [--by-answer]

The gold pool per answer mixes a sample of predicted clue sentences (P) and unpredicted
neighbors of mentions (N), so both numbers are estimates over that pool:
  precision = gold-yes among pool sentences the stage now predicts;
  recall    = predicted among gold-yes pool sentences.
"""

import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def predicted(conn, book: str, norm: str) -> set[str]:
    rows = conn.execute(
        """SELECT DISTINCT json_extract(c.sentence_ids, '$[0]') FROM clues c
           JOIN terms t ON t.id = c.term_id JOIN books b ON b.id = t.book_id
           WHERE b.slug = ? AND t.norm = ? AND t.class = 'answer'""", (book, norm)).fetchall()
    keys = set()
    for (sid,) in rows:
        r = conn.execute("""SELECT (SELECT lkey FROM lines WHERE id = json_extract(s.line_ids, '$[0]')),
                                   s.start_char FROM sentences s WHERE s.id = ?""", (sid,)).fetchone()
        keys.add(f"{r[0]}+{r[1]}")
    return keys


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "omnibowl.db")
    ap.add_argument("--gold", type=Path, default=ROOT / "eval" / "clues.tsv")
    ap.add_argument("--by-answer", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    with open(args.gold) as f:
        gold = list(csv.DictReader((l for l in f if not l.startswith("#")), delimiter="\t"))
    by = defaultdict(list)
    for g in gold:
        by[g["book"], g["answer"]].append(g)
    tp = fp = fn = 0
    lines = []
    for (book, ans), gs in sorted(by.items()):
        pred = predicted(conn, book, ans)
        a_tp = sum(1 for g in gs if g["sentence"] in pred and g["gold"] == "yes")
        a_fp = sum(1 for g in gs if g["sentence"] in pred and g["gold"] == "no")
        a_fn = sum(1 for g in gs if g["sentence"] not in pred and g["gold"] == "yes")
        tp, fp, fn = tp + a_tp, fp + a_fp, fn + a_fn
        lines.append(f"  {ans:<32} P {a_tp}/{a_tp + a_fp:<3} R {a_tp}/{a_tp + a_fn}")
    p, r = tp / max(1, tp + fp), tp / max(1, tp + fn)
    print(f"precision {tp}/{tp + fp} = {p:.0%}   recall {tp}/{tp + fn} = {r:.0%}   F1 {2 * p * r / max(1e-9, p + r):.0%}")
    if args.by_answer:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
