# Omnibowl

Omnibowl turns a textbook into quizbowl tossups so a class can be studied in the style of protobowl.com and qbreader.org. A build pipeline reads a textbook PDF and writes tossups to a database; a reader app reveals them word by word and lets the player buzz.

The developer (Chong) is building this partly to design the algorithms himself. That shapes the ground rules below.

## Scope

**v0 (build this):** one textbook (see "v0 book" below), tossups only, single player, fully algorithmic generation, a minimal web reader.

**Later (do not build unless asked):** the LLM rewrite stage, mixing in real QBReader tossups, multiplayer rooms (any path after the domain is a room, as on protobowl), bonuses, power marks, non-textbook sources, using buzz data to re-rank clues.

## Ground rules

1. **No LLM or hosted-model calls in the pipeline**, except in the `rewrite` stage, which is off by default and out of scope for v0. Do not suggest an LLM or embedding API as a shortcut for any other stage. Local classical NLP (spaCy models, wordfreq, regex, statistics) is fine.
2. **Chong designs the algorithms.** For any scoring function, threshold, ranking rule, or heuristic: propose two or three options with tradeoffs and wait for a choice. Do not silently pick one. Plumbing (I/O, schema, CLI, tests, the reader UI) can be implemented without asking.
3. **Every clue is grounded.** A clue record must point to the sentence id(s) it came from. No fact appears in a generated tossup unless it is in the textbook.
4. **No magic numbers in code.** Thresholds and weights live in `pipeline/config.toml`.
5. **Stages are independent and deterministic.** Each stage reads the previous stage's stored output and writes its own, so any stage can be rerun alone. Seed anything random.
6. **Never commit textbook content.** PDFs, extracted text, and the built database stay in `data/`, which is gitignored. Tests and eval files refer to sentences by id, never by quoted text. Questions generated from the v0 book are for personal study and are not deployed publicly.
7. **Show output, don't claim success.** After changing a pipeline stage, print five sample outputs from that stage. After changing a scoring function, report before and after numbers on the eval set.
8. **Ask before adding a dependency.**
9. **Build for any textbook.** The v0 book is a test case, not a target. Code and `config.toml` hold generic rules only; facts about a particular book (size clusters, feature-marker names, panel styles, back-matter titles) are discovered per book and stored as a per-book profile. When proposing or finishing a rule, say what would break on a different textbook.

## Architecture

Two halves joined by one contract, the question database. The pipeline writes it; the reader only reads it and logs buzzes.

```
textbook PDF -> parse -> candidates -> clues -> rank -> assemble -> [rewrite] -> question DB -> reader
```

| Stage | Produces | Notes |
|---|---|---|
| `parse` | sections, sentences, blocks tagged by kind | PyMuPDF. Use the PDF outline (bookmarks) for chapters and sections; fall back to font-size heading detection. Text-layer PDFs only, no OCR. Tag every block as body, box, caption, margin definition, or exercise. Also capture glossary terms, index entries, and their link targets. |
| `candidates` | terms with scores and a class | Terms are noun phrases and named entities, not single words. Two scores: keyness (frequency in the book vs. general English via wordfreq) and coverage (distinct sentences and sections about the term). Class is `answer`, `clue_only` (too few facts), or `too_broad`. Glossary and index entries are labels for tuning and evaluating the scores, not a replacement for them: the method has to work on a book with no glossary. |
| `clues` | atomic clue records per answer | Find every sentence about an answer, including ones that refer to it by pronoun or description (coreference). One clue is one fact. |
| `rank` | answer referent, giveaway descriptor, clue difficulty | Referent is the vague noun used all question ("this thinker"). The giveaway descriptor is the specific one used only at the end ("this Swiss developmental psychologist"). Difficulty signals: textbook emphasis (bolded, defined, in a summary = easy; one passing mention = hard) and QBReader data. |
| `assemble` | tossups | Replace answer mentions with the referent, check for leaks (a clue sharing a word with the answer, e.g. "Pavlovian" for Pavlov), order clues hard to easy, end with "For 10 points, name this ...". Build the answerline with accepted alternates. |
| `rewrite` | reworded tossups | Optional LLM stage. Off in v0. Input is an ordered clue list; it may rephrase but not add facts. |

## v0 book

*Language in Mind: An Introduction to Psycholinguistics*, Julie Sedivy (Sinauer, 2019). Expected at `data/books/language_in_mind.pdf`.

Measured from the PDF. Treat these as starting facts and re-verify in code:

