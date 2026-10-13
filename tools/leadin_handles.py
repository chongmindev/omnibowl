"""Broaden lead-in handles that fit too few answers (decision "lead-in handles: pool check", A).

uv run python tools/leadin_handles.py export   # -> data/rewrites/leadin_in.json
  (a Claude Code session writes data/rewrites/leadin_out.json: {id: new first sentence})
uv run python tools/leadin_handles.py apply    # splices them into out_NN.json, re-verifies

Exports every rewrite the verifier fails only for its lead-in handle, with the first
sentence, the handle, the rest of the tossup, and the handles allowed in a lead-in (nouns
fitting enough answers, plus the generic ones). The new first sentence may change only the
handle (a verb's number may change to agree, and up to MAX_DROPPED of the old phrase's
modifiers may go): `apply` keeps it when its handle is allowed, it adds no other word, and
the whole tossup passes `check`; otherwise the
old sentence stays. Run verify_rewrites.py afterwards for the pool-level check.
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_rewrites import STYLE, WORD, always_fit, check, handle_fits, stem  # noqa: E402

from omnibowl import config  # noqa: E402
from omnibowl.handles import lead_handle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RDIR = ROOT / "data" / "rewrites"
MAX_DROPPED = 2  # old modifier words a new lead-in may leave out
FIRST = re.compile(r"(?<=[.!?])\s+")


def items_and_outs():
    for batch in sorted(RDIR.glob("batch_*.json")):
        out = RDIR / batch.name.replace("batch_", "out_")
        if out.exists():
            yield {i["id"]: i for i in json.loads(batch.read_text())}, out


def nlp_and_cfg():
    import spacy
    cfg = config.load()
    return spacy.load(cfg["sentences"]["spacy_model"], exclude=["ner"]), cfg


def passing_pool(verified: dict) -> dict:
    """Rewrites that pass, or fail only on their lead-in: the pool handles are counted in."""
    pool = {}
    for items, out in items_and_outs():
        for r in json.loads(out.read_text()):
            v = verified.get(r["id"], {})
            rest = [i for i in v.get("issues", []) if not i.startswith(STYLE) and not i.startswith("lead-in")]
            if r["id"] in items and not r.get("skip") and v and not rest:
                pool[r["id"]] = (items[r["id"]], r["question"])
    return pool


def allowed(fit: dict, cfg: dict) -> set[str]:
    return {h for h, a in fit.items() if len(a) >= cfg["verify"]["lead_handle_min_answers"]} | always_fit(cfg)


SKIP = {"is", "are", "was", "were", "has", "have"}


def words(text: str) -> Counter:
    """Stems, ignoring forms of "be"/"have" and a final s, so number agreement may change."""
    return Counter(stem(w).rstrip("s") for w in WORD.findall(text) if w.lower() not in SKIP)


def only_handle_changed(old: str, old_h, new: str, new_h, max_dropped: int) -> bool:
    """Outside the new handle, the new sentence adds no word the old sentence lacked, and
    drops at most `max_dropped` of the old words outside the old handle (modifiers such as
    "sign" in "this sign language")."""
    new_rest = words(new[:new_h.start] + " " + new[new_h.end:])
    old_rest = words(old[:old_h.start] + " " + old[old_h.end:])
    old_all = old_rest + words(old[old_h.start:old_h.end])
    return not (new_rest - old_all) and sum((old_rest - new_rest).values()) <= max_dropped


def export() -> None:
    nlp, cfg = nlp_and_cfg()
    verified = json.loads((RDIR / "verified.json").read_text())
    pool = passing_pool(verified)
    fit = handle_fits(pool, nlp)
    ok_handles = sorted(allowed(fit, cfg), key=lambda h: (-len(fit.get(h, ())), h))
    rows = []
    for tid, (item, q) in pool.items():
        if not any(i.startswith("lead-in") for i in verified[tid]["issues"]):
            continue
        first, *rest = FIRST.split(q.strip(), maxsplit=1)
        h = lead_handle(first, nlp)
        rows.append({"id": tid, "answer": item["answer"], "kind": item["kind"], "first_sentence": first,
                     "handle": h.text if h else None, "rest_of_tossup": rest[0] if rest else ""})
    (RDIR / "leadin_in.json").write_text(json.dumps(
        {"allowed_handles": ok_handles, "tossups": rows}, ensure_ascii=False, indent=1))
    print(f"{len(rows)} lead-ins to broaden, {len(ok_handles)} allowed handles -> {RDIR / 'leadin_in.json'}")


def apply() -> None:
    nlp, cfg = nlp_and_cfg()
    verified = json.loads((RDIR / "verified.json").read_text())
    fixes = json.loads((RDIR / "leadin_out.json").read_text())
    ok_handles = allowed(handle_fits(passing_pool(verified), nlp), cfg)
    kept, rejected = 0, []
    for items, out in items_and_outs():
        rows = json.loads(out.read_text())
        changed = False
        for r in rows:
            new = (fixes.get(r["id"]) or "").strip()
            if not new or r.get("skip") or not r["question"]:
                continue
            first, *rest = FIRST.split(r["question"].strip(), maxsplit=1)
            old_h, new_h = lead_handle(first, nlp), lead_handle(new, nlp)
            q = f"{new} {rest[0]}" if rest else new
            why = []
            if not new_h or new_h.head not in ok_handles:
                why.append(f"handle '{new_h.text if new_h else None}' not allowed")
            if old_h and new_h and not only_handle_changed(first, old_h, new, new_h, MAX_DROPPED):
                why.append("changed more than the handle")
            why += [i for i in check(items[r["id"]], q) if not i.startswith(STYLE)]
            if why:
                rejected.append(f"{r['id']}: {'; '.join(why)}")
                continue
            r["question"], changed = q, True
            kept += 1
        if changed:
            out.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print(f"applied {kept} of {len(fixes)} lead-in fixes")
    for line in rejected:
        print("  kept old lead-in:", line)


if __name__ == "__main__":
    {"export": export, "apply": apply}[sys.argv[1]]()
