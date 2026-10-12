"""Pyramidality of the played tossups (the verified v1 rewrites) under a difficulty score.

uv run python eval/pyramid.py [--book language_in_mind] [--db data/omnibowl.db] [--column difficulty]

Each rewritten sentence before the giveaway is linked to the source sentence it came from
(omnibowl.pyramid.link), and scored with the mean `--column` of clue_scores over the
answer's clues in that sentence. Two references:
  - rewrite order: the order the tossups are played in, written by the rewrite sessions
    ("hardest first" by judgment). A weak reference: it is what we are checking.
  - gold: Chong's hand order of sentences, hardest first (eval/difficulty_gold.json,
    made with eval/review_difficulty.py). The reference scores are tuned against.
Reports the share of sentence pairs a score puts the same way round as the reference
(1.0 = agrees, 0.5 = no relation) and drop-backs per tossup.
"""

import argparse
import json
import sqlite3
from pathlib import Path

from omnibowl import config
from omnibowl.pyramid import clue_sentences, dropbacks, link, order_agreement, pair_agreement

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ["difficulty", "emphasis", "specificity", "repetition", "detail", "familiarity"]


def played(book: str) -> list[dict]:
    """Verified rewrites with their sources: [{id, term_id, question, answer, sources}]."""
    ok = {k for k, v in json.loads((ROOT / "data/rewrites/verified.json").read_text()).items() if v["ok"]}
    out = []
    for bf in sorted((ROOT / "data/rewrites").glob("batch_*.json")):
        of = bf.with_name(bf.name.replace("batch_", "out_"))
        if not of.exists():
            continue
        src = {t["id"]: t for t in json.loads(bf.read_text())}
        for r in json.loads(of.read_text()):
            if r["id"] not in ok or r["id"] not in src or not r["id"].startswith(book + "-"):
                continue
            tid = int(r["id"].rsplit("-v", 1)[0].rsplit("-", 1)[1])
            out.append({"id": r["id"], "term_id": tid, "question": r["question"], "answer": src[r["id"]]["answer"],
                        "sources": [c["source"] for c in src[r["id"]]["clues"]]})
    return out


def scored_sentences(conn, t: dict, column: str, cfg: dict) -> dict[int, float]:
    """{index of a clue sentence in the question: score} for the sentences that link to a
    source the answer has clues in."""
    p, stop = cfg["pyramid"], set(cfg["rank"]["specificity_stopwords"])
    sents, _ = clue_sentences(t["question"])
    links = link(sents, t["sources"], stop, p["link_word_prefix"], p["link_min_share"])
    group = [t["term_id"]] + [r[0] for r in conn.execute(
        "SELECT term_id FROM term_variants WHERE head_id = ?", (t["term_id"],))]
    out = {}
    for i, j in enumerate(links):
        if j is None:
            continue
        sid = conn.execute("SELECT id FROM sentences WHERE text = ?", (t["sources"][j],)).fetchone()
        if not sid:
            continue
        row = conn.execute(
            f"""SELECT AVG(s.{column}) FROM clues c JOIN clue_scores s ON s.clue_id = c.id
                WHERE c.term_id IN ({','.join('?' * len(group))})
                AND EXISTS (SELECT 1 FROM json_each(c.sentence_ids) WHERE value = ?)""",
            (*group, sid[0])).fetchone()
        if row[0] is not None:
            out[i] = row[0]
    return out


def evaluate(conn, tossups: list[dict], column: str, cfg: dict, gold: dict) -> dict:
    agree, drops, gold_agree, n_sents, n_scored = [], [], [], 0, 0
    for t in tossups:
        scores = scored_sentences(conn, t, column, cfg)
        n_sents += len(clue_sentences(t["question"])[0])
        n_scored += len(scores)
        seq = [scores[i] for i in sorted(scores)]
        if (a := pair_agreement(seq)) is not None:
            agree.append(a)
            drops.append(dropbacks(seq, cfg["pyramid"]["dropback_eps"]))
        if t["id"] in gold and (g := order_agreement(scores, gold[t["id"]])) is not None:
            gold_agree.append(g)
    mean = lambda xs: sum(xs) / len(xs) if xs else None
    return {"tossups": len(tossups), "sentences": n_sents, "scored": n_scored,
            "rewrite_agree": mean(agree), "dropbacks": mean(drops),
            "gold_tossups": len(gold_agree), "gold_agree": mean(gold_agree)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="language_in_mind")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "omnibowl.db")
    ap.add_argument("--column", choices=COLUMNS + ["all"], default="all")
    args = ap.parse_args()
    cfg = config.load()
    conn = sqlite3.connect(args.db)
    have = {r[1] for r in conn.execute("PRAGMA table_info(clue_scores)")}
    gold_path = ROOT / cfg["pyramid"]["gold_file"]
    gold = json.loads(gold_path.read_text())["tossups"] if gold_path.exists() else {}
    tossups = played(args.book)
    cols = [c for c in COLUMNS if c in have] if args.column == "all" else [args.column]
    print(f"{args.db.name}: {len(tossups)} played tossups, gold orders for {len(gold)}")
    fmt = lambda x: "  -  " if x is None else f"{x:.2f}"
    print(f"{'score':12s} {'vs rewrite':>10s} {'drop-backs':>10s} {'vs gold':>8s}   sentences scored")
    for c in cols:
        r = evaluate(conn, tossups, c, cfg, gold)
        print(f"{c:12s} {fmt(r['rewrite_agree']):>10s} {fmt(r['dropbacks']):>10s} {fmt(r['gold_agree']):>8s}"
              f"   {r['scored']}/{r['sentences']}")


if __name__ == "__main__":
    main()
