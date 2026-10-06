# -*- coding: utf-8 -*-
"""Il workflow CI (`.github/workflows/ci.yml`) letto come lo legge GitHub: YAML valido, i tre
job attesi (Linux con matrice 3.12+3.14, desktop Windows, sorgente macOS), i passi che il
mandato 12/09 chiede al job macOS, nessun segreto nel file (la ANTHROPIC_API_KEY e' un dummy
DICHIARATO) e il job macOS che non spende minuti a pagamento sul repo privato senza un ordine
esplicito (i runner macOS costano ~10x Linux; gratuiti solo sui repo pubblici). Dal 05/10 anche
il job `suite-linux`: `pytest tests/` intero, `npm ci` prima, mai sulle PR, sul privato a comando.

Batteria OFFLINE: legge un file del repo, non chiama GitHub. `yaml` arriva con chromadb e
uvicorn[standard] (requirements.txt): se manca, il test cade con la causa, non salta.
"""
import os
import re
import sys

import pytest
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(REPO, ".github", "workflows", "ci.yml")


def _testo():
    with open(WORKFLOW, encoding="utf-8") as f:
        return f.read()


def _workflow():
    wf = yaml.safe_load(_testo())
    assert isinstance(wf, dict) and "jobs" in wf, "ci.yml non e' un workflow GitHub leggibile"
    return wf


def _job(nome):
    jobs = _workflow()["jobs"]
    assert nome in jobs, "job %s assente: presenti %s" % (nome, sorted(jobs))
    return jobs[nome]


def _run_di(job):
    """Il testo di tutti i passi `run` del job, concatenato (cio' che il runner esegue)."""
    return "\n".join(str(s.get("run", "")) for s in job.get("steps", []))


def _trigger():
    wf = _workflow()
    # PyYAML (YAML 1.1) legge la chiave `on` come booleano True
    return wf.get("on", wf.get(True))


# ---------------------------------------------------------------------------------------------
# i job e i loro nomi
# ---------------------------------------------------------------------------------------------
def test_i_tre_job_esistono_con_il_loro_nome():
    jobs = _workflow()["jobs"]
    assert {"test", "desktop-windows", "macos-source"} <= set(jobs), sorted(jobs)


def test_il_job_macos_gira_su_un_runner_apple_silicon():
    job = _job("macos-source")
    assert job["runs-on"] == "macos-latest"


def test_il_job_linux_prova_python_312_e_314():
    job = _job("test")
    versioni = [str(v) for v in job["strategy"]["matrix"]["python-version"]]
    assert "3.12" in versioni and "3.14" in versioni, versioni
    setup = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/setup-python")]
    assert setup and setup[0]["with"]["python-version"] == "${{ matrix.python-version }}"


def test_il_job_windows_costruisce_ancora_l_installer_nsis():
    assert "electron-builder --win nsis" in _run_di(_job("desktop-windows"))


# ---------------------------------------------------------------------------------------------
# i passi del job macOS (mandato 12/09, sezione 4)
# ---------------------------------------------------------------------------------------------
def test_il_job_macos_installa_il_pacchetto_e_ne_verifica_la_coerenza():
    run = _run_di(_job("macos-source"))
    assert "pip install -e . pytest" in run
    assert "pip check" in run


def test_il_job_macos_compila_e_lancia_la_suite_offline():
    run = _run_di(_job("macos-source"))
    assert "compileall" in run and "src/bellomberg" in run
    for nome in ("test", "macos-source"):
        run = _run_di(_job(nome))
        assert "python -I tools/testing/research_ci.py" in run, run
        assert not re.search(r"pytest tests/?(?:\s|$)", run), run


def test_il_job_macos_costruisce_i_bundle_e_prova_electron():
    job = _job("macos-source")
    run = _run_di(job)
    for comando in ("npm ci", "npm run build:bundles", "npm run test:release",
                    "npm run desktop:install", "npm run test:desktop"):
        assert comando in run, "manca %r nel job macos-source" % comando
    working = {s.get("working-directory") for s in job["steps"] if "npm" in str(s.get("run", ""))}
    assert working == {"app"}, working


def test_il_job_macos_usa_python_312():
    setup = [s for s in _job("macos-source")["steps"]
             if str(s.get("uses", "")).startswith("actions/setup-python")]
    assert setup and str(setup[0]["with"]["python-version"]) == "3.12"


# ---------------------------------------------------------------------------------------------
# nessun segreto, nessuna spesa non dichiarata
# ---------------------------------------------------------------------------------------------
def test_nessun_segreto_nel_workflow():
    testo = _testo()
    assert "secrets." not in testo, "il workflow legge un segreto: la suite e' offline"
    for job in _workflow()["jobs"].values():
        chiave = (job.get("env") or {}).get("ANTHROPIC_API_KEY")
        if chiave is not None:
            assert chiave == "dummy-ci-offline", chiave
    assert not re.search(r"sk-(ant|or)-[A-Za-z0-9_-]{8,}", testo)


def test_il_job_macos_non_spende_minuti_sul_repo_privato_senza_ordine():
    """Runner macOS: gratuiti sui repo pubblici, ~10x Linux sui privati (doc GitHub billing,
    letta il 12/09). Il job gira sui repo pubblici e, sul privato, solo a comando (dispatch)."""
    job = _job("macos-source")
    condizione = str(job.get("if", ""))
    assert "github.event.repository.private" in condizione, condizione
    assert "workflow_dispatch" in condizione, condizione
    assert "workflow_dispatch" in _trigger(), _trigger()


