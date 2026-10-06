"""Tagger rules on synthetic lines and a synthetic profile (no book text)."""

import json

import pytest

from omnibowl.profile import normalize
from omnibowl.tag import Tagger, starts_definition

BOLD = 16
DEF_FILL = [0.94, 0.92, 0.96]
BOX_FILL = [0.97, 0.9, 0.86]
PROFILE = {
    "sizes": {"body": [0.875, 1.065]},
    "furniture": {"page_numbers": True, "running_heads": ["Chapter N Title"], "margin_zone": 0.06},
    "side_bold": {"role": "definition"},
    "panel_styles": [{"fill": DEF_FILL, "role": "definition"}, {"fill": BOX_FILL, "role": "box"}],
    "markers": [{"pattern": "BOX N.N", "role": "box"},
                {"pattern": "WEB ACTIVITY N.N", "role": "exercise"},
                {"pattern": "Module N REVIEW", "role": "summary"},
                {"pattern": "DIGGING DEEPER", "role": "box"},
                {"pattern": "What we still know", "role": "heading"}],
}
PANELS = {1: DEF_FILL, 5: BOX_FILL}


def line(text, rel=1.0, x0=77.0, y0=100.0, page=1, bold=False, panel=None, spans=None, section=1):
    spans = spans or [[text, 15.0 * rel, BOLD if bold else 0]]
    return {"text": text, "rel_size": rel, "x0": x0, "y0": y0, "y1": y0 + 12, "page": page,
            "bold_start": bold, "panel_id": panel, "spans_json": json.dumps(spans),
            "section_id": section}


@pytest.fixture
def tagger(cfg):
    return Tagger(cfg, PROFILE, page_height=800.0, panel_fill=PANELS)


def test_normalize():
    assert normalize("BOX 3.1") == "BOX N.N"
    assert normalize("Unit IV Review") == "Unit R Review"
    assert normalize("Multiple-Choice Questions1") == "Multiple-Choice Questions"


def test_markers_come_from_profile(tagger):
    assert tagger.marker(line("BOX 3.1", rel=1.41, bold=True)).kind == "box"
    assert tagger.marker(line("Module 7 REVIEW", rel=1.5, bold=True)).kind == "summary"
    assert tagger.marker(line("BOX 3.1", rel=1.41)) is None            # not bold
    assert tagger.marker(line("What we still know", rel=1.13, bold=True)) is None  # heading role
    assert tagger.marker(line("LANGUAGE AT LARGE 3.1", rel=1.41, bold=True)) is None  # not in profile


def test_furniture(tagger):
    assert tagger.outside(line("87", y0=790), None) == "furniture"
    assert tagger.outside(line("Chapter 3 Title", y0=10), None) == "furniture"
    assert tagger.outside(line("87", y0=400), None) == "body"


def test_outside_rules(tagger):
    assert tagger.outside(line("A", rel=2.25, bold=True), None) == "dropcap"
    assert tagger.outside(line("Some subsection", rel=1.13, bold=True), None) == "heading"
    assert tagger.outside(line("x" * 70, rel=1.13), None) == "body"
    assert tagger.outside(line("■"), None) == "furniture"
    assert tagger.outside(line("Figure 2.1", rel=0.75, bold=True), None) == "caption"
    d = line("term Definition text", rel=0.75, bold=True)
    assert tagger.outside(d, None) == "definition"
    cont = line("more definition", rel=0.75, y0=115)
    assert tagger.outside(cont, ("definition", d, 77.0)) == "definition"
    indented = line("An example sentence.", rel=0.75, x0=95, y0=115)
    assert tagger.outside(indented, ("definition", d, 77.0)) == "example"
    assert tagger.outside(line("in panel", rel=0.75, panel=1), None) == "definition"


def test_same_row_continues(tagger):
    label = line("Figure 2.1", rel=0.75, bold=True, y0=200)
    beside = line("Caption text", rel=0.75, x0=137, y0=200.5)
    assert tagger.outside(beside, ("caption", label, 77.0)) == "caption"


def test_box_region_ends_with_panel(tagger):
    r = tagger.marker(line("BOX 1.1", rel=1.41, bold=True))
    assert tagger.in_region(r, line("Box title", rel=1.41)) == "box"
    assert tagger.in_region(r, line("inside", rel=0.75, panel=5)) == "box"
    assert tagger.in_region(r, line("Figure 1.2", rel=0.75, bold=True, panel=5, x0=85)) == "caption"
    assert tagger.in_region(r, line("caption more", rel=0.75, panel=5, x0=85, y0=115)) == "caption"
    assert tagger.in_region(r, line("next paragraph", rel=0.75, panel=5, x0=96, y0=130)) == "box"
    assert tagger.in_region(r, line("H", rel=2.25, bold=True, panel=5)) == "dropcap"
    assert tagger.in_region(r, line("body again", rel=1.0)) is None


def test_open_region_side_content_ends_at_prose(tagger):
    r = tagger.marker(line("WEB ACTIVITY 2.1", bold=True))
    assert tagger.in_region(r, line("Title In this activity", bold=True)) == "exercise"
    assert tagger.in_region(r, line("side description", rel=0.75)) == "exercise"
    assert tagger.in_region(r, line("https://example.org/a-", rel=1.0)) == "exercise"
    assert tagger.in_region(r, line("activity-1", rel=1.0)) == "exercise"
    assert tagger.in_region(r, line("Prose resumes here.", rel=1.0)) is None


def test_open_region_body_content_runs_to_heading(tagger):
    r = tagger.marker(line("Module 1 REVIEW", rel=1.5, bold=True))
    assert tagger.in_region(r, line("objective?", rel=0.95, panel=1)) == "definition"  # item panel keeps role
    assert tagger.in_region(r, line("Summary prose.", rel=1.0)) == "summary"
    assert tagger.in_region(r, line("More summary prose.", rel=1.0)) == "summary"
    assert tagger.in_region(r, line("Next heading", rel=1.4, bold=True)) is None


def test_open_region_ends_at_new_section(tagger):
    r = tagger.marker(line("Module 1 REVIEW", rel=1.5, bold=True))
    assert tagger.in_region(r, line("Summary prose.", rel=1.0)) == "summary"
    assert tagger.in_region(r, line("New section prose.", rel=1.0, section=2)) is None


def test_essay_definitions(tagger):
    r = tagger.marker(line("DIGGING DEEPER", rel=1.41, bold=True))
    defn = line("term Definition", rel=0.75, spans=[["term", 11, BOLD], [" ", 11, 0], ["Definition", 11, 0]])
    prose = line("term is used", rel=0.75, spans=[["term", 11, BOLD], [" is used", 11, 0]])
    assert tagger.in_region(r, line("essay text", rel=0.75)) == "box"
    assert tagger.in_region(r, prose) == "box"
    assert tagger.in_region(r, defn) == "definition"
    assert tagger.in_region(r, line("continues and ends.", rel=0.75)) == "definition"
    assert tagger.in_region(r, line("essay again", rel=0.75)) == "box"


def test_starts_definition():
    assert starts_definition(line("x", spans=[["vocal folds", 11, BOLD], [" Also", 11, 0]]))
    assert starts_definition(line("x", spans=[["a", 11, BOLD], [" ", 11, 0], ["b", 11, BOLD], [" The", 11, 0]]))
    assert not starts_definition(line("x", spans=[["term", 11, BOLD], [" is", 11, 0]]))
    assert not starts_definition(line("x", spans=[["plain", 11, 0]]))
