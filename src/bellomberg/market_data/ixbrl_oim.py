"""Inline XBRL (ESEF) -> xBRL-JSON (OIM) nella forma che serve filings.xbrl.org.

Per i depositi che il repository pubblica senza JSON: il pacchetto `.xbri`/`.zip`
(`<radice>/reports/*.xhtml`) diventa lo stesso dict che `filing_esef.blocchi` e
`filing_esef._fatti_numerici` gia' leggono: `documentInfo` + `facts`, ogni fatto con
`value` (+ `decimals`) e `dimensions` {concept, entity `scheme:<id>`, period con fine
ESCLUSIVA (`2025-12-31` -> `2026-01-01T00:00:00`), unit, language, assi}.
Lettura in streaming (iterparse): il sottoalbero si tiene solo dentro fatti,
continuation, contesti e unita', il resto si pota man mano. Prova reale del
2026-10-04 (banca, XHTML da 84 MB, 28 mila continuation): vedi il report di fase F.
Limiti dichiarati in `conversione.limiti` (ix:fraction, footnote, tuple, formati non
gestiti), mai silenziosi. Nessuna rete: niente DTD, niente entita' esterne.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import html
from pathlib import PurePosixPath
import re
import zipfile
import zlib

from lxml import etree

IX = "http://www.xbrl.org/2013/inlineXBRL"
XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
LINK = "http://www.xbrl.org/2003/linkbase"
XLINK = "http://www.w3.org/1999/xlink"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
TIPO_DOCUMENTO = "https://xbrl.org/2021/xbrl-json"
MAX_XHTML_BYTES = 200 * 1024 * 1024
MAX_RAPPORTO = 100  # non compresso / compresso del membro: oltre e' un sospetto zip bomb

# Prefissi canonici: i consumatori cercano `ifrs-full:` alla lettera, qualunque prefisso
# abbia scelto il tagger nel documento.
_CANONICI = ((re.compile(r"^https?://xbrl\.ifrs\.org/taxonomy/[\d-]+/ifrs-full$"), "ifrs-full"),
             (re.compile(r"^http://www\.xbrl\.org/2003/iso4217$"), "iso4217"),
             (re.compile(r"^http://www\.xbrl\.org/2003/instance$"), "xbrli"))
_VUOTI = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_TAG = re.compile(r"<[^>]*>")
_REPORT = re.compile(r"\.x?html?$", re.I)


class PacchettoNonValido(ValueError):
    """Pacchetto o XHTML rifiutato: il messaggio dice perche' (in italiano)."""


def _locale(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _ns(tag):
    return tag[1:].split("}", 1)[0] if isinstance(tag, str) and tag.startswith("{") else ""


class _Prefissi:
    """uri -> prefisso del documento, con i canonici imposti e i conflitti numerati."""

    def __init__(self):
        self.per_uri, self.usati = {}, {}

    def prefisso(self, uri, suggerito):
        if uri in self.per_uri:
            return self.per_uri[uri]
        base = next((p for r, p in _CANONICI if r.match(uri)), None) or suggerito or "ns"
        scelto, n = base, 1
        while scelto in self.usati:
            n += 1
            scelto = f"{base}{n}"
        self.per_uri[uri], self.usati[scelto] = scelto, uri
        return scelto

    def qname(self, testo, nsmap):
        """QName del documento ("ifrs-full:Revenue") nella forma canonica, o None."""
        testo = (testo or "").strip()
        pref, _, nome = testo.rpartition(":")
        uri = nsmap.get(pref or None)
        if not nome or not uri:
            return None
        return f"{self.prefisso(uri, pref)}:{nome}"


def _markup(el, parti, radice=True):
    """Contenuto XHTML di `el` senza tag ix (resta il loro testo), senza ix:exclude e
    senza namespace: e' la forma dei text block con escape, e la base del testo semplice."""
    if not radice:
        ns, nome = _ns(el.tag), _locale(el.tag)
        if not isinstance(el.tag, str):  # commenti e processing instruction: fuori dal valore
            if el.tail:
                parti.append(html.escape(el.tail, quote=False))
            return
        if ns == IX and nome == "exclude":
            if el.tail:
                parti.append(html.escape(el.tail, quote=False))
            return
        tag = None if ns == IX else nome
        if tag:
            attributi = "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in el.attrib.items()
                                if not k.startswith("{"))
            parti.append(f"<{tag}{attributi}{'/' if tag in _VUOTI and not len(el) and not el.text else ''}>")
    else:
        tag = None
    if el.text:
        parti.append(html.escape(el.text, quote=False))
    for figlio in el:
        _markup(figlio, parti, radice=False)
    if not radice:
        if tag and not (tag in _VUOTI and not len(el) and not el.text):
            parti.append(f"</{tag}>")
        if el.tail:
            parti.append(html.escape(el.tail, quote=False))


