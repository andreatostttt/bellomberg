"""Costruttori di xBRL-JSON e indici ESEF SINTETICI (fase B).

Forma osservata su filings.xbrl.org (sonda 2026-10-03, documenti reali solo nello
scratchpad): `documentInfo` + `facts`; ogni fatto ha `value` e `dimensions` con
concept, entity (`scheme:<LEI>`), period (`AAAA-01-01T00:00:00/AAAA+1-01-01T00:00:00`
per le durate, istante a mezzanotte del giorno dopo la chiusura), language per i
testi, unit per i numeri. Text block in XHTML con `<span>` vuoti dentro le parole
(convertitori PDF). Nomi e LEI inventati.
"""
import json

LEI_NOVA = "999900NOVA0000000001"
LEI_KORE = "999900KORE0000000001"


def periodo(anno):
    return f"{anno}-01-01T00:00:00/{anno + 1}-01-01T00:00:00"


def istante(anno):
    return f"{anno + 1}-01-01T00:00:00"


def xbrl_json(anno, *, lei=LEI_NOVA, lingua="en", blocchi=None, numeri=None, numeri_prec=None,
              entita=None, extra=None):
    """bytes di uno xBRL-JSON annuale. `blocchi`: {concetto_locale: xhtml};
    `numeri`/`numeri_prec`: {concetto_locale: valore} (EUR) dell'anno e del comparativo."""
    facts, n = {}, 0

    def fatto(value, **dims):
        nonlocal n
        n += 1
        facts[f"f{n}"] = {"value": value, "dimensions": {"entity": f"scheme:{entita or lei}", **dims}}

    for concetto, html in (blocchi or {}).items():
        fatto(html, concept=f"ifrs-full:{concetto}", period=periodo(anno), language=lingua)
    for anno_n, valori in ((anno, numeri or {}), (anno - 1, numeri_prec or {})):
        for concetto, valore in valori.items():
            istantaneo = concetto in ("Inventories", "NoncurrentBorrowings")
            fatto(str(valore), concept=f"ifrs-full:{concetto}", unit="iso4217:EUR",
                  period=istante(anno_n) if istantaneo else periodo(anno_n))
    # rumore realistico: un fatto dimensionale, un text block di estensione, un comparativo testuale
    fatto("<p>Breakdown by segment.</p>", concept="ifrs-full:DisclosureOfLeasesExplanatory",
          period=periodo(anno), language=lingua, **{"ifrs-full:SegmentsAxis": "nova:RetailMember"})
    fatto("<p>Company-specific note.</p>", concept="nova:DisclosureOfSpecialItemsExplanatory",
          period=periodo(anno), language=lingua)
    fatto("<p>Prior year narrative.</p>", concept="ifrs-full:DisclosureOfGoingConcernExplanatory",
          period=periodo(anno - 1), language=lingua)
    for chiave, valore in (extra or {}).items():
        facts[chiave] = valore
    return json.dumps({"documentInfo": {"documentType": "https://xbrl.org/2021/xbrl-json",
                                        "namespaces": {"ifrs-full": "https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full",
                                                       "scheme": "http://standards.iso.org/iso/17442"}},
                       "facts": facts}).encode("utf-8")


RISCHI_2024 = ("<div><p>3. Management of financial risks</p><p>The Group is exposed to interest "
               "rate risk and credit risk.</p><p>Liquidity is monitored daily by trea<span class=\"_ _3\"></span>sury.</p></div>")
RISCHI_2025 = ("<div><p>3. Management of financial risks</p><p>The Group is exposed to interest "
               "rate risk, credit risk and a new exposure to commodity prices.</p>"
               "<p>Liquidity is monitored daily by trea<span class=\"_ _3\"></span>sury.</p>"
               "<p>A covenant breach would trigger early repayment of the term loan.</p></div>")
CONTENZIOSI_2024 = "<p>The Group is defendant in a tax claim of EUR 12 million.</p>"
CONTENZIOSI_2025 = "<p>The tax claim was settled in March.</p>"
POLITICHE = "<p>Revenue is recognised when control transfers to the customer.</p>"


def blocchi_nova(anno):
    if anno >= 2025:
        return {"DisclosureOfFinancialRiskManagementExplanatory": RISCHI_2025,
                "DisclosureOfContingentLiabilitiesExplanatory": CONTENZIOSI_2025,
                "DescriptionOfAccountingPolicyForRevenueExplanatory": POLITICHE,
                "DisclosureOfTreasurySharesExplanatory": "<p>The Company bought back 1% of its shares.</p>"}
    return {"DisclosureOfFinancialRiskManagementExplanatory": RISCHI_2024,
            "DisclosureOfContingentLiabilitiesExplanatory": CONTENZIOSI_2024,
            "DescriptionOfAccountingPolicyForRevenueExplanatory": POLITICHE}


def numeri_nova(anno):
    base = {2023: 900.0, 2024: 1000.0, 2025: 1150.0}[anno]
    return {"Revenue": base * 1e6, "ProfitLossFromOperatingActivities": base * 0.1e6,
            "ProfitLossAttributableToOwnersOfParent": base * 0.07e6}


def json_nova(anno, **kw):
    kw.setdefault("blocchi", blocchi_nova(anno))
    kw.setdefault("numeri", numeri_nova(anno))
    kw.setdefault("numeri_prec", numeri_nova(anno - 1) if anno - 1 in (2023, 2024, 2025) else None)
    return xbrl_json(anno, **kw)


def riga_indice(fid, anno, *, lei=LEI_NOVA, lingua="en", json=True, aggiunto=None, suffisso=True):
    """Riga del catalogo come la produce esef._list_filings."""
    nome = f"{lei}-{anno}-12-31" + (f"-{lingua}" if suffisso else "")
    base = f"https://filings.xbrl.org/{lei}/{anno}-12-31/ESEF/IT/0/{nome}"
    riga = {"id": str(fid), "period_end": f"{anno}-12-31", "date_added": aggiunto or f"{anno + 1}-04-0{fid % 9 + 1} 10:00:00",
            "emittente_id": f"LEI:{lei}", "fonte": "filings.xbrl.org (repository non esaustivo)",
            "language": None, "country": "IT", "report_url": f"{base}/reports/{nome}.xhtml", "package_url": f"{base}.zip"}
    if json:
        riga["json_url"] = f"{base}/{nome}.json"
    else:
        riga["no_json"] = True
    return riga
