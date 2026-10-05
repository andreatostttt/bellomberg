"""Prerequisiti Node dei test Python che eseguono codice dell'app (cantiere ZERO ROSSI, 05/10).

I test che lanciano `node` sui moduli veri di app/ (typescript, react, ...) hanno bisogno di
`app/node_modules`, che un clone non contiene finche' non si esegue `npm ci` in app/ (README
passo 4, CONTRIBUTING «Development setup»; la CI lo fa prima della suite Python).

Senza il prerequisito il test FALLISCE con un messaggio che dice cosa fare: non e' uno skip,
perche' uno skip renderebbe verde un clone o una CI che ha dimenticato `npm ci` e le garanzie
sulla UI sparirebbero zitte.
"""
from __future__ import annotations

from pathlib import Path
import shutil

import pytest

APP = Path(__file__).resolve().parents[1] / "app"


def richiedi_node_modules(*moduli: str, app: Path = APP) -> str:
    """Torna il percorso dell'eseguibile `node`; fallisce se node o un modulo di app/ manca.

    `moduli` sono nomi di pacchetto npm (anche con scope, es. "@babel/parser"): un modulo e'
    presente se esiste `app/node_modules/<modulo>/package.json`.
    """
    node = shutil.which("node")
    if not node:
        pytest.fail("prerequisito mancante: Node.js non e' nel PATH (serve Node 22.12 o "
                    "successivo, vedi CONTRIBUTING «Development setup»)", pytrace=False)
    mancanti = [m for m in moduli if not (Path(app) / "node_modules" / m / "package.json").is_file()]
    if mancanti:
        pytest.fail("prerequisito mancante: " + ", ".join(mancanti) + " assente in "
                    + str(Path(app) / "node_modules") + " - eseguire `npm ci` nella cartella app/ "
                    "(README passo 4, CONTRIBUTING «Development setup»)", pytrace=False)
    return node
