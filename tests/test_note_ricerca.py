"""Note di ricerca / di broker riconosciute nel testo (revisione R-SITI2, E7) — Opus 5.5.

Societa' sintetiche Zz*, niente rete, niente AI. La negazione annulla SOLO i disclaimer chiusi.
"""
import pytest

from bellomberg.market_data.note_ricerca import motivo_nota_ricerca

# E7: le quattro note con negazione «finta» devono restare note (oggi passavano)
NOTE_E7 = [
    ("Zztest AG\nWir bestaetigen nicht nur unsere Kaufempfehlung, sondern erhoehen auch das Kursziel auf 45 EUR.\n",
     "Kaufempfehlung"),
    ("Zztest SA\nNous ne modifions pas notre recommandation : Achat, avec un objectif de cours de 32 EUR.\n",
     "recommandation : Achat"),
    ("Zzsub S.p.A.\nConfermiamo non solo il giudizio: ACQUISTARE ma anche il prezzo obiettivo a 4,20 euro.\n",
     "giudizio: ACQUISTARE"),
    ("Zztest Ltd\nNo change to our rating: BUY and price target of 12 USD after strong results.\n",
     "rating: BUY"),
]


@pytest.mark.parametrize("testo,parola", NOTE_E7)
def test_E7_negazione_finta_resta_nota(testo, parola):
    motivo = motivo_nota_ricerca(testo)
    assert motivo == f"nota di ricerca o di un broker («{parola}»): non e' un documento dell'emittente"


# Altre note, una per lingua/segno del lessico
ALTRE_NOTE = [
    "Zzalfa Inc.\nInitiation of Coverage\nWe initiate with Overweight.\n",
    "Zzalfa Inc.\nAnalyst: Jane Zzdoe\nQ2 preview\n",
    "Zzalfa Inc.\nReiterate BUY rating after the quarter.\n",
    "Zzbeta S.p.A.\nRaccomandazione: NEUTRALE\nTarget price 3,10 euro\n",
    "Zzbeta S.p.A.\nUfficio studi\nRicerca azionaria - aggiornamento\n",
    "Zzgamma AG\nEmpfehlung: Halten\nDie Aktie notiert bei 10 EUR.\n",
    "Zzgamma AG\nAnalyst: Max Zzmuster\nUpdate nach Zahlen\n",
    "Zzdelta SA\nRecommandation: Achat\n",
    "Zzeps SA\nRecomendación: COMPRAR\nPrecio objetivo 5,2 EUR\n",
    "Zzeps SA\nMantenemos nuestro precio objetivo pese a no cambiar la recomendación de compra.\n",
    "Zzrice SIM S.p.A.\nZzbeta S.p.A. - Aggiornamento risultati 1H\n",
    "Zzalfa Inc.\nThis does not constitute investment advice, but our rating is BUY.\n",
]


@pytest.mark.parametrize("testo", ALTRE_NOTE)
def test_note_in_cinque_lingue(testo):
    assert motivo_nota_ricerca(testo) is not None


# Disclaimer MAR / offerta veri di emittenti: NON sono note
DISCLAIMER_EMITTENTI = [
    "Zzbeta S.p.A.\nComunicato stampa\nIl presente comunicato non costituisce un'offerta al pubblico di prodotti "
    "finanziari né una sollecitazione all'investimento, né una raccomandazione di investimento ai sensi del "
    "Regolamento (UE) n. 596/2014.\n",
    "Zzalfa plc\nThis announcement does not constitute an offer to sell or the solicitation of an offer to buy any "
    "securities, nor does it constitute investment advice or an investment recommendation within the meaning of "
    "Regulation (EU) No 596/2014.\n",
    "Zzalfa plc\nThis presentation is not intended as an investment recommendation and should not be construed as "
    "investment advice.\n",
    "Zzgamma AG\nDiese Mitteilung stellt weder ein Angebot zum Kauf noch eine Aufforderung zur Abgabe eines "
    "Angebots dar und ist keine Anlageempfehlung im Sinne der Marktmissbrauchsverordnung.\n",
    "Zzgamma AG\nDiese Veroeffentlichung stellt keine Anlageempfehlung oder Kaufempfehlung dar.\n",
    "Zzdelta SA\nLe présent communiqué ne constitue pas une offre de vente ni la sollicitation d'une offre d'achat, "
    "ni une recommandation d'investissement.\n",
    "Zzeps SA\nEste documento no constituye una oferta de venta ni una recomendación de inversión.\n",
]


@pytest.mark.parametrize("testo", DISCLAIMER_EMITTENTI)
def test_disclaimer_mar_emittente_non_e_nota(testo):
    assert motivo_nota_ricerca(testo) is None


# Bilanci dell'emittente: «Operating» non e' «rating»; un rating CREDITIZIO citato non e' una nota
BILANCI = [
    "Zzalfa Inc.\nSegment Operating - Neutral FX impact in the quarter.\n",
    "Zzalfa Inc.\nOperating income rose to 120.5 million; operating margin 14%.\n",
    "Zzalfa Inc.\nIn May S&P confirmed the credit rating of the Company at BBB with a stable outlook.\n",
    "Zzalfa Inc.\nMoody's upgraded the Company's long-term rating to Baa1; outlook positive.\n",
    "Zzalfa Inc.\nOur credit rating: neutral outlook was confirmed by Fitch.\n",
    "Zzalfa Inc.\nThe ESG rating: Neutral was assigned by MSCI.\n",
    "Zzalfa Inc.\nThe conference call with analysts and investors will be held at 3 pm.\n",
    ("Zzbeta S.p.A.\nRelazione finanziaria semestrale al 30 giugno 2026\nSignori Azionisti, nel primo semestre i ricavi "
     "consolidati sono cresciuti a 45,3 milioni di euro. La società di revisione ha emesso la relazione di revisione "
     "contabile limitata; il giudizio della società di revisione è senza rilievi. Il Consiglio ha recepito le "
     "raccomandazioni del Comitato per la Corporate Governance. Zzrice SIM S.p.A. opera in qualità di specialist.\n"),
]


@pytest.mark.parametrize("testo", BILANCI)
def test_documento_emittente_non_e_nota(testo):
    assert motivo_nota_ricerca(testo) is None


def test_vuoto_e_none():
    assert motivo_nota_ricerca("") is None
    assert motivo_nota_ricerca(None) is None


def test_sim_lontana_dall_intestazione_non_e_autore():
    testo = "Zzbeta S.p.A.\n" + "Ricavi in crescita. " * 30 + "Zzrice SIM S.p.A. è il Nomad.\n"
    assert motivo_nota_ricerca(testo) is None
