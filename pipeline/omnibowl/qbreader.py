"""QBReader API client (cached) and stage `qbreader`: how real quizbowl uses the book's terms.

Every response is cached under data/qbreader_cache/<endpoint>/<sha1 of params>.json and read
from there first; the network is used only for a request never made before, at most one
request per `min_interval_s`. Endpoint parameters follow https://www.qbreader.org/tools/api-docs/query.

Stage `qbreader` (decision "clue difficulty: A + B2"): for every book term mentioned inside
some clue (classes in `phrase_classes`), search QBReader tossup texts for the exact phrase
and store how many tossups use it and where in those questions it tends to sit (0 = first
word, 1 = last). Stage `rank` turns these into a familiarity score per clue. Nothing here is
book-specific; a book whose terms quizbowl never uses simply gets no familiarity signal.
"""

import hashlib
import json
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import config, db

_last_call = 0.0
_lock = threading.Lock()


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def get(endpoint: str, params: dict, cfg: dict) -> dict:
    """GET <base_url>/<endpoint> with params, through the cache."""
    q = cfg["qbreader"]
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()
    path = config.ROOT / cfg["paths"]["qbreader_cache"] / endpoint.strip("/") / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())["response"]
    global _last_call
    with _lock:  # requests start at least min_interval_s apart, across threads
        wait = q["min_interval_s"] - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
    url = f"{q['base_url']}/{endpoint.strip('/')}?" + urllib.parse.urlencode(params)
    for attempt in range(q["retries"] + 1):
        try:
            with urllib.request.urlopen(url, context=_ssl_context(), timeout=q["timeout_s"]) as r:
                response = json.load(r)
            break
        except OSError:
            if attempt == q["retries"]:
                raise
            time.sleep(q["min_interval_s"] * 10 * (attempt + 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"params": params, "response": response}))
    return response


def query_phrase(phrase: str) -> str:
    """The search form of a term: curly apostrophes and quotes made straight."""
    return phrase.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').strip()


def phrase_stats(phrase: str, cfg: dict) -> tuple[int, float | None, int]:
    """(tossups whose text contains the phrase, mean relative position of its first
    occurrence among the returned ones, how many returned ones it was found in)."""
    q = query_phrase(phrase)
    resp = get("query", {"q": q, "questionType": "tossup", "searchType": "question", "exactPhrase": "true",
                         "maxReturnLength": cfg["qbreader"]["max_return"]}, cfg)
    tossups = resp["tossups"]
    pat = re.compile(r"(?<![A-Za-z])" + re.escape(q.lower()) + r"(?![A-Za-z])")
    pos = []
    for t in tossups["questionArray"]:
        text = (t.get("question_sanitized") or t.get("question") or "").lower().replace("’", "'")
        if text and (m := pat.search(text)):
            pos.append(m.start() / len(text))
    return tossups["count"], (sum(pos) / len(pos) if pos else None), len(pos)


def phrase_terms(conn, book_id: int, classes: list[str]) -> list:
    """Book terms (in `classes`) mentioned inside the span of some other answer's clue."""
    return conn.execute(
        f"""SELECT DISTINCT t.id, t.display FROM clues c
            JOIN mentions m ON m.sentence_id = json_extract(c.sentence_ids, '$[0]')
                 AND m.start_char >= c.start_char AND m.end_char <= c.end_char AND m.term_id != c.term_id
            JOIN terms t ON t.id = m.term_id AND t.class IN ({','.join('?' * len(classes))})
            WHERE c.book_id = ? ORDER BY t.id""", (*classes, book_id)).fetchall()


def run(book_id: int, conn, cfg: dict) -> None:
    db.clear(conn, book_id, db.QBREADER_TABLES)
    terms = phrase_terms(conn, book_id, cfg["qbreader"]["phrase_classes"])
    done = [0]

    def one(t):
        stats = phrase_stats(t["display"], cfg)
        done[0] += 1
        if done[0] % 250 == 0:
            print(f"  {done[0]}/{len(terms)} phrases", flush=True)
        return (book_id, t["id"], query_phrase(t["display"]), *stats)

    # QBReader answers a text search in 1 to 2 s, so a few requests run at once (still far
    # under the rate limit); results keep term order, so the stage stays deterministic.
    with ThreadPoolExecutor(cfg["qbreader"]["workers"]) as pool:
        rows = list(pool.map(one, terms))
    conn.executemany("""INSERT INTO qb_phrases (book_id, term_id, query, n_tossups, mean_pos, n_found)
                        VALUES (?, ?, ?, ?, ?, ?)""", rows)
    conn.commit()
    used = sum(1 for r in rows if r[3])
    print(f"  {len(rows)} phrases, {used} used in QBReader tossups")
