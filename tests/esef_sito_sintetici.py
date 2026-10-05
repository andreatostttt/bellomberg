"""Fase F: pacchetti ESEF sintetici (iXBRL) e un sito IR finto, per i test di esef_sito.

`ixbrl_da_json` riscrive uno xBRL-JSON sintetico (filing_esef_sintetici) come Inline XBRL:
la conversione di ritorno (ixbrl_oim) deve ridare gli stessi fatti. Dati inventati.
"""
import io
import json
import zipfile
from datetime import date, timedelta
from xml.sax.saxutils import escape

NS = {"ifrs-full": "https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full", "nova": "http://nova.example/2025",
      "iso4217": "http://www.xbrl.org/2003/iso4217"}


def _giorno_prima(valore):
    return (date.fromisoformat(valore[:10]) - timedelta(days=1)).isoformat()


def ixbrl_da_json(grezzo):
    dati = json.loads(grezzo)
    contesti, unita, corpo = {}, {}, []
    for fid, f in dati["facts"].items():
        d = dict(f["dimensions"])
        concetto, entita, periodo = d.pop("concept"), d.pop("entity"), d.pop("period")
        unit, lingua = d.pop("unit", None), d.pop("language", None)
        chiave = (entita, periodo, tuple(sorted(d.items())))
        if chiave not in contesti:
            cid = f"c{len(contesti) + 1}"
            if "/" in periodo:
                inizio, fine = periodo.split("/")
                per = f"<xbrli:startDate>{inizio[:10]}</xbrli:startDate><xbrli:endDate>{_giorno_prima(fine)}</xbrli:endDate>"
            else:
                per = f"<xbrli:instant>{_giorno_prima(periodo)}</xbrli:instant>"
            seg = "".join(f'<xbrldi:explicitMember dimension="{k}">{v}</xbrldi:explicitMember>' for k, v in d.items())
            contesti[chiave] = (cid, '<xbrli:context id="%s"><xbrli:entity><xbrli:identifier scheme="http://standards.iso.org/iso/17442">%s</xbrli:identifier>%s</xbrli:entity><xbrli:period>%s</xbrli:period></xbrli:context>'
                                % (cid, entita.split(":", 1)[1], f"<xbrli:segment>{seg}</xbrli:segment>" if seg else "", per))
        cid = contesti[chiave][0]
        if unit:
            uid = unita.setdefault(unit, f"u{len(unita) + 1}")
            v = str(f["value"])
            segno = ' sign="-"' if v.startswith("-") else ""
            corpo.append(f'<ix:nonFraction id="{fid}" name="{concetto}" contextRef="{cid}" unitRef="{uid}" '
                         f'decimals="0"{segno}>{v.lstrip("-")}</ix:nonFraction>')
        else:
            lang = f' xml:lang="{lingua}"' if lingua else ""
            corpo.append(f'<div{lang}><ix:nonNumeric id="{fid}" name="{concetto}" contextRef="{cid}" escape="true">'
                         f'{f["value"]}</ix:nonNumeric></div>')
    unita_xml = "".join(f'<xbrli:unit id="{uid}"><xbrli:measure>{u}</xbrli:measure></xbrli:unit>' for u, uid in unita.items())
    ns = " ".join(f'xmlns:{p}="{u}"' for p, u in NS.items())
    return (f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
            f'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance" '
            f'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" {ns}><head><title>Nova</title></head><body>'
            f'<div style="display:none"><ix:header><ix:resources>{"".join(c for _, c in contesti.values())}{unita_xml}'
            f'</ix:resources></ix:header></div>{"".join(corpo)}</body></html>').encode("utf-8")


def pacchetto(grezzo_json, nome="nova"):
    """bytes di un pacchetto .xbri con il report iXBRL sotto <nome>/reports/."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{nome}/META-INF/reportPackage.json", '{"documentInfo": {}}')
        zf.writestr(f"{nome}/reports/{nome}.xhtml", ixbrl_da_json(grezzo_json))
    return buf.getvalue()


def pagina(*link, titolo="Nova"):
    """HTML di una pagina con i link dati come (href, testo)."""
    return ("<html><head><title>%s</title></head><body>%s</body></html>"
            % (escape(titolo), "".join(f'<a href="{escape(h)}">{escape(t)}</a>' for h, t in link))).encode()


class RispostaFinta:
    def __init__(self, url, corpo=b"", *, status=200, tipo="text/html; charset=utf-8", location=None):
        self.url, self.content, self.status_code = url, corpo, status
        self.headers = {"Content-Type": tipo}
        if location:
            self.headers["Location"] = location
        self.encoding = "utf-8"
        self.raw = None


def get_finto(pagine, chiamate):
    """requests.get finto per Navigatore: `pagine` {url: bytes | RispostaFinta}; 404 altrove."""
    def get(url, **kw):
        chiamate.append(url)
        v = pagine.get(url)
        if v is None:
            return RispostaFinta(url, b"", status=404)
        return v if isinstance(v, RispostaFinta) else RispostaFinta(url, v)
    return get
