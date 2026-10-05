"""Il prerequisito `npm ci` si dichiara per nome: rosso con l'istruzione, mai skip (ZERO ROSSI 05/10)."""
import pytest

from tests import _prerequisiti_node as prereq


def _app_finta(tmp_path, *moduli):
    app = tmp_path / "app"
    for m in moduli:
        cartella = app / "node_modules" / m
        cartella.mkdir(parents=True)
        (cartella / "package.json").write_text('{"name": "%s"}' % m, encoding="utf-8")
    return app


def test_modulo_assente_fallisce_con_istruzione_npm_ci(tmp_path, monkeypatch):
    monkeypatch.setattr(prereq.shutil, "which", lambda nome: "/finto/node")
    app = _app_finta(tmp_path, "react")
    with pytest.raises(pytest.fail.Exception) as esito:
        prereq.richiedi_node_modules("react", "typescript", app=app)
    messaggio = str(esito.value)
    assert messaggio.startswith("prerequisito mancante: typescript assente")
    assert "react" not in messaggio.split("assente")[0]
    assert "`npm ci` nella cartella app/" in messaggio


def test_node_modules_intera_assente_e_un_fallimento_non_uno_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(prereq.shutil, "which", lambda nome: "/finto/node")
    with pytest.raises(pytest.fail.Exception) as esito:
        prereq.richiedi_node_modules("typescript", app=tmp_path / "app")
    assert not isinstance(esito.value, pytest.skip.Exception)
    assert "npm ci" in str(esito.value)


def test_moduli_presenti_anche_con_scope_tornano_il_percorso_di_node(tmp_path, monkeypatch):
    monkeypatch.setattr(prereq.shutil, "which", lambda nome: "/finto/node")
    app = _app_finta(tmp_path, "typescript", "@babel/parser")
    assert prereq.richiedi_node_modules("typescript", "@babel/parser", app=app) == "/finto/node"


def test_cartella_senza_package_json_non_conta_come_installata(tmp_path, monkeypatch):
    monkeypatch.setattr(prereq.shutil, "which", lambda nome: "/finto/node")
    app = tmp_path / "app"
    (app / "node_modules" / "typescript").mkdir(parents=True)
    with pytest.raises(pytest.fail.Exception, match="typescript assente"):
        prereq.richiedi_node_modules("typescript", app=app)


def test_node_assente_dal_path_fallisce_per_nome(tmp_path, monkeypatch):
    monkeypatch.setattr(prereq.shutil, "which", lambda nome: None)
    app = _app_finta(tmp_path, "typescript")
    with pytest.raises(pytest.fail.Exception, match="Node.js non e' nel PATH"):
        prereq.richiedi_node_modules("typescript", app=app)
