"""Shorten long giveaways in verified rewrites without re-reading whole tossups.

uv run python tools/giveaway_trims.py export   # -> data/rewrites/trim_in.json
  (a Claude Code session writes data/rewrites/trim_out.json: {id: new last sentence})
uv run python tools/giveaway_trims.py apply    # splices them into out_NN.json, re-verifies

Only giveaways the verifier warns about (longer than GIVEAWAY_MAX_WORDS) are exported,
each with the answer, the tossup's other sentences (so the trim does not repeat one) and
the book's definition. `apply` keeps a trim only if it starts "For 10 points, name",
is within the limit, and the whole tossup still passes the verifier; otherwise the old
giveaway stays.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_rewrites import GIVEAWAY_MAX_WORDS, STYLE, check  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RDIR = ROOT / "data" / "rewrites"


def split_last(q: str) -> tuple[str, str]:
    """(everything before the giveaway, the giveaway)."""
    k = q.rfind("For 10 points")
    return q[:k].rstrip(), q[k:].strip()


def items_and_outs():
    for batch in sorted(RDIR.glob("batch_*.json")):
        out = RDIR / batch.name.replace("batch_", "out_")
        if out.exists():
            yield {i["id"]: i for i in json.loads(batch.read_text())}, out


def export() -> None:
    verified = json.loads((RDIR / "verified.json").read_text())
    rows = []
    for items, out in items_and_outs():
        for r in json.loads(out.read_text()):
            v = verified.get(r["id"], {})
            if v.get("ok") and any(i.startswith(STYLE + "giveaway") for i in v["issues"]):
                body, last = split_last(r["question"].strip())
                item = items[r["id"]]
                rows.append({"id": r["id"], "answer": item["answer"], "giveaway": last,
                             "rest_of_tossup": body, "definition": item.get("definition")})
    (RDIR / "trim_in.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print(f"{len(rows)} long giveaways -> {RDIR / 'trim_in.json'}")


def apply() -> None:
    trims = json.loads((RDIR / "trim_out.json").read_text())
    kept, rejected = 0, []
    for items, out in items_and_outs():
        rows = json.loads(out.read_text())
        changed = False
        for r in rows:
            new = (trims.get(r["id"]) or "").strip()
            if not new or r.get("skip") or not r["question"]:
                continue
            body = split_last(r["question"].strip())[0]
            q = f"{body} {new}"
            issues = [i for i in check(items[r["id"]], q) if not i.startswith(STYLE)]
            if not new.startswith("For 10 points, name") or len(new.split()) > GIVEAWAY_MAX_WORDS or issues:
                rejected.append(f"{r['id']}: {'; '.join(issues) or 'form or length'}")
                continue
            r["question"], changed = q, True
            kept += 1
        if changed:
            out.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print(f"applied {kept} of {len(trims)} trims")
    for line in rejected:
        print("  kept old giveaway:", line)


if __name__ == "__main__":
    {"export": export, "apply": apply}[sys.argv[1]]()
