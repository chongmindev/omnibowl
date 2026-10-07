# Reader v0: design spec

Date: 2026-10-07. Status: draft for Chong's review.
Visual and interaction system: `DESIGN.md` (this spec does not repeat it).

## Goal

A single-player, protobowl-style reader for one textbook (*Language in Mind*). It
reveals tossups word by word, takes a buzz, checks the answer, scores it, and logs the
buzz. It reads the same tossup format the pipeline will write, so generated tossups
replace the stand-ins without changes to the reader.

## Decisions already made (2026-10-06/07)

- Reader first; the pipeline's tossup stages come after.
- One screen: reader on the left, sidebar on the right (textbook, scoreboard, speed,
  chapters); tossup log under the question. Bauhaus grid per `DESIGN.md`.
- The textbook is fixed. A "Change" control is shown disabled with a hover note.
- The scoreboard is a ranked player table; solo play is one row, so rooms (later) need
  no redesign. Multiplayer itself is out of scope.
- Speed changes only via the slider. Keys: Space buzz, Enter submit, → next/skip.
- A yellow time bar runs the buzz window after the question ends and the answer window
  after a buzz. No progress indicator while reading.
- **Stand-in tossups: real QBReader tossups**, stored with `source = 'qbreader'` and
  labeled as QBReader in the UI. They are never presented as generated.

## Out of scope

Multiplayer and rooms, choosing a textbook, power marks, bonuses, accounts, results
screen, deploying anywhere public, generated tossups (they come from the pipeline later).

## Architecture

```
pipeline (Python) ── writes ──▶ data/omnibowl.db ◀── reads / logs buzzes ── server (FastAPI)
                                                                              │  JSON over HTTP
QBReader API ◀── cached proxy (seed script, check-answer) ── server            ▼
                                                                         web (Vite + TS)
```

