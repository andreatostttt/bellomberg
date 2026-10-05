"""Documenti ESEF per il motore filing_diff: text block IFRS dello xBRL-JSON.

Lo xBRL-JSON di filings.xbrl.org porta le note del bilancio come fatti testuali
`ifrs-full:*TextBlock` / `*Explanatory` in XHTML (block tagging obbligatorio dal
FY2022). Qui diventano un documento nella forma di `filing_diff.prepara_documento`:
testo = blocchi ripuliti e concatenati, una sezione per concetto con offset
letterali, sha256 dei byte JSON archiviati. Identita' (LEI), periodo, lingua e
tipo sono provati dai fatti stessi. Sonda reale del 2026-10-03: blocchi identici
o annidati sotto piu' concetti (deduplicati), testi da milioni di caratteri nelle
banche (tetto per documento in ordine di priorita'), `<span>` vuoti dentro le
parole (tag in linea tolti senza spazio). Nessuna rete, nessun LLM.
"""
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re

MAX_CARATTERI_DOC = 1_500_000
MAX_SEGMENTI_DOC = 8_000
_DIMENSIONI_BASE = {"concept", "entity", "period", "unit", "language"}
_TESTUALE = re.compile(r"^ifrs-full:\w+(?:TextBlock|Explanatory)$")
_FINE_FRASE = re.compile(r"[.!?;](?=\s|$)")
_FRASE = re.compile(r"\S.*?(?:[.!?;](?=\s|$)|$)")  # come filing_diff._unita

# concetto -> (sezione, rango). Rango: 0 rischi, 1 contenziosi, 2 gestione, 3 altre note
# rilevanti, 4 note, 5 principi contabili. I nomi entrano nel punteggio del contesto
# (filing_context._peso: «rischi» 3, «contenziosi»/«gestione» 2).
SEZIONI = {
    "DisclosureOfFinancialRiskManagementExplanatory": ("rischi finanziari", 0),
    "DisclosureOfNatureAndExtentOfRisksArisingFromFinancialInstrumentsExplanatory": ("rischi da strumenti finanziari", 0),
    "DisclosureOfCreditRiskExplanatory": ("rischio di credito", 0),
    "DisclosureOfLiquidityRiskExplanatory": ("rischio di liquidità", 0),
    "DisclosureOfMarketRiskExplanatory": ("rischio di mercato", 0),
    "DisclosureOfContingentLiabilitiesExplanatory": ("contenziosi e passività potenziali", 1),
    "DisclosureOfOtherProvisionsContingentLiabilitiesAndContingentAssetsExplanatory": ("contenziosi: fondi e passività potenziali", 1),
    "DisclosureOfCommitmentsAndContingentLiabilitiesExplanatory": ("contenziosi: impegni e passività potenziali", 1),
    "DisclosureOfProvisionsExplanatory": ("contenziosi: fondi e accantonamenti", 1),
    "DisclosureOfOtherProvisionsExplanatory": ("contenziosi: altri fondi", 1),
    "DisclosureOfAccountingJudgementsAndEstimatesExplanatory": ("gestione: stime e giudizi", 2),
    "DisclosureOfEventsAfterReportingPeriodExplanatory": ("gestione: eventi successivi", 2),
    "DisclosureOfGoingConcernExplanatory": ("gestione: continuità aziendale", 2),
    "DisclosureOfCapitalManagementExplanatory": ("gestione del capitale", 3),
    "DisclosureOfEntitysReportableSegmentsExplanatory": ("settori operativi", 3),
    "DisclosureOfGoodwillExplanatory": ("avviamento", 3),
    "DisclosureOfImpairmentOfAssetsExplanatory": ("riduzioni di valore", 3),
    "DisclosureOfRelatedPartyExplanatory": ("parti correlate", 3),
}
_POLITICHE = ("DescriptionOfAccountingPolicy", "DisclosureOfSummaryOfSignificantAccountingPolicies",
              "DisclosureOfSignificantAccountingPolicies", "DisclosureOfBasisOfPreparation",
              "DisclosureOfChangesInAccountingPolicies", "DisclosureOfInitialApplicationOfStandards",
              "DisclosureOfExpectedImpactOfInitialApplicationOfNewStandards")