def _testo(markup):
    return html.unescape(_TAG.sub("", markup))


def _data_oim(valore, fine):
    """Data XBRL -> OIM: una data senza ora che chiude un periodo vale fino a mezzanotte
    del giorno dopo (fine esclusiva); un inizio vale dalla mezzanotte del giorno stesso."""
    valore = (valore or "").strip()
    if "T" in valore:
        return valore
    giorno = date.fromisoformat(valore)
    return datetime.combine(giorno + timedelta(days=1) if fine else giorno, datetime.min.time()).isoformat()


def _decimale(d):
    """Decimale canonico senza esponente ne' zeri finali ("1234000000", "0.25")."""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


_ZERO = {"fixed-zero", "zerodash", "numdash"}


def _numero(testo, formato):
    """Testo visualizzato -> Decimal secondo il formato ixt (None se il formato non e' gestito)."""
    nome = (formato or "").rsplit(":", 1)[-1].lower()
    grezzo = " ".join((testo or "").replace(" ", " ").split())
    if nome in _ZERO:
        return Decimal(0)
    if nome in ("", "num-dot-decimal", "numdotdecimal", "numcommadot", "numspacedot"):
        pulito = re.sub(r"[^\d.]", "", grezzo)
    elif nome in ("num-comma-decimal", "numcommadecimal", "numdotcomma", "numspacecomma"):
        pulito = re.sub(r"[^\d,]", "", grezzo).replace(",", ".")
    else:
        return None
    if not pulito or pulito.count(".") > 1:
        raise InvalidOperation(grezzo)
    return Decimal(pulito)


