"""Room rules with fake connections and fast timers. No book text: fake tossups only."""

import asyncio
import random

from server.room import Room, Settings
from server.tossups import Library, Tossup

FAST = Settings(buzz_window=0.15, answer_window=0.15, loading_delay=0.0, default_wpm=6000, max_wpm=60000)


def tossup(id: str, chapter: int, words: int = 6, prompt: tuple = ()) -> Tossup:
    return Tossup(id=id, chapter=chapter, section=f"{chapter}.1", sections=f"{chapter}.1",
                  words=tuple(f"w{i}" for i in range(words)), answer_html=f"<b><u>Answer {id}</u></b>",
                  accept=frozenset({f"answer {id}"}), prompt=frozenset(prompt), rewritten=True)


LIB = Library(book="Fake", chapters=((1, "One"), (2, "Two")),
              tossups=(tossup("a", 1, prompt=("answer",)), tossup("b", 2)))


class Fake:
    def __init__(self) -> None:
        self.msgs: list[dict] = []

    async def send_json(self, data) -> None:
        self.msgs.append(data)

    @property
    def state(self) -> dict:
        return [m for m in self.msgs if m["type"] == "state"][-1]


def room(**kw) -> Room:
    return Room(name="t", library=lambda: LIB, settings=kw.pop("settings", FAST), rng=random.Random(0), **kw)


async def until(conn: Fake, state: str, timeout: float = 2.0) -> dict:
    for _ in range(int(timeout / 0.005)):
        if conn.msgs and conn.state["state"] == state:
            return conn.state
        await asyncio.sleep(0.005)
    raise AssertionError(f"never reached {state}; at {conn.state['state']}")


def run(coro):
    return asyncio.run(coro)


def test_answer_hidden_until_judged():
    async def go():
        r, a = room(), Fake()
        await r.join(a, "p1", "alice")
        await until(a, "window")
        for m in a.msgs:
            assert "Answer" not in str(m)  # neither the answerline nor accepted forms leak
        s = await until(a, "judged")  # buzz window runs out
        assert s["result"]["answer"].startswith("<b><u>Answer")
        assert s["result"]["buzzes"] == [] and s["players"][0]["points"] == 0
        assert s["players"][0]["heard"] == 1
    run(go())


def test_first_buzz_locks_and_scores_neg_then_correct():
    async def go():
        slow = Settings(buzz_window=1, answer_window=1, loading_delay=0, default_wpm=600)
        r, a, b = room(settings=slow), Fake(), Fake()
        await r.join(a, "p1", "alice")
        await r.join(b, "p2", "bob")
        await until(a, "reading")
        await r.handle(a, {"type": "buzz"})
        await r.handle(b, {"type": "buzz"})  # too late: alice holds the buzz
        s = b.state
        assert s["state"] == "buzzed" and s["buzzer"] == "p1"
        await r.handle(a, {"type": "answer", "text": "wrong"})
        s = a.state
        assert s["state"] == "reading" and "p1" in s["locked"]  # reveal resumes for bob
        await r.handle(a, {"type": "buzz"})  # locked out
        assert a.state["buzzer"] is None
        await r.handle(b, {"type": "buzz"})
        tid = r.tossup.id
        await r.handle(b, {"type": "answer", "text": f"Answer {tid}!"})
        s = a.state
        assert s["state"] == "judged"
        pts = {p["pid"]: p["points"] for p in s["players"]}
        assert pts == {"p1": -5, "p2": 10}
        assert [p["pid"] for p in s["players"]] == ["p2", "p1"]  # ranked
        assert s["log"][0]["pid"] == "p2" and s["log"][0]["points"] == 10
        assert s["log"][0]["question"] == "w0 w1 w2 w3 w4 w5"  # rereadable once finished
        assert [b["pid"] for b in s["log"][0]["buzzes"]] == ["p1", "p2"]
    run(go())


def test_wrong_after_question_ends_is_zero_and_last_player_ends_tossup():
    async def go():
        r, a = room(), Fake()
        await r.join(a, "p1", "alice")
        await until(a, "window")
        await r.handle(a, {"type": "buzz"})
        await r.handle(a, {"type": "answer", "text": "nope"})
        s = a.state
        assert s["state"] == "judged"  # everyone present has buzzed
        assert s["players"][0]["points"] == 0 and s["result"]["buzzes"][0]["points"] == 0
    run(go())


def test_answer_timeout_submits_typed_text():
    async def go():
        r, a, b = room(), Fake(), Fake()
        await r.join(a, "p1", "alice")
        await r.join(b, "p2", "bob")
        await until(a, "window")
        await r.handle(a, {"type": "buzz"})
        await r.handle(a, {"type": "typing", "text": f"answer {r.tossup.id}"})
        assert {"type": "typing", "pid": "p1", "text": f"answer {r.tossup.id}"} in b.msgs
        s = await until(a, "judged")
        assert s["result"]["buzzes"][0]["points"] == 10
    run(go())


def test_prompt_once_then_reject():
    async def go():
        r, a = room(), Fake()
        await r.join(a, "p1", "alice")
        await r.handle(a, {"type": "chapters", "chapters": [1]})
        await r.handle(a, {"type": "next"})
        await until(a, "judged")
        await r.handle(a, {"type": "next"})  # chapter 1 only: tossup "a", which has a prompt form
        await until(a, "window")
        await r.handle(a, {"type": "buzz"})
        await r.handle(a, {"type": "answer", "text": "answer"})
        assert a.state["state"] == "buzzed" and a.state["prompted"]
        await r.handle(a, {"type": "answer", "text": "answer"})
        assert a.state["state"] == "judged" and a.state["result"]["buzzes"][0]["points"] == 0
    run(go())


def test_chapters_filter_and_empty_state():
    async def go():
        r, a = room(), Fake()
        await r.join(a, "p1", "alice")
        await r.handle(a, {"type": "chapters", "chapters": []})
        await r.handle(a, {"type": "next"})  # skip the running tossup
        await r.handle(a, {"type": "next"})
        s = a.state
        assert s["state"] == "empty" and s["empty_reason"] == "no_chapters"
        await r.handle(a, {"type": "chapters", "chapters": [2, 99]})
        s = a.state
        assert s["selected"] == [2] and s["state"] == "loading" and s["tossup"]["chapter"] == 2
    run(go())


def test_names_wpm_and_rejoin_keep_score():
    async def go():
        r, a = room(), Fake()
        await r.join(a, "p1", "  ")
        assert a.state["players"][0]["name"] == "guest"
        await r.handle(a, {"type": "name", "name": "x" * 30})
        assert a.state["players"][0]["name"] == "x" * 20
        await r.handle(a, {"type": "name", "name": "   "})  # empty keeps the old name
        assert a.state["players"][0]["name"] == "x" * 20
        await r.handle(a, {"type": "wpm", "wpm": 10})
        assert a.state["wpm"] == FAST.min_wpm
        r.players["p1"].points = 30
        b = Fake()
        await r.join(b, "p2", "bob")
        await r.leave(a)
        assert [p["away"] for p in b.state["players"] if p["pid"] == "p1"] == [True]
        a2 = Fake()
        await r.join(a2, "p1", "ignored")
        p1 = [p for p in a2.state["players"] if p["pid"] == "p1"][0]
        assert p1["points"] == 30 and not p1["away"] and p1["name"] == "x" * 20
    run(go())
