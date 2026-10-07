"""Display-only mandate labels: synthetic preview and actual CLI read branch."""
import ast
from copy import deepcopy
from pathlib import Path
import sys

import pytest
from bellomberg.core import mandato_pm as mp
from bellomberg.core.language import language_context


@pytest.mark.parametrize("language,label", [("it", "soglia minima d'azione"), ("en", "minimum action amount")])
def test_preview_opt_in_same_fingerprint_no_write(tmp_path, monkeypatch, language, label):
    values = mp.profilo_esempio()
    original = deepcopy(values)
    written = []
    def denied(*a, **k):
        written.append((a, k))
        raise AssertionError("view must not write")
    monkeypatch.setattr(mp, "_scrivi_atomico", denied)
    monkeypatch.setattr(mp, "salva", denied)
    before = list(tmp_path.rglob("*"))
    with language_context(language):
        out = mp.anteprima(values)
    assert label in out["testo"]
    assert out["impronta"] == mp.impronta(values)
    assert out["output_language"] == language
    assert values == original
    assert written == [] and list(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("language,label", [("it", "soglia minima d'azione"), ("en", "minimum action amount")])
def test_actual_cli_read_branch_opt_in_no_example_write(monkeypatch, capsys, language, label):
    tree = ast.parse(Path(mp.__file__).read_text(encoding="utf-8"))
    branch = next(n for n in tree.body if isinstance(n, ast.If) and "__name__" in ast.unparse(n.test))
    values = mp.profilo_esempio()
    before = deepcopy(values)
    calls = []
    def load():
        calls.append("read")
        return values
    def no_write(*args, **kwargs):
        calls.append("write")
        raise AssertionError("CLI read branch must not write example")
    namespace = {**mp.__dict__, "__name__": "__main__", "carica": load, "scrivi_esempio": no_write}
    monkeypatch.setattr(sys, "argv", ["mandato_pm"])
    with language_context(language):
        exec(compile(ast.Module(body=[branch], type_ignores=[]), mp.__file__, "exec"), namespace)
    assert label in capsys.readouterr().out
    assert calls == ["read"] and values == before


def test_general_renderer_and_economic_fingerprint_remain_legacy():
    values = mp.profilo_esempio()
    old = mp.blocco_prompt(values)
    assert "posizione minima" in old
    assert "soglia minima d'azione" not in old
    assert mp.anteprima(values)["impronta"] == mp.impronta(values)
