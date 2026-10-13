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
  - opens with a handle ("this organ") that fits fewer than `lead_handle_min_answers`
    answers in the pool of passing rewrites (omnibowl.handles; config [verify]).
Style warning only (the rewrite still passes): a giveaway longer than 20 words.
Writes data/rewrites/verified.json: {tossup id: {"ok": bool, "issues": [...]}}.
"""

import json
import re
import sys
from pathlib import Path

from wordfreq import zipf_frequency

from omnibowl import config
from omnibowl.handles import fits, lead_handle

ROOT = Path(__file__).resolve().parents[1]
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*|\d[\d.,–-]*")
PRONOUNS = {"he", "she", "it", "they", "him", "her", "them", "his", "its", "their", "hers",
            "theirs", "himself", "herself", "itself", "themselves"}
# Words this common in everyday English are not "new facts" (zipf scale: 4 = 10 per million).
COMMON_ZIPF = 4.0
# Style warnings are reported but do not fail a rewrite (it stays playable).
STYLE = "style: "
# A giveaway is short: "For 10 points, name this <descriptor> that <one easy fact>."
GIVEAWAY_MAX_WORDS = 20


def stem(w: str) -> str:
    w = re.sub(r"'(?:s|re|ve|ll|d)$", "", w.lower().replace("’", "'"))
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
    for w in WORD.findall(re.sub(r"For 10 points, name\b", "", rewrite)):
        if stem(w) in answer_stems:
            issues.append(f"names the answer: '{w}'")
    if not sentences or not sentences[-1].startswith("For 10 points"):
        issues.append("last sentence does not start 'For 10 points'")
    if not 4 <= len(sentences) <= 7:
        issues.append(f"{len(sentences)} sentences (want 4-7)")
    if sentences and len(sentences[-1].split()) > GIVEAWAY_MAX_WORDS:
        issues.append(f"{STYLE}giveaway has {len(sentences[-1].split())} words (want {GIVEAWAY_MAX_WORDS} or fewer)")
    return sorted(set(issues))


def answer_key(tid: str) -> str:
    """Variants ("-v2") are the same answer."""
    return tid.rsplit("-v", 1)[0]


def always_fit(cfg: dict) -> set[str]:
    return set(cfg["rank"]["generic_categories"]) | set(cfg["verify"]["extra_generic_handles"])


def handle_fits(passing: dict[str, tuple[dict, str]], nlp) -> dict[str, set[str]]:
    """{handle noun: answers it fits} over the passing rewrites {id: (item, question)}."""
    pool = {}
    for tid, (item, q) in passing.items():
        pool.setdefault(answer_key(tid), []).extend([q, f"this {item['referent']}"])
    return fits(pool, nlp)


def lead_issues(passing: dict[str, tuple[dict, str]], nlp, cfg: dict) -> dict[str, str]:
    """{id: issue} for rewrites whose lead-in handle fits too few answers in the pool."""
    need, generic = cfg["verify"]["lead_handle_min_answers"], always_fit(cfg)
    fit = handle_fits(passing, nlp)
    out = {}
    for tid, (_, q) in passing.items():
        first = re.split(r"(?<=[.!?])\s+", q.strip())[0]
        h = lead_handle(first, nlp)
        if h and h.head not in generic and len(fit[h.head]) < need:
            out[tid] = f"lead-in '{h.text}' fits {len(fit[h.head])} answer(s) in the pool (want {need}+)"
    return out


def main() -> None:
    show_style = "--style" in sys.argv
    d = ROOT / "data" / "rewrites"
    results, questions = {}, {}
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
            results[r["id"]] = {"ok": not any(not i.startswith(STYLE) for i in issues), "issues": issues}
            questions[r["id"]] = (item, r["question"])
    import spacy
    cfg = config.load()
    nlp = spacy.load(cfg["sentences"]["spacy_model"], exclude=["ner"])
    passing = {tid: questions[tid] for tid, v in results.items() if v["ok"]}
    for tid, issue in lead_issues(passing, nlp, cfg).items():
        results[tid] = {"ok": False, "issues": sorted(results[tid]["issues"] + [issue])}
    (d / "verified.json").write_text(json.dumps(results, indent=1))
    ok = sum(v["ok"] for v in results.values())
    style = sum(v["ok"] and bool(v["issues"]) for v in results.values())
    print(f"{ok}/{len(results)} rewrites pass ({style} of them with style warnings: --style lists them)")
    for tid, v in results.items():
        if not v["ok"] or (show_style and v["issues"]):
            print(f"  {tid}: " + "; ".join(v["issues"]))


if __name__ == "__main__":
    main()