def _parole(camel):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", camel).lower().strip()


def sezione_di(concetto):
    """(nome sezione, rango) di un concetto ifrs-full (con o senza prefisso)."""
    locale = concetto.split(":", 1)[-1]
    if locale in SEZIONI:
        return SEZIONI[locale]
    base = re.sub(r"(?:Explanatory|TextBlock)$", "", locale)
    if base.startswith(_POLITICHE):
        resto = re.sub(r"^DescriptionOfAccountingPolicy(?:For)?", "", base) or base
        return "principio: " + _parole(re.sub(r"^DisclosureOf", "", resto)), 5
    nome = "nota: " + _parole(re.sub(r"^DisclosureOf", "", base))
    if "Risk" in base:
        return nome, 0
    if any(k in base for k in ("Contingent", "Provision", "Litigation", "Legal")):
        return nome, 1
    return nome, 4


class _Testo(HTMLParser):
    _BLOCCO = {"p", "div", "br", "tr", "li", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6",
               "section", "article", "header", "footer", "blockquote", "pre", "dt", "dd", "caption"}
    _CELLA = {"td", "th"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parti = []
        self.tabelle = 0  # testo delle tabelle escluso: i numeri chiave vengono dai fatti XBRL

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tabelle += 1
        if tag in self._BLOCCO:
            self.parti.append("\n")
        elif tag in self._CELLA:
            self.parti.append(" ")

    def handle_endtag(self, tag):
        if tag == "table":
            self.tabelle = max(0, self.tabelle - 1)
        if tag in self._BLOCCO:
            self.parti.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if not self.tabelle:
            self.parti.append(data)


def testo_html(valore):
    """Testo di un text block XHTML: righe per i blocchi, niente spazi dai tag in linea."""
    parser = _Testo()
    parser.feed(str(valore or ""))
    parser.close()
    righe = (" ".join(r.replace("\u00a0", " ").split()) for r in "".join(parser.parti).split("\n"))
    return "\n".join(r for r in righe if r and not _numerica(r))


_NUMERO = re.compile(r"^[(\-–+]?[\d.,]+%?\)?$")


def _numerica(riga):
    """Riga di tabella impaginata senza <table> (convertitori PDF): almeno 4 numeri e
    numeri per almeno meta' delle parole. Prova reale: queste righe dominavano il punteggio."""
    parole = riga.split()
    numeri = sum(bool(_NUMERO.match(p)) for p in parole)
    return numeri >= 4 and numeri * 2 >= len(parole)


def _periodo(valore):
    """(inizio, fine) ISO di una durata xBRL-JSON (fine esclusiva a mezzanotte), o None."""
    try:
        a, b = str(valore).split("/", 1)
        inizio = datetime.fromisoformat(a.replace("Z", ""))
        fine = datetime.fromisoformat(b.replace("Z", ""))
    except ValueError:
        return None
    if fine.time() == datetime.min.time():
        fine -= timedelta(days=1)
    return inizio.date().isoformat(), fine.date().isoformat()


def _istante(valore):
    try:
        t = datetime.fromisoformat(str(valore).replace("Z", ""))
    except ValueError:
        return None
    return (t - timedelta(days=1) if t.time() == datetime.min.time() else t).date().isoformat()


def _senza_dimensioni(dims):
    return not (set(dims) - _DIMENSIONI_BASE)


def blocchi(raw):
    """Text block IFRS del periodo annuale corrente, senza frasi ripetute, ordinati per priorita'.

    {"blocchi": [{concetto, sezione, rango, testo, fatto}], "scartati": [{concetto, sezione, motivo}],
     "periodo": (inizio, fine) | None, "lingue": set, "entita": set}
    """
    facts = (raw or {}).get("facts") or {}
    if not isinstance(facts, dict):
        raise ValueError("xBRL-JSON senza fatti")
    entita, candidati, annuali = set(), [], set()
    for fid, f in facts.items():
        dims = (f or {}).get("dimensions") or {}
        if dims.get("entity"):
            entita.add(str(dims["entity"]).split(":", 1)[-1])
        periodo = _periodo(dims.get("period")) if "/" in str(dims.get("period") or "") else None
        if not periodo:
            continue
        durata = (date.fromisoformat(periodo[1]) - date.fromisoformat(periodo[0])).days + 1
        if not 350 <= durata <= 380:
            continue
        annuali.add(periodo)
        concetto = str(dims.get("concept") or "")
        if _TESTUALE.match(concetto) and _senza_dimensioni(dims):
            candidati.append((fid, concetto, periodo, str(dims.get("language") or "").lower().split("-")[0], f.get("value")))
    out = {"blocchi": [], "scartati": [], "periodo": None, "lingue": set(), "entita": entita}
    if not annuali:
        return out
    # Periodo del deposito: l'esercizio annuale piu' recente fra TUTTI i fatti (numeri compresi),
    # cosi' un comparativo testuale non diventa il periodo quando mancano i blocchi correnti.
    periodo = max(annuali, key=lambda p: (p[1], p[0]))
    out["periodo"] = periodo
    visti = []
    for fid, concetto, p, lingua, valore in candidati:
        if p != periodo:
            continue  # comparativo testuale dell'anno prima
        testo = testo_html(valore)
        if not testo:
            continue
        sezione, rango = sezione_di(concetto)
        out["lingue"].add(lingua)
        visti.append({"concetto": concetto, "sezione": sezione, "rango": rango, "testo": testo,
                      "fatto": fid, "lingua": lingua})
    # Deduplica per FRASE: le note si ripetono annidate (il rischio di credito dentro la gestione
    # dei rischi finanziari in un anno, a se' nell'altro). Ogni frase ripetuta resta solo nella
    # nota piu' specifica (la piu' corta), cosi' i due anni restano coerenti e una nota
    # ristrutturata non diventa testo «aggiunto». Le ripetizioni dentro la stessa nota restano.
    proprietario = {}
    for b in sorted(visti, key=lambda b: (len(b["testo"]), b["concetto"])):
        # Frasi sul testo intero (a capo = spazio), come le segmenta filing_diff._unita:
        # gli a capo dei PDF cadono in punti diversi nelle due note.
        frasi = _FRASE.findall(" ".join(b["testo"].split()))
        tenute, tolte, mie = [], 0, set()
        for frase in frasi:
            chiave = (b["lingua"], " ".join(frase.split()))
            if chiave in proprietario:
                tolte += 1
                continue
            mie.add(chiave)
            tenute.append(frase.strip())
        if not tenute:
            altre = sorted({proprietario[(b["lingua"], " ".join(f.split()))]["sezione"] for f in frasi})
            out["scartati"].append({"concetto": b["concetto"], "sezione": b["sezione"],
                                    "motivo": "testo gia' presente in " + ", ".join(altre)[:120]})
            continue
        for chiave in mie:
            proprietario[chiave] = b
        out["blocchi"].append({**b, "testo": "\n".join(tenute), "frasi_ripetute_tolte": tolte})
    out["blocchi"].sort(key=lambda b: (b["rango"], b["concetto"]))
    return out


def componi(lista):
    """(testo, sezioni, esclusi): blocchi in ordine con intestazione, entro il tetto del documento.

    Le sezioni oltre il tetto restano `non_disponibile` (motivo dichiarato), mai silenziose.
    """
    parti, sezioni, esclusi, lunghezza, segmenti = [], {}, [], 0, 0
    for b in lista:
        nome = b["sezione"]
        if nome in sezioni:  # due concetti con la stessa etichetta derivata: il secondo si distingue
            nome = f"{nome} ({b['concetto'].split(':', 1)[-1]})"
        intestazione = f"[{nome}]\n"
        aggiunta = len(intestazione) + len(b["testo"]) + (2 if parti else 0)
        n_seg = len(_FINE_FRASE.findall(b["testo"])) + 1
        if lunghezza + aggiunta > MAX_CARATTERI_DOC or segmenti + n_seg > MAX_SEGMENTI_DOC:
            sezioni[nome] = {"stato": "non_disponibile", "motivo": "oltre il limite del documento"}
            esclusi.append(nome)
            continue
        if parti:
            parti.append("\n\n")
            lunghezza += 2
        parti.append(intestazione)
        inizio = lunghezza + len(intestazione)
        parti.append(b["testo"])
        lunghezza += len(intestazione) + len(b["testo"])
        segmenti += n_seg
        sezioni[nome] = {"stato": "ok", "inizio": inizio, "fine": lunghezza}
    return "".join(parti), sezioni, esclusi


def _prova(url, digest, fatto, testo):
    return {"url": url, "sha256": digest, "supporto": "xbrl-json", "fatto": fatto, "testo": testo}


def documento_esef(path, *, url, profilo, catalogo=None):
    """{"stato": "ok", "documento"} | {"stato": "non_applicabile" | "non_verificato", "motivi"}.
    Le prove testuali (pattern del profilo) hanno un tempo TOTALE per documento (REV2_G2b C4)."""
    from bellomberg.market_data import regex_sandbox
    with regex_sandbox.budget(regex_sandbox.TEMPO_DOCUMENTO_S):
        return _documento_esef(path, url=url, profilo=profilo, catalogo=catalogo)


def _documento_esef(path, *, url, profilo, catalogo=None):
    from bellomberg.market_data.filing_profili_auto import LINGUA_NON_VERIFICABILE
    from bellomberg.market_data.filing_verifica import _cerca_prova
    catalogo = catalogo or {}
    try:
        grezzo = Path(path).read_bytes()
        digest = hashlib.sha256(grezzo).hexdigest()
        raw = json.loads(grezzo.decode("utf-8-sig"))
        lei = str(profilo.get("lei") or str(profilo["emittente_id"]).split(":", 1)[1]).upper()
        estratti = blocchi(raw)
        if estratti["entita"] != {lei}:
            raise ValueError(f"identificatore LEI dei fatti diverso dall'emittente atteso ({', '.join(sorted(estratti['entita']))[:120]})")
        if not estratti["blocchi"] and not estratti["scartati"]:
            raise ValueError("nessun text block IFRS sul periodo annuale (block tagging obbligatorio dal FY2022)")
        inizio, fine = estratti["periodo"]
        attesa = catalogo.get("period_end")
        if attesa and str(attesa)[:10] != fine:
            raise ValueError(f"periodo dei text block ({fine}) diverso dal catalogo ({attesa}): possibile comparativo")
        lingua = profilo["lingua"]
        lingue_doc = estratti["lingue"] - {""}
        if not catalogo.get("language") and lingua not in lingue_doc and len(lingue_doc) == 1:
            # Nome file senza suffisso di lingua: vale l'unica lingua del deposito; la coppia
            # resta confrontabile solo con un deposito nella stessa lingua (_omologhi).
            lingua = next(iter(lingue_doc))
        mie = [b for b in estratti["blocchi"] if b["lingua"] in (lingua, "")]
        if not mie:
            return {"stato": "non_applicabile",
                    "motivi": [f"lingua del deposito {'/'.join(sorted(estratti['lingue']))} diversa dal profilo {lingua}"]}
        testo, sezioni, esclusi = componi(mie)
        # Le note fatte solo di frasi gia' presenti altrove non sono sezioni: il loro testo e'
        # confrontato dove sta (prova reale: altrimenti ogni confronto risultava parziale).
        primo = mie[0]
        prove = {"emittente": _prova(url, digest, primo["fatto"], f"scheme:{lei}"),
                 "periodo": _prova(url, digest, primo["fatto"], f"{inizio}/{fine}"),
                 "tipo": _prova(url, digest, primo["fatto"], f"durata {(date.fromisoformat(fine) - date.fromisoformat(inizio)).days + 1} giorni")}
        avvisi = []
        if primo["lingua"]:
            prove["lingua"] = _prova(url, digest, primo["fatto"], f"language {primo['lingua']}")
        elif profilo["verifica"]["lingua"] == LINGUA_NON_VERIFICABILE:
            # REV_G2b B2: nessuna regola testuale per questa lingua e fatti senza lingua: dichiarato
            motivo = (f"lingua non verificabile ({lingua}): fatti senza lingua dichiarata e nessuna "
                      "regola testuale per questa lingua")
            prove["lingua"] = {"stato": "non_verificabile", "motivo": motivo}
            avvisi.append(motivo)
        else:
            prove["lingua"] = _cerca_prova(testo, profilo["verifica"]["lingua"], "lingua", url, digest)
        prove["perimetro"] = _cerca_prova(testo, profilo["verifica"]["perimetro"], "perimetro", url, digest)
        if date.fromisoformat(fine) > date.today():
            raise ValueError("periodo futuro: non e' una relazione consuntiva")
        metadati = {"emittente_id": f"LEI:{lei}", "lingua": lingua, "tipo": "annuale",
                    "perimetro": profilo["perimetro"], "periodo_inizio": inizio, "periodo_fine": fine}
        doc = {"url": url, "stato": "ok", "metadati": metadati, "sezioni": sezioni, "sha256": digest,
               "estrazione": {"stato": "ok", "formato": "xbrl-json", "testo": testo, "riferimenti": []},
               "metadati_verifica": "verificati sui fatti xBRL-JSON del deposito (LEI, periodo, lingua)",
               "prove_verifica": prove, "avvisi": avvisi,
               "esef": {"blocchi": len(mie), "scartati": estratti["scartati"], "oltre_limite": esclusi}}
        return {"stato": "ok", "motivi": [], "documento": doc}
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        return {"stato": "non_verificato", "motivi": [f"{type(exc).__name__}: {exc}"]}


def allinea_sezioni(prima, dopo):
    """Una nota presente in un solo anno diventa testo aggiunto/rimosso, non una sezione mancante.

    Solo per i concetti del tutto assenti dall'altro documento: quelli deduplicati o oltre
    il tetto restano non disponibili (il testo potrebbe esserci altrove).
    """
    out = []
    for doc, altro in ((prima, dopo), (dopo, prima)):
        sezioni = dict(doc["sezioni"])
        fine = len(doc["estrazione"]["testo"])
        for nome, s in altro["sezioni"].items():
            if nome not in sezioni and s.get("stato") == "ok":
                sezioni[nome] = {"stato": "ok", "inizio": fine, "fine": fine, "assente": True}
        out.append({**doc, "sezioni": sezioni})
    return out[0], out[1]


def _fatti_numerici(raw):
    """Fatti ifrs-full numerici senza dimensioni nella forma companyfacts (us-gaap vuoto)."""
    tassonomia = {}
    for f in ((raw or {}).get("facts") or {}).values():
        dims = (f or {}).get("dimensions") or {}
        concetto = str(dims.get("concept") or "")
        if not concetto.startswith("ifrs-full:") or not _senza_dimensioni(dims) or not dims.get("unit"):
            continue
        try:
            valore = float(f.get("value"))
        except (TypeError, ValueError):
            continue
        periodo = str(dims.get("period") or "")
        if "/" in periodo:
            p = _periodo(periodo)
            if not p:
                continue
            riga = {"start": p[0], "end": p[1], "val": valore}
        else:
            fine = _istante(periodo)
            if not fine:
                continue
            riga = {"end": fine, "val": valore}
        unita = str(dims["unit"]).split(":", 1)[-1]
        tassonomia.setdefault(concetto.split(":", 1)[1], {"units": {}})["units"].setdefault(unita, []).append(riga)
    return tassonomia


def numeri_esef(coppia):
    """Numeri chiave della coppia: fatti del deposito `dopo` (con il comparativo), poi `prima`."""
    from bellomberg.market_data.filing_numeri import variazioni
    fatti = {}
    for lato in ("dopo", "prima"):  # righe del deposito piu' recente prima: il comparativo rideterminato vince
        raw = json.loads(Path(coppia[lato]["path"]).read_bytes().decode("utf-8-sig"))
        for nome, voce in _fatti_numerici(raw).items():
            for unita, righe in voce["units"].items():
                fatti.setdefault(nome, {"units": {}})["units"].setdefault(unita, []).extend(righe)
    periodo = {lato: tuple(coppia[lato]["metadati"][k] for k in ("periodo_inizio", "periodo_fine"))
               for lato in ("prima", "dopo")}
    return {**variazioni({"facts": {"ifrs-full": fatti}}, periodo["prima"], periodo["dopo"]),
            "fonte": "ESEF xBRL-JSON (ifrs-full)"}


ESERCIZI_SCARICATI = 3
GIORNI_OBSOLESCENZA = 548  # ~18 mesi: oltre, l'ultimo esercizio sul repository non e' "corrente"
_LINGUA_URL = re.compile(r"-([a-z]{2})\.(?:xhtml|html?|json|zip)$", re.I)


def lingua_url(riga):
    """Lingua dal suffisso del nome file (l'attributo language del repository e' vuoto)."""
    for campo in ("report_url", "json_url"):
        m = _LINGUA_URL.search(str(riga.get(campo) or ""))
        if m:
            return m.group(1).lower()
    return None


def lingue_indizio(righe):
    """{id(riga): lingua | None}. Il suffisso del nome file e' la lingua solo se l'emittente ha
    almeno un esercizio con versioni a suffissi diversi (it/en): prova reale, in
    «<emittente>-2025-12-31-0-IT.json» il suffisso e' il paese e il testo e' inglese. Altrimenti
    decide la lingua dichiarata nei fatti del deposito."""
    per_periodo = {}
    for r in righe:
        per_periodo.setdefault(r.get("period_end"), set()).add(lingua_url(r))
    affidabile = any(len(lingue - {None}) >= 2 for lingue in per_periodo.values())
    return {id(r): lingua_url(r) if affidabile else None for r in righe}


def scegli_lingua(righe, preferita):
    """Lingua della coppia: la preferita se c'e' nei due esercizi piu' recenti, altrimenti
    la prima lingua presente in entrambi (le righe senza suffisso valgono per tutte)."""
    indizio = lingue_indizio(righe)
    periodi = sorted({r["period_end"] for r in righe}, reverse=True)[:2]
    lingue = [preferita] + sorted(set(indizio.values()) - {None, preferita})
    for lingua in lingue:
        if periodi and all(any(r["period_end"] == p and indizio[id(r)] in (lingua, None) for r in righe)
                           for p in periodi):
            return lingua
    return preferita


def righe_catalogo(lei, preferita, *, oggi, indice_fn=None, sito_fn=None):
    """Candidati ESEF per la pipeline e per l'impronta, con le stesse regole.

    {"righe", "lingua", "limiti", "motivi", "origine", "ultimo_periodo", "fermo", "dal_sito"}:
    righe con xBRL-JSON nella lingua scelta (o senza lingua dichiarata), un caricamento per
    esercizio (l'ultimo), gli ESERCIZI_SCARICATI piu' recenti. Indice illeggibile:
    eccezione (fonte in errore, run da ritentare).

    Fase F: se il repository non ha l'esercizio atteso (fermo o emittente assente), si
    aggiungono gli esercizi mancanti dai pacchetti ufficiali trovati sul sito dell'emittente
    (`sito_fn`, solo cache: nessuna rete qui), con `origine: "sito"`; per lo stesso esercizio
    vince il repository.
    """
    from bellomberg.market_data import esef
    indice = (indice_fn or esef.indice_depositi)(lei)
    tutte = [r for r in indice["righe"] if isinstance(r, dict) and r.get("period_end")]
    limiti, motivi = [], []
    if indice.get("motivo"):
        motivi.append(indice["motivo"])
    con_json = [r for r in tutte if r.get("json_url")]
    for periodo in sorted({r["period_end"] for r in tutte} - {r["period_end"] for r in con_json}):
        limiti.append(f"ESEF FY{periodo[:4]}: deposito senza xBRL-JSON sul repository (non confrontabile).")
    lingua = scegli_lingua(con_json, preferita)
    if con_json and lingua != preferita:
        limiti.append(f"ESEF: lingua {lingua}, perche' {preferita} manca in uno dei due esercizi piu' recenti.")
    scelte, duplicati = {}, 0
    indizio = lingue_indizio(con_json)
    for r in sorted(con_json, key=lambda r: (str(r.get("date_added") or ""), str(r.get("id")))):
        lr = indizio[id(r)]
        if lr not in (lingua, None):
            continue
        attuale = scelte.get(r["period_end"])
        if attuale is not None:
            if indizio[id(attuale)] == lingua and lr is None:
                continue  # la versione nella lingua scelta vince su quella senza suffisso
            duplicati += 1
        scelte[r["period_end"]] = r
    if duplicati:
        limiti.append(f"ESEF: {duplicati} caricamenti duplicati sul repository, tenuto il piu' recente per esercizio.")
    periodi = sorted(scelte, reverse=True)
    if len(periodi) > ESERCIZI_SCARICATI:
        limiti.append(f"ESEF: scaricati solo gli ultimi {ESERCIZI_SCARICATI} esercizi del repository "
                      "(bastano per la coppia e un ripiego).")
    righe = [{**scelte[p], "language": lingua if indizio[id(scelte[p])] == lingua else None}
             for p in periodi[:ESERCIZI_SCARICATI]]
    ultimo = max((r["period_end"] for r in tutte), default=None)  # anche i depositi senza JSON
    try:
        vecchio = bool(ultimo) and (oggi - date.fromisoformat(str(ultimo)[:10])).days > GIORNI_OBSOLESCENZA
    except ValueError:
        vecchio = False
    from bellomberg.market_data import esef_sito
    # Revisione finale: conta l'ultimo esercizio CON xBRL-JSON (un deposito senza JSON non e'
    # confrontabile) e un esercizio del sito piu' recente si aggiunge comunque.
    ultimo_json = max((r["period_end"] for r in con_json), default=None)
    presenti = {r["period_end"] for r in righe}
    atteso = esef_sito.atteso_piu_recente(ultimo_json, oggi)
    dal_sito = [r for r in (sito_fn or esef_sito.righe_da_cache)(lei)
                if r.get("period_end") and r["period_end"] not in presenti
                and (atteso or not ultimo_json or r["period_end"] > ultimo_json)]
    if dal_sito:
        stato_repo = (f"fermo all'esercizio FY{str(ultimo_json)[:4]}" if ultimo_json
                      else "senza xBRL-JSON di questo emittente" if ultimo else "senza depositi di questo emittente")
        righe = sorted(righe + dal_sito, key=lambda r: r["period_end"], reverse=True)[:ESERCIZI_SCARICATI]
        anni = ", ".join("FY" + r["period_end"][:4] for r in righe if r.get("origine") == "sito")
        if anni:
            limiti.append(f"ESEF: {anni} dal sito dell'emittente (pacchetto ufficiale convertito in locale; "
                          f"repository filings.xbrl.org {stato_repo}).")
        ultimo = max([r["period_end"] for r in dal_sito] + ([ultimo] if ultimo else []))
        vecchio = (oggi - date.fromisoformat(str(ultimo)[:10])).days > GIORNI_OBSOLESCENZA
    if vecchio:
        motivi.append(f"repository fermo all'esercizio FY{str(ultimo)[:4]} (chiuso il {str(ultimo)[:10]}): "
                      "confronto non corrente")
    return {"righe": righe, "lingua": lingua, "limiti": limiti, "motivi": motivi,
            "origine": indice.get("origine"), "ultimo_periodo": ultimo, "fermo": vecchio,
            "dal_sito": any(r.get("origine") == "sito" for r in righe)}
