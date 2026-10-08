"""How well a candidate ranking puts labeled terms (glossary, subject index, authors)
at the top: precision at k, for one-word terms and multi-word terms separately.

uv run python eval/candidate_ranking.py [--book language_in_mind]

Compares the stored keyness against baselines and alternative phrase scores, all
computed from the terms table. Labels are used only to grade, never to rank.
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

KS = (50, 100, 250, 500)


def label_keys(conn, bid, nlp) -> tuple[set, set]:
    terms = [r[0] for r in conn.execute(
        "SELECT term FROM labels WHERE book_id = ? AND role IN ('glossary','subject_index') AND in_text = 1",
        (bid,))]
    clean = [c for t in terms if (c := re.sub(r"\s*\([^)]*\)\s*$", "", t).strip())]
    concepts = {concept_key(d) for d in nlp.pipe(clean)}
    people = {r[0].split(",")[0].strip().lower().split()[-1] for r in conn.execute(
        "SELECT term FROM labels WHERE book_id = ? AND role = 'author_index'", (bid,))}
    return concepts, people


def prec(ranked, is_label) -> str:
    out = []
    for k in KS:
        top = ranked[:k]
        out.append(f"P@{k} {sum(map(is_label, top)) / max(1, len(top)):.0%}")
    return "  ".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "omnibowl.db")
    ap.add_argument("--book", default="language_in_mind")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    bid = conn.execute("SELECT id FROM books WHERE slug = ?", (args.book,)).fetchone()[0]
    nlp = spacy.load("en_core_web_sm", exclude=["ner", "parser"])
    concepts, people = label_keys(conn, bid, nlp)
    terms = [dict(r) for r in conn.execute("SELECT * FROM terms WHERE book_id = ?", (bid,))]
    is_label = lambda t: (t["norm"] in people) if t["kind"] == "person" else (t["norm"] in concepts)

    one = [t for t in terms if t["keyness_kind"] == "log_ratio"]
    multi = [t for t in terms if t["keyness_kind"] == "local_mi"]
    print(f"== {args.book}: {len(one)} one-word/people, {len(multi)} multi-word")
    print("one-word and people")
    print(f"  by mentions (baseline)  {prec(sorted(one, key=lambda t: -t['n_mentions']), is_label)}")
    print(f"  by log ratio            {prec(sorted(one, key=lambda t: -t['keyness']), is_label)}")
    print("multi-word")
    n = lambda t: t["n_mentions"]
    pmi = lambda t: t["keyness"] / t["n_mentions"]  # stored keyness is local MI = n x PMI
    variants = {
        "by mentions (baseline)": n,
        "by PMI": pmi,
        "by local MI (stored)": lambda t: t["keyness"],
    }
    for name, f in variants.items():
        print(f"  {name:<24}{prec(sorted(multi, key=lambda t: -f(t)), is_label)}")


if __name__ == "__main__":
    main()
