"""Fill the prototype player with generated tossups from the database.

uv run python tools/play.py [--book language_in_mind] && open data/play/index.html

The output holds textbook text, so it is written to data/ (gitignored) and stays local.
Answer judging in the prototype is a local match against the answerline and its
accepted alternates (normalized); prompts are not generated yet.
"""

import argparse
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def norm(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s).lower().replace("’", "").replace("'", "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="language_in_mind")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "omnibowl.db")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    book = conn.execute("SELECT * FROM books WHERE slug = ?", (args.book,)).fetchone()
    chapters = [[int(r["number"]), r["title"]] for r in conn.execute(
        "SELECT number, title FROM sections WHERE book_id = ? AND kind = 'chapter' ORDER BY ord", (book["id"],))
        if r["number"].isdigit()]
    secnum = {r["id"]: r["number"] or "" for r in conn.execute(
        "SELECT id, number FROM sections WHERE book_id = ?", (book["id"],))}
    # v1 hand-assisted rewrites (docs/rewrite-v1.md): used when they passed verification.
    rewrites, rdir = {}, ROOT / "data" / "rewrites"
    verified = json.loads((rdir / "verified.json").read_text()) if (rdir / "verified.json").exists() else {}
    for out in sorted(rdir.glob("out_*.json")):
        for r in json.loads(out.read_text()):
            if verified.get(r["id"], {}).get("ok"):
                rewrites[r["id"]] = r["question"]
    tossups = []
    for t in conn.execute("SELECT * FROM tossups WHERE book_id = ? ORDER BY number", (book["id"],)):
        term = conn.execute("SELECT display, forms FROM terms WHERE id = ?", (t["term_id"],)).fetchone()
        forms = [term["display"]] + [f for f, _ in json.loads(term["forms"])]
        accept = sorted({norm(f) for f in forms} | {norm(f).rstrip("s") for f in forms}
                        | {norm(f).split()[-1] for f in forms if len(norm(f).split()) == 2 and term["display"][0].isupper()})
        packet = json.loads(t["packet_json"] or "{}")
        secs = [secnum.get(s, "") for s in json.loads(t["section_ids"])]
        tossups.append({"ch": packet.get("number") or 0, "sec": secs[0] if secs else "",
                        "secs": ", ".join(s for s in secs if s) or "the book",
                        "q": rewrites.get(t["id"], t["question"]), "a": t["answer"],
                        "acc": [a for a in accept if a], "prompt": [], "rw": t["id"] in rewrites})
    page = (ROOT / "tools" / "play_template.html").read_text()
    page = (page.replace("__CHAPTERS__", json.dumps(chapters, ensure_ascii=False))
                .replace("__TOSSUPS__", json.dumps(tossups, ensure_ascii=False))
                .replace("__COUNT__", str(len(tossups))))
    out = ROOT / "data" / "play" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    print(f"wrote {out} ({len(tossups)} tossups, {sum(x['rw'] for x in tossups)} rewritten)")


if __name__ == "__main__":
    main()
