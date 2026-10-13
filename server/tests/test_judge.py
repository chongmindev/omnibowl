import json
import shutil
import subprocess
from pathlib import Path

import pytest

from server.judge import Rules, distance, judge

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CASES = json.loads((HERE / "judge_cases.json").read_text())


def test_distance():
    assert distance("phoneme", "phonme") == 1
    assert distance("ab", "ba") == 1
    assert distance("", "abc") == 3


@pytest.mark.parametrize("case", CASES["cases"])
def test_python_judge(case):
    given, accept, prompt, prompted, want, *previous = case  # previous: the prompted answer
    assert judge(given, accept, prompt, Rules.from_json(CASES["rules"]), prompted, *previous) == want


def test_stem_maps_inflections_together():
    from server.judge import stem
    for words in [("test", "tests", "testing", "tested"), ("code", "coding", "coded"),
                  ("plan", "planning", "planned"), ("category", "categories"), ("process", "processing")]:
        assert len({stem(w, 4) for w in words}) == 1, words


def test_exact_only_by_default():
    assert judge("phonme", ["phoneme"], [], Rules(), False) == "reject"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_judge_agrees():
    script = (f"const j=require({json.dumps(str(ROOT / 'tools' / 'judge.js'))});"
              f"const c={json.dumps(CASES)};const r=j.judgeRules(c.rules);"
              "console.log(JSON.stringify(c.cases.map(x=>j.judgeAnswer(x[0],x[1],x[2],r,x[3],x[5]))))")
    got = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert got == [c[4] for c in CASES["cases"]]
