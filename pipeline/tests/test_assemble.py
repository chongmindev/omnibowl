"""Assemble helpers on made-up sentences (no book text)."""

import pytest
import spacy

from omnibowl.assemble import answerline, answer_stems, bad_mention, leaks, mask


@pytest.fixture(scope="module")
def nlp():
    return spacy.load("en_core_web_sm", exclude=["ner", "lemmatizer"])


def test_mask_first_mention_and_title():
    text = "The well-known linguist Charles Hockett listed design features."
    a = text.index("Charles")
    out = mask(text, [(a, a + len("Charles Hockett"), "named")], "linguist", False)
    assert out == "The well-known this linguist listed design features." or out.startswith("This linguist") \
        or "this linguist listed" in out
    assert "linguist this linguist" not in out


def test_mask_capitalizes_at_start():
    assert mask("The N400 is an ERP.", [(4, 8, "named")], "effect", False) == "This effect is an ERP."


def test_bad_mentions(nlp):
    def why(text, word):
        a = text.index(word)
        return bad_mention(nlp(text), a, a + len(word))
    assert why("They measured N400 activity in readers.", "N400") == "modifier"
    assert why("Broca and Wernicke studied patients.", "Broca") == "coordinated"
    assert why("Words yield a bigger N400 in lists.", "N400") == "modified"
    assert why("The N400 reflects meaning.", "N400") is None


def test_leaks_and_answerline():
    stems = answer_stems(["Broca", "Paul Broca"], "researcher", {})
    assert leaks("A Brocan view of speech.", stems)
    assert not leaks("This researcher examined a patient.", stems)
    html, plain = answerline("VOT", ["VOT", "voice onset time", "VOTs"])
    assert plain == "VOT [accept voice onset time]"


def test_bad_mention_hyphen_and_noun_modifiers(nlp):
    for text, word in [("Researchers used eye-tracking equipment.", "eye"),
                       ("Workers with low job stress lived longer.", "stress"),
                       ("It activates more motor cortex neurons.", "neurons")]:
        doc = nlp(text)
        a = text.index(word)
        assert bad_mention(doc, a, a + len(word)), text
    doc = nlp("Stress can set off hormonal changes.")
    assert bad_mention(doc, 0, 6) is None