class _Convertitore:
    def __init__(self):
        self.prefissi = _Prefissi()
        self.contesti, self.unita, self.grezzi, self.continuazioni = {}, {}, [], {}
        self.schemi, self.tassonomia, self.limiti = {}, [], {}

    def limite(self, motivo):
        self.limiti[motivo] = self.limiti.get(motivo, 0) + 1

    # -- elementi completi (chiamati all'evento end, sottoalbero ancora intero) --

    def contesto(self, el):
        ident = el.find(f"{{{XBRLI}}}entity/{{{XBRLI}}}identifier")
        periodo = el.find(f"{{{XBRLI}}}period")
        if ident is None or periodo is None:
            self.limite("contesto senza entita' o periodo")
            return
        schema = (ident.get("scheme") or "").strip()
        if schema not in self.schemi:
            self.schemi[schema] = "scheme" if not self.schemi else f"scheme{len(self.schemi) + 1}"
        dims = {"entity": f"{self.schemi[schema]}:{(ident.text or '').strip()}"}
        istante = periodo.find(f"{{{XBRLI}}}instant")
        try:
            if istante is not None:
                dims["period"] = _data_oim(istante.text, fine=True)
            elif periodo.find(f"{{{XBRLI}}}forever") is not None:
                self.limite("contesti forever (senza periodo OIM)")
                return
            else:
                dims["period"] = (_data_oim(periodo.findtext(f"{{{XBRLI}}}startDate"), fine=False) + "/"
                                  + _data_oim(periodo.findtext(f"{{{XBRLI}}}endDate"), fine=True))
        except (TypeError, ValueError):
            self.limite("contesto con data non valida")
            return
        for m in el.iter(f"{{{XBRLDI}}}explicitMember", f"{{{XBRLDI}}}typedMember"):
            asse = self.prefissi.qname(m.get("dimension"), m.nsmap)
            if not asse:
                self.limite("asse con QName non risolto")
                continue
            if _locale(m.tag) == "explicitMember":
                dims[asse] = self.prefissi.qname(m.text, m.nsmap) or (m.text or "").strip()
            else:
                dims[asse] = "".join(m.itertext()).strip()
        self.contesti[el.get("id")] = dims

    def unita_xbrl(self, el):
        def misure(padre):
            return "*".join(sorted(self.prefissi.qname(m.text, m.nsmap) or (m.text or "").strip()
                                   for m in padre.findall(f"{{{XBRLI}}}measure")))
        div = el.find(f"{{{XBRLI}}}divide")
        if div is None:
            u = misure(el)
        else:
            num = misure(div.find(f"{{{XBRLI}}}unitNumerator"))
            den = misure(div.find(f"{{{XBRLI}}}unitDenominator"))
            u = f"{f'({num})' if '*' in num else num}/{f'({den})' if '*' in den else den}"
        self.unita[el.get("id")] = u

    def fatto(self, el, lingua):
        locale = _locale(el.tag)
        nome = self.prefissi.qname(el.get("name"), el.nsmap)
        if not nome:
            self.limite("fatto con concetto non risolto (scartato)")
            return
        parti = []
        _markup(el, parti)
        self.grezzi.append({"tipo": locale, "id": el.get("id"), "concept": nome, "contesto": el.get("contextRef"),
                            "unita": el.get("unitRef"), "markup": "".join(parti), "lingua": lingua,
                            "nil": el.get(f"{{{XSI}}}nil") in ("true", "1"),
                            "escape": el.get("escape") in ("true", "1"), "segue": el.get("continuedAt"),
                            "formato": el.get("format"), "scala": el.get("scale"), "segno": el.get("sign"),
                            "decimali": el.get("decimals"), "precisione": el.get("precision")})

    def continuazione(self, el):
        parti = []
        _markup(el, parti)
        self.continuazioni[el.get("id")] = ("".join(parti), el.get("continuedAt"))

    # -- assemblaggio finale: contesti/unita' possono stare dopo i fatti, le continuation prima --

    def valore_testo(self, g):
        markup, visti, segue = g["markup"], set(), g["segue"]
        pezzi = [markup]
        while segue:
            if segue in visti or segue not in self.continuazioni:
                self.limite("catena continuedAt interrotta o ciclica (testo troncato)")
                break
            visti.add(segue)
            pezzo, segue = self.continuazioni[segue]
            pezzi.append(pezzo)
        markup = "".join(pezzi)
        valore = markup if g["escape"] else _testo(markup)
        formato = (g["formato"] or "").rsplit(":", 1)[-1].lower()
        if formato in ("fixed-empty", "nocontent"):
            return ""
        if formato in ("fixed-true", "booleantrue"):
            return "true"
        if formato in ("fixed-false", "booleanfalse"):
            return "false"
        if formato:
            self.limite(f"formato {formato} su ix:nonNumeric non applicato (testo visualizzato)")
        return valore

    def risultato(self):
        facts, usati = {}, {g["id"] for g in self.grezzi if g["id"]}
        for n, g in enumerate(self.grezzi, 1):
            dims_ctx = self.contesti.get(g["contesto"])
            if dims_ctx is None:
                self.limite("fatto con contextRef mancante (scartato)")
                continue
            dims = {"concept": g["concept"], "entity": dims_ctx["entity"], "period": dims_ctx["period"]}
            fatto = {}
            if g["tipo"] == "nonFraction":
                unita = self.unita.get(g["unita"])
                if unita is None:
                    self.limite("fatto numerico con unitRef mancante (scartato)")
                    continue
                if unita != "xbrli:pure":  # in OIM l'unita' pura e' l'assenza della dimensione
                    dims["unit"] = unita
                if g["nil"]:
                    fatto["value"] = None
                else:
                    try:
                        numero = _numero(_testo(g["markup"]), g["formato"])
                        scala = int(g["scala"] or 0)
                        decimali = (g["decimali"] or "").strip()
                        decimali = int(decimali) if decimali and decimali != "INF" else None  # INF = assente
                    except (InvalidOperation, ValueError):  # scale/decimals malformati: un fatto, non il pacchetto
                        self.limite("fatto numerico non leggibile (scartato)")
                        continue
                    if numero is None:
                        self.limite(f"formato {g['formato']} non gestito (fatto scartato)")
                        continue
                    numero = numero.scaleb(scala)
                    fatto["value"] = _decimale(-numero if g["segno"] == "-" else numero)
                    if decimali is not None:
                        fatto["decimals"] = decimali
                    if g["precisione"]:
                        self.limite("attributo precision ignorato")
            else:
                fatto["value"] = None if g["nil"] else self.valore_testo(g)
                if g["lingua"]:
                    dims["language"] = g["lingua"]
            dims.update((k, v) for k, v in dims_ctx.items() if k not in ("entity", "period"))
            fatto["dimensions"] = dims
            fid = g["id"]
            if not fid or fid in facts:
                base, k = fid or f"ixf-{n}", 1
                fid = base
                while fid in facts or (fid != g["id"] and fid in usati):
                    k += 1
                    fid = f"{base}-{k}"
            facts[fid] = fatto
        namespaces = {p: u for p, u in self.prefissi.usati.items()}
        namespaces.update({p: u for u, p in self.schemi.items()})
        return {"documentInfo": {"documentType": TIPO_DOCUMENTO, "namespaces": namespaces,
                                 "taxonomy": self.tassonomia},
                "facts": facts,
                "conversione": {"origine": "inline XBRL convertito localmente (ixbrl_oim)", "report": None,
                                "fatti": len(facts), "contesti": len(self.contesti), "unita": len(self.unita),
                                "continuation": len(self.continuazioni),
                                "limiti": [f"{motivo}: {n}" for motivo, n in sorted(self.limiti.items())]}}


