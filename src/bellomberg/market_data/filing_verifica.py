"""I-20: verifica documentale tramite profili curati, riusabili fra periodi.

Il profilo non contiene date o pagine del singolo report. Le regex sono regole
di riconoscimento dell'emittente, non un certificato universale: ogni campo
richiede una prova nei byte esaminati. Nessun LLM, DB o download qui.
"""
from datetime import date
from copy import deepcopy
import hashlib
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re

from bellomberg.market_data import regex_sandbox
from bellomberg.market_data.filing_diff import prepara_documento, _norm
from bellomberg.market_data.lettore_trimestrali import estrai_testo


_DURATE = {"annuale": (350, 380), "semestrale": (170, 195),
           "trimestrale": (80, 100), "nove_mesi": (260, 285)}
_MESI = (
    ("january", "gennaio", "januar"), ("february", "febbraio", "februar"),
    ("march", "marzo", "märz"), ("april", "aprile"), ("may", "maggio", "mai"),
    ("june", "giugno", "juni"), ("july", "luglio", "juli"),
    ("august", "agosto"), ("september", "settembre"), ("october", "ottobre", "oktober"),
    ("november", "novembre"), ("december", "dicembre", "dezember"))


class _MarkupAttivo(HTMLParser):
    """Maschera commenti/script mantenendo gli offset nel markup originale.

    ix:hidden resta attivo: contiene identificatori XBRL legittimi. La
    visibilita' CSS non decide la validita' di un fatto Inline XBRL.
    """
    def __init__(self, markup):
        super().__init__()
        self.markup = markup
        self.righe = [0] + [m.end() for m in re.finditer("\n", markup)]
        self.esclusi, self.lingue, self.muto = [], [], None

    def posizione(self):
        riga, colonna = self.getpos()
        return self.righe[riga-1] + colonna

    def handle_starttag(self, tag, attrs):
        if self.muto:
            if tag in ("script", "style", "noscript", "template"):
                self.muto[2].append(tag)
            return
        if tag in ("script", "style", "noscript", "template"):
            self.muto = (tag, self.posizione(), [tag])
        elif tag == "html":
            self.lingue.extend(value for key, value in attrs if key in ("lang", "xml:lang"))

    def handle_endtag(self, tag):
        if self.muto and tag == self.muto[2][-1]:
            self.muto[2].pop()
            if self.muto[2]:
                return
            fine = self.markup.find(">", self.posizione())
            self.esclusi.append((self.muto[1], fine+1 if fine >= 0 else len(self.markup)))
            self.muto = None

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_comment(self, data):
        if not self.muto:
            fine = re.search(r"--!?>", self.markup[self.posizione():])
            self.esclusi.append((self.posizione(), self.posizione()+fine.end() if fine else len(self.markup)))

    def testo_attivo(self):
        self.feed(self.markup)
        self.close()
        if self.muto:
            self.esclusi.append((self.muto[1], len(self.markup)))
        pezzi, posizione = [], 0
        for a, b in sorted(self.esclusi):
            if a < posizione:
                continue
            pezzi.extend((self.markup[posizione:a], " "*(b-a)))
            posizione = b
        pezzi.append(self.markup[posizione:])
        return "".join(pezzi)


def _data(value):
    value = _norm(unescape(value)).lower().strip()
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    for numero, nomi in enumerate(_MESI, 1):
        for nome in nomi:
            patterns = (rf"(\d{{1,2}})\.?\s+{nome}\s+(\d{{4}})",
                        rf"{nome}\s+(\d{{1,2}}),?\s+(\d{{4}})")
            for pattern in patterns:
                match = re.fullmatch(pattern, value)
                if match:
                    return date(int(match[2]), numero, int(match[1]))
    raise ValueError(f"data completa non riconosciuta: {value!r}")


def _mesi_prima(fine, mesi):
    """Stesso giorno `mesi` mesi prima (giorno ridotto a fine mese se serve)."""
    from calendar import monthrange
    indice = fine.year * 12 + fine.month - 1 - mesi
    anno, mese = divmod(indice, 12)
    mese += 1
    return date(anno, mese, min(fine.day, monthrange(anno, mese)[1]))


