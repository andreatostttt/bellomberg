"""Documenti SEC SINTETICI per i test filing.

La struttura riproduce quella osservata il 2026-10-03 su 10-K, 10-Q, 20-F e 6-K
reali (righe estratte da `estrai_testo`); contenuti, nomi e identificativi sono
inventati. Due stili di indice osservati:
- "uguale": la voce d'indice e' identica all'intestazione del corpo
  (serve `sezioni_salta_indice`);
- "spezzato": numero dell'Item e titolo su righe separate.
"""
CIK_NOVA = "0009990001"
CIK_KORE = "0009990011"


def _contesto(cik, inizio, fine, ident="c1"):
    return (f'<xbrli:context id="{ident}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">'
            f'{cik}</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>{inizio}</xbrli:startDate>'
            f'<xbrli:endDate>{fine}</xbrli:endDate></xbrli:period></xbrli:context>')


def _html(form, contesti, corpo, nome):
    return (f'<html lang="en"><body><div style="display:none"><ix:header><ix:hidden>'
            f'<ix:nonNumeric name="dei:DocumentType" contextRef="c1">{form}</ix:nonNumeric>'
            f'</ix:hidden><ix:resources>{contesti}</ix:resources></ix:header></div>'
            f'<p>{nome}</p>{corpo}</body></html>').encode()


def _righe(*righe):
    return "".join(f"<p>{r}</p>" for r in righe)


def _indice(voci, stile):
    if not stile:
        return ""
    if stile == "spezzato":
        celle = "".join(f"<tr><td>{v.split(' ', 2)[0]} {v.split(' ', 2)[1]}</td></tr><tr><td>{v.split(' ', 2)[2]}</td></tr>"
                        if v.lower().startswith("item ") else f"<tr><td>{v}</td></tr>" for v in voci)
        return f"<table>{celle}</table>"
    return "<table>" + "".join(f"<tr><td>{v}</td></tr>" for v in voci) + "</table>"


def documento_10k(anno, *, cik=CIK_NOVA, nome="Nova Semiconductors Inc.", rischio="Demand may weaken.",
                  indice="uguale", gestione=None, periodo=None):
    """`periodo` (inizio, fine) ISO: esercizio non solare (es. chiuso a giugno)."""
    voci = ("Item 1A. Risk Factors", "Item 1B. Unresolved Staff Comments", "Item 3. Legal Proceedings",
            "Item 4. Mine Safety Disclosures",
            "Item 7. Management’s Discussion and Analysis of Financial Condition and Results of Operations",
            "Item 7A. Quantitative and Qualitative Disclosures About Market Risk",
            "Item 8. Financial Statements and Supplementary Data")
    corpo = _indice(voci, indice) + _righe(
        "ANNUAL REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES EXCHANGE ACT OF 1934",
        "Consolidated financial statements are included in Item 8.",
        *(v.upper() for v in voci[:1]), rischio, voci[1].upper(), "None.", voci[2].upper(),
        "We are party to ordinary litigation.", voci[3].upper(), "Not applicable.", voci[4].upper(),
        gestione or f"Revenue grew in fiscal {anno}.", voci[5].upper(), "Interest rate risk is limited.",
        voci[6].upper(), "See the consolidated statements.")
    return _html("10-K", _contesto(cik, *(periodo or (f"{anno}-01-01", f"{anno}-12-31"))), corpo, nome)