_IGNORATI = {"fraction": "ix:fraction non convertiti", "footnote": "ix:footnote non convertite",
             "relationship": "ix:relationship (note) non convertite", "tuple": "ix:tuple non convertite"}


def converti_xhtml(sorgente):
    """xBRL-JSON (dict) da un XHTML inline XBRL: percorso o file binario aperto.

    Ritorna {"documentInfo", "facts", "conversione": {report, fatti, contesti, unita,
    continuation, limiti}}. Solleva PacchettoNonValido se l'XHTML non e' ben formato.
    """
    conv = _Convertitore()
    lingue = []  # xml:lang ereditato, una voce per elemento aperto
    tenuti = 0  # elementi aperti il cui sottoalbero serve intero (fatti, continuation, contesti, unita')
    da_tenere = {(IX, "nonNumeric"), (IX, "nonFraction"), (IX, "continuation"), (IX, "fraction"),
                 (IX, "footnote"), (IX, "tuple"), (XBRLI, "context"), (XBRLI, "unit")}
    if isinstance(sorgente, (str, bytes)) or hasattr(sorgente, "__fspath__"):
        sorgente = str(sorgente)
    try:
        for evento, el in etree.iterparse(sorgente, events=("start", "end"), huge_tree=True,
                                          resolve_entities=False, no_network=True, load_dtd=False,
                                          remove_comments=True, remove_pis=True):
            chiave = (_ns(el.tag), _locale(el.tag))
            if evento == "start":
                lingue.append(el.get(XML_LANG) or (lingue[-1] if lingue else None))
                tenuti += chiave in da_tenere
                continue
            lingua = lingue.pop()
            if chiave in da_tenere:
                tenuti -= 1
                if chiave in ((IX, "nonNumeric"), (IX, "nonFraction")):
                    conv.fatto(el, (lingua or "").lower() or None)
                elif chiave == (IX, "continuation"):
                    conv.continuazione(el)
                elif chiave == (XBRLI, "context"):
                    conv.contesto(el)
                elif chiave == (XBRLI, "unit"):
                    conv.unita_xbrl(el)
                else:
                    conv.limite(_IGNORATI[chiave[1]])
            elif chiave == (IX, "relationship"):
                conv.limite(_IGNORATI["relationship"])
            elif chiave == (LINK, "schemaRef"):
                conv.tassonomia.append(el.get(f"{{{XLINK}}}href"))
            if not tenuti:
                # Potatura: niente sottoalberi gia' letti in memoria (84 MB di XHTML restano tali).
                el.clear(keep_tail=True)
                padre = el.getparent()
                while padre is not None and el.getprevious() is not None:
                    del padre[0]
    except etree.XMLSyntaxError as exc:
        raise PacchettoNonValido(f"XHTML non ben formato: {exc}") from exc
    except RecursionError as exc:  # revisione 04/10: annidamento patologico = pacchetto invalido, non guasto transitorio
        raise PacchettoNonValido("XHTML con annidamento troppo profondo per la conversione") from exc
    return conv.risultato()