def test_i_job_linux_e_windows_sul_privato_solo_a_comando():
    """06/10 (decisione PM, sostituisce la regola del 12/09): i 2000 minuti gratuiti del privato sono
    finiti; sul pubblico (gratis) test e desktop-windows girano a ogni push e PR, sul privato solo
    a comando. Stessa condizione di macos-source."""
    atteso = "${{ github.event.repository.private == false || github.event_name == 'workflow_dispatch' }}"
    for nome in ("test", "desktop-windows"):
        assert _job(nome).get("if") == atteso, (nome, _job(nome).get("if"))


# ---------------------------------------------------------------------------------------------
# suite intera su Linux (decisione PM 05/10): pytest tests/ completo, npm ci prima, mai sulle PR,
# sul privato solo a comando
# ---------------------------------------------------------------------------------------------
def test_la_suite_intera_gira_su_linux_con_pytest_completo():
    job = _job("suite-linux")
    assert job["runs-on"] == "ubuntu-latest"
    pytest_run = [str(s.get("run", "")) for s in job["steps"] if "pytest tests/" in str(s.get("run", ""))]
    assert len(pytest_run) == 1, pytest_run
    comando = pytest_run[0]
    # completa: nessun elenco di file, nessuna selezione -k/-m, non il runner ristretto
    assert re.match(r"python -m pytest tests/ ", comando), comando
    opzioni = comando[len("python -m pytest tests/ "):]
    assert not re.search(r"(^|\s)(-k|-m)\s", opzioni) and "tests/" not in opzioni, comando
    assert "research_ci" not in comando, comando


def test_la_suite_intera_installa_npm_prima_di_pytest():
    passi = _job("suite-linux")["steps"]
    indice = {}
    for i, s in enumerate(passi):
        run = str(s.get("run", ""))
        if "npm ci" in run:
            assert s.get("working-directory") == "app", s
            indice.setdefault("npm", i)
        if "pytest tests/" in run:
            indice.setdefault("pytest", i)
    assert "npm" in indice and "pytest" in indice and indice["npm"] < indice["pytest"], indice


def test_la_suite_intera_usa_le_versioni_del_job_test():
    job, base = _job("suite-linux"), _job("test")
    py = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/setup-python")]
    assert py and str(py[0]["with"]["python-version"]) in [str(v) for v in base["strategy"]["matrix"]["python-version"]]

    def node(j):
        return [s["with"]["node-version"] for s in j["steps"] if str(s.get("uses", "")).startswith("actions/setup-node")]
    assert node(job) == node(base), (node(job), node(base))
    # stesse action, stessi tag del job test
    assert {s["uses"] for s in job["steps"] if "uses" in s} <= {s["uses"] for s in base["steps"] if "uses" in s}


def test_la_suite_intera_ha_un_tetto_e_permessi_minimi():
    job = _job("suite-linux")
    assert 60 <= int(job["timeout-minutes"]) <= 120, job["timeout-minutes"]
    assert job["permissions"] == {"contents": "read"}, job["permissions"]


def test_la_suite_intera_non_gira_sulle_pr_e_sul_privato_solo_a_comando():
    condizione = str(_job("suite-linux").get("if", ""))
    assert "github.event.repository.private == false" in condizione, condizione
    assert "github.event_name == 'push'" in condizione, condizione
    assert "workflow_dispatch" in condizione and "workflow_dispatch" in _trigger(), condizione
    assert "pull_request" not in condizione, condizione


_SONDA_HOOK_EXCEL = r"""
import runpy, sys
from pathlib import Path
guard = runpy.run_path(sys.argv[1])
sys.addaudithook(guard["forbid_excel_write"])
base = Path(sys.argv[2])
target = base / "forbidden.XLSX"
try:
    target.write_bytes(b"must never be written")
except guard["ExcelGenerationForbidden"] as exc:
    print("RIFIUTATO:" + str(exc))
else:
    print("SCRITTO")
print("ESISTE:" + str(target.exists()))
pdf = base / "research.pdf"
pdf.write_bytes(b"synthetic PDF fixture")
print("PDF:" + str(pdf.read_bytes() == b"synthetic PDF fixture"))
"""


def test_research_ci_rifiuta_la_creazione_excel_prima_di_scrivere(tmp_path):
    # L'audit hook vero si installa in un SOTTOPROCESSO: sys.addaudithook non si toglie piu', e
    # nel processo pytest farebbe cadere con ExcelGenerationForbidden ogni test successivo che
    # scrive un .xlsx (suite dipendente dall'ordine; rapporto IX, 04/10).
    import subprocess

    guard = os.path.join(REPO, "tools", "testing", "research_ci.py")
    esito = subprocess.run(
        [sys.executable, "-I", "-c", _SONDA_HOOK_EXCEL, guard, str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert esito.returncode == 0, esito.stderr
    righe = esito.stdout.splitlines()
    assert len(righe) == 3, esito.stdout
    assert righe[0].startswith("RIFIUTATO:") and "forbidden" in righe[0], righe
    assert righe[1] == "ESISTE:False", righe
    assert righe[2] == "PDF:True", righe
    assert not (tmp_path / "forbidden.XLSX").exists()
