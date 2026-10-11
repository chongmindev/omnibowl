"""Candidate extraction rules on made-up sentences (no book text)."""

import pytest
import spacy

from omnibowl.candidates import capitalized_share, concept_key, concept_spans, person_spans, topic_names


@pytest.fixture(scope="module")
def nlp():
    return spacy.load("en_core_web_sm")


def keys(nlp, text, max_words=5):
    return {concept_key(s) for s in concept_spans(nlp(text), max_words)}


def test_noun_phrases_and_nested(nlp):
    k = keys(nlp, "Readers struggle with garden path sentences.")
    assert {"garden path sentence", "path sentence", "sentence"} <= k


def test_possessive_and_of(nlp):
    k = keys(nlp, "Damage to Broca's area impairs the production of speech.")
    assert "broca's area" in k
    assert "production of speech" in k


def test_hyphenated(nlp):
    assert "speech-in-noise" not in keys(nlp, "We tested speech perception.")  # sanity
    assert "top-down processing" in keys(nlp, "This is an example of top-down processing.")


def test_max_words(nlp):
    k = keys(nlp, "The large blue wooden kitchen table broke.", max_words=3)
    assert all(len(x.split()) <= 3 for x in k)


def test_people_grouped_by_surname(nlp):
    doc = nlp("Paul Broca examined a patient in 1861.")
    assert [key for _, key in person_spans(doc)] == ["broca"]


def test_group_key_variants():
    from omnibowl.candidates import group_key
    syn = {"word": "term", "name": "term", "term": "term"}
    assert group_key("color term", syn) == group_key("color word", syn) == group_key("term for color", syn)
    assert group_key("word order", syn) == group_key("order of word", syn)   # modifier "word" not mapped
    assert group_key("word order", syn) != group_key("term order", syn)
    assert group_key("phoneme", syn) == "phoneme"


def test_drop_subsumed_fragments():
    from omnibowl.candidates import drop_subsumed
    found = {("concept", "head-turn preference paradigm"): [(1, 0, 29, ""), (2, 0, 29, "")],
             ("concept", "turn preference paradigm"): [(1, 5, 29, ""), (2, 5, 29, "")],
             ("concept", "paradigm"): [(1, 21, 29, ""), (3, 0, 8, "")]}
    kept = drop_subsumed(found, 0.9)
    assert ("concept", "turn preference paradigm") not in kept
    assert ("concept", "head-turn preference paradigm") in kept
    assert ("concept", "paradigm") in kept          # single words are judged by keyness


def test_leading_modifier_and_initials(nlp):
    from omnibowl.candidates import concept_key, concept_spans
    ks = {concept_key(s) for s in concept_spans(nlp("Other languages differ, as J. Mem said."), 5, frozenset({"other"}))}
    assert "other language" not in ks and "language" in ks
    assert "j. mem" not in ks


def test_citation_parentheticals():
    from omnibowl.candidates import citation_parens
    text = "Listeners adapt (Adapted from Kamide et al., 2003, J. Mem. Lang.) to talkers (see Figure 2)."
    refs = citation_parens(text)
    assert len(refs) == 1 and text[refs[0][0]:refs[0][1]].endswith("Lang.)")


def test_abbreviations_merge():
    from omnibowl.candidates import abbreviation_pairs, long_form, merge_abbreviations
    assert long_form("VOT", "a continuous dimension of voice onset time") == "voice onset time"
    assert long_form("XYZ", "nothing matching here") is None
    texts = ["We measured voice onset time (VOT) for each sound.", "ERPs (event-related potentials) were recorded."]
    pairs = abbreviation_pairs(texts)
    assert ("VOT", "voice onset time") in pairs and ("ERP", "event-related potentials") in pairs
    found = {("concept", "vot"): [(1, 0, 3, "VOT")] * 3,
             ("concept", "voice onset time"): [(2, 0, 16, "voice onset time")],
             ("concept", "erp"): [(3, 0, 3, "ERP")], ("concept", "erps"): [(4, 0, 4, "ERPs")],
             ("concept", "event-related potential"): [(5, 0, 23, "event-related potentials")]}
    merged = merge_abbreviations(found, texts)
    assert ("concept", "voice onset time") not in merged and len(merged["concept", "vot"]) == 4
    assert ("concept", "erps") not in merged and len(merged["concept", "erp"]) == 3


def test_topic_names_from_headings_and_labels():
    assert topic_names("The Eye") == ["eye"]
    assert topic_names("Sleep and Dreams") == ["sleep and dreams", "sleep", "dreams"]
    assert "eye" in topic_names("Information Processing in the Eye and Brain")
    assert topic_names("Eye, structure of") == ["eye"]
    assert topic_names("The Role of Sleep") == ["role of sleep", "sleep"]
    assert topic_names("event-related potentials (ERPs)") == ["event-related potentials", "erps"]
    assert topic_names("Module 21 Vision") == ["vision"]
    assert topic_names("3.2 Speech Perception") == ["speech perception"]


def test_surname_merge_needs_capitals():
    def m(surfaces):
        return [(0, 0, 0, s) for s in surfaces]
    assert capitalized_share(m(["Broca", "Broca"])) == 1.0
    assert capitalized_share(m(["memory"] * 9 + ["Memory"])) < 0.5
