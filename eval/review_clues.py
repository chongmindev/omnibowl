"""Build a local review page for eval/clues.tsv.

uv run python eval/review_clues.py && open data/review/clues.html

Each gold sentence is shown with the sentence before and after it, grouped by answer.
Toggle yes/no (is this, on its own, a usable fact about the answer?), add a note if
useful, then "Download TSV" and replace eval/clues.tsv with the downloaded file.
The page holds book text, so it is written to data/ (gitignored) and never published.
"""

import csv
import html
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
from clue_accuracy import predicted  # noqa: E402


def main() -> None:
    gold_path = ROOT / "eval" / "clues.tsv"
    with open(gold_path) as f:
        header = "".join(l for l in f if l.startswith("#"))
    with open(gold_path) as f:
        gold = list(csv.DictReader((l for l in f if not l.startswith("#")), delimiter="\t"))
    conn = sqlite3.connect(ROOT / "data" / "omnibowl.db")
    conn.row_factory = sqlite3.Row

    def sentence(book, key):
        lkey, off = key.split("+")
        return conn.execute(
            """SELECT s.* FROM sentences s JOIN books b ON b.id = s.book_id
               JOIN lines l ON l.id = json_extract(s.line_ids, '$[0]')
               WHERE b.slug = ? AND l.lkey = ? AND s.start_char = ?""", (book, lkey, int(off))).fetchone()

    def neighbor(s, d):
        r = conn.execute("SELECT text FROM sentences WHERE book_id = ? AND unit = ? AND ord = ?",
                         (s["book_id"], s["unit"], s["ord"] + d)).fetchone()
        return r[0] if r else ""

    items, groups = [], defaultdict(list)
    preds = {}
    for i, g in enumerate(gold):
        s = sentence(g["book"], g["sentence"])
        term = conn.execute("""SELECT t.display, t.kind FROM terms t JOIN books b ON b.id = t.book_id
                               WHERE b.slug = ? AND t.norm = ? AND t.class = 'answer'""",
                            (g["book"], g["answer"])).fetchone()
        p = preds.setdefault((g["book"], g["answer"]), predicted(conn, g["book"], g["answer"]))
        item = {"i": i, **g, "display": term["display"] if term else g["answer"],
                "kindterm": term["kind"] if term else "", "page": s["page_start"], "skind": s["kind"],
                "prev": neighbor(s, -1), "text": s["text"], "next": neighbor(s, 1),
                "now": g["sentence"] in p}
        items.append(item)
        groups[g["answer"]].append(item)

    esc = html.escape
    sections = []
    for ans, its in groups.items():
        rows = "".join(f"""
<div class="item" data-i="{it['i']}">
  <div class="meta">p. {it['page']} · {it['skind']} · stage {'picks' if it['now'] else 'skips'} it</div>
  <p class="ctx">{esc(it['prev'][-220:])}</p>
  <p class="this">{esc(it['text'])}</p>
  <p class="ctx">{esc(it['next'][:220])}</p>
  <div class="ctl">
    <button class="tog {'yes' if it['gold'] == 'yes' else 'no'}">{'Clue' if it['gold'] == 'yes' else 'Not a clue'}</button>
    <input class="note" placeholder="note (optional)" value="{esc(it.get('note', '') or '')}">
  </div>
</div>""" for it in its)
        first = its[0]
        sections.append(f"""<section><h2>{esc(first['display'])} <span>{esc(first['kindterm'])} · {len(its)} sentences</span></h2>{rows}</section>""")

    page = f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clue review</title>
