"""Clue difficulty features (difficulty.py), pyramidality metrics (pyramid.py), and the
QBReader phrase stats read from the cache. No network: the cache is filled by hand."""

import hashlib
import json

from omnibowl import difficulty, pyramid, qbreader

R = {"detail_saturation": 3, "familiar_tossups": 50, "unknown_pos": 0.5,
     "w_emphasis": 0.5, "w_detail": 0.5, "w_familiarity": 0.0}


def test_repetition_counts_other_sentences_only():
    ws = [{"infan", "stati", "sylla", "learn"}, {"infan", "stati", "sylla", "track"},
          {"infan", "stati", "sylla", "learn"}, {"brain", "regio"}]
    rep = difficulty.repetition(ws, [1, 2, 1, 3], min_shared=3)
    # clue 0 is restated by sentence 2 (clue 2 shares its sentence, so it does not count)
    assert rep == [0.5, 0.5, 0.5, 1.0]


def test_detail_markers():
    assert difficulty.detail("This effect is common.", 0, R) == 0.0
    # citation counts once (its year is not a second marker), plus 40 percent and a quote
    assert difficulty.detail("Saffran (Saffran, 1996) found 40% of infants heard “bidaku.”", 0, R) == 1.0
    assert difficulty.detail("Two researchers studied it.", 2, R) == 2 / 3


def test_familiar_late_phrase_is_easy_unknown_is_hard():
    easy = difficulty.phrase_hardness(500, 0.95, R)
    early = difficulty.phrase_hardness(500, 0.05, R)
    unknown = difficulty.phrase_hardness(0, None, R)
    assert easy < early and unknown == 1.0
    assert difficulty.familiarity([(0, None), (500, 0.95)], R) == easy
    assert difficulty.familiarity([], R) is None


def test_combine_skips_missing_and_unweighted():
    assert difficulty.combine({"emphasis": 1.0, "detail": 0.0, "familiarity": None}, R) == 0.5
    assert difficulty.combine({"emphasis": 1.0, "familiarity": 0.0}, R) == 1.0


def test_split_and_giveaway():
    q = ("In a study by Saffran et al. (1996), infants heard “bidaku.” This method uses e.g. words. "
         "For 10 points, name this method.")
    sents, give = pyramid.clue_sentences(q)
    assert len(sents) == 2 and give.startswith("For 10 points")


def test_link_and_order_metrics():
    stop = {"this", "the", "that"}
    sources = ["Infants tracked transitional probabilities between syllables.",
               "The method was first described by Fantz in the 1960s."]
    sents = ["Fantz first described this method in the 1960s.", "Infants tracked syllable probabilities."]
    assert pyramid.link(sents, sources, stop, 5, 0.3) == [1, 0]
    assert pyramid.pair_agreement([0.9, 0.5, 0.1]) == 1.0
    assert pyramid.pair_agreement([0.1, 0.5]) == 0.0
    assert pyramid.dropbacks([0.9, 0.2, 0.6, 0.1], 0.1) == 1
    assert pyramid.order_agreement({0: 0.2, 1: 0.8, 2: 0.5}, [1, 2, 0]) == 1.0


def test_phrase_stats_reads_cache(tmp_path, monkeypatch):
    cfg = {"paths": {"qbreader_cache": str(tmp_path)},
           "qbreader": {"max_return": 25, "min_interval_s": 0, "retries": 0, "timeout_s": 1,
                        "base_url": "http://invalid"}}
    monkeypatch.setattr(qbreader.config, "ROOT", tmp_path.parent)
    params = {"q": "Wernicke's area", "questionType": "tossup", "searchType": "question",
              "exactPhrase": "true", "maxReturnLength": 25}
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()
    (tmp_path / "query").mkdir()
    texts = ["Damage to Wernicke's area causes fluent aphasia. Name this.", "x " * 50 + "near Wernicke's area"]
    (tmp_path / "query" / f"{key}.json").write_text(json.dumps({"params": params, "response": {
        "tossups": {"count": 7, "questionArray": [{"question_sanitized": t} for t in texts]}}}))
    count, pos, found = qbreader.phrase_stats("Wernicke’s area", cfg)  # curly apostrophe searched straight
    assert count == 7 and found == 2 and 0.0 < pos < 0.7
