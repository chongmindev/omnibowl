"""Clue packs for broad answers ("eye", "sleep") that have no verified rewrite yet.

uv run python tools/broad_packs.py [--book language_in_mind] [--size 20]

Drafts for broad answers came out thin: assemble picks clues hardest first, and for a
broad answer the hardest sentences are mostly passing mentions. A pack instead takes the
sentences where the answer is most central (topicality from `assemble`, the answer as
subject beats a passing mention; mentions inside compounds or under modifiers are left
out), at most `--per-chapter` per chapter, up to `--max-clues`, ordered hardest first. The
rewrite session picks the 4 to 6 best (decision "broad answers: clue packs", option A).

Only answers that reach `broad_answer` through the book's own topics (glossary, index,
headings) are packed: the keyness-only route produced no usable tossups in batches 16-19.
Pack ids equal the draft ids, so a passing rewrite replaces an earlier skip.
Appends data/rewrites/batch_NN.json after the existing batches (gitignored: book text).
"""

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import spacy

from omnibowl import candidates, config
from omnibowl.assemble import bad_mention, topicality
from omnibowl.rank import chapter_of

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="language_in_mind")
    ap.add_argument("--size", type=int, default=20)
    ap.add_argument("--max-clues", type=int, default=12, help="sentences per pack")
    ap.add_argument("--min-clues", type=int, default=5, help="sentences a pack needs")
    ap.add_argument("--per-chapter", type=int, default=2, help="sentences per chapter")
    ap.add_argument("--min-topicality", type=float, default=0.5)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "omnibowl.db")
    args = ap.parse_args()
    cfg = config.load()
    a = cfg["assemble"]
    meta_re = re.compile("|".join(a["meta_patterns"]), re.I)
    source_re = re.compile("|".join(a["source_patterns"]))
    nlp = spacy.load(cfg["sentences"]["spacy_model"], exclude=["ner", "lemmatizer"])
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    book = conn.execute("SELECT id FROM books WHERE slug = ?", (args.book,)).fetchone()
    top = chapter_of(conn, book["id"])
    topics = candidates.book_topics(conn, book["id"], cfg["candidates"]["classes"])

    rdir = ROOT / "data" / "rewrites"
    verified = json.loads((rdir / "verified.json").read_text())
    packed = {i["id"] for f in rdir.glob("batch_*.json") for i in json.loads(f.read_text()) if i.get("pack")}

    packs = []
    for t in conn.execute(
            """SELECT t.*, m.referent, m.referent_source, m.giveaway FROM terms t
               JOIN answer_meta m ON m.term_id = t.id WHERE t.book_id = ? AND t.class = 'broad_answer'
               ORDER BY t.id""", (book["id"],)):
        tid = f"{args.book}-{t['id']}"
        forms = [t["norm"]] + [f for f, _ in json.loads(t["forms"])]
        if verified.get(tid, {}).get("ok") or tid in packed or not topics & {f.lower() for f in forms}:
            continue
        spans = defaultdict(list)
        for r in conn.execute("SELECT sentence_id, start_char, end_char FROM mentions WHERE term_id = ?", (t["id"],)):
            spans[r["sentence_id"]].append((r["start_char"], r["end_char"]))
        rows = conn.execute(
            """SELECT json_extract(c.sentence_ids, '$[0]') AS sid, MAX(cs.difficulty) AS d, s.text, s.unit,
                      s.ord, s.page_start, s.section_id
               FROM clues c JOIN clue_scores cs ON cs.clue_id = c.id
               JOIN sentences s ON s.id = json_extract(c.sentence_ids, '$[0]')
               WHERE c.term_id = ? AND c.via = 'named' AND s.kind != 'definition' GROUP BY sid""",
            (t["id"],)).fetchall()
        rows = [r for r in rows if not meta_re.search(r["text"]) and not source_re.search(r["text"])]
        scored = []
        for r, doc in zip(rows, nlp.pipe(r["text"] for r in rows)):
            ok = [(x, y) for x, y in spans[r["sid"]] if not bad_mention(doc, x, y)]
            if not ok:
                continue
            score = max(topicality(doc, x, y, a) for x, y in ok)
            if score >= args.min_topicality:
                scored.append((score, r["d"], r))
        scored.sort(key=lambda s: (-s[0], -s[1]))
        picked, per_ch = [], Counter()
        for score, d, r in scored:
            ch = top(r["section_id"])
            if per_ch[ch] >= args.per_chapter:
                continue
            per_ch[ch] += 1
            picked.append((d, r))
            if len(picked) >= args.max_clues:
                break
        if len(picked) < args.min_clues:
            continue
        clues = []
        for d, r in sorted(picked, key=lambda p: -p[0]):  # hardest first
            prev = conn.execute("SELECT text FROM sentences WHERE book_id = ? AND unit = ? AND ord = ?",
                                (book["id"], r["unit"], r["ord"] - 1)).fetchone()
            clues.append({"source": r["text"], "previous": prev[0] if prev else "", "page": r["page_start"]})
        packs.append({
            "id": tid,
            "answer": t["display"],
            "accept": sorted({f for f, _ in json.loads(t["forms"])} - {t["display"]}),
            "kind": t["kind"],
            "referent": t["referent"],
            "referent_is_generic": t["referent_source"] == "generic",
            "definition": t["giveaway"],
            "broad": True,
            "pack": True,
            "draft": None,
            "clues": clues,
        })
    start = max([int(p.stem.split("_")[1]) for p in rdir.glob("batch_*.json")] or [0]) + 1
    n = 0
    for i in range(0, len(packs), args.size):
        (rdir / f"batch_{start + n:02d}.json").write_text(json.dumps(packs[i:i + args.size], ensure_ascii=False, indent=1))
        n += 1
    print(f"{len(packs)} broad packs -> " + (f"batches {start:02d}-{start + n - 1:02d}" if n else "no new batches"))
    for p in packs:
        print(f"  {p['answer']}: {len(p['clues'])} sentences")


if __name__ == "__main__":
    main()
