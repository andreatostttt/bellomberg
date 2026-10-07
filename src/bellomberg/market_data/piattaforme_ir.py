"""Piattaforme IR che caricano l'elenco dei documenti con JavaScript (richiesta PM 06/10: bilanci nuovi per
QUALSIASI societa').

Nessun JavaScript eseguito: si rileva la piattaforma dall'HTML della pagina IR (script e configurazione
caratteristici) e si legge la STESSA chiamata pubblica JSON che farebbe la pagina:
- Q4 (siti «q4web», documenti su q4cdn): GET <host della pagina>/feed/FinancialReport.svc/GetFinancialReportList;
- MZiQ (file manager): POST <fmBase>/company/<fmId>/filter/categories/meta con le categorie della pagina.
Le richieste passano dal Navigatore di esef_sito (robots.txt dell'host, ritmo, IP pubblici, 401/403/429
dichiarati). I documenti diventano voci {"url", "testo", "pagina", "piattaforma"} come i link a PDF della
pagina e seguono le stesse regole ed etichette (classifica_pdf). Nessun nome di societa' qui.
Euroland/Notified: non ancora (limite dichiarato).
"""
import re
from datetime import date
from urllib.parse import urljoin, urlsplit

ANNI_INDIETRO = 3       # documenti degli ultimi esercizi (gli archivi Q4 risalgono a decenni)
MAX_DOCUMENTI = 200

_Q4 = re.compile(r"q4cdn\.com|q4App|q4FinancialDetails|q4web|/feed/FinancialReport\.svc", re.I)
_Q4_FEED = "/feed/FinancialReport.svc/GetFinancialReportList?LanguageId=1&year=-1"
_Q4_TRIMESTRI = {"first quarter": "Q1", "second quarter": "Q2", "third quarter": "Q3", "fourth quarter": "Q4"}
# categoria del documento Q4 -> parole che classifica_pdf riconosce (tipo di documento e di relazione)
_Q4_CATEGORIE = {"tenq": "10-Q quarterly report", "tenk": "10-K annual report", "presentation": "earnings presentation",
                 "news": "earnings release", "transcript": "earnings call transcript"}

_MZ_ID = re.compile(r"""fmId\s*=\s*['"]([0-9a-fA-F-]{36})['"]""")
_MZ_BASE = re.compile(r"""fmBase\s*=\s*['"](https://[^'"\s]+/mzfilemanager)['"]""")
_MZ_CATEGORIA = re.compile(r"""internal_name\s*:\s*['"]([^'"]{1,80})['"]""")
_MZ_LINGUA = re.compile(r"""language\w*\s*[:=]\s*['"]([a-z]{2})[-_]([A-Z]{2})['"]""")
_NON_PDF = re.compile(r"planilha|spreadsheet|excel|xlsx?|video|replay|webcast|audio|confer[eê]ncia|podcast", re.I)


def _mz_parole(categoria):
    """Categoria MZiQ -> parole per classifica_pdf (ITR = informazioni trimestrali, DFP = bilancio annuale)."""
    c = categoria.lower()
    if c.startswith("itr"):
        return "quarterly financial statements"
    if c.startswith("dfp"):
        return "annual financial statements"
    if "release" in c:
        return "earnings release"
    if "apresenta" in c or "presentation" in c:
        return "earnings presentation"
    if "script" in c or "transcri" in c:
        return "earnings call transcript"
    return ""


def rileva(html, pagina):
    """[{"tipo", "url", ("corpo"), "pagina"}] delle piattaforme riconosciute nella pagina IR `pagina`."""
    out = []
    p = urlsplit(pagina)
    if p.scheme != "https" or not p.hostname:
        return out
    if _Q4.search(html or ""):
        out.append({"tipo": "q4", "url": f"https://{p.hostname.lower()}{_Q4_FEED}", "pagina": pagina})
    fm, base = _MZ_ID.search(html or ""), _MZ_BASE.search(html or "")
    if fm and base:
        categorie = []
        for nome in _MZ_CATEGORIA.findall(html):
            if _NON_PDF.search(nome):
                continue
            # la pagina manda il nome com'e' (anche con lo spazio finale): si chiedono le due forme
            for forma in (nome, nome.strip()):
                if forma not in categorie:
                    categorie.append(forma)
        lingua = _MZ_LINGUA.search(html)
        if categorie:
            out.append({"tipo": "mziq", "url": f"{base.group(1)}/company/{fm.group(1)}/filter/categories/meta",
                        # parametro della richiesta (lingua dei titoli), non un dato: inglese se la pagina non la dice
                        "corpo": {"categoryInternalNames": categorie,
                                  "language": f"{lingua.group(1)}_{lingua.group(2)}" if lingua else "en_US",
                                  "published": True},
                        "pagina": pagina})
    return out


