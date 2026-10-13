"""Lead-in handles and how many answers in a pool they fit (handles.py)."""

import pytest
import spacy

from omnibowl.handles import fits, lead_handle


@pytest.fixture(scope="module")
def nlp():
    return spacy.load("en_core_web_sm", exclude=["ner"])


def test_lead_handle_is_first_this_phrase(nlp):
    h = lead_handle("Researchers damaged this organ in the study, and these cells died.", nlp)
    assert (h.text, h.det, h.head) == ("this organ", "this", "organ")
    assert lead_handle("A sentence with no handle.", nlp) is None


def test_plural_and_singular_share_a_lemma(nlp):
    f = fits({"neurons": ["In the brain, these cells transmit signals."], "glia": ["In the brain, this cell supports neurons."],
              "tongue": ["Speakers move this organ to make sounds."]}, nlp)
    assert f["cell"] == {"neurons", "glia"} and f["organ"] == {"tongue"}
