# Multiplayer rooms (v0)

A protobowl-style room for playing generated tossups with one or two other people. The
questions come from a copyrighted textbook: share the link privately and never deploy
this publicly.

## Run

```
uv run python tools/play.py        # writes data/play/tossups.json (the server reads only this)
uv run python -m server            # binds 0.0.0.0:8000 and prints the links
```

It prints a link for this machine and one for the same Wi-Fi, each carrying the private
key (`?k=...`). The first visit with the key sets a cookie and drops the key from the
address bar; later visits and the WebSocket need only the cookie. Without it every
page and socket is refused (403). The key lives in `data/server_key` (gitignored); set
`OMNIBOWL_KEY` to use a different one, or delete the file to rotate it.

Any path is a room: `/lobby`, `/psych101`, `/anything` (letters, digits, `-`, `_`).
Rooms live in memory and reset when the server restarts.

Development: `uv run uvicorn server.main:create_app --factory --reload` (localhost only).
Tests: `uv run pytest server/tests`.

## Same Wi-Fi

Send the "Same Wi-Fi" link. If it doesn't load on the other laptop:

- macOS may ask whether Python can accept incoming connections: allow it (System
  Settings > Network > Firewall).
- Campus and guest networks often block traffic between devices ("client isolation").
  Use a tunnel instead.

## Different networks

**Tailscale (recommended: private to the people you invite).** Install Tailscale on both
machines (`brew install --cask tailscale`, or tailscale.com/download) and sign in. Invite
the other student to your tailnet, or share just this machine with them (admin console >
Machines > Share). They then open `http://<your-tailscale-ip>:8000/lobby?k=<key>`, where
`tailscale ip -4` prints the address. Nothing is reachable from the public internet. Do
not use `tailscale funnel`, which publishes the port to everyone.

**cloudflared quick tunnel (no account; the URL is public, the key is the only lock).**

```
brew install cloudflared
cloudflared tunnel --url http://localhost:8000
```

It prints a random `https://<words>.trycloudflare.com` address. Send
`https://<words>.trycloudflare.com/lobby?k=<key>`. WebSockets pass through. Stop the
tunnel (Ctrl-C) when you finish playing, and rotate the key if the link went anywhere
it shouldn't have.

## How a room works

The server owns all game state; the page sends intents and renders what the server
broadcasts.

- **Reveal:** the server sends one word at a time at the room's speed (the slider sets
  it for everyone). The page never holds words that haven't been read.
- **Buzz:** the first buzz locks the question for everyone and starts an 8 s answer
  window; other players see the buzzer's typing live. A wrong answer locks that player
  out for the tossup and reading resumes for the rest, as on protobowl. The tossup ends
  on a correct answer, when everyone present has buzzed, or when the 5 s buzz window
  after the last word runs out.
- **Judging:** on the server (`server/judge.py`), with the same rules as the single-player
  page (`tools/judge.js`): the answerline or an accepted alternate, ignoring case,
  punctuation, articles and word order; small typos unless another book term is as
  close; a prompt on the last words of a multi-word answer, once per buzz (decision
  "answer judging"). The answerline reaches the page only after the tossup ends.
- **Scores:** +10 correct, −5 wrong before the last word, 0 wrong after it. If the answer
  window runs out, whatever was typed is submitted.
- **Next / skip:** after a tossup, → (or the yellow Next button) starts the next one for
  the whole room. Skipping a tossup while it is being read is off until someone ticks
  "Allow skipping mid-question" (sidebar, Room). The chapter filter is shared too.
- **Buzz markers:** each buzz is marked in the question text where it happened, with the
  player's color and result, for everyone, during the tossup and afterwards.
- **History:** finished tossups, "joined the game" and renames, newest first. Click a
  tossup (or focus it and press Enter) to reread the full question with its buzz
  markers, the answerline, who said what, and the sections its clues came from.
- **Players:** each browser tab is one player (a reload keeps your seat and score). Names
  start as `guest####`; click your name on the scoreboard to edit it in place (Enter or
  clicking away saves, Esc cancels). Players who close the page stay on the board as
  "away". The header doesn't name the chapter, since that would be a clue.

Timings and limits are in `server/room.py` (`Settings`).
