"""B1 (02/09, pubblicazione): .env.example elenca ESATTAMENTE le variabili che il
codice di prodotto legge — nei due versi. Una variabile letta e non documentata e'
un buco per chi installa; una documentata e non letta e' una bugia.
"""
import os
import re
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(REPO, ".env.example")
LETTURA = re.compile(r"""os\.(?:getenv|environ\.get)\(\s*["']([A-Z][A-Z0-9_]+)["']|os\.environ\[\s*["']([A-Z][A-Z0-9_]+)["']\s*\](?!\s*=)""")
RUNTIME_PATH = re.compile(r'_runtime_path\("([A-Z][A-Z0-9_]+)"')
# 04/10 (import Andrea): letture col nome passato a un helper di validazione
# (assente = default; vuota o non valida = errore col nome) e la forma
# `nome = "X"` + `os.environ.get(nome)` di news_refresh_manager.
HELPER_ENV = re.compile(r"""\b(?:effort_env|_secondi_da_env|_secondi_env|_numero_env|_env_positive_int)\(\s*["']([A-Z][A-Z0-9_]+)["']\s*[,)]""")
NOME_IN_VARIABILE = re.compile(r'\bnome\s*=\s*"([A-Z][A-Z0-9_]+)"\s*\n\s*raw\s*=\s*os\.environ\.get\(nome\)')
# Variabili di sistema lette dal codice ma che NON si configurano in .env
SISTEMA = {"TEMP", "TMP", "USERPROFILE", "PATH", "PYTHONIOENCODING"}
# Le uniche righe del template a cui e' permesso un valore: sono DEFAULT dichiarati
CON_DEFAULT = {"PM_NAME", "CONSIGLIERE_PARALLEL", "CONSIGLIERE_R0_MODEL"}
# 04/10 (import Andrea): variabili di taratura col DEFAULT DEL CODICE scritto nel template.
# Nel codice nuovo una riga presente ma vuota e' un errore col nome, quindi un template
# vuoto copiato in .env fermerebbe comitato, notizie, filing e coda email: il valore
# e' il default, fissato qui perche' un cambio sia sempre deliberato.
DEFAULT_TARATURA = {
    "CONSIGLIERE_R0_EFFORT": "low", "CONSIGLIERE_R1_EFFORT": "high", "CONSIGLIERE_R2_EFFORT": "high",
    "CONSIGLIERE_MACRO_EFFORT": "high", "CONSIGLIERE_QUANT_EFFORT": "high",
    "CONSIGLIERE_OPTIONS_EFFORT": "high", "CONSIGLIERE_FUNDAMENTALS_EFFORT": "high",
    "CONSIGLIERE_CRYPTO_EFFORT": "adaptive", "CONSIGLIERE_EVENTDESK_EFFORT": "adaptive",
    "CAPO_EFFORT": "high", "RED_TEAM_EFFORT": "high", "REFLECTION_EFFORT": "low",
    "VALUATION_PREPARER_EFFORT": "high",
    "NEWS_AUTO_REFRESH_ENABLED": "true", "NEWS_REFRESH_INTERVAL_MINUTES": "15",
    "NEWS_REFRESH_FIRST_DELAY_MINUTES": "10", "NEWS_REFRESH_QUIET_HOURS": "22:00-07:00",
    "NEWS_REFRESH_QUIET_DAYS": "none", "NEWS_REFRESH_WEEKEND_INTERVAL_MINUTES": "120",
    "FILING_AUTO_REFRESH_ENABLED": "true", "FILING_AUTO_REFRESH_INTERVAL_S": "86400",
    "FILING_AUTO_REFRESH_DELAY_S": "600", "FILING_AI_INTERVALLO_TICKER_S": "300",
    "FILING_AI_TETTO_GIORNO_EUR": "1.0",
    "VENUE_POLL_MIN_SECONDS": "120", "VENUE_BACKOFF_MAX_SECONDS": "1800",
    "EMAIL_SMTP_TIMEOUT": "120", "EMAIL_SEND_ATTEMPTS": "3", "EMAIL_RETRY_DELAY_SECONDS": "15",
    "EMAIL_OUTBOX_TTL_HOURS": "24", "EMAIL_OUTBOX_RETENTION_DAYS": "7",
}
# 05/09 (OpenRouter, ordine PM): le variabili MODELLO portano la tabella scelta dal PM
# (uno slug `provider/modello` per funzione) e CHAT_MAX_TOKENS il tetto: sono la
# configurazione consigliata, NON un default nel codice (assente = errore col nome).
MODELLI_OPENROUTER = re.compile(r"^(?:CHAT_[A-Z]+_MODEL|CHAT_MODEL|CONSIGLIERE_[A-Z0-9]+_MODEL|"
                                r"CONSIGLIERE_MODEL|CAPO_MODEL|RED_TEAM_MODEL|REFLECTION_MODEL|"
                                r"ACTION_EXTRACTOR_MODEL|BRIEFING_MODEL|NEWS_CLASSIFIER_MODEL|TRADE_IDEA_[A-Z_]+_MODEL|"
                                r"CHAT_MAX_TOKENS)$")
