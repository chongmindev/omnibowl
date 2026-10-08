"""Candidate extraction rules on made-up sentences (no book text)."""

import pytest
import spacy

from omnibowl.candidates import concept_key, concept_spans, person_spans


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
