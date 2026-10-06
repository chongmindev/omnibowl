"""Profile discovery on synthetic inputs (no book text)."""

from omnibowl import profile


def test_size_bands_halfway_between_peaks():
    peaks = [(0.75, 0.3), (1.0, 0.6), (1.13, 0.02), (1.41, 0.01)]
    assert profile.size_bands(peaks)["body"] == [0.875, 1.065]
    # A book whose side stream is close to body size.
    peaks = [(0.8, 0.07), (0.9, 0.3), (1.0, 0.55), (1.1, 0.02)]
    assert profile.size_bands(peaks)["body"] == [0.95, 1.05]


def test_merge_overrides_by_key():
    auto = {"book": "b", "sizes": {"body": [0.9, 1.1], "peaks": []},
            "furniture": {"page_numbers": True}, "side_bold": {"role": "caption"},
            "markers": [{"pattern": "GO TO", "role": "box", "count": 13}],
            "panel_styles": [{"fill": [0.9, 0.9, 0.9], "role": "box"}]}
    override = {"side_bold": {"role": "definition"},
                "markers": [{"pattern": "GO TO", "role": "exercise"},
                            {"pattern": "NEW ONE", "role": "box"}],
                "panel_styles": [{"fill": [0.9, 0.9, 0.9], "role": "table"}]}
    p = profile.merge(auto, override)
    assert p["side_bold"]["role"] == "definition"
    assert {m["pattern"]: m["role"] for m in p["markers"]} == {"GO TO": "exercise", "NEW ONE": "box"}
    assert p["markers"][0]["count"] == 13
    assert p["panel_styles"][0]["role"] == "table"
    assert auto["markers"][0]["role"] == "box"  # auto profile untouched


def test_toml_round_trip(tmp_path):
    import tomllib
    p = {"book": "b", "sizes": {"body": [0.9, 1.1], "peaks": [[1.0, 0.5]]},
         "furniture": {"page_numbers": False, "running_heads": ["X N"], "margin_zone": 0.06},
         "side_bold": {"role": "caption", "evidence": "3 lines"},
         "panel_styles": [{"fill": [0.9, 0.9, 0.9], "role": "box", "instances": 2}],
         "markers": [{"pattern": "AP® EXAM TIP", "role": "box", "count": 5}]}
    assert tomllib.loads(profile.to_toml(p)) == p