def _recente(anno, oggi):
    try:
        return int(anno) >= (oggi or date.today()).year - ANNI_INDIETRO
    except (TypeError, ValueError):
        return False


def documenti_q4(dati, *, pagina, oggi=None):
    """Voci dei PDF dal feed Q4 (solo documenti PDF degli ultimi esercizi). ValueError se la forma e' inattesa."""
    righe = dati.get("GetFinancialReportListResult") if isinstance(dati, dict) else None
    if not isinstance(righe, list):
        raise ValueError("feed Q4: forma inattesa (manca GetFinancialReportListResult)")
    out, visti = [], set()
    for r in righe:
        if not isinstance(r, dict) or not _recente(r.get("ReportYear"), oggi):
            continue
        anno, sotto = r.get("ReportYear"), str(r.get("ReportSubType") or "")
        trimestre = _Q4_TRIMESTRI.get(sotto.lower())
        periodo = f"{trimestre} {anno}" if trimestre else (f"annual report {anno}" if "annual" in sotto.lower()
                                                           else str(r.get("ReportTitle") or anno))
        for d in r.get("Documents") or []:
            if not isinstance(d, dict) or not d.get("DocumentPath"):
                continue
            url = urljoin(pagina, str(d["DocumentPath"]))
            pdf = str(d.get("DocumentFileType") or "").upper() == "PDF" or urlsplit(url).path.lower().endswith(".pdf")
            if not pdf or urlsplit(url).scheme != "https" or url in visti:
                continue
            visti.add(url)
            parole = _Q4_CATEGORIE.get(str(d.get("DocumentCategory") or "").lower(), "")
            out.append({"url": url, "testo": f"{d.get('DocumentTitle') or ''} — {periodo}; {parole}".strip("; "),
                        "pagina": pagina, "piattaforma": "q4"})
    return out[:MAX_DOCUMENTI]


def documenti_mziq(dati, *, pagina, oggi=None):
    """Voci dei documenti dal file manager MZiQ (URL senza estensione: il PDF si riconosce al download)."""
    if not isinstance(dati, dict) or dati.get("success") is not True:
        raise ValueError("file manager MZiQ: risposta senza success")
    metas = (dati.get("data") or {}).get("document_metas")
    if not isinstance(metas, list):
        raise ValueError("file manager MZiQ: forma inattesa (manca document_metas)")
    out, visti = [], set()
    for m in metas:
        if not isinstance(m, dict) or not _recente(m.get("file_year"), oggi):
            continue
        categoria = str(m.get("category_internal_name") or m.get("internal_name") or "")
        estensione = str(m.get("extension_file") or "").lower().lstrip(".")
        titolo = str(m.get("file_title") or "")
        if _NON_PDF.search(categoria) or _NON_PDF.search(titolo) or (estensione and estensione != "pdf"):
            continue
        url = m.get("link_url") or m.get("permalink") or m.get("file_url")
        if not url or urlsplit(str(url)).scheme != "https" or url in visti:
            continue
        visti.add(url)
        q = m.get("file_quarter")
        periodo = f"Q{q} {m.get('file_year')}" if q in (1, 2, 3, 4) else str(m.get("file_year"))
        out.append({"url": str(url), "testo": f"{titolo} — {periodo}; {_mz_parole(categoria)}".strip("; "),
                    "pagina": pagina, "piattaforma": "mziq"})
    return out[:MAX_DOCUMENTI]


def documenti(piattaforma, dati, *, oggi=None):
    """Voci dei documenti per una piattaforma rilevata da `rileva`."""
    leggi = {"q4": documenti_q4, "mziq": documenti_mziq}[piattaforma["tipo"]]
    return leggi(dati, pagina=piattaforma["pagina"], oggi=oggi)
