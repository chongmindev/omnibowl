"""A local page for hand-ordering sentences by difficulty: the gold set for eval/pyramid.py.

uv run python eval/review_difficulty.py [--book language_in_mind] [--n 20] [--seed 0]
open data/review/difficulty.html

Picks `--n` played tossups (seeded) with at least four sentences before the giveaway, not
already in eval/difficulty_gold.json, and shows each one's sentences shuffled. Skip any
tossup whose topic you don't know; only ordered ones go into the JSON. Click them hardest first; "Copy JSON" gives
{"tossups": {id: [sentence index, ...]}} with indices into the question's sentences (no
text), to save as eval/difficulty_gold.json. The page holds book text, so it is written to
data/review/ (gitignored).
"""

import argparse
import html
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

from pyramid import played  # noqa: E402

from omnibowl.pyramid import clue_sentences  # noqa: E402

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Difficulty order</title>
<style>
:root{--ink:#141414;--paper:#F4F2EC;--mute:#5D5A53;--hair:#CFCABD;--blue:#1D4E9E;--red:#D7261E}
body{background:var(--paper);color:var(--ink);font:15px/1.5 'Red Hat Mono',ui-monospace,monospace;margin:0;padding:24px;max-width:960px}
h1{font:700 16px system-ui,sans-serif;text-transform:uppercase;letter-spacing:.06em}
p{color:var(--mute)}
.t{border-top:3px solid var(--ink);padding:16px 0 24px}
.t h2{font:700 13px system-ui,sans-serif;text-transform:uppercase;letter-spacing:.08em;margin:0 0 8px}
.s{display:grid;grid-template-columns:32px 1fr;border-bottom:1px solid var(--hair);padding:8px 0;cursor:pointer}
.s:hover{background:#fff}.s b{color:var(--blue)}.s.done{color:var(--mute)}
button{font:inherit;border:2px solid var(--ink);background:none;padding:4px 12px;cursor:pointer;margin-right:8px}
textarea{width:100%;height:120px;font:12px ui-monospace,monospace}
</style></head><body>
<h1>Order by difficulty</h1>
<p>Skip any tossup you don't know well. For the rest, click the sentences from hardest (fewest students would know it) to easiest.
The giveaway is left out. Reset a tossup to redo it. When done, copy the JSON into eval/difficulty_gold.json.</p>
__TOSSUPS__
<div class="t"><button id="copy">Copy JSON</button><span id="count"></span><textarea id="out" readonly></textarea></div>
<script>
const order={};
function render(){const out={};for(const[k,v]of Object.entries(order))if(v.length)out[k]=v;
 document.getElementById('out').value=JSON.stringify({tossups:out},null,1);
 const full=[...document.querySelectorAll('.t[data-id]')].filter(t=>(order[t.dataset.id]||[]).length===t.querySelectorAll('.s').length).length;
 document.getElementById('count').textContent=full+' of '+document.querySelectorAll('.t[data-id]').length+' tossups fully ordered';}
document.querySelectorAll('.t[data-id]').forEach(t=>{const id=t.dataset.id;order[id]=[];
 t.querySelectorAll('.s').forEach(s=>s.onclick=()=>{const i=+s.dataset.i;if(order[id].includes(i))return;order[id].push(i);s.classList.add('done');s.querySelector('b').textContent=order[id].length;render()});
 t.querySelector('.reset').onclick=()=>{order[id]=[];t.querySelectorAll('.s').forEach(s=>{s.classList.remove('done');s.querySelector('b').textContent=''});render()};});
document.getElementById('copy').onclick=()=>{const o=document.getElementById('out');o.select();navigator.clipboard&&navigator.clipboard.writeText(o.value)};
render();
</script></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="language_in_mind")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    gold = ROOT / "eval" / "difficulty_gold.json"
    done = set(json.loads(gold.read_text())["tossups"]) if gold.exists() else set()
    pool = [t for t in played(args.book) if len(clue_sentences(t["question"])[0]) >= 4 and t["id"] not in done]
    blocks = []
    for k, t in enumerate(rng.sample(pool, min(args.n, len(pool))), 1):
        sents = clue_sentences(t["question"])[0]
        idx = list(range(len(sents)))
        rng.shuffle(idx)
        rows = "".join(f'<div class="s" data-i="{i}"><b></b><span>{html.escape(sents[i])}</span></div>' for i in idx)
        blocks.append(f'<div class="t" data-id="{html.escape(t["id"])}"><h2>{k}. {html.escape(t["answer"])}</h2>'
                      f'{rows}<p><button class="reset">Reset</button></p></div>')
    out = ROOT / "data" / "review" / "difficulty.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(PAGE.replace("__TOSSUPS__", "\n".join(blocks)))
    print(f"wrote {out} ({len(blocks)} tossups)")


if __name__ == "__main__":
    main()