SLUG_OPENROUTER = re.compile(r"^[a-z0-9.-]+/[A-Za-z0-9._:-]+$")


def _file_prodotto():
    root = Path(REPO)
    files = list(root.glob("*.py")) + list((root / "src" / "bellomberg").rglob("*.py"))
    assert files, "nessun sorgente di prodotto trovato"
    return [str(path.relative_to(root)) for path in files]


def _lette_dal_codice():
    nomi = set()
    for f in _file_prodotto():
        source = Path(REPO, f).read_text(encoding="utf-8")
        for a, b in LETTURA.findall(source):
            nomi.add(a or b)
        nomi.update(RUNTIME_PATH.findall(source))
        nomi.update(HELPER_ENV.findall(source))
        nomi.update(NOME_IN_VARIABILE.findall(source))
    return nomi - SISTEMA


def _righe_template():
    for riga in open(TEMPLATE, encoding="utf-8"):
        m = re.match(r"^([A-Z][A-Z0-9_]+)=(.*)$", riga.rstrip("\r\n"))
        if m:
            yield m.group(1), m.group(2).strip()


def _nel_template():
    return {nome for nome, _ in _righe_template()}


def test_template_esiste_e_non_ha_valori():
    con_valore = [nome for nome, valore in _righe_template()
                  if valore and nome not in CON_DEFAULT and nome not in DEFAULT_TARATURA
                  and not MODELLI_OPENROUTER.match(nome)]
    assert not con_valore, f"valori nel template (una chiave vera?): {con_valore}"


def test_i_valori_di_taratura_sono_esattamente_i_default_fissati():
    valori = dict(_righe_template())
    diversi = {nome: (valori.get(nome), atteso) for nome, atteso in DEFAULT_TARATURA.items()
               if valori.get(nome) != atteso}
    assert not diversi, f"taratura del template diversa dai default fissati: {diversi}"


def test_i_default_dichiarati_coincidono_col_codice():
    valori = dict(_righe_template())
    assert valori["PM_NAME"] == "PM"
    assert valori["CONSIGLIERE_PARALLEL"] == "4"


def test_le_variabili_modello_portano_slug_openrouter_e_il_tetto_un_intero():
    """05/09: ogni riga modello del template e' uno slug OpenRouter `provider/modello`
    (eventuale variante `:exacto`), non vuota e non una chiave; CHAT_MAX_TOKENS e' un
    intero. Il codice NON ha default (llm_client: variabile assente = errore col nome),
    quindi il template e' l'unico posto dove la tabella del PM e' scritta per chi installa."""
    valori = dict(_righe_template())
    modelli = {n: v for n, v in valori.items() if MODELLI_OPENROUTER.match(n) and n != "CHAT_MAX_TOKENS"}
    assert len(modelli) >= 11, sorted(modelli)
    brutti = {n: v for n, v in modelli.items() if not SLUG_OPENROUTER.match(v)}
    assert not brutti, brutti
    assert valori["CHAT_MAX_TOKENS"].isdigit() and int(valori["CHAT_MAX_TOKENS"]) > 0
    # la chiave di OpenRouter e' documentata e VUOTA (mai una chiave vera nel template)
    assert valori.get("OPENROUTER_API_KEY") == ""


def test_ogni_variabile_letta_e_documentata():
    mancano = _lette_dal_codice() - _nel_template()
    assert not mancano, f"lette dal codice ma non in .env.example: {sorted(mancano)}"


def test_ogni_variabile_documentata_e_letta():
    inutili = _nel_template() - _lette_dal_codice()
    assert not inutili, f"in .env.example ma nessun modulo le legge: {sorted(inutili)}"


def test_nessun_riferimento_al_template_fantasma():
    for f in ("src/bellomberg/core/config.py", "README.md"):
        assert "env.example.txt" not in open(os.path.join(REPO, f), encoding="utf-8").read(), f


def test_ogni_variabile_ha_una_riga_di_commento_sopra():
    """Chi installa legge il template: ogni variabile (o blocco di variabili contigue,
    es. le tre EMAIL_*) ha SUBITO sopra un commento che dice a cosa serve — non un
    header di sezione, non un'altra variabile (review 02/09: la versione a «3 righe
    sopra» passava anche con nessun commento).

    Non scandisce app/: process.env vi appare solo per BELLOMBERG_LAUNCH_ID e
    VITE_DEV_SERVER_URL, variabili interne che l'utente non configura."""
    righe = open(TEMPLATE, encoding="utf-8").read().splitlines()
    senza = []
    for i, riga in enumerate(righe):
        if re.match(r"^[A-Z][A-Z0-9_]+=", riga):
            j = i - 1
            while j >= 0 and re.match(r"^[A-Z][A-Z0-9_]+=", righe[j]):
                j -= 1                       # risali il blocco di variabili contigue
            sopra = righe[j] if j >= 0 else ""
            if not (sopra.startswith("#") and not sopra.startswith("# ===")):
                senza.append(riga.split("=")[0])
    assert not senza, senza
