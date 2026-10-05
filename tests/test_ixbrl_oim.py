"""ixbrl_oim: inline XBRL ESEF -> xBRL-JSON (OIM). Solo dati SINTETICI (LEI 999900..., nomi inventati)."""
import io
import zipfile

import pytest

from bellomberg.market_data import filing_esef, ixbrl_oim
from bellomberg.market_data.ixbrl_oim import PacchettoNonValido, converti_pacchetto, converti_xhtml

LEI = "999900NOVA0000000001"
NS = ('xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
      'xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
      'xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink" '
      'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" '
      'xmlns:ifrs="https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full" xmlns:nova="http://nova.example/2025" '
      'xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"')
ENTITA = f'<xbrli:entity><xbrli:identifier scheme="http://standards.iso.org/iso/17442">{LEI}</xbrli:identifier>'
DURATA_25 = "2025-01-01T00:00:00/2026-01-01T00:00:00"
DURATA_24 = "2024-01-01T00:00:00/2025-01-01T00:00:00"


def _contesto(cid, periodo, segmento=""):
    seg = f"<xbrli:segment>{segmento}</xbrli:segment>" if segmento else ""
    return f'<xbrli:context id="{cid}">{ENTITA}{seg}</xbrli:entity><xbrli:period>{periodo}</xbrli:period></xbrli:context>'


HEADER = (
    '<div style="display:none"><ix:header>'
    '<ix:hidden><ix:nonNumeric id="h1" name="ifrs:NameOfReportingEntityOrOtherMeansOfIdentification" contextRef="d25">Nova Example S.p.A.</ix:nonNumeric></ix:hidden>'
    '<ix:references><link:schemaRef xlink:type="simple" xlink:href="https://nova.example/nova-2025.xsd"/></ix:references>'
    '<ix:resources>'
    + _contesto("d25", "<xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate>")
    + _contesto("d24", "<xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate>")
    + _contesto("i25", "<xbrli:instant>2025-12-31</xbrli:instant>")
    + _contesto("d25seg", "<xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate>",
                '<xbrldi:explicitMember dimension="ifrs:SegmentsAxis">nova:RetailMember</xbrldi:explicitMember>'
                '<xbrldi:typedMember dimension="nova:ContractAxis"><nova:ContractDomain>C-42</nova:ContractDomain></xbrldi:typedMember>')
    + '<xbrli:unit id="eur"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit>'
    '<xbrli:unit id="eps"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unitNumerator>'
    '<xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>'
    '<xbrli:unit id="pure"><xbrli:measure>xbrli:pure</xbrli:measure></xbrli:unit>'
    '</ix:resources></ix:header></div>')


def _xhtml(corpo, header=HEADER, lingua="en"):
    return (f'<?xml version="1.0" encoding="utf-8"?><html {NS} xml:lang="{lingua}"><head><title>Nova</title></head>'
            f'<body>{header}{corpo}</body></html>').encode("utf-8")


def _fatti(corpo, **kw):
    return converti_xhtml(io.BytesIO(_xhtml(corpo, **kw)))["facts"]


def _nf(fid, concetto, testo, contesto="d25", unita="eur", **attr):
    extra = "".join(f' {k.replace("_", ":", 1) if k.startswith("xsi_") else k}="{v}"' for k, v in attr.items())
    return (f'<ix:nonFraction id="{fid}" name="{concetto}" contextRef="{contesto}" unitRef="{unita}"{extra}>'
            f'{testo}</ix:nonFraction>')


def test_periodi_con_fine_esclusiva_entita_e_lingua_ereditata():
    out = converti_xhtml(io.BytesIO(_xhtml(
        '<p>Revenue ' + _nf("r1", "ifrs:Revenue", "1,150.5", decimals="-5", scale="6", format="ixt:num-dot-decimal")
        + _nf("a1", "ifrs:Assets", "2.000", contesto="i25", decimals="INF", format="ixt:num-comma-decimal") + '</p>'
        '<div xml:lang="IT"><ix:nonNumeric id="t1" name="nova:Commento" contextRef="d25">Testo <b>italiano</b></ix:nonNumeric></div>')))
    f = out["facts"]
    assert f["r1"] == {"value": "1150500000", "decimals": -5, "dimensions": {
        "concept": "ifrs-full:Revenue", "entity": f"scheme:{LEI}", "period": DURATA_25, "unit": "iso4217:EUR"}}
    assert f["a1"] == {"value": "2000", "dimensions": {  # decimals INF: assente come in OIM
        "concept": "ifrs-full:Assets", "entity": f"scheme:{LEI}", "period": "2026-01-01T00:00:00", "unit": "iso4217:EUR"}}
    assert f["t1"]["value"] == "Testo italiano" and f["t1"]["dimensions"]["language"] == "it"
    assert f["h1"] == {"value": "Nova Example S.p.A.", "dimensions": {  # ix:hidden incluso, lingua della radice
        "concept": "ifrs-full:NameOfReportingEntityOrOtherMeansOfIdentification", "entity": f"scheme:{LEI}",
        "period": DURATA_25, "language": "en"}}
    info = out["documentInfo"]
    assert info["documentType"] == "https://xbrl.org/2021/xbrl-json"
    assert info["namespaces"]["scheme"] == "http://standards.iso.org/iso/17442"
    assert info["namespaces"]["ifrs-full"] == "https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full"
    assert info["taxonomy"] == ["https://nova.example/nova-2025.xsd"]
    assert out["conversione"]["fatti"] == 4 and out["conversione"]["limiti"] == []


