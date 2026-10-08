"""Where labeled terms land among the candidate classes, and how many of each class are
labeled. uv run python eval/candidate_classes.py [--book language_in_mind]"""

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import spacy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
from candidate_ranking import label_keys  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "omnibowl.db")
    ap.add_argument("--book", default="language_in_mind")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    bid = conn.execute("SELECT id FROM books WHERE slug = ?", (args.book,)).fetchone()[0]
    concepts, people = label_keys(conn, bid, spacy.load("en_core_web_sm", exclude=["ner", "parser"]))
    terms = list(conn.execute("SELECT kind, norm, class FROM terms WHERE book_id = ?", (bid,)))
    lab = lambda t: (t["norm"] in people) if t["kind"] == "person" else (t["norm"] in concepts)
    print(f"== {args.book}")
    print(f"  {'class':<11}{'terms':>7}{'labeled':>9}{'share':>8}")
    for cls in ("answer", "clue_only", "too_broad", "not_term"):
        ts = [t for t in terms if t["class"] == cls]
        n = sum(map(lab, ts))
        print(f"  {cls:<11}{len(ts):>7}{n:>9}{n / max(1, len(ts)):>8.0%}")
    found = Counter(t["class"] for t in terms if t["kind"] == "concept" and t["norm"] in concepts)
    total = len(concepts)
    print(f"  labeled concept terms by class (of {total}): " +
          ", ".join(f"{c} {found[c]}" for c in ("answer", "clue_only", "too_broad", "not_term")) +
          f", not found {total - sum(found.values())}")


if __name__ == "__main__":
    main()
