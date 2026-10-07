# Omnibowl

Turn a textbook into quizbowl tossups, so a class can be studied in the style of
[protobowl](https://protobowl.com) and [QBReader](https://www.qbreader.org).

A build pipeline reads a textbook PDF and writes pyramidal tossups to a database. A
reader app reveals them word by word and lets you buzz. Generation is fully
algorithmic (classical NLP, no LLM calls), and every clue points back to the textbook
sentence it came from.

## Status

Early. The first stage, `parse`, works on two test textbooks; the later stages and the
reader are not built yet.

| Stage | Produces | Status |
|---|---|---|
| `parse` | sections, lines, tagged blocks, sentences, glossary and index labels | done |
| `candidates` | answer terms with scores | not started |
| `clues` | one-fact clues per answer, grounded in sentence ids | not started |
| `rank` | referent, giveaway, clue difficulty | not started |
| `assemble` | tossups, ordered hard to easy | not started |
| reader | web app that reveals tossups and records buzzes | not started |

## How parsing works

1. **Measure** (`pipeline/omnibowl/parse.py`): read the PDF outline into chapters and
   sections, and record every text line with its size, weight, position, background
   panel and links. Two-column pages are read column by column.
2. **Profile** (`pipeline/omnibowl/profile.py`): discover the book's layout and write
   it to `data/profiles/<book>.auto.toml`: font-size bands, recurring features
   (boxes, exercises, reviews, objectives) with a guessed role, panel styles, page
   furniture. Corrections go in `profiles/<book>.toml` (committed) and override the guess.
3. **Tag** (`pipeline/omnibowl/tag.py`): label every line (body, box, caption,
   definition, exercise, objective, summary, table, example, heading, ...) using only
   the profile and generic rules, then group lines into blocks.
4. **Sentences** (`pipeline/omnibowl/sentences.py`): stitch each stream of text back
   together (drop caps, hyphens, body text interrupted by sidebars) and split it with
   spaCy. Every sentence keeps the line ids and character offsets it came from.
5. **Back matter** (`pipeline/omnibowl/backmatter.py`): read the glossary and indexes
   into labels (terms, definitions, linked pages) used to evaluate later stages.

Nothing in the code names a particular book's features; per-book facts live in the
profile. See `docs/decisions.md` for each design choice and the alternatives rejected.

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```
uv sync
```

Textbooks are not included. Put a text-layer PDF (no OCR) in `data/books/`.

## Usage

```
uv run python -m omnibowl.run --stage parse --book data/books/<file>.pdf    # measure, profile, tag
uv run python -m omnibowl.run --stage profile --book data/books/<file>.pdf  # re-profile and re-tag
uv run python -m omnibowl.run --stage tag --book data/books/<file>.pdf      # re-tag and re-split
uv run python -m omnibowl.run --stage sentences --book data/books/<file>.pdf  # re-split only
uv run pytest
```

Output goes to `data/omnibowl.db` (SQLite).

## Evaluation

`eval/parse_tags.tsv` holds hand-labeled lines from the test books, keyed by page
position, never by quoted text.

```
uv run python eval/tag_accuracy.py                                  # tagging accuracy per book
uv run python eval/sentence_accuracy.py                             # sentence-split accuracy
uv run python eval/review_tags.py && open data/review/index.html    # review labels against page crops
```

## Layout

```
pipeline/omnibowl/   pipeline stages and CLI (run.py)
pipeline/config.toml all thresholds and weights; no magic numbers in code
pipeline/tests/      pytest suite
profiles/            hand corrections to generated layout profiles
eval/                labeled data and metric scripts
docs/                design notes and decisions.md
data/                books, database, profiles, caches (gitignored)
```

## Content policy

Textbook content never enters the repository: PDFs, extracted text, the database and
review images stay in `data/`. Questions generated from a copyrighted textbook are for
personal study.
