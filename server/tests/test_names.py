import json
import shutil
import subprocess
from pathlib import Path

import pytest

from server.names import NameRules, name_ok

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CASES = json.loads((HERE / "name_cases.json").read_text())


@pytest.mark.parametrize("case", CASES["cases"])
def test_python_names(case):
    name, want = case
    assert name_ok(name, NameRules.from_json(CASES["rules"])) is want


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_names_agree():
    script = (f"const j=require({json.dumps(str(ROOT / 'tools' / 'judge.js'))});"
              f"const c={json.dumps(CASES)};const r=j.nameRules(c.rules);"
              "console.log(JSON.stringify(c.cases.map(x=>j.nameOk(x[0],r))))")
    got = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert got == [c[1] for c in CASES["cases"]]


def test_blocked_word_list_loads():
    words = [w for w in (ROOT / "server" / "blocked_words.txt").read_text().splitlines()
             if w.strip() and not w.startswith("#")]
    rules = NameRules.from_json({"blocked": words, "edge_min_letters": 4})
    assert len(rules.phrases) > 300 and rules.edges