def documento_10q(anno, trimestre, *, cik=CIK_NOVA, nome="Nova Semiconductors Inc.", rischio="Demand may weaken.",
                  indice="uguale", gestione=None):
    # trimestre 4: trimestre solare ott-dic (10-Q di un esercizio chiuso a giugno)
    fine = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[trimestre]
    inizio = {1: "01-01", 2: "04-01", 3: "07-01", 4: "10-01"}[trimestre]
    voci = ("Item 2. Management’s Discussion and Analysis of Financial Condition and Results of Operations",
            "Item 3. Quantitative and Qualitative Disclosures About Market Risk",
            "Item 4. Controls and Procedures", "Item 1. Legal Proceedings", "Item 1A. Risk Factors",
            "Item 2. Unregistered Sales of Equity Securities and Use of Proceeds")
    corpo = _indice(voci, indice) + _righe(
        "QUARTERLY REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES EXCHANGE ACT OF 1934",
        "Condensed consolidated financial statements (unaudited).",
        "PART I — FINANCIAL INFORMATION", voci[0], gestione or f"Revenue in the quarter of {anno} increased.",
        voci[1], "Market risk unchanged.", voci[2], "Controls are effective.",
        "PART II — OTHER INFORMATION", voci[3], "No material proceedings.", voci[4], rischio, voci[5], "None.")
    return _html("10-Q", _contesto(cik, f"{anno}-{inizio}", f"{anno}-{fine}"), corpo, nome)


def documento_20f(anno, *, cik=CIK_KORE, nome="Kore Mining plc", rischio="Commodity prices may fall.",
                  indice="spezzato", gestione=None):
    voci = ("Item 3. Key Information", "Item 4. Information on the Company",
            "Item 5. Operating and Financial Review and Prospects", "Item 6. Directors, Senior Management and Employees")
    corpo = _indice(voci, indice) + _righe(
        "ANNUAL REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES EXCHANGE ACT OF 1934",
        "The consolidated financial statements are prepared under IFRS.",
        "Item 3.    Key Information", "RISK FACTORS", rischio, "Item 4.    Information on the Company",
        "We mine copper.", "Item 5.    Operating and Financial Review and Prospects",
        gestione or f"Results for {anno} reflect lower prices.", "Item 6.Directors, Senior Management and Employees",
        "Board composition.")
    return _html("20-F", _contesto(cik, f"{anno}-01-01", f"{anno}-12-31"), corpo, nome)


def allegato_6k(anno, *, nome="Kore Mining plc", mesi="six", fine=None, testo="Outlook is unchanged.", extra=""):
    """Comunicato/relazione infrannuale: periodo corrente e comparativo dell'anno prima.
    mesi="three" produce la forma STM «three and six months ended June 27, 2026»."""
    fine = fine or (f"30 June {anno}" if mesi == "six" else f"June 27, {anno}")
    prima = fine.replace(str(anno), str(anno - 1))
    frase = "three and six months" if mesi == "three" else f"{mesi} months"
    return (f'<html lang="en"><body><p>{nome}</p><p>Interim report for the {frase} ended {fine}</p>'
            f'<p>Unaudited condensed consolidated financial statements.</p>'
            f'<p>Revenue for the {frase} ended {fine} compared with the {frase} ended {prima}.</p>'
            f'<p>Outlook</p><p>{testo}</p>{extra}<p>Forward-looking statements</p><p>Boilerplate.</p></body></html>').encode()


def indice_filing_6k(documenti, *, cik=CIK_KORE, accession="0009990011-26-000001"):
    """documenti: [(nome_file, tipo, descrizione, ixbrl: bool)] come nella tabella '-index.htm' della SEC."""
    cartella = f"/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
    righe = "".join(f'<tr><td>{i}</td><td>{d}</td><td><a href="{cartella}{f}">{f}</a>{" &nbsp;&nbsp;iXBRL" if ix else ""}</td>'
                    f'<td>{t}</td><td>1000</td></tr>' for i, (f, t, d, ix) in enumerate(documenti, 1))
    return (f'<html><body><table class="tableFile" summary="Document Format Files">'
            f'<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>{righe}'
            f'<tr><td>&nbsp;</td><td>Complete submission text file</td><td><a href="{cartella}{accession}.txt">{accession}.txt</a></td>'
            f'<td>&nbsp;</td><td>2000</td></tr></table></body></html>').encode()


def submissions(cik, nome, righe):
    """righe: [(form, filingDate, accession, primaryDocument, reportDate)]."""
    cols = ("form", "filingDate", "accessionNumber", "primaryDocument", "reportDate")
    recent = {c: [r[i] for r in righe] for i, c in enumerate(cols)}
    recent["items"] = ["" for _ in righe]
    return {"cik": cik, "name": nome, "filings": {"recent": recent, "files": []}}