def test_dimensioni_esplicite_e_tipizzate_e_unita():
    f = _fatti(_nf("s1", "ifrs:Revenue", "10", contesto="d25seg", decimals="0")
               + _nf("e1", "ifrs:BasicEarningsPerShare", "0,25", unita="eps", decimals="2", format="ixt:numcommadecimal")
               + _nf("p1", "nova:Ratio", "12.5", unita="pure", decimals="1", scale="-2"))
    assert f["s1"]["dimensions"] == {"concept": "ifrs-full:Revenue", "entity": f"scheme:{LEI}", "period": DURATA_25,
                                     "unit": "iso4217:EUR", "ifrs-full:SegmentsAxis": "nova:RetailMember",
                                     "nova:ContractAxis": "C-42"}
    assert f["e1"]["value"] == "0.25" and f["e1"]["dimensions"]["unit"] == "iso4217:EUR/xbrli:shares"
    assert f["p1"]["value"] == "0.125" and "unit" not in f["p1"]["dimensions"]  # xbrli:pure = unita' assente


def test_scala_segno_formati_nil_e_annidati():
    f = _fatti(_nf("n1", "ifrs:ProfitLoss", "(1,234)", decimals="-6", scale="6", sign="-", format="ixt:num-dot-decimal")
               + _nf("z1", "nova:Redeemable", "-", decimals="-6", scale="6", format="ixt:fixed-zero")
               + _nf("z2", "nova:Other", "—", decimals="0", format="ixt:zerodash")
               + _nf("c1", "ifrs:Inventories", "1.234.567,8", contesto="i25", decimals="1", format="ixt:num-comma-decimal")
               + '<ix:nonFraction id="nil1" name="ifrs:Goodwill" contextRef="i25" unitRef="eur" xsi:nil="true"/>'
               + _nf("o1", "ifrs:Equity", _nf("i1", "nova:EquityInner", "7", contesto="i25", decimals="0"),
                     contesto="i25", decimals="0"))
    assert f["n1"]["value"] == "-1234000000"
    assert f["z1"]["value"] == "0" and f["z2"]["value"] == "0"
    assert f["c1"]["value"] == "1234567.8"
    assert f["nil1"]["value"] is None and "decimals" not in f["nil1"]
    assert f["o1"]["value"] == "7" and f["i1"]["value"] == "7"


def test_escape_continuation_exclude_e_ordine():
    corpo = ('<div><ix:continuation id="k0" continuedAt="k1"><p>Seconda parte.</p></ix:continuation>'
             '<ix:nonNumeric id="tb" name="ifrs:DisclosureOfFinancialRiskManagementExplanatory" contextRef="d25" '
             'escape="true" continuedAt="k0"><p class="x">Rischio di <b>credito</b> &amp; tassi.'
             '<ix:exclude><span>Pagina 12</span></ix:exclude></p></ix:nonNumeric>'
             '<p>Fuori.</p><ix:continuation id="k1"><p>Terza<br/>parte.</p></ix:continuation>'
             '<ix:nonNumeric id="pl" name="nova:Nota" contextRef="d25" continuedAt="k2">Uno <i>due</i>'
             '<ix:exclude>NO</ix:exclude> tre</ix:nonNumeric><ix:continuation id="k2"> quattro</ix:continuation></div>')
    f = _fatti(corpo)
    assert f["tb"]["value"] == ('<p class="x">Rischio di <b>credito</b> &amp; tassi.</p>'
                                "<p>Seconda parte.</p><p>Terza<br/>parte.</p>")
    assert f["pl"]["value"] == "Uno due tre quattro"
    assert filing_esef.testo_html(f["tb"]["value"]) == "Rischio di credito & tassi.\nSeconda parte.\nTerza\nparte."


