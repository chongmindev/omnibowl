"""Check hand-assisted rewrites against their sources (classical, no model calls).

uv run python tools/verify_rewrites.py

Reads data/rewrites/batch_NN.json and the matching out_NN.json, and flags a rewritten
tossup when it:
  - contains a name, number or uncommon word that appears nowhere in its sources
    (source sentences, previous sentences, definition, answer forms, referent);
  - uses a pronoun (he, she, it, they, ...);
  - names the answer or a stem of it;
  - does not end with a "For 10 points, name ..." sentence, or has fewer than 4 or more
    than 7 sentences.
Writes data/rewrites/verified.json: {tossup id: {"ok": bool, "issues": [...]}}.
"""

import json
import re
from pathlib import Path

from wordfreq import zipf_frequency

ROOT = Path(__file__).resolve().parents[1]
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*|\d[\d.,–-]*")
PRONOUNS = {"he", "she", "it", "they", "him", "her", "them", "his", "its", "their", "hers",
            "theirs", "himself", "herself", "itself", "themselves"}
# Words this common in everyday English are not "new facts" (zipf scale: 4 = 10 per million).
COMMON_ZIPF = 4.0


def stem(w: str) -> str:
    w = w.lower().replace("’", "'")
    return w[:max(4, len(w) - 3)] if len(w) > 4 else w


def check(item: dict, rewrite: str) -> list[str]:
    issues = []
    source = " ".join([item.get("definition") or "", item["answer"], " ".join(item["accept"]),
                       item["referent"]] + [c["source"] + " " + c["previous"] for c in item["clues"]])
    known = {stem(w) for w in WORD.findall(source)}
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", rewrite.strip()) if s]
    for i, s in enumerate(sentences):
        s = re.sub(r"^For 10 points,\s*", "", s)
        for k, w in enumerate(WORD.findall(s)):
            lw = w.lower()
            if lw in PRONOUNS:
                issues.append(f"pronoun '{w}'")
                continue
            new = stem(w) not in known
            is_name = w[0].isupper() and k > 0
            is_num = w[0].isdigit()
            rare = zipf_frequency(lw, "en") < COMMON_ZIPF and len(lw) > 3
            if new and (is_name or is_num or rare):
                issues.append(f"not in sources: '{w}'")
    answer_stems = {stem(w) for f in [item["answer"], *item["accept"]] for w in WORD.findall(f)
                    if len(w) > 3 and w.lower() not in item["referent"].lower()}
    for w in WORD.findall(rewrite):
        if stem(w) in answer_stems:
            issues.append(f"names the answer: '{w}'")
    if not sentences or not sentences[-1].startswith("For 10 points"):
        issues.append("last sentence does not start 'For 10 points'")
    if not 4 <= len(sentences) <= 7:
        issues.append(f"{len(sentences)} sentences (want 4-7)")
    return sorted(set(issues))


def main() -> None:
    d = ROOT / "data" / "rewrites"
    results = {}
    for batch in sorted(d.glob("batch_*.json")):
        out = d / batch.name.replace("batch_", "out_")
        if not out.exists():
            continue
        items = {i["id"]: i for i in json.loads(batch.read_text())}
        for r in json.loads(out.read_text()):
            item = items.get(r["id"])
            if item is None:
                continue
            if r.get("skip"):
                results[r["id"]] = {"ok": False, "issues": ["skipped by the rewriter"]}
                continue
            issues = check(item, r["question"])
            results[r["id"]] = {"ok": not issues, "issues": issues}
    (d / "verified.json").write_text(json.dumps(results, indent=1))
    ok = sum(v["ok"] for v in results.values())
    print(f"{ok}/{len(results)} rewrites pass")
    for tid, v in results.items():
        if not v["ok"]:
            print(f"  {tid}: " + "; ".join(v["issues"]))


if __name__ == "__main__":
    main()