def _periodo_testuale(match, tipo, fine_libera=False):
    """Read explicit dates or a declared number of complete calendar months.

    `fine_libera` (solo con periodo_regola dichiarata nel profilo): anni fiscali a
    52 settimane chiudono il trimestre di sabato, non a fine mese.
    """
    groups = match.groupdict()
    if 'anno' in groups:
        if not re.fullmatch(r'\d{4}', groups['anno'] or ''):
            raise ValueError('anno comune non completo')
        anno = int(groups['anno'])
        numeric = {'giorno_inizio', 'mese_inizio', 'giorno_fine', 'mese_fine', 'anno'}
        if set(groups) == numeric:
            inizio = date(anno, int(groups['mese_inizio']), int(groups['giorno_inizio']))
            fine = date(anno, int(groups['mese_fine']), int(groups['giorno_fine']))
        elif set(groups) == {'inizio', 'fine', 'anno'}:
            inizio, fine = (_data(groups[k] + ' ' + groups['anno']) for k in ('inizio', 'fine'))
        else:
            raise ValueError('anno comune: estremi testuali o gruppi giorno/mese espliciti richiesti')
        if inizio > fine:
            raise ValueError('anno comune: intervallo invertito; nessun anno precedente implicito')
        return inizio, fine, {'regola': 'anno_comune_esplicito', 'anno': anno,
            'periodo_inizio': inizio.isoformat(), 'periodo_fine': fine.isoformat()}
    if {"inizio", "fine"} <= set(groups) and "mesi" not in groups:
        return _data(groups["inizio"]), _data(groups["fine"]), None
    if set(groups) != {"mesi", "fine"}:
        raise ValueError("periodo: servono gruppi inizio/fine oppure mesi/fine")
    counts = {"3": 3, "three": 3, "6": 6, "six": 6,
              "9": 9, "nine": 9, "12": 12, "twelve": 12,
              # fase C: relazioni IR italiane e tedesche («sei mesi chiusi al …»)
              "tre": 3, "sei": 6, "nove": 9, "dodici": 12,
              "drei": 3, "sechs": 6, "neun": 9, "zwölf": 12,
              # durata dichiarata dal titolo della relazione («Half-Year … as of», «semestrale al»)
              "half": 6, "semestrale": 6}
    mesi = counts.get(_norm(groups["mesi"]).lower())
    expected = {"annuale": 12, "semestrale": 6, "trimestrale": 3, "nove_mesi": 9}[tipo]
    if mesi != expected:
        raise ValueError("durata testuale in mesi assente o incompatibile con il tipo")
    fine = _data(groups["fine"])
    from calendar import monthrange
    if fine_libera and fine.day != monthrange(fine.year, fine.month)[1]:
        from datetime import timedelta
        inizio = _mesi_prima(fine, mesi) + timedelta(days=1)
        return inizio, fine, {"regola": "mesi_con_fine_libera", "mesi": mesi,
                              "periodo_inizio": inizio.isoformat(), "periodo_fine": fine.isoformat()}
    if fine.day != monthrange(fine.year, fine.month)[1]:
        raise ValueError("durata in mesi: data finale non a fine mese; servono date esplicite")
    first = fine.year * 12 + fine.month - mesi
    inizio = date(first // 12, first % 12 + 1, 1)
    return inizio, fine, {"regola": "mesi_calendario_con_fine_mese", "mesi": mesi,
                          "periodo_inizio": inizio.isoformat(), "periodo_fine": fine.isoformat()}


def _id(value):
    namespace, ident = str(value).split(":", 1)
    if namespace.upper() == "CIK":
        if not ident.isdigit():
            raise ValueError("CIK non numerico")
        ident = str(int(ident)).zfill(10)
    elif namespace.upper() == "LEI":
        ident = ident.upper()
    elif namespace.upper() != "EMITTENTE":
        raise ValueError("namespace emittente non supportato")
    if not ident.strip():
        raise ValueError("identificatore emittente vuoto")
    return f"{namespace.upper()}:{ident}"


def _campo_xml(block, campo):
    return re.search(rf"<(?:[\w.-]+:)?{campo}\b[^>]*>(.*?)</(?:[\w.-]+:)?{campo}\s*>",
                     block, re.S | re.I)


def _contesti(markup):
    """I contesti con dimensioni non sono prova del periodo dell'intero emittente."""
    for match in re.finditer(r"<(?:[\w.-]+:)?context\b[^>]*>.*?</(?:[\w.-]+:)?context\s*>",
                             markup, re.S | re.I):
        block = match[0]
        if re.search(r"<(?:[\w.-]+:)?(?:scenario|segment)\b", block, re.I):
            continue
        identifier = _campo_xml(block, "identifier")
        start, end = _campo_xml(block, "startDate"), _campo_xml(block, "endDate")
        if not identifier or not start or not end:
            continue
        scheme = re.search(r'\bscheme\s*=\s*[\"\']([^\"\']+)', identifier[0], re.I)
        scheme = scheme[1].lower() if scheme else ""
        namespace = "CIK" if "sec.gov/cik" in scheme else "LEI" if "17442" in scheme or "lei" in scheme else None
        if not namespace:
            continue
        yield {"emittente_id": _id(f"{namespace}:{unescape(identifier[1]).strip()}"),
               "inizio": _data(start[1]), "fine": _data(end[1]), "match": match}


def _prova(testo, inizio, fine, supporto, url, digest):
    return {"url": url, "sha256": digest, "supporto": supporto,
            "unita_offset": "caratteri Unicode nel markup decodificato" if supporto == "markup"
                            else "caratteri Unicode nel testo estratto",
            "inizio": inizio, "fine": fine, "testo": testo[inizio:fine]}


def _cerca_prova(testo, pattern, campo, url, digest):
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError(f"regola di verifica {campo} mancante")
    # REV_G2b A1/C1: il pattern del profilo si ESEGUE solo nel processo separato con tempo massimo;
    # qui arriva l'esito (posizioni), mai una riesecuzione nel backend. PatternTroppoLento e' un
    # ValueError: «verifica non completata: pattern troppo lento».
    match = regex_sandbox.cerca(testo, pattern)
    if not match or not match[0].strip():
        raise ValueError(f"{campo}: prova testuale assente")
    return _prova(testo, match.start(), match.end(), "testo", url, digest)


def _periodo_con_prova(testo, pattern, tipo, fine_attesa, url, digest, regola=None):
    """Shared textual-period check for original verification and PDF replay.

    `regola="piu_recente"` (dichiarata nel profilo): senza data di catalogo, il
    periodo corrente e i comparativi dell'anno prima nella stessa pagina non sono
    un'ambiguita': vince la fine piu' recente.
    """
    if not pattern:
        raise ValueError("periodo esatto non verificato: mancano contesto XBRL compatibile o date testuali")
    parsed = []
    # REV_G2b C1: corrispondenze calcolate nel processo separato (posizioni e gruppi nominati)
    for match in regex_sandbox.trova_tutti(testo, pattern):
        start, end, calculation = _periodo_testuale(match, tipo, fine_libera=regola == "piu_recente")
        parsed.append((start, end, match, calculation))
    if fine_attesa:
        if parsed and max(p[1] for p in parsed) != fine_attesa:
            raise ValueError("data catalogo diversa dal periodo testuale piu' recente: possibile comparativo")
        parsed = [p for p in parsed if p[1] == fine_attesa]
    elif regola == "piu_recente" and parsed:
        recente = max(p[1] for p in parsed)
        parsed = [p for p in parsed if p[1] == recente]
    if len({(a, b) for a, b, _, _ in parsed}) != 1:
        raise ValueError("periodo testuale assente o ambiguo")
    inizio, fine, match, calculation = parsed[0]
    proof = _prova(testo, match.start(), match.end(), "testo", url, digest)
    if calculation is not None:
        proof['calcolo'] = calculation
    return inizio, fine, proof


def _selettori(testo, regole, salta_indice=False):
    """Intestazione unica e primo confine successivo: nessuna pagina fissa.

    Inizio ripetuto anche nell'indice = ambiguita' dichiarata. Il primo confine
    completo successivo chiude la sezione; un altro nel bilancio individuale
    successivo non allunga arbitrariamente l'intervallo consolidato.
    """
    if not isinstance(regole, dict) or not regole:
        raise ValueError("regole sezioni mancanti")
    righe = [m for m in re.finditer(r"[^\n]+", testo) if m[0].strip()]
    normalizzate = [_norm(m[0]) for m in righe]
    result, motivi = {}, {}
    for nome, regola in regole.items():
        if not isinstance(regola, dict) or any(k in regola for k in ("da_pagina", "a_pagina")):
            raise ValueError(f"{nome}: pagine fisse non ammesse nell'automazione")
        try:
            a, b = regola["inizio"], regola["fine"]
            if not isinstance(a, str) or not isinstance(b, str):
                raise TypeError("regola di sezione non testuale")
            # REV_G2b C1: righe che combaciano calcolate nel processo separato; nessun re sui
            # pattern del profilo nel backend (nemmeno in ha_corpo, che usa gli stessi indici).
            idx_a, idx_b = regex_sandbox.righe_che_combaciano(normalizzate, a, b)
            idx_b = set(idx_b)
            starts = [righe[i] for i in idx_a]
            if len(starts) > 1 and salta_indice:
                # Opt-in del profilo: una voce d'indice e' seguita solo da altre voci
                # (Item/Part) o numeri di pagina fino all'intestazione finale; il corpo
                # ha almeno una riga di testo, anche breve (rimando a una nota), e poi
                # l'intestazione finale. Un'occorrenza senza fine successiva non e' la sezione.
                def ha_corpo(start):
                    testo_visto = False
                    for i, m in enumerate(righe):
                        if m.start() <= start.end():
                            continue
                        riga = normalizzate[i]
                        if i in idx_b:
                            return testo_visto
                        if not re.fullmatch(r"\d{1,4}|(?:item|part)\b.{0,200}", riga, re.I):
                            testo_visto = True
                    return False
                starts = [m for m in starts if ha_corpo(m)]
            if len(starts) != 1:
                raise ValueError("intestazione iniziale assente o ambigua")
            start = starts[0]
            ends = [righe[i] for i in sorted(idx_b) if righe[i].start() > start.end()]
            if not ends:
                raise ValueError("intestazione finale assente")
            end = ends[0]
            # Il motore pubblicato usa righe esatte e occorrenze esplicite.
            result[nome] = {"inizio": _norm(start[0]), "fine": _norm(end[0]),
                            "occorrenza_inizio": sum(_norm(m[0]) == _norm(start[0]) for m in righe
                                                     if m.start() <= start.start()),
                            "occorrenza_fine": sum(_norm(m[0]) == _norm(end[0]) for m in righe
                                                   if m.start() <= end.start())}
        except (KeyError, ValueError, TypeError, re.error) as exc:
            result[nome] = {}  # il motore marca la sezione non disponibile
            motivi[nome] = str(exc)
    return result, motivi


# Collegamenti decisi dal codice (non da un alias verificato, dall'utente o a mano): seguito revisione G1.
_COLLEGAMENTI_AUTOMATICI = ("ticker", "nome")


def _registrante(attivo):
    """dei:EntityRegistrantName del documento inline-XBRL, None se non dichiarato."""
    m = re.search(r'<(?:[\w.-]+:)?nonNumeric\b(?=[^>]*\sname\s*=\s*[\"\'](?:[\w.-]+:)?EntityRegistrantName[\"\'])'
                  r'[^>]*>(.*?)</(?:[\w.-]+:)?nonNumeric\s*>', attivo or "", re.I | re.S)
    nome = _norm(unescape(re.sub(r"<[^>]+>", "", m[1]))) if m else ""
    return nome or None


def verifica_documento(path, *, url, profilo, catalogo=None):
    """Verifica del documento con un tempo TOTALE per le prove dei pattern del profilo (REV_G2b A1:
    un profilo con molti pattern lenti non somma N volte il tempo di una prova)."""
    with regex_sandbox.budget(regex_sandbox.TEMPO_DOCUMENTO_S):
        return _verifica_documento(path, url=url, profilo=profilo, catalogo=catalogo)


def _verifica_documento(path, *, url, profilo, catalogo=None):
    """Restituisce il documento I-20 soltanto se ogni metadato ha una prova.

    HTML: identita'/periodo da contesto XBRL e data del catalogo, oppure dalle
    regole testuali esplicite come nei PDF. Date solo in titolo/filename e
    periodo riportato nel catalogo non bastano, da soli, alla verifica.
    """
    out = {"stato": "non_verificato", "motivi": []}
    ocr = None
    try:
        catalogo = catalogo or {}
        meta = {k: profilo[k] for k in ("emittente_id", "lingua", "tipo", "perimetro")}
        meta["emittente_id"] = _id(meta["emittente_id"])
        if meta["tipo"] not in _DURATE:
            raise ValueError("tipo di relazione non supportato")
        if not all(isinstance(v, str) and v.strip() for v in meta.values()):
            raise ValueError("metadati del profilo incompleti")
        if catalogo.get("emittente_id") and _id(catalogo["emittente_id"]) != meta["emittente_id"]:
            raise ValueError("emittente del catalogo diverso dal profilo")
        form = catalogo.get("form", "")
        if form.endswith("/A") or catalogo.get("rettifica"):
            raise ValueError("rettifica: confronto delle versioni non ancora implementato")
        expected_types = {"10-K": {"annuale"}, "20-F": {"annuale"}, "40-F": {"annuale"},
                          "10-Q": {"trimestrale", "semestrale", "nove_mesi"}}
        if form in expected_types and meta["tipo"] not in expected_types[form]:
            raise ValueError("tipo di relazione incompatibile con il form SEC")
        if catalogo.get("language") and catalogo["language"].lower().split("-")[0] != meta["lingua"]:
            raise ValueError("lingua del catalogo diversa dal profilo")
        raw = Path(path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        estrazione = estrai_testo(str(path), contenuto=raw)
        if estrazione["stato"] != "ok":
            raise ValueError(estrazione.get("motivo", "documento illeggibile"))
        vuote = estrazione.get("pagine_senza_testo") or []
        if estrazione["formato"] == "pdf" and len(vuote) >= 5 and len(vuote) * 2 > (estrazione.get("pagine") or 0):
            # Solo per spiegare un fallimento: un PDF che si verifica comunque resta valido.
            ocr = f"PDF quasi senza testo ({len(vuote)} pagine su {estrazione['pagine']}): serve OCR"
        testo = estrazione["testo"]
        if len(testo) > 5_000_000:
            raise ValueError("documento oltre il limite del motore I-20")
        regole = profilo.get("verifica", {})
        prove = {k: _cerca_prova(testo, regole.get(k), k, url, digest)
                 for k in ("lingua", "tipo", "perimetro")}
        markup, contexts = "", []
        if estrazione["formato"] == "html":
            markup = raw.decode(estrazione.get("codifica", "utf-8-sig"))
            parser = _MarkupAttivo(markup)
            attivo = parser.testo_attivo()
            if any(not lang or lang.lower().split("-")[0] != meta["lingua"] for lang in parser.lingue):
                raise ValueError("lingua HTML diversa dal profilo; nessuna traduzione automatica")
            tipi_dichiarati = re.findall(
                r'<(?:[\w.-]+:)?nonNumeric\b(?=[^>]*\sname\s*=\s*[\"\'](?:[\w.-]+:)?DocumentType[\"\'])[^>]*>(.*?)</(?:[\w.-]+:)?nonNumeric\s*>',
                attivo, re.I | re.S)
            for dichiarazione in tipi_dichiarati:
                tipo_doc = _norm(unescape(re.sub(r"<[^>]+>", "", dichiarazione)))
                if tipo_doc.endswith("/A"):
                    raise ValueError("rettifica dichiarata nel documento: versione non supportata")
                if form and tipo_doc != form:
                    raise ValueError("tipo SEC dichiarato nel documento diverso dal catalogo")
            contexts = list(_contesti(attivo))
        matching = [c for c in contexts if c["emittente_id"] == meta["emittente_id"]]
        if contexts and not matching:
            raise ValueError("identificatore XBRL diverso dall'emittente atteso")
        if matching:
            m = matching[0]["match"]
            prove["emittente"] = _prova(markup, m.start(), m.end(), "markup", url, digest)
            if profilo.get("origine_collegamento") in _COLLEGAMENTI_AUTOMATICI:
                # Seguito revisione G1: il CIK del markup lega il documento al CIK del profilo, non
                # il CIK al titolo del portafoglio. Per i collegamenti automatici il registrante deve
                # avere un nome compatibile col titolo; se il documento non lo dichiara, vale la
                # prova testuale del nome come per i documenti senza XBRL.
                registrante = _registrante(attivo)
                if registrante is not None:
                    from bellomberg.market_data.filing_identita import nomi_compatibili
                    if not nomi_compatibili(profilo.get("nome"), registrante):
                        raise ValueError(f"registrante del documento ({registrante[:80]}) diverso dal titolo del "
                                         f"portafoglio ({str(profilo.get('nome'))[:80]}): collegamento da confermare")
                else:
                    prove["emittente_nome"] = _cerca_prova(testo[:20_000], regole.get("emittente"), "emittente",
                                                           url, digest)
        else:
            # Identita' testuale limitata alla parte iniziale; non il nome di un
            # concorrente citato nella lunga sezione dei rischi.
            prove["emittente"] = _cerca_prova(testo[:20_000], regole.get("emittente"), "emittente", url, digest)
        period_end = catalogo.get("report_date") or catalogo.get("period_end")
        fine_attesa = _data(period_end) if period_end else None
        low, high = _DURATE[meta["tipo"]]
        stessa_durata = [c for c in matching if low <= (c["fine"]-c["inizio"]).days+1 <= high]
        # Un contesto mensile dopo la chiusura (es. buyback successivi) non e'
        # un nuovo annuale; confrontare solo la durata del report richiesto.
        if stessa_durata and fine_attesa and max(c["fine"] for c in stessa_durata) != fine_attesa:
            raise ValueError("data catalogo diversa dal periodo XBRL omologo piu' recente: possibile comparativo o catalogo errato")
        regola = profilo.get("periodo_regola")
        if not fine_attesa and regola == "piu_recente" and stessa_durata:
            fine_attesa = max(c["fine"] for c in stessa_durata)  # comparativi XBRL dell'anno prima
        eligible = [c for c in stessa_durata if fine_attesa == c["fine"]]
        periods = {(c["inizio"], c["fine"]) for c in eligible}
        if len(periods) > 1:
            raise ValueError("periodi XBRL ambigui per la stessa data di report")
        if periods:
            inizio, fine = next(iter(periods))
            m = eligible[0]["match"]
            prove["periodo"] = _prova(markup, m.start(), m.end(), "markup", url, digest)
        else:
            inizio, fine, prove["periodo"] = _periodo_con_prova(
                testo, regole.get("periodo"), meta["tipo"], fine_attesa, url, digest, regola=regola)
        if not low <= (fine-inizio).days+1 <= high:
            raise ValueError("durata del periodo incompatibile con il tipo")
        if fine > date.today():
            raise ValueError("periodo futuro: non e' una relazione consuntiva")
        meta.update(periodo_inizio=inizio.isoformat(), periodo_fine=fine.isoformat())
        if profilo.get("sezioni_intero"):
            # Opt-in (allegati 6-K a struttura libera): una sola sezione, tutto il testo.
            non_vuote = [_norm(m[0]) for m in re.finditer(r"[^\n]+", testo) if m[0].strip()]
            if len(non_vuote) < 2:
                raise ValueError("testo intero: documento senza righe sufficienti")
            selettori = {"testo": {"inizio": non_vuote[0], "fine": non_vuote[-1], "occorrenza_inizio": 1,
                                   "occorrenza_fine": non_vuote.count(non_vuote[-1])}}
            problemi_sezioni = {}
        else:
            selettori, problemi_sezioni = _selettori(testo, profilo.get("sezioni"),
                                                     salta_indice=bool(profilo.get("sezioni_salta_indice")))
        doc = prepara_documento(path, url=url, metadati=meta, sezioni=selettori)
        if doc.get("sha256") != digest:
            raise ValueError("documento cambiato durante la verifica: hash differente")
        if doc["stato"] != "ok":
            raise ValueError(doc.get("motivo", "preparazione fallita"))
        for nome, motivo in problemi_sezioni.items():
            doc["sezioni"][nome] = {"stato": "non_disponibile", "motivo": motivo}
        doc["metadati_verifica"] = "verificati con regole del profilo e prove nei byte del documento"
        doc["prove_verifica"] = prove
        if estrazione['formato'] == 'pdf':
            doc['filing_verification'] = {
                'schema': 'filing_pdf_v1', 'url': url, 'document_sha256': digest,
                'text_sha256': hashlib.sha256(testo.encode('utf-8')).hexdigest(),
                'metadata': deepcopy(meta), 'proofs': deepcopy(prove),
                'rules': {k: regole[k] for k in ('emittente', 'lingua', 'tipo', 'perimetro', 'periodo')},
                'identity_basis': 'curated_filing_profile', 'security_identity_verified': False}
        return {"stato": "ok", "motivi": [], "documento": doc}
    except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError, re.error) as exc:
        out["motivi"].append((f"{ocr}; " if ocr else "") + f"{type(exc).__name__}: {exc}")
        return out
