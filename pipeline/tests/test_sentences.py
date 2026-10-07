"""Stitching rules (no book text)."""

from collections import Counter

from omnibowl.sentences import drop_runin_label, join, keep_term_attached, merge_open_parens


def test_join_hyphen_by_book_vocabulary():
    counts = Counter({"information": 5, "well-known": 3})
    assert join("the informa-", "tion age", counts) == "the information age"
    assert join("his well-", "known essay", counts) == "his well-known essay"
    assert join("a rare-", "word here", Counter()) == "a rare-word here"   # unknown: keep


def test_join_dashes_and_spaces():
    assert join("the Earth—", "as a magnet", Counter()) == "the Earth—as a magnet"
    assert join("plain line", "next line", Counter()) == "plain line next line"


def test_term_stays_with_definition():
    text = "phonetics The study of speech sounds. It is old."
    bounds = [(0, 9), (10, 37), (38, 48)]
    assert keep_term_attached(bounds, text, "phonetics") == [(0, 37), (38, 48)]
    text = "placebo [pluh-SEE-bo] effect results from expectation. Next."
    bounds = [(0, 7), (8, 54), (55, 60)]
    assert keep_term_attached(bounds, text, "placebo [pluh-SEE-bo] effect") == [(0, 54), (55, 60)]
    assert keep_term_attached([(0, 5)], "short", "") == [(0, 5)]


def test_no_split_inside_parentheses():
    text = "Rates are falling (CDC, 2016; Twenge et al., 2016). Why?"
    bounds = [(0, 27), (28, 51), (52, 56)]
    assert merge_open_parens(bounds, text) == [(0, 51), (52, 56)]
    assert merge_open_parens([(0, 5), (6, 9)], "A b. C.") == [(0, 5), (6, 9)]


def test_caption_label_dropped():
    text = "Culture and normality Young men dress up. They dance."
    bounds = [(0, 41), (42, 53)]
    assert drop_runin_label(bounds, text, "Culture and normality") == [(22, 41), (42, 53)]
    assert drop_runin_label([(0, 10), (11, 20)], "Figure 2.1 The waggle", "Figure 2.1") == [(11, 20)]
    sentence_title = "A full sentence. More."
    assert drop_runin_label([(0, 16), (17, 22)], sentence_title, "A full sentence.") == [(0, 16), (17, 22)]