def test_id_mancanti_e_duplicati_stabili_e_limiti_dichiarati():
    corpo = ('<ix:nonNumeric name="nova:A" contextRef="d25">a</ix:nonNumeric>'
             '<ix:nonNumeric id="dup" name="nova:B" contextRef="d25">b</ix:nonNumeric>'
             '<ix:nonNumeric id="dup" name="nova:C" contextRef="d25">c</ix:nonNumeric>'
             '<ix:fraction name="nova:F" contextRef="d25" unitRef="eur"><ix:numerator>1</ix:numerator>'
             '<ix:denominator>2</ix:denominator></ix:fraction>'
             '<ix:nonNumeric id="x" name="nova:D" contextRef="manca">d</ix:nonNumeric>')
    out = converti_xhtml(io.BytesIO(_xhtml(corpo)))
    assert {k: v["value"] for k, v in out["facts"].items()} == {"h1": "Nova Example S.p.A.", "ixf-2": "a", "dup": "b", "dup-2": "c"}
    assert out == converti_xhtml(io.BytesIO(_xhtml(corpo)))  # stessi id a ogni conversione
    assert out["conversione"]["limiti"] == ["fatto con contextRef mancante (scartato): 1", "ix:fraction non convertiti: 1"]


def test_xhtml_malformato_rifiutato():
    with pytest.raises(PacchettoNonValido, match="non ben formato"):
        converti_xhtml(io.BytesIO(b"<html><body><p></body></html>"))


# -- pacchetti --

def _pacchetto(tmp_path, membri, nome="nova.xbri", metodo=zipfile.ZIP_DEFLATED):
    p = tmp_path / nome
    with zipfile.ZipFile(p, "w", metodo) as zf:
        for n, dati in membri.items():
            zf.writestr(n, dati)
    return p


def test_pacchetto_sceglie_il_report_piu_grande_e_lo_dichiara(tmp_path):
    grande = _xhtml(_nf("r1", "ifrs:Revenue", "5", decimals="0") + "<p>" + "x" * 500 + "</p>")
    p = _pacchetto(tmp_path, {"nova-2025/META-INF/reportPackage.json": "{}",
                              "nova-2025/reports/nova-2025-en.xhtml": grande,
                              "nova-2025/reports/indice.html": _xhtml(""),
                              "nova-2025/nova.example/nova-2025.xsd": "<schema/>"}, nome="nova.zip")
    out = converti_pacchetto(p)
    assert out["conversione"]["report"] == "nova-2025/reports/nova-2025-en.xhtml"
    assert out["facts"]["r1"]["value"] == "5"
    assert "altri report nel pacchetto ignorati: 1" in out["conversione"]["limiti"]


@pytest.mark.parametrize("membro", ["../evil/reports/r.xhtml", "/etc/reports/r.xhtml", "nova/reports/../../r.xhtml",
                                    "C:/nova/reports/r.xhtml"])
def test_pacchetto_con_percorsi_non_sicuri_rifiutato(tmp_path, membro):
    p = _pacchetto(tmp_path, {"nova/reports/r.xhtml": _xhtml(""), membro: "x"})
    with pytest.raises(PacchettoNonValido, match="percorso non sicuro"):
        converti_pacchetto(p)


def test_pacchetto_senza_reports_rifiutato(tmp_path):
    p = _pacchetto(tmp_path, {"nova/altro/r.xhtml": _xhtml("")})
    with pytest.raises(PacchettoNonValido, match="sotto reports/"):
        converti_pacchetto(p)


def test_zip_bomb_e_report_troppo_grande_rifiutati(tmp_path):
    bomba = _xhtml("<p>" + " " * 2_000_000 + "</p>")
    p = _pacchetto(tmp_path, {"nova/reports/r.xhtml": bomba})
    with pytest.raises(PacchettoNonValido, match="zip bomb"):
        converti_pacchetto(p)
    p2 = _pacchetto(tmp_path, {"nova/reports/r.xhtml": bomba}, nome="piatto.xbri", metodo=zipfile.ZIP_STORED)
    with pytest.raises(PacchettoNonValido, match="oltre il limite"):
        converti_pacchetto(p2, max_xhtml_bytes=1_000_000)
    assert list(converti_pacchetto(p2)["facts"]) == ["h1"]


def test_flusso_oltre_il_limite_interrotto_anche_se_l_intestazione_mente():
    with pytest.raises(PacchettoNonValido, match="oltre 100 byte"):
        converti_xhtml(ixbrl_oim._Limitato(io.BytesIO(_xhtml("<p>" + "y" * 1000 + "</p>")), 100))


def test_zip_non_valido_rifiutato(tmp_path):
    p = tmp_path / "rotto.xbri"
    p.write_bytes(b"non e' uno zip")
    with pytest.raises(PacchettoNonValido, match="zip non valido"):
        converti_pacchetto(p)


# -- equivalenza con lo xBRL-JSON del repository per i consumatori di filing_esef --

