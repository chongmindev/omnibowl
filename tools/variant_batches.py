"""Clue packs for 2nd and 3rd tossups on answers whose first tossup passed (v1 rewrite).

uv run python tools/variant_batches.py [--book language_in_mind] [--size 20] [--max-extra 2]

For each answer whose first rewrite passed verification, the clue sentences it did not
use are sorted by difficulty and dealt alternately into up to `--max-extra` packs, so
every pack spans hard to easy and no two tossups on the same answer share a sentence.
Packs use all of the answer's clue sentences (broader than the strict assemble rules);
the rewrite session shapes them. Each pack carries the first tossup's text so the
rewrite avoids repeating its facts. The answer's own definition may end every version.

Writes data/rewrites/batch_NN.json after the existing batches (gitignored: book text).
Pack ids are "<first tossup id>-v2", "-v3". See docs/rewrite-v1.md.
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
    ap.add_argument("--max-extra", type=int, default=2)
    ap.add_argument("--min-clues", type=int, default=5, help="clue sentences a pack needs")
    ap.add_argument("--max-clues", type=int, default=8, help="clue sentences per pack")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "omnibowl.db")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    book = conn.execute("SELECT id FROM books WHERE slug = ?", (args.book,)).fetchone()
    rdir = ROOT / "data" / "rewrites"
    verified = json.loads((rdir / "verified.json").read_text())
    firsts = {}
    for out in sorted(rdir.glob("out_*.json")):
        for r in json.loads(out.read_text()):
            if verified.get(r["id"], {}).get("ok") and "-v" not in r["id"]:
                firsts[r["id"]] = r["question"]
    existing = {json.loads(p.read_text())[0]["id"].rsplit("-v", 1)[0] for p in rdir.glob("batch_*.json")
                if json.loads(p.read_text()) and "-v" in json.loads(p.read_text())[0]["id"]}

    packs = []
    for tid, first_text in firsts.items():
        if tid in existing:
            continue
        t = conn.execute("SELECT * FROM tossups WHERE id = ?", (tid,)).fetchone()
        term = conn.execute(
            """SELECT t.display, t.kind, t.forms, m.referent, m.referent_source, m.giveaway
               FROM terms t JOIN answer_meta m ON m.term_id = t.id WHERE t.id = ?""", (t["term_id"],)).fetchone()
        used = {json.loads(conn.execute("SELECT sentence_ids FROM clues WHERE id = ?", (cid,)).fetchone()[0])[0]
                for cid in json.loads(t["clue_ids"])}
        rows = conn.execute(
            """SELECT json_extract(c.sentence_ids, '$[0]') AS sid, MAX(cs.difficulty) AS d, s.text, s.unit,
                      s.ord, s.page_start, s.kind
               FROM clues c JOIN clue_scores cs ON cs.clue_id = c.id
               JOIN sentences s ON s.id = json_extract(c.sentence_ids, '$[0]')
               WHERE c.term_id = ? AND s.kind != 'definition' GROUP BY sid ORDER BY d DESC""",
            (t["term_id"],)).fetchall()
        rest = [r for r in rows if r["sid"] not in used]
        n_packs = min(args.max_extra, len(rest) // args.min_clues)
        if n_packs < 1:
            continue
        deals = [rest[k::n_packs][:args.max_clues] for k in range(n_packs)]  # hard..easy in each
        for k, deal in enumerate(deals):
            if len(deal) < args.min_clues:
                continue
            clues = []
            for r in deal:
                prev = conn.execute("SELECT text FROM sentences WHERE book_id = ? AND unit = ? AND ord = ?",
                                    (book["id"], r["unit"], r["ord"] - 1)).fetchone()
                clues.append({"source": r["text"], "previous": prev[0] if prev else "", "page": r["page_start"]})
            packs.append({
                "id": f"{tid}-v{k + 2}",
                "answer": term["display"],
                "accept": sorted({f for f, _ in json.loads(term["forms"])} - {term["display"]}),
                "kind": term["kind"],
                "referent": term["referent"],
                "referent_is_generic": term["referent_source"] == "generic",
                "definition": term["giveaway"],
                "draft": None,
                "first_tossup": first_text,
                "clues": clues,
                "packet": t["packet_json"],
                "sections": t["section_ids"],
            })
    start = max([int(p.stem.split("_")[1]) for p in rdir.glob("batch_*.json")] or [0]) + 1
    n = 0
    for i in range(0, len(packs), args.size):
        (rdir / f"batch_{start + n:02d}.json").write_text(
            json.dumps(packs[i:i + args.size], ensure_ascii=False, indent=1))
        n += 1
    print(f"{len(packs)} packs for {len({p['id'].rsplit('-v', 1)[0] for p in packs})} answers "
          f"-> batches {start:02d}-{start + n - 1:02d}")


if __name__ == "__main__":
    main()