- **Format:** 1,279 letter-size pages, about 355k words in the chapters. It is an ebook converted to PDF, so PDF page numbers do not match print page numbers. Store PDF pages only.
- **Text layer:** clean. Curly quotes, em dashes, and IPA characters extract correctly; no replacement characters.
- **Outline:** 86 entries. Level 1 is 13 numbered chapters plus front and back matter; level 2 is 61 sections numbered `N.M`. Front-matter entries all point to page 2, so ignore them. Outline titles contain mojibake (for example `â€™` for an apostrophe) and need repair; body text does not.
- **Chapters:** PDF pages 24 to 1112. Back matter: Glossary (1113), Literature Cited (1131), Author Index (1176), Subject Index (1199).
- **Two text streams:** main prose is about 15 pt. Boxes, figure captions, and margin definitions are about 11.3 pt and make up roughly 40% of the characters in the chapters. Font size is the first signal for telling them apart.
- **Chapters are scaled differently:** chapter 9 is about 0.83x (12.5 / 9.4 pt) and chapter 11 about 0.99x. Ratios hold, so use `lines.rel_size` (size / chapter body size): 1.0 body, 0.75 side stream, 1.13 subsection, 1.41 section heading or box title, 2.25 drop cap. Point sizes below are for the unscaled chapters.
- **Headings:** bold spans at about 33.8, 21.2, and 16.9 pt look like chapter, section, and subsection headings. Subsections are not in the outline.
- **Key terms:** shown in the chapter as a bold 11.3 pt term followed by its definition in regular 11.3 pt, near first use. In-text terms link to the glossary.
- **Glossary:** about 250 terms, each a bold term followed by a definition. Each term links to the PDF page where it is introduced.
- **Indexes:** the printed numbers are print pages, but each one is an internal link whose target is the correct PDF page. Use the link target. The subject index has about 4,000 linked references and the author index about 1,150. The author index is the source of researcher names.
- **Citations:** in-text citations link to Literature Cited.
- **Box backgrounds:** every BOX, LANGUAGE AT LARGE, METHOD, and RESEARCHERS AT WORK sits on a light-filled rectangle (stored in `panels`). Pages were cut from long scrolls, so one rectangle runs past the page edges and is redrawn on each page it covers. Fill color separates feature types; gray is tables. Exercise features and DIGGING DEEPER have no light panel. DIGGING DEEPER is a chapter-end essay, not an exercise.
- **Recurring features** (identified by an all-caps marker line): `BOX N.M`, `LANGUAGE AT LARGE N.M`, `METHOD N.M`, `RESEARCHERS AT WORK N.M`, `WEB ACTIVITY N.M`, `DIGGING DEEPER`, `PROJECT`, `GO TO`, plus "Questions to Contemplate" at the end of each section. There are about 400 figure captions and 39 tables.

What this means for the pipeline:

- Exercises and prompts (Questions to Contemplate, Web Activity, Project, Go To) are never clue sources.
- Boxes and captions can supply clues but must stay tagged, since they are tangents and tend to be harder material.
- Numbered linguistic examples, quoted stimulus sentences, and IPA strings are data, not facts. Exclude them from clue extraction.
- Box ends: decided (panel first, size fallback). See `docs/decisions.md`.
- Expected answer types for this book: phenomena and effects, brain regions, researchers, experimental methods, theories and hypotheses, disorders, and linguistic concepts.

## Quizbowl conventions

- A tossup is one paragraph of four to seven sentences, about 500 to 700 characters (limits in `config.toml`). If an answer has too few good clues, it gets no tossup.
- It is pyramidal: the hardest clue comes first and the easiest (the giveaway) last. No drop-back clues: clue difficulty never rises again later in the question.
- The lead-in (first clue) must uniquely identify the answer. No vague clues that could fit several answers, and no hostage clues that leave a guess between two named options.
- The answer is never named. It is referred to by one consistent referent ("this psychologist", "this effect"), and the referent appears in the first sentence so the answer type is clear from the start.
- The last sentence starts "For 10 points," and asks for the answer with the easiest clue.
- Tossups only: no bonuses. No pronunciation guides.
- The reader reveals words one at a time at an adjustable speed. A buzz stops the reveal. Record the word index of every buzz.
- Scoring: +10 correct, -5 for a wrong answer given before the question finished, 0 for a wrong answer after it finished.
- After each question, show the full text and the textbook sections its clues came from.

## Data model

SQLite at `data/omnibowl.db`. Tables: `books`, `sections`, `sentences`, `terms`, `clues`, `tossups`, `buzzes`. `parse` also writes a measurement layer: `lines` (every text line with layout features), `panels` (light-filled rectangles), and `links` (internal link targets). Later steps classify from these without reopening the PDF.

Tossups mirror the QBReader tossup object so real and generated questions are interchangeable:

