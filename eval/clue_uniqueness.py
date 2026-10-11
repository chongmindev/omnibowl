"""Does clue uniqueness predict which answers the v1 rewrite skipped (rule 9: too few
specific clues)? Labels come from data/rewrites/out_*.json: an answer is "written" if any
of its tossups was written, "skipped" if every one was skipped. Answer score = share of
its clues whose answer ranks first. Reports clue-level rank shares, the two groups'
mean scores, and AUC (chance that a written answer outscores a skipped one; 0.5 = no
signal).

uv run python eval/clue_uniqueness.py [--book language_in_mind]
"""

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="language_in_mind")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "omnibowl.db")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    bid = conn.execute("SELECT id FROM books WHERE slug = ?", (args.book,)).fetchone()[0]

    n, r1, r3 = conn.execute("""SELECT COUNT(*), SUM(rank = 1), SUM(rank <= 3) FROM clue_uniqueness
                                WHERE book_id = ?""", (bid,)).fetchone()
    print(f"clues {n}: answer ranks 1st {r1 / n:.0%}, top 3 {r3 / n:.0%}")
    for cls, m, a in conn.execute(
            """SELECT t.class, COUNT(*), AVG(u.rank = 1) FROM clue_uniqueness u JOIN clues c ON c.id = u.clue_id
               JOIN terms t ON t.id = c.term_id WHERE u.book_id = ? GROUP BY 1""", (bid,)):
        print(f"  {cls:13s} {m:6d} clues, ranks 1st {a:.0%}")

    score = dict(conn.execute(
        """SELECT c.term_id, AVG(u.rank = 1) FROM clue_uniqueness u JOIN clues c ON c.id = u.clue_id
           WHERE u.book_id = ? GROUP BY c.term_id""", (bid,)))
    label = {}
    for out in sorted((ROOT / "data" / "rewrites").glob("out_*.json")):
        for r in json.loads(out.read_text()):
            base = r["id"].rsplit("-v", 1)[0]
            if not base.startswith(args.book + "-"):
                continue
            tid = int(base.rsplit("-", 1)[1])
            label[tid] = label.get(tid, False) or not r.get("skip")
    pos = [score[t] for t, ok in label.items() if ok and t in score]
    neg = [score[t] for t, ok in label.items() if not ok and t in score]
    if not pos or not neg:
        print("no rewrite labels")
        return
    auc = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg))
    print(f"answers written {len(pos)}: mean {sum(pos) / len(pos):.2f}   "
          f"skipped {len(neg)}: mean {sum(neg) / len(neg):.2f}   AUC {auc:.2f}")


if __name__ == "__main__":
    main()
