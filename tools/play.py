"""Fill the prototype player with generated tossups from the database.

uv run python tools/play.py [--book language_in_mind] && open data/play/index.html

Also writes data/play/tossups.json (book, chapters, tossups) for the multiplayer server.

The output holds textbook text, so it is written to data/ (gitignored) and stays local.
Answer judging (tools/judge.js here, server/judge.py in the server) accepts the answerline,
its alternates, small typos and inflections, and prompts on the last words of a multi-word
answer. Answerlines (decision "answer judging: leniency"): a person's surname is accepted;
only the surname and the required part are underlined ("Paul <u>Broca</u>",
"<u>test</u> phase", an optional trailing word left out); an
abbreviated answer shows its expansion, from its forms or the book's own "words (ABBR)".
"""

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from omnibowl import config
from omnibowl.assemble import answerline

ROOT = Path(__file__).resolve().parents[1]


def norm(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s).lower().replace("’", "").replace("'", "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def prompts(accept: list[str], skip: set[str]) -> list[str]:
    """P1 (decision "answer judging"): the last words of a multi-word accepted form ask for
    more ("aphasia" for "brocas aphasia"), unless they are themselves accepted."""
    out = set()
    for a in accept:
        w = a.split()
        if len(w) > 1 and not skip & set(w):
            out |= {" ".join(w[k:]) for k in range(1, len(w))}
    return sorted(out - set(accept))


ACRONYM = re.compile(r"[A-Z][A-Z0-9]{1,6}")
SMALL = {"of", "and", "the", "in", "for", "to", "a", "an", "on"}


def initials(phrase: str, split_hyphens: bool) -> str:
    words = re.split(r"[\s\-‐]+" if split_hyphens else r"\s+", phrase)
    return "".join(w[0] for w in words if w and w.lower() not in SMALL).lower()


def is_expansion(acronym: str, phrase: str) -> bool:
    return acronym.lower() in (initials(phrase, True), initials(phrase, False))


def expansion(conn, book_id: int, acronym: str, forms: list[str]) -> str | None:
    """The spelled-out form of an abbreviated answer: one of its forms whose initials
    spell it, else the most common "<words> (ACRONYM)" in the book."""
    for f in forms:
        if len(f.split()) > 1 and is_expansion(acronym, f):
            return f
    found = Counter()
    before = re.compile(r"((?:[\w’'‐-]+\s+){1,8})\(" + re.escape(acronym) + r"\)")
    for (text,) in conn.execute("SELECT text FROM sentences WHERE book_id = ? AND text LIKE ?",
                                (book_id, f"%({acronym})%")):
        for m in before.finditer(text):
            words = m.group(1).split()
            for k in range(2, len(words) + 1):
                if is_expansion(acronym, " ".join(words[-k:])):
                    found[" ".join(words[-k:])] += 1
                    break
    return found.most_common(1)[0][0] if found else None


def optional_core(display: str, jc: dict) -> str | None:
    """The required part of an answer ending in an optional word ("test" of "test phase")."""
    words = display.split()
    if len(words) > 1 and words[-1].lower() in jc["optional_tails"]:
        core = display[: display.rstrip().rfind(" ")].rstrip()
        if len(re.sub(r"[^A-Za-z]", "", core)) >= jc["optional_tail_min_letters"]:
            return core
    return None


def is_person(conn, book_id: int, display: str, kind: str) -> bool:
    """A person, or a name the book also has as a person term (a duplicate term can carry
    another kind)."""
    return kind == "person" or conn.execute(
        "SELECT 1 FROM terms WHERE book_id = ? AND kind = 'person' AND lower(display) = lower(?)",
        (book_id, display)).fetchone() is not None


def answer_group(conn, term_id: int):
    """(display, forms) of the answer a term is played as: a variant ("N400 effect",
    decision "answer variants") plays as its head, and every form in the group is accepted."""
    head = conn.execute("SELECT head_id FROM term_variants WHERE term_id = ?", (term_id,)).fetchone()
    head_id = head[0] if head else term_id
    display, kind = conn.execute("SELECT display, kind FROM terms WHERE id = ?", (head_id,)).fetchone()
    forms = []
    for r in conn.execute("SELECT forms FROM terms WHERE id = ? OR id IN "
                          "(SELECT term_id FROM term_variants WHERE head_id = ?) ORDER BY id = ? DESC, id",
                          (head_id, head_id, head_id)):
        forms += [f for f, _ in json.loads(r[0])]
    return display, kind, forms


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="language_in_mind")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "omnibowl.db")
    ap.add_argument("--all", action="store_true",
                    help="include unrewritten drafts (default: only verified rewrites, when any exist)")
    args = ap.parse_args()
    cfg = config.load()
    jc, nc = cfg["judge"], cfg["names"]
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
    # Extra tossups on the same answer ("<id>-v2", "-v3") reuse the first one's row.
    variants = {}
    for vid in rewrites:
        if "-v" in vid:
            variants.setdefault(vid.rsplit("-v", 1)[0], []).append(vid)
    rows = []
    for t in conn.execute("SELECT * FROM tossups WHERE book_id = ? ORDER BY number", (book["id"],)):
        rows.append((t["id"], t))
        rows += [(vid, t) for vid in sorted(variants.get(t["id"], []))]
    # A rewrite outlives its draft: if a later pipeline run no longer drafts (or even
    # ranks) that answer, rebuild the row's answerline and chapter from the term (ids are "<book>-<term id>").
    have = {rid for rid, _ in rows}
    for rid in sorted(rewrites):
        base = rid.rsplit("-v", 1)[0]
        if rid in have or not base.startswith(args.book + "-"):
            continue
        t = conn.execute(
            """SELECT t.id AS term_id, t.display, t.forms, s.number, s.title FROM terms t
               LEFT JOIN answer_meta m ON m.term_id = t.id LEFT JOIN sections s ON s.id = m.main_chapter
               WHERE t.id = ?""", (int(base.rsplit("-", 1)[1]),)).fetchone()
        if t is None:
            continue
        ans_html, _ = answerline(*answer_group(conn, t["term_id"])[::2])
        num = int(t["number"]) if (t["number"] or "").isdigit() else None
        rows.append((rid, {"term_id": t["term_id"], "answer": ans_html, "question": "", "section_ids": "[]",
                           "packet_json": json.dumps({"number": num})}))
    for rid, t in rows:
        if rewrites and not args.all and rid not in rewrites:
            continue
        display, kind, forms = answer_group(conn, t["term_id"])
        forms = [display] + forms
        person = is_person(conn, book["id"], display, kind)
        core = display.split()[-1] if person and len(display.split()) > 1 else optional_core(display, jc)
        expand = expansion(conn, book["id"], display, forms) if ACRONYM.fullmatch(display) else None
        # A person's surname alone is accepted ("Broca" for "Paul Broca").
        accept = sorted({norm(f) for f in forms} | {norm(f).rstrip("s") for f in forms}
                        | {norm(f).split()[-1] for f in forms if len(norm(f).split()) >= 2 and person}
                        | {norm(x) for x in (core, expand) if x})
        packet = json.loads(t["packet_json"] or "{}")
        secs = [secnum.get(s, "") for s in json.loads(t["section_ids"])]
        tossups.append({"id": rid, "ch": packet.get("number") or 0, "sec": secs[0] if secs else "",
                        "secs": ", ".join(s for s in secs if s) or "the book",
                        "q": rewrites.get(rid, t["question"]), "a": answerline(display, forms[1:], core, expand)[0],
                        "acc": [a for a in accept if a], "prompt": prompts(accept, set(jc["prompt_skip_words"])) if not person else [],
                        "rw": rid in rewrites})
    # Judging rules and the book's other terms (a typo match is refused when the guess is
    # as close to one of them), shared by the page (tools/judge.js) and the server.
    guard = sorted({norm(r[0]) for r in conn.execute(
        f"SELECT display FROM terms WHERE book_id = ? AND class IN ({','.join('?' * len(jc['guard_classes']))})",
        (book["id"], *jc["guard_classes"]))} - {""})
    judge = {k: jc[k] for k in ("ignore_words", "typo_free_below", "one_typo_up_to", "long_typos", "stem_from")} | {"terms": guard}
    # Words a player name may not contain, and the edge rule (server/names.py, nameOk in judge.js).
    names = {"blocked": [w.strip() for w in (ROOT / nc["blocked_words"]).read_text().splitlines()
                         if w.strip() and not w.startswith("#")],
             "edge_min_letters": nc["edge_min_letters"]}
    page = (ROOT / "tools" / "play_template.html").read_text()
    page = (page.replace("__JUDGE_JS__", (ROOT / "tools" / "judge.js").read_text())
                .replace("__JUDGE__", json.dumps(judge, ensure_ascii=False))
                .replace("__NAMES__", json.dumps(names, ensure_ascii=False))
                .replace("__CHAPTERS__", json.dumps(chapters, ensure_ascii=False))
                .replace("__TOSSUPS__", json.dumps(tossups, ensure_ascii=False))
                .replace("__COUNT__", str(len(tossups))))
    out = ROOT / "data" / "play" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    # The same tossups as data for the multiplayer server (server/ reads this file, never
    # the database, so pipeline reruns cannot break a game in progress).
    (out.parent / "tossups.json").write_text(json.dumps(
        {"book": book["title"], "slug": args.book, "chapters": chapters, "judge": judge, "names": names, "tossups": tossups},
        ensure_ascii=False, indent=1))
    print(f"wrote {out} and tossups.json ({len(tossups)} tossups, {sum(x['rw'] for x in tossups)} rewritten)")


if __name__ == "__main__":
    main()