- QBReader fields: `_id`, `question`, `question_sanitized`, `answer`, `answer_sanitized`, `category`, `subcategory`, `difficulty` (0-10), `number`, `packet {_id, name, number}`, `set {_id, name, year}`.
- Omnibowl fields: `source` (`generated` or `qbreader`), `book_id`, `section_ids`, `clue_ids` in question order, `pipeline_version`.

For generated tossups, `set.name` is the book title and `packet` is the chapter.

## QBReader API

- Base URL: `https://www.qbreader.org/api`. Rate limit: 20 requests per second.
- Docs: https://www.qbreader.org/tools/api-docs/ and schemas at https://www.qbreader.org/tools/api-docs/schemas/. A Python wrapper is on PyPI as `qbreader`.
- **Read the docs page for an endpoint before calling it. Do not guess parameter names.**
- Endpoints this project uses:
  - `GET /query`: find real tossups by answerline (answer validation, referent mining, clue-position signals).
  - `GET /frequency-list`: most frequent answers for a subcategory (prior for what makes a good answer).
  - `GET /question-stats/tossup`: aggregated play statistics for a tossup.
  - `GET /check-answer`: judges an answer as correct, incorrect, or prompt given an answerline. The reader uses this in v0.
- Cache every response under `data/qbreader_cache/` and read from the cache first. Do not bulk-download the database.
- Coverage of this book's topics will be partial. The relevant questions are probably under Social Science (linguistics, psychology) and Science (biology, for the brain); confirm the exact category and subcategory names in the docs. Every stage must work when QBReader has nothing on an answer.
- QBReader questions are other people's writing. Use them as signals and, later, as attributed mix-ins. Never present one as generated.

## Stack (proposed defaults; change here if they change)

- Pipeline: Python 3.12, uv, PyMuPDF, spaCy (en_core_web_sm), wordfreq, SQLite, pytest.
- Server: FastAPI. Serves tossups, proxies `check-answer`, logs buzzes. WebSocket rooms come later.
- Web: Vite and TypeScript, no framework in v0.

## Layout

```
pipeline/omnibowl/   parse.py profile.py tag.py sentences.py backmatter.py candidates.py clues.py rank.py assemble.py rewrite.py run.py
pipeline/config.toml
pipeline/tests/
server/
web/
eval/                golden answers and clues, metric scripts
docs/                one design note per stage, plus decisions.md
data/                gitignored
```

## Commands

Create these to match, then keep this section accurate.

```
uv run python -m omnibowl.run --stage <name> --book data/books/<file>.pdf
uv run python -m omnibowl.run --all --book data/books/<file>.pdf
uv run pytest
uv run uvicorn server.main:app --reload   # not built yet
cd web && npm run dev                      # not built yet
```

Implemented stages: `parse` = measure (`parse.py`) -> profile (`profile.py`, writes `data/profiles/<book>.auto.toml`; hand corrections go in `profiles/<book>.toml`, committed) -> tag (`tag.py`) -> sentences (`sentences.py`, spaCy) -> back matter (`backmatter.py`: glossary and index entries into `labels`). `--stage profile`, `--stage tag`, `--stage sentences` and `--stage backmatter` rerun from that step on. Evals: `eval/tag_accuracy.py`, `eval/sentence_accuracy.py`. Test books: `language_in_mind.pdf` (v0) and `myers_ap_psych.pdf` (generalization check). Tagging accuracy: `uv run python eval/tag_accuracy.py`. The real-book test in `pipeline/tests/test_parse.py` is skipped when the PDF is absent.

## Evaluation

- `candidates`: precision and recall against the book's glossary (about 250 terms) and subject index.
- `clues` and `assemble`: a hand-checked golden set of about 20 answers in `eval/`, with expected clues listed as sentence ids, and a test that no tossup leaks its answer.
- Pyramidality: clue difficulty should be non-increasing through each tossup.

## Working style

- Work one stage at a time, in pipeline order. A stage is done when it has tests and sample output Chong has looked at.
- Record each algorithm decision in `docs/decisions.md`: what was chosen, what was rejected, and why.
- When a stage is hard to do well without an LLM (coreference, masking), say so and propose the best classical approach. Do not quietly lower quality or reach for a model.

## Open decisions

- Answer vs. clue-only vs. too-broad thresholds: TBD, to be tuned against the index and glossary.
- How much weight QBReader signals get relative to textbook signals in `rank`: TBD.
- How clue difficulty is scored (textbook emphasis, mention counts, QBReader signals): TBD.
- How clue uniqueness is measured, and the threshold a lead-in must pass: TBD. The same measure drops vague clues.
- How the referent ("this hypothesis") is chosen: TBD.
- How hostage clues (a guess between two named options) are detected without an LLM: TBD.
