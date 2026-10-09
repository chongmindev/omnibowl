# v1: hand-assisted rewrite of the draft pool

A one-book, v1-only exception to ground rule 1 (decision 2026-10-09). The pipeline stays
fully algorithmic: it chooses every answer and every fact, and drafts every tossup. A
Claude Code session (your Claude subscription, no API key, no per-token bill) then
rewrites the drafts into quizbowl wording, and a classical script checks every rewrite
against its source sentences. Rewrites hold book text, so they stay in `data/rewrites/`
(gitignored) and are for personal study only.

## Prepare (no model tokens)

```
uv run python -m omnibowl.run --stage assemble --pool --book data/books/language_in_mind.pdf
uv run python tools/rewrite_batches.py --size 20
```

This writes `data/rewrites/batch_01.json`, `batch_02.json`, ... (about 20 tossups each).

## Rewrite one batch (in a fresh Claude Code session, from the repo root)

Start a new session so the long build history is not carried along, and paste:

> Rewrite data/rewrites/batch_01.json following docs/rewrite-v1.md, "Rewriting rules".
> Write the result to data/rewrites/out_01.json, then run
> `uv run python tools/verify_rewrites.py` and fix any flagged tossup in out_01.json
> once. Do not read other files or regenerate anything.

## Rewriting rules

Input: for each tossup, `answer`, `accept`, `kind`, `referent`, `referent_is_generic`,
`definition`, `draft` (the algorithmic version), and `clues`: each clue's `source`
sentence and the `previous` sentence for context.

Write one tossup per input, as JSON: `[{"id": "...", "question": "..."}, ...]`.

1. **Facts only from the sources.** Every fact must come from that tossup's `source`
   sentences or `definition`. Use `previous` only to understand what a pronoun refers
   to. No outside knowledge, no new names, numbers or terms.
2. **Pyramidal.** Order clues from hardest (most obscure detail, specific study,
   numbers) to easiest. End with: `For 10 points, name this <referent> <easiest fact>.`
   using the definition when there is one.
3. **One referent, used consistently.** Use `referent` ("this ERP"); if
   `referent_is_generic` is true, choose a specific noun the sources support ("this
   ERP component", "this linguist"). The first sentence must contain it.
4. **No pronouns.** Never "he/she/it/they/his/its/their". Repeat the referent or name
   the thing.
5. **Never name the answer** or any word that shares its stem.
6. **Tight quizbowl phrasing.** 4 to 7 sentences, about 500 to 700 characters. Cut
   textbook framing ("researchers have found that", "as we saw", "for instance"). Keep
   attributions that carry information ("In a 2007 study by Moll and Tomasello, ...").
7. **One fact per sentence**, and each clue should identify this answer, not something
   general.
8. If the sources cannot support at least four good clues, return the draft unchanged
   with `"skip": true`.

## Check and play (no model tokens)

```
uv run python tools/verify_rewrites.py        # flags new facts, pronouns, leaks, form
uv run python tools/play.py && open data/play/index.html
```

The player uses a rewrite only when it passed verification and labels it "rewritten";
everything else shows the algorithmic draft.

## Measuring usage

Check your plan's usage before and after batch 1; that difference is the cost of one
batch on your plan. Expected size: the batch file is roughly 10-15k tokens, the output
a few thousand, plus the session's own reasoning.