- **server/**: FastAPI app. Reads books, sections and tossups; writes buzzes; proxies
  QBReader's `check-answer` with a cache. No pipeline imports except `omnibowl.config`
  and `omnibowl.db` for paths and the schema.
- **web/**: Vite + TypeScript, no framework. Static files in dev via Vite, proxied to
  the server at `/api`.
- **Stand-in seeding**: `omnibowl.qbreader` (a small client with an on-disk cache) and
  a one-off command `uv run python -m omnibowl.seed_qbreader` that fills the tossups
  table.

## Data

Two tables join the schema in `pipeline/omnibowl/db.py` (CLAUDE.md data model):

**tossups**: QBReader's tossup fields plus Omnibowl's.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | QBReader `_id`, or a generated id later |
| `question`, `question_sanitized` | TEXT | |
| `answer`, `answer_sanitized` | TEXT | `answer` keeps `<b>`/`<u>` for check-answer |
| `category`, `subcategory`, `alternate_subcategory` | TEXT | |
| `difficulty` | INTEGER | 0 to 10 |
| `number` | INTEGER | |
| `packet_json`, `set_json` | TEXT | `{_id, name, number}`, `{_id, name, year}` |
| `source` | TEXT | `generated` or `qbreader` |
| `book_id` | INTEGER | the textbook the tossup is filed under |
| `section_ids` | TEXT | JSON list; drives the chapter filter |
| `clue_ids` | TEXT | JSON list; empty for QBReader tossups |
| `pipeline_version` | TEXT | null for QBReader tossups |

**buzzes**: one row per judged tossup.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `tossup_id` | TEXT | |
| `session_id` | TEXT | random per page load |
| `word_index` | INTEGER | null if never buzzed |
| `word_count` | INTEGER | |
| `given_answer` | TEXT | |
| `directive` | TEXT | accept, reject, prompt-then-reject, none |
| `points` | INTEGER | +10, −5, 0 |
| `checked_offline` | INTEGER | 0/1 |
| `wpm` | INTEGER | speed at the time |
| `created_at` | TEXT | ISO timestamp |

## Stand-in tossups (seed)

Goal: real tossups that are on topic and filed under the right chapter.

1. Take each chapter's definition terms from the parsed sentences (`kind = 'definition'`;
   the term is the line's bold run), about 290 terms.
2. For each term, `GET /api/query` with `q=<term>`, `questionType=tossup`,
   `searchType=answer`, `exactPhrase=true`, `maxReturnLength=5`.
3. Keep tossups whose sanitized answer contains the term. File each under the
   chapter and section where the term is defined (`section_ids`).
4. Store with `source = 'qbreader'`. Deduplicate by `_id`.

Rules: every QBReader response is cached under `data/qbreader_cache/` (key = endpoint +
sorted params) and the cache is read first; requests are throttled under 20 per second;
nothing is bulk-downloaded. Before the first call, confirm parameter names against the
endpoint's docs page (already read on 2026-10-07: `/query` and `/check-answer`). If a
chapter ends up with no tossups, the reader shows the "none available" state for it.

## Server API

All JSON. The answer is never sent before the tossup is judged or skipped.

| Method and path | Request | Response |
|---|---|---|
| `GET /api/textbook` | none | `{title, author, edition, year, chapters: [{number, title, tossups}]}` |
| `GET /api/tossups/next` | `chapters=1,3,13`, `exclude=<ids>` | `{id, words: [...], chapter, section, source, set}`; 404 with `{reason}` when none. For `source = 'qbreader'`, `chapter`/`section` are where it is filed, and the header labels it QBReader with the set name and year (`DESIGN.md`, Header). |
| `POST /api/tossups/{id}/judge` | `{given_answer, word_index, word_count, finished, wpm, session_id}` | `{directive, directed_prompt, points, answer, checked_offline}` |
| `POST /api/tossups/{id}/reveal` | `{word_count, wpm, session_id}` (skip or time out) | `{answer, points: 0}` |

`judge` behavior:
- Calls QBReader `check-answer` with `answerline` = stored `answer` and `givenAnswer`.
  Cached by (answerline, normalized given answer).
- `prompt` returns without logging; the client asks again. A second prompt is judged
  as reject.
- If QBReader cannot be reached within 3 s, falls back to a local check (normalized
  given answer equals the underlined required part or the whole sanitized answer) and
  sets `checked_offline`.
- Points: accept +10; reject before the question finished −5; reject after it
  finished 0. Logs one row in `buzzes`.

Word splitting for the reveal happens on the server (whitespace split of
`question_sanitized`), so `word_index` always means the same thing in the log.

## Frontend

Modules (each one file, one job, testable alone):

- `api.ts`: fetch wrappers with timeouts; typed responses; raises a typed error the
  UI turns into the "server unreachable" state.
- `reader.ts`: the state machine. States: `loading → reading → window → buzzed →
  checking → (prompt → buzzed) → judged`, plus `empty` and `error`. Pure: given events
  (tick, buzz, submit, result, timeout, next) it returns the next state.
- `clock.ts`: word-reveal timer from wpm, and the time-bar timers. Paused on buzz.
- `score.ts`: points from directive and finished flag; the session scoreboard.
- `view/*.ts`: renders header, question, answer row, verdict, log, time bar, footer,
  and the sidebar sections from state. No logic.
- `main.ts`: wires keys and pointer events to the state machine.

Session state (score, log, chapters, wpm) lives in memory; chapters and wpm also go to
`localStorage` (wrapped in try/catch) so they survive a reload.

Timings (constants in `web/src/config.ts`, tunable): default speed 240 wpm, slider
range 120 to 600; buzz window 5 s; answer window 8 s.

## Error handling

Covered by the states table in `DESIGN.md`: server unreachable (retry), no tossups for
the selected chapters, QBReader unreachable (offline check), answer window timeout
(submit what's typed), buzz window timeout (0 and reveal).

## Testing

- **server**: pytest with FastAPI's `TestClient` against a temporary database seeded
  with two fake tossups (no book text): chapter filter, exclude list, the answer is
  absent before judging, scoring rules, prompt handling, offline fallback (QBReader
  client stubbed), buzz logging.
- **seed**: pytest with the QBReader client stubbed: filing by section, dedupe,
  cache-first behavior, throttle.
- **web**: Vitest for `reader.ts`, `clock.ts` and `score.ts` (state transitions,
  timings with fake timers, points).
- **by eye**: run both servers and play five tossups; check every state in the
  `DESIGN.md` table, keyboard only and pointer only, and at phone width.

## New dependencies (approving this spec approves them)

- Python: `fastapi`, `uvicorn` (server); `httpx` (dev only, required by `TestClient`).
  QBReader calls use the standard library.
- Web (dev): `vite`, `typescript`, `vitest`. Fonts from Google Fonts (League Spartan,
  Red Hat Mono).

## Commands (added to CLAUDE.md and README when built)

```
uv run python -m omnibowl.seed_qbreader          # fill stand-in tossups (cached)
uv run uvicorn server.main:app --reload          # API on :8000
cd web && npm install && npm run dev             # reader on :5173
cd web && npm test
```