<link href="https://fonts.googleapis.com/css2?family=League+Spartan:wght@500;700&family=Red+Hat+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{{--ink:#141414;--paper:#F4F2EC;--mute:#5D5A53;--hair:#CFCABD;--blue:#1D4E9E;--red:#D7261E;--yellow:#F2B705}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font-family:'League Spartan',sans-serif}}
header{{position:sticky;top:0;z-index:2;background:var(--ink);color:#fff;display:flex;gap:16px;align-items:center;padding:10px 16px;font-size:13px}}
header b{{font-size:15px;letter-spacing:.06em;text-transform:uppercase}} header .sp{{flex:1}}
header button{{all:unset;cursor:pointer;background:var(--yellow);color:var(--ink);padding:6px 12px;font-weight:700}}
main{{max-width:980px;margin:0 auto;padding:16px}}
.rules{{font-family:'Red Hat Mono',monospace;font-size:12px;color:var(--mute);line-height:1.5;border-left:3px solid var(--ink);padding:4px 12px;margin:8px 0 20px}}
section{{border:3px solid var(--ink);margin-bottom:22px;background:#fff}}
h2{{margin:0;padding:10px 14px;border-bottom:3px solid var(--ink);font-size:17px}} h2 span{{font-family:'Red Hat Mono',monospace;font-size:12px;color:var(--mute);font-weight:400;margin-left:8px}}
.item{{padding:12px 14px;border-bottom:1px solid var(--hair)}} .item:last-child{{border-bottom:0}}
.meta{{font-family:'Red Hat Mono',monospace;font-size:11px;color:var(--mute);margin-bottom:6px}}
.ctx{{margin:2px 0;font-family:'Red Hat Mono',monospace;font-size:12px;color:#8b877d;line-height:1.5}}
.this{{margin:6px 0;font-family:'Red Hat Mono',monospace;font-size:14px;line-height:1.6;border-left:4px solid var(--blue);padding-left:10px}}
.ctl{{display:flex;gap:10px;margin-top:8px}}
.tog{{all:unset;cursor:pointer;padding:5px 12px;font-weight:700;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#fff;transition:background-color 120ms cubic-bezier(.2,.7,.2,1)}}
.tog.yes{{background:var(--blue)}} .tog.no{{background:#8b877d}}
.changed .tog{{outline:3px solid var(--red);outline-offset:-3px}}
.note{{all:unset;flex:1;border-bottom:1px solid var(--hair);font-family:'Red Hat Mono',monospace;font-size:12px}}
</style>
<header><b>Clue review</b><span>{len(items)} sentences · {len(groups)} answers · changed: <b id="n">0</b></span><span class="sp"></span><button id="dl">Download TSV</button></header>
<main>
<p class="rules">Mark <b>Clue</b> if the highlighted sentence, on its own, states a usable fact about the answer itself.
Not a clue: facts about something named after the answer (Broca's area for Broca), meta-text ("as you saw", "see Table"),
questions, citations or source lines, fragments that need the sentence before, example sentences.
Grey lines are the sentences before and after, for context.</p>
{''.join(sections)}
</main>
<script>
const items = {json.dumps([{k: it[k] for k in ("book", "answer", "sentence", "sample", "gold")} for it in items])};
const head = {json.dumps(header)};
document.querySelectorAll('.item').forEach(el => {{
  const i = +el.dataset.i, b = el.querySelector('.tog');
  b.onclick = () => {{
    const yes = !b.classList.contains('yes');
    b.classList.toggle('yes', yes); b.classList.toggle('no', !yes);
    b.textContent = yes ? 'Clue' : 'Not a clue';
    el.classList.toggle('changed', (yes ? 'yes' : 'no') !== items[i].gold);
    document.getElementById('n').textContent = document.querySelectorAll('.changed').length;
  }};
}});
document.getElementById('dl').onclick = () => {{
  let tsv = head + 'book\\tanswer\\tsentence\\tsample\\tgold\\tnote\\n';
  document.querySelectorAll('.item').forEach(el => {{
    const it = items[+el.dataset.i];
    const g = el.querySelector('.tog').classList.contains('yes') ? 'yes' : 'no';
    const note = el.querySelector('.note').value.replace(/[\\t\\n]/g, ' ');
    tsv += [it.book, it.answer, it.sentence, it.sample, g, note].join('\\t') + '\\n';
  }});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([tsv], {{type: 'text/tab-separated-values'}}));
  a.download = 'clues.tsv'; a.click();
}};
</script>"""
    out = ROOT / "data" / "review" / "clues.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    print(f"wrote {out} ({len(items)} sentences)")


if __name__ == "__main__":
    main()