RISCHI = "<div><p>Management of financial risks.</p><p>The Group is exposed to credit risk.</p></div>"
LITI = "<p>The tax claim was settled in March.</p>"


def _repository_json():
    """Gli stessi fatti nella forma servita da filings.xbrl.org (vedi filing_esef_sintetici)."""
    ent = f"scheme:{LEI}"

    def num(valore, concetto, periodo, decimali=-6):
        return {"value": valore, "decimals": decimali,
                "dimensions": {"concept": concetto, "entity": ent, "period": periodo, "unit": "iso4217:EUR"}}
    return {"documentInfo": {"documentType": "https://xbrl.org/2021/xbrl-json"}, "facts": {
        "tb1": {"value": RISCHI, "dimensions": {"concept": "ifrs-full:DisclosureOfFinancialRiskManagementExplanatory",
                                                "entity": ent, "period": DURATA_25, "language": "en"}},
        "tb2": {"value": LITI, "dimensions": {"concept": "ifrs-full:DisclosureOfContingentLiabilitiesExplanatory",
                                              "entity": ent, "period": DURATA_25, "language": "en"}},
        "tb3": {"value": "<p>Prior year.</p>", "dimensions": {"concept": "ifrs-full:DisclosureOfGoingConcernExplanatory",
                                                              "entity": ent, "period": DURATA_24, "language": "en"}},
        "r25": num("1150000000", "ifrs-full:Revenue", DURATA_25),
        "r24": num("1000000000", "ifrs-full:Revenue", DURATA_24),
        "inv": num("-35000000", "ifrs-full:Inventories", "2026-01-01T00:00:00"),
        "seg": {"value": "10", "decimals": 0, "dimensions": {
            "concept": "ifrs-full:Revenue", "entity": ent, "period": DURATA_25, "unit": "iso4217:EUR",
            "ifrs-full:SegmentsAxis": "nova:RetailMember", "nova:ContractAxis": "C-42"}}}}


def test_filing_esef_legge_la_conversione_come_il_repository(tmp_path):
    corpo = ('<ix:nonNumeric id="tb1" name="ifrs:DisclosureOfFinancialRiskManagementExplanatory" contextRef="d25" '
             'escape="true" continuedAt="tbc">' + RISCHI[:-len("<p>The Group is exposed to credit risk.</p></div>")]
             + '</div></ix:nonNumeric>'
             '<ix:nonNumeric id="tb2" name="ifrs:DisclosureOfContingentLiabilitiesExplanatory" contextRef="d25" escape="true">'
             + LITI + '</ix:nonNumeric>'
             '<ix:continuation id="tbc"><p>The Group is exposed to credit risk.</p></ix:continuation>'
             '<ix:nonNumeric id="tb3" name="ifrs:DisclosureOfGoingConcernExplanatory" contextRef="d24" escape="true">'
             '<p>Prior year.</p></ix:nonNumeric>'
             + _nf("r25", "ifrs:Revenue", "1,150", decimals="-6", scale="6", format="ixt:num-dot-decimal")
             + _nf("r24", "ifrs:Revenue", "1,000", contesto="d24", decimals="-6", scale="6", format="ixt:num-dot-decimal")
             + _nf("inv", "ifrs:Inventories", "35", contesto="i25", decimals="-6", scale="6", sign="-")
             + _nf("seg", "ifrs:Revenue", "10", contesto="d25seg", decimals="0"))
    p = _pacchetto(tmp_path, {"nova-2025/reports/nova-2025-en.xhtml": _xhtml(corpo)})
    convertito, atteso = converti_pacchetto(p), _repository_json()
    assert filing_esef.blocchi(convertito) == filing_esef.blocchi(atteso)
    assert filing_esef._fatti_numerici(convertito) == filing_esef._fatti_numerici(atteso)
    estratti = filing_esef.blocchi(convertito)
    assert estratti["periodo"] == ("2025-01-01", "2025-12-31") and estratti["entita"] == {LEI}
    assert [b["fatto"] for b in estratti["blocchi"]] == ["tb1", "tb2"]
    assert filing_esef._fatti_numerici(convertito)["Inventories"] == {
        "units": {"EUR": [{"end": "2025-12-31", "val": -35000000.0}]}}


def test_revisione_attributi_malformati_scartano_il_fatto_non_il_pacchetto():
    out = converti_xhtml(io.BytesIO(_xhtml(_nf("ok", "ifrs:Revenue", "10", decimals="0")
                                           + _nf("bad1", "ifrs:Revenue", "11", decimals="x")
                                           + _nf("bad2", "ifrs:Revenue", "12", scale="sei"))))
    assert set(out["facts"]) >= {"ok"} and not {"bad1", "bad2"} & set(out["facts"])
    assert "fatto numerico non leggibile (scartato): 2" in out["conversione"]["limiti"]
