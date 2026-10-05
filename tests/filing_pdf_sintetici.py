"""PDF sintetici per la fase C (proposta AI da PDF IR): solo nomi inventati (Nova/Kore/Acme).

`pdf(pagine)` scrive un PDF vero con reportlab, una riga per `drawString`: pypdf la rilegge come una
riga del testo estratto, come succede nei PDF IR reali. `semestrale_nova(anno)` riproduce i casi della
sonda sui documenti reali: indice con numeri di pagina in coda, indice senza puntini (numero sulla riga
dopo), pagina divisoria di capitolo, intestazione corrente ripetuta in testa a ogni pagina, righe di tabella.
"""
from io import BytesIO


def pdf(pagine):
    from reportlab.pdfgen import canvas
    buffer = BytesIO()
    c = canvas.Canvas(buffer)
    for righe in pagine:
        for i, riga in enumerate(righe):
            c.drawString(40, 800 - i * 16, riga)
        c.showPage()
    c.save()
    return buffer.getvalue()


PROSA_OUTLOOK = ("We expect revenue to rise moderately in the {anno} fiscal year.",
                 "The Segment Result Margin should reach around 20 percent.",
                 "Free cash flow is expected to improve compared with the prior year.")
PROSA_RISCHI = ("Demand for power semiconductors remains volatile across end markets.",
                "Export restrictions could limit sales to certain customers.",
                "We monitor currency risks and hedge the main exposures.")


def semestrale_nova(anno=2026, *, indice_con_numeri_a_capo=False, divisoria=True, prosa_outlook=None,
                    emittente="Nova AG", titolo_rischi="Risks and opportunities"):
    """Relazione semestrale di Nova AG chiusa al 31 marzo `anno` (comparativi dell'anno prima)."""
    testata = f"{emittente} Half-Year Financial Report 31 March {anno}"
    capitolo = "Interim Group Management Report"
    if indice_con_numeri_a_capo:
        indice = ["Contents", capitolo, "3", f"Outlook for the {anno} fiscal year", "5",
                  "Risks and opportunities", "6", "Notes to the Financial Statements", "7"]
    else:
        indice = ["Contents", f"{capitolo} 3", f"Outlook for the {anno} fiscal year 5",
                  "Risks and opportunities 6", "Notes to the Financial Statements 7"]
    outlook = prosa_outlook or [r.format(anno=anno) for r in PROSA_OUTLOOK]
    pagine = [
        [emittente, "Half-Year Financial Report", f"31 March {anno}"],
        [f"{testata} 2"] + indice,
        [f"{testata} 3", capitolo, "Review of results of operations",
         f"Revenue increased in the six months ended 31 March {anno}.",
         "Revenue 7,475 7,014 7", "Operating profit 846 637 33",
         f"The consolidated financial statements of the Group cover the first half of {anno}."],
    ]
    if divisoria:
        pagine.append([f"{testata} 4", capitolo, f"OUTLOOK FOR THE {anno} FISCAL YEAR",
                       "RISKS AND OPPORTUNITIES", "NOTES TO THE FINANCIAL STATEMENTS"])
    else:
        pagine.append([f"{testata} 4", capitolo, "Employees", "The number of employees was stable."])
    pagine += [
        [f"{testata} 5", capitolo, f"Outlook for the {anno} fiscal year", *outlook],
        [f"{testata} 6", capitolo, titolo_rischi, *PROSA_RISCHI],
        [f"{testata} 7", "Notes to the Financial Statements",
         f"Condensed consolidated interim financial statements for the six months ended 31 March {anno}.",
         "The accounting policies are unchanged.", "Responsibility Statement"],
    ]
    return pdf(pagine)


def semestrale_kore_it(anno=2026):
    """Relazione semestrale in italiano di Kore S.p.A. (indice con puntini, maiuscole)."""
    testata = f"RELAZIONE FINANZIARIA SEMESTRALE AL 30 GIUGNO {anno}"
    return pdf([
        [testata, "1"],
        [testata, "2", "INDICE", "RISULTATI DEL GRUPPO ........................ 3",
         "EVOLUZIONE PREVEDIBILE DELLA GESTIONE ........................ 4",
         "OPERAZIONI CON PARTI CORRELATE ........................ 5"],
        [testata, "3", "RISULTATI DEL GRUPPO", f"Il primo semestre {anno} si e' chiuso con ricavi in crescita.",
         "Ricavi 10.003 8.919 12,2%", "Il bilancio consolidato del Gruppo Kore e' redatto secondo gli IFRS."],
        [testata, "4", "EVOLUZIONE PREVEDIBILE DELLA GESTIONE",
         "Per l'intero esercizio il Gruppo prevede ricavi in crescita.",
         "Il flusso di cassa operativo dovrebbe migliorare."],
        [testata, "5", "OPERAZIONI CON PARTI CORRELATE",
         "Le operazioni con parti correlate sono regolate a condizioni di mercato.",
         f"Periodo dal 1 gennaio {anno} al 30 giugno {anno}."],
    ])
