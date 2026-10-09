# Plan: optional LLM layer (grounded rewrite)

Status: proposal (2026-10-09), not built. Adopting it changes ground rule 1 in
`CLAUDE.md` (see "Rule change" below); until then the pipeline stays LLM-free.

## Idea in one line

The pipeline keeps deciding **what** goes into a tossup (which answer, which facts, which
sentences they came from, in what order). An LLM only improves **how it reads**, plus a
few judgments classical methods do badly, and every rewritten clue is checked against
its source sentences before it is kept.

## What the LLM does, and what it never does

| Job | Today (classical) | With the LLM layer | Guardrail |
|---|---|---|---|
| Clue wording | Book sentence with the answer swapped for "this X"; often clunky | Rephrase each selected clue into a tight quizbowl clue | May not add facts, numbers or names that are not in the clue's source sentences |
| Referent and giveaway descriptor | Book's wording or "this term" (generic for ~1 in 3 answers) | Choose "this ERP component", "this Swiss psychologist" | One short noun phrase; must not contain the answer or its stems (leak check stays) |
| Difficulty | Emphasis + specificity score | Add a third signal: the LLM's 1-5 difficulty rating per clue | One input to `rank`'s weighted score, not the final order |
| Uniqueness | Not checked yet | Flag clues that could also describe another answer in the book | Flags only; `assemble` decides what to drop |
| Verification | n/a | Second pass: is each rewritten clue fully supported by its cited source sentences? | Unsupported clues revert to the original wording or are dropped |

Never: choosing answers, choosing facts, reading the whole book, writing clues from its
own knowledge, or deciding the final order alone.

## How it runs

```
clues -> rank -> assemble -> [rewrite] -> [verify] -> tossups
```

1. `assemble` builds tossups exactly as today and stores them.
2. **`rewrite`** (new stage) sends one request per tossup: the answer, its accepted
   forms, its definition, and the 4-7 selected clues, each with its source sentence(s).
   It returns JSON (structured outputs): a rewritten clue per input clue (same order,
   same count), a referent, a giveaway descriptor, a 1-5 difficulty per clue, and
   uniqueness flags.
3. **`verify`** (new stage) sends one request per tossup: each rewritten clue next to
   its source sentences, asking "supported / not supported / adds information" per clue.
   Unsupported clues fall back to the classical wording.
4. Both stages store their outputs with the model ID, prompt version and a hash of the
   inputs, so a rerun only calls the API for tossups whose inputs changed.
5. The reader and evals can show either version: `source = generated` plus a
   `rewritten` flag, so raw and rewritten tossups can be compared side by side.

## Is it an API call to Claude?

Yes. Each request goes to Anthropic's **Claude API** (the Messages API) through the
official `anthropic` Python SDK (`client.messages.create(...)`), or through the
**Message Batches API**, which runs the same requests asynchronously (usually within an
hour, at most 24 hours) at half price. Nothing runs in the cloud except these requests;
the rest of the pipeline stays local.

What is sent: the selected clue sentences for each tossup (a few hundred words per
tossup), not the PDF or the whole book. Across a book that is still a large share of its
key sentences, which is another reason generated tossups from a copyrighted book stay
personal.

## Will you get billed?

Only if you set it up and run it:

- **API usage is billed separately** from a Claude.ai subscription (Pro/Max) and from
  Claude Code. It goes to an organization in the **Claude Console**
  (platform.claude.com), where you add credits or a payment method, create an API key,
  and can set a monthly **spend limit**.
- The pipeline only calls the API when you run `--stage rewrite` / `--stage verify`
  with a key configured. Without a key, nothing is sent and nothing is billed.
- Every response reports exact token usage; the stage would print a running total and
  refuse to start if the estimated cost exceeds a limit set in `config.toml`.

## Model and price

Default model: **Claude Opus 5.5** (`claude-opus-5-5`): $4 per million input tokens,
$20 per million output tokens, cached input reads $0.20 per million. A cheaper option
is **Claude Sonnet 5.5** (`claude-sonnet-5-5`): $2 / $10 per million. That is a quality
vs. cost choice to make after a pilot, not a default.

Cost levers that do not reduce quality:
- **Prompt caching**: the instructions and examples (the same for every tossup) are
  marked cacheable; repeat reads cost 5% of the normal input price on Opus 5.5. The
  minimum cacheable prefix on these models is 512 tokens, which the system prompt
  exceeds.
- **Batch API**: all tossups for a book go in one batch at 50% off; nothing here needs
  an instant answer.
- **Effort**: thinking tokens are billed as output and dominate cost; rewriting is a
  modest task, so run at `low` or `medium` effort and raise it only if the pilot shows
  better clues at higher effort.

## Token estimate (one book, ~220 tossups)

Per tossup, rough sizes:

| | Rewrite call | Verify call |
|---|---|---|
| Instructions + examples (cached after the first call) | ~1,800 tokens | ~600 tokens |
| Tossup-specific input | ~1,200 tokens (answer, definition, 4-7 clues with source sentences) | ~800 tokens (rewritten clues + sources) |
| Output, including thinking at low/medium effort | ~1,500 tokens | ~650 tokens |

For ~220 tossups: about **440,000 uncached input tokens**, **400,000 cached input
tokens**, and **470,000 output tokens**.

| Model | Standard | With Batch API (50% off) |
|---|---|---|
| Claude Opus 5.5 | ~$11 per book | **~$6 per book** |
| Claude Sonnet 5.5 | ~$6 per book | ~$3 per book |

These are estimates, possibly off by half in either direction; thinking length is the
biggest unknown. Reruns cost only the changed tossups. Before a full run: count tokens on
real requests (`messages.count_tokens`) and run a **10-tossup pilot (well under $1)**,
then report measured cost per tossup.

## How we would know it helps

- **Verification pass rate**: share of rewritten clues judged fully supported (target:
  nearly all; anything unsupported reverts).
- **Blind comparison**: you play or read raw vs. rewritten versions of the same tossups
  without knowing which is which, and pick the better one.
- **Existing evals unchanged**: answers and clue selection come from the pipeline, so
  candidate and clue scores do not move; only readability and referents should.
- **Leak and length checks** re-run on rewritten tossups.

## Rule change

Ground rule 1 would become: "LLMs may rephrase, label and judge pipeline output; every
fact must come from cited textbook sentences and pass automatic verification. No LLM
chooses answers or facts." The rewrite/verify stages stay optional (off by default) so
the classical pipeline always runs on its own.

## Build steps (when approved)

1. Add `anthropic` as a dependency; read the key from the environment, never from code.
2. `rewrite.py` and `verify.py` stages with structured-output schemas, prompt caching on
   the instructions, batch submission and result polling, input hashing for reruns, and
   a cost ceiling in config.
3. Store rewritten text, verdicts, model ID, prompt version and token usage.
4. 10-tossup pilot; report measured tokens, cost, verification pass rate and examples.
5. Full run on approval; blind comparison in the player.
