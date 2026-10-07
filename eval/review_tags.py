"""Build a local review page for eval/parse_tags.tsv.

uv run python eval/review_tags.py && open data/review/index.html

Each labeled line is shown as a crop of the PDF page with the line outlined in red.
Change any wrong label in its dropdown, then click "Download TSV" and replace
eval/parse_tags.tsv with the downloaded file. Output lives in data/ (gitignored)
because the crops are book content.
"""

import csv
import html
import json
import sqlite3
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
KINDS = ["body", "box", "caption", "definition", "example", "exercise", "objective",
         "summary", "table", "figure", "heading", "dropcap", "furniture", "other"]
# Points of page shown above and below the labeled line.
CONTEXT = 110


def main() -> None:
    gold_path = ROOT / "eval" / "parse_tags.tsv"
    with open(gold_path) as f:
        header = [l for l in f if l.startswith("#")]
    with open(gold_path) as f:
        gold = list(csv.DictReader((l for l in f if not l.startswith("#")), delimiter="\t"))
    conn = sqlite3.connect(ROOT / "data" / "omnibowl.db")
    conn.row_factory = sqlite3.Row

    out = ROOT / "data" / "review"
    (out / "img").mkdir(parents=True, exist_ok=True)
    items = []
    for i, g in enumerate(gold):
        ln = conn.execute("""SELECT l.*, b.path FROM lines l JOIN books b ON b.id = l.book_id
                             WHERE b.slug = ? AND l.lkey = ?""", (g["book"], g["lkey"])).fetchone()
        # A fresh copy per crop: drawing the outline changes the page in memory.
        doc = pymupdf.open(ROOT / ln["path"])
        page = doc[ln["page"] - 1]
        line_rect = pymupdf.Rect(ln["x0"], ln["y0"], ln["x1"], ln["y1"])
        clip = pymupdf.Rect(0, max(0, ln["y0"] - CONTEXT), page.rect.width,
                            min(page.rect.height, ln["y1"] + CONTEXT))
        page.draw_rect(line_rect + (-3, -2, 3, 2), color=(0.85, 0.1, 0.1), width=1.5)
        page.get_pixmap(clip=clip, dpi=110).save(out / "img" / f"{i:03}.png")
        doc.close()
        items.append({"i": i, "book": g["book"], "lkey": g["lkey"], "sample": g["sample"], "gold": g["gold"],
                      "note": g["note"], "pred": ln["kind"], "feature": ln["feature"] or ""})

    rows = "\n".join(f"""
<section data-i="{it['i']}">
  <div class="meta"><b>#{it['i'] + 1}</b> {it['book']} {it['lkey']} · sample {it['sample']}
    · tagger: <code>{it['pred']}</code>{' (' + html.escape(it['feature']) + ')' if it['feature'] else ''}</div>
  <img src="img/{it['i']:03}.png" loading="lazy">
  <div class="ctl">label:
    <select>{''.join(f'<option{" selected" if k == it["gold"] else ""}>{k}</option>' for k in KINDS)}</select>
    note: <input value="{html.escape(it['note'])}" size="50"></div>
</section>""" for it in items)

    page_html = f"""<!doctype html><meta charset="utf-8"><title>Tag review</title>
<style>
body{{font:14px system-ui;max-width:900px;margin:0 auto;padding:16px;background:#fafafa;color:#222}}
section{{margin:0 0 28px;padding-bottom:16px;border-bottom:1px solid #ddd}}
img{{width:100%;border:1px solid #ccc;background:#fff}}
.changed .meta{{color:#b00}} code{{background:#eee;padding:0 4px}}
header{{position:sticky;top:0;background:#fafafa;padding:8px 0;border-bottom:1px solid #ccc;margin-bottom:16px}}
</style>
<header>Check that the red-outlined line has the right label. Changed: <b id="n">0</b>
 <button id="dl">Download TSV</button></header>
{rows}
<script>
const items = {json.dumps(items)};
const head = {json.dumps("".join(header))};
const secs = [...document.querySelectorAll("section")];
function changed() {{
  let n = 0;
  secs.forEach((s, i) => {{
    const c = s.querySelector("select").value !== items[i].gold;
    s.classList.toggle("changed", c); n += c;
  }});
  document.getElementById("n").textContent = n;
}}
document.addEventListener("change", changed);
document.getElementById("dl").onclick = () => {{
  let tsv = head + "book\\tlkey\\tsample\\tgold\\tnote\\n";
  secs.forEach((s, i) => {{
    const note = s.querySelector("input").value.replace(/[\\t\\n]/g, " ");
    tsv += [items[i].book, items[i].lkey, items[i].sample, s.querySelector("select").value, note].join("\\t") + "\\n";
  }});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([tsv], {{type: "text/tab-separated-values"}}));
  a.download = "parse_tags.tsv"; a.click();
}};
</script>"""
    (out / "index.html").write_text(page_html)
    print(f"wrote {out / 'index.html'} ({len(items)} lines)")


if __name__ == "__main__":
    main()
