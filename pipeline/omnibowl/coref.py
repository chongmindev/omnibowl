"""Coreference chains over each text unit (stage between candidates and clues).

Runs a local pretrained model, fastcoref's F-coref (MIT, pinned revision in config;
decision 2026-10-08), over every unit of clue-source text, and stores each chain's
mentions in `coref` with sentence ids and character offsets inside the sentence.
Slow (minutes per book), so it is its own stage; `clues` reads the stored chains.
"""

import os
from collections import defaultdict

from . import db


def load_model(cfg: dict):
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    from fastcoref import FCoref  # heavy import, only when this stage runs
    c = cfg["coref"]
    import torch
    device = "mps" if c["device"] == "auto" and torch.backends.mps.is_available() else (
        "cpu" if c["device"] == "auto" else c["device"])
    from huggingface_hub import snapshot_download
    path = snapshot_download(c["model"], revision=c["revision"])  # cached after first use
    return FCoref(model_name_or_path=path, device=device, enable_progress_bar=False)


def units(conn, book_id: int, kinds: list[str]):
    """Each unit's text (its sentences joined by a space) and each sentence's span."""
    rows = conn.execute(
        f"""SELECT id, unit, text FROM sentences WHERE book_id = ?
            AND kind IN ({','.join('?' * len(kinds))}) ORDER BY unit, ord""", (book_id, *kinds)).fetchall()
    out = defaultdict(lambda: ["", []])
    for r in rows:
        u = out[r["unit"]]
        if u[0]:
            u[0] += " "
        u[1].append((r["id"], len(u[0]), len(u[0]) + len(r["text"])))
        u[0] += r["text"]
    return out


def run(book_id: int, conn, cfg: dict, model=None) -> None:
    c = cfg["coref"]
    db.clear(conn, book_id, db.COREF_TABLES)
    model = model or load_model(cfg)
    us = units(conn, book_id, cfg["clues"]["sentence_kinds"])
    keys = list(us)
    preds = model.predict(texts=[us[k][0] for k in keys], max_tokens_in_batch=c["max_tokens_in_batch"])
    rows = []
    for key, pred in zip(keys, preds):
        text, spans = us[key]
        for chain_no, chain in enumerate(pred.get_clusters(as_strings=False)):
            for a, b in chain:
                for sid, s0, s1 in spans:
                    if s0 <= a < s1:
                        rows.append((book_id, key, chain_no, sid, a - s0, min(b, s1) - s0, text[a:b]))
                        break
    conn.executemany(
        """INSERT INTO coref (book_id, unit, chain, sentence_id, start_char, end_char, text)
           VALUES (?, ?, ?, ?, ?, ?, ?)""", rows)
    conn.commit()