class _Limitato:
    """Flusso del membro zip che si ferma oltre `massimo` byte letti davvero
    (la dimensione dichiarata nell'intestazione zip puo' mentire)."""

    def __init__(self, flusso, massimo):
        self.flusso, self.massimo, self.letti = flusso, massimo, 0

    def read(self, n=-1):
        dati = self.flusso.read(n)
        self.letti += len(dati)
        if self.letti > self.massimo:
            raise PacchettoNonValido(f"report oltre {self.massimo} byte non compressi")
        return dati


def _membro_sicuro(nome):
    p = PurePosixPath(nome.replace("\\", "/"))
    return not (p.is_absolute() or ".." in p.parts or re.match(r"^[A-Za-z]:", nome))


def _candidati(voci):
    """Report del pacchetto: XHTML/HTML sotto `reports/` (radice o primo livello), il piu' grande."""
    return [i for i in voci if not i.is_dir() and _REPORT.search(i.filename)
            and "reports" in PurePosixPath(i.filename).parts[:2]]


def converti_pacchetto(path, *, max_xhtml_bytes=MAX_XHTML_BYTES, max_rapporto=MAX_RAPPORTO):
    """xBRL-JSON (dict) da un report package ESEF (`.xbri` / `.zip`), senza estrarre su disco.

    Rifiuta (PacchettoNonValido) zip non validi, membri con percorso assoluto o `..`,
    pacchetti senza report sotto `reports/`, report oltre `max_xhtml_bytes` non compressi
    o con rapporto di compressione oltre `max_rapporto`. `conversione.report` dice quale
    file e' stato letto (il piu' grande se sono piu' d'uno; gli altri in `limiti`).
    """
    try:
        with zipfile.ZipFile(path) as zf:
            voci = zf.infolist()
            insicuri = [i.filename for i in voci if not _membro_sicuro(i.filename)]
            if insicuri:
                raise PacchettoNonValido(f"membro con percorso non sicuro nel pacchetto: {insicuri[0][:120]}")
            candidati = _candidati(voci)
            if not candidati:
                raise PacchettoNonValido("nessun report .xhtml/.html sotto reports/ nel pacchetto")
            scelto = max(candidati, key=lambda i: (i.file_size, i.filename))
            if scelto.file_size > max_xhtml_bytes:
                raise PacchettoNonValido(f"report di {scelto.file_size} byte non compressi, oltre il limite di {max_xhtml_bytes}")
            if scelto.file_size > max_rapporto * max(scelto.compress_size, 1):
                raise PacchettoNonValido(f"rapporto di compressione sospetto ({scelto.file_size} / {scelto.compress_size} byte): possibile zip bomb")
            with zf.open(scelto) as flusso:
                out = converti_xhtml(_Limitato(flusso, max_xhtml_bytes))
    except zipfile.BadZipFile as exc:
        raise PacchettoNonValido(f"pacchetto zip non valido: {exc}") from exc
    except (RuntimeError, NotImplementedError) as exc:  # membro cifrato o compressione non supportata
        raise PacchettoNonValido(f"report non leggibile: {exc}") from exc
    except (zlib.error, EOFError) as exc:  # revisione 04/10: deflate corrotto o troncato = invalido, non transitorio
        raise PacchettoNonValido(f"report compresso corrotto: {type(exc).__name__}: {exc}") from exc
    out["conversione"]["report"] = scelto.filename
    if len(candidati) > 1:
        out["conversione"]["limiti"].append(f"altri report nel pacchetto ignorati: {len(candidati) - 1}")
    return out
