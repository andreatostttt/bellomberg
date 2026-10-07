"""Riconoscimento delle NOTE DI RICERCA / DI BROKER nel testo di un documento (copertina e prime pagine).

Revisione R-SITI2, punto E7 (Opus 5.5): la versione a regex dentro `filing_attivazione` ignorava la parola da
nota appena la frase conteneva una negazione qualsiasi, cosi' «Wir bestaetigen nicht nur unsere
Kaufempfehlung», «Nous ne modifions pas notre recommandation : Achat», «Confermiamo non solo il giudizio:
ACQUISTARE», «No change to our rating: BUY» passavano per documenti dell'emittente.

Regola di questo modulo:
- il LESSICO da nota (EN/IT/DE/FR/ES) si cerca a parole intere («Operating» non e' «rating»);
- l'unica cosa che lo annulla e' un DISCLAIMER di forma chiusa e dichiarata (`_DISCLAIMER`): verbo negato di
  "costituire / essere inteso come / dover essere considerato" + soli articoli/aggettivi della lista chiusa +
  un oggetto della lista chiusa (raccomandazione, consulenza, offerta, sollecitazione...). Il tratto
  annullato va dalla formula alla fine della frase, ma si ferma a una congiunzione avversativa («, but»,
  «, ma», «, aber», «, sondern», «, mais», «, pero»): «does not constitute advice, but our rating is BUY» resta
  una nota. Ogni altra negazione non annulla niente;
- «rating», «raccomandazione», «giudizio», «Empfehlung», «recommandation», «recomendacion» contano SOLO se
  seguiti da un giudizio azionario (Buy/Hold/Sell/Overweight/Acquistare/Kaufen/Achat/Comprar...). Un rating
  CREDITIZIO o ESG citato dall'emittente («credit rating of the Company by S&P», «rating BBB») NON e' una nota:
  ha un qualificatore creditizio/agenzia vicino oppure non ha un giudizio azionario dopo;
- «SIM S.p.A.» conta come AUTORE solo nelle prime `_TESTA_AUTORE` battute (intestazione): piu' avanti nel
  documento dell'emittente e' lo specialist / il Nomad / il global coordinator;
- «analyst» da solo NON conta (un comunicato dell'emittente ha la «conference call with analysts»): contano
  l'intestazione «Analyst: Nome», «research/equity analyst», «analyst certification».

Il chiamante passa il testo gia' tagliato (copertina / prime pagine): qui non si taglia nulla.
Nessuna rete, nessun AI, nessun default zitto: None vuol dire «nessun segno di nota nel testo dato».
"""
from __future__ import annotations

import re
import unicodedata

__all__ = ["motivo_nota_ricerca"]

# Intestazione in cui un «... SIM S.p.A.» e' l'autore del documento
_TESTA_AUTORE = 300

# Giudizi azionari (non creditizi): EN + IT + DE + FR + ES
_GIUDIZI = (r"(?:strong[\s-]+buy|buy|hold|sell|strong[\s-]+sell|outperform|underperform|market[\s-]+perform"
            r"|sector[\s-]+perform|overweight|underweight|equal[\s-]*weight|accumulate|reduce"
            r"|acquistare|acquisto|comprare|vendere|mantenere|accumulare|ridurre|neutrale|neutral"
            r"|kaufen|halten|verkaufen|(?:ü|ue|u)bergewichten|untergewichten|akkumulieren|reduzieren"
            r"|achat[\s-]+fort|achat|acheter|vente|vendre|conserver|neutre|renforcer|all[ée]ger"
            r"|surperformance|sous[\s-]*performance"
            r"|comprar|compra|mantener|vender|venta|sobreponderar|infraponderar|acumular)")
# Sostantivi di raccomandazione che diventano nota solo davanti a un giudizio azionario
_SOSTANTIVI = (r"(?:rating|recommendation|raccomandazione|giudizio|empfehlung|votum|anlageurteil|einstufung"
               r"|recommandation|opinion|recomendaci[oó]n)")
_PONTE = r"(?:to|at|of|is|remains|a|à|auf|su|di|de|en|ist|bleibt|resta|rimane|reste|se\s+mantiene|confirmed|reiterated)"
# Qualificatori di un rating creditizio/ESG: entro 40 battute prima del sostantivo -> non e' un giudizio azionario
_CREDITIZIO = (r"(?:credit|issuer|long[\s-]*term|short[\s-]*term|debt|bond|notes|corporate\s+family|investment[\s-]+grade"
               r"|s\s*&\s*p|standard\s*&\s*poor|moody|fitch|dbrs|scope|kroll|r\.?\s?&\s?i|esg|msci|sustainalytics|cdp"
               r"|creditizi\w*|emittente|agenzi\w*|bonit(?:ä|ae|a)t|kredit\w*|notation\s+financi\w*|cr[ée]dit"
               r"|crediticia|calificaci[oó]n)")

# Lessico forte: basta la presenza (fuori disclaimer) per dire nota
_LESSICO_FORTE = [
    r"price[\s-]+target", r"target[\s-]+price", r"prezzo[\s-]+obiettivo", r"target[\s-]+prezzo",
    r"kurs[\s-]*ziel\w*", r"kauf[\s-]*empfehlung\w*", r"verkaufs[\s-]*empfehlung\w*", r"halte[\s-]*empfehlung\w*",
    r"anlage[\s-]*empfehlung\w*", r"objectif[\s-]+de[\s-]+cours", r"precio[\s-]+objetivo", r"precio[\s-]+obje?tivo",
    r"initiat\w*\s+(?:of\s+)?coverage", r"initiation\s+de\s+couverture", r"inicio\s+de\s+cobertura",
    r"avvio\s+(?:della\s+)?copertura", r"coverage[\s-]*aufnahme",
    r"equity[\s-]+research", r"research[\s-]+(?:note|report|update)", r"ricerca[\s-]+azionaria",
    r"analyst[\s-]+certification", r"(?:research|equity|sell[\s-]*side)\s+analysts?",
    r"investment[\s-]+recommendation", r"raccomandazion\w*\s+di\s+investimento",
    r"recommandation\s+d['’]investissement", r"recomendaci[oó]n\s+de\s+inversi[oó]n",
]
# «Analyst: Mario Rossi» (nome con iniziale maiuscola, case-sensitive)
_ANALISTA = (r"(?:analysts?|analysten|analystin|analista|analisti|analistas|analystes?)"
             r"\s*:(?=\s*(?-i:[A-ZÀ-Ý]))")
_AUTORE_SIM = r"(?-i:SIM)\s+S\.?\s?p\.?\s?A\b"

_PAROLE_RX = re.compile(
    r"\b(?:" + "|".join(_LESSICO_FORTE) + r")\b"
    r"|\b" + _ANALISTA +
    r"|\b" + _SOSTANTIVI + r"(?:\s*[:\-–]\s*|\s+(?:" + _PONTE + r"\s+){0,2})" + r"(?:our\s+|unser\w*\s+|notre\s+)?"
    + _GIUDIZI + r"\b"
    r"|\b" + _GIUDIZI + r"[\s-]+(?:rating|recommendation|empfehlung|votum|einstufung)\b",
    re.I)
_SOSTANTIVO_RX = re.compile(r"\b" + _SOSTANTIVI + r"\b", re.I)
_CREDITIZIO_RX = re.compile(r"\b" + _CREDITIZIO, re.I)
_AUTORE_RX = re.compile(r"\b" + _AUTORE_SIM, re.I)

# --- Disclaimer chiusi (MAR / offerta) -------------------------------------------------------------
_FORMULE = (
    # EN
    r"do(?:es)?\s+not\s+constitute|shall\s+not\s+constitute|nor\s+does\s+(?:it|this\s+\w+)\s+constitute"
    r"|(?:is|are)\s+not\s+intended\s+(?:as|to\s+(?:be|constitute|provide))"
    r"|should\s+not\s+be\s+(?:construed|considered|regarded|relied\s+upon)\s+as"
    r"|(?:is|are)\s+not\s+(?:and\s+should\s+not\s+be\s+construed\s+as\s+)?(?=an?\s+(?:investment\s+)?(?:recommendation|offer|solicitation))"
    # IT
    r"|non\s+costitui\w*|non\s+rappresenta(?:no)?|non\s+(?:deve|devono|va|vanno)\s+essere\s+(?:considerat|intes|interpretat)\w*\s+come"
    # DE
    r"|(?:stellt|stellen)\s+(?:weder|keine?[nrs]?)|(?:ist|sind)\s+(?:weder|keine?[nrs]?)(?=\s)"
    # FR
    r"|ne\s+constitu\w*\s+(?:pas|ni|en\s+aucun\s+cas)|ne\s+saurai\w*\s+constituer|ne\s+doi\w*\s+pas\s+[êe]tre\s+consid[ée]r\w*\s+comme"
    # ES
    r"|no\s+constituy\w*|no\s+(?:debe|deben)\s+(?:considerarse|interpretarse)\s+como"
)
_RIEMPITIVI = (r"(?:an?|any|the|investment|financial|legal|tax|personal|specific|or|nor|neither|either|and|as"
               r"|un['’]?|una|uno|alcuna|alcun|qualsiasi|né|ne|o|e|di|del|della|investimento|forma"
               r"|ein|eine|einen|einer|keine?[nrs]?|jegliche\w*|noch|oder|und|zum|zur|als"
               r"|une?|aucune?|ni|ou|et|d['’]|de|la|le|en|aucun|cas"
               r"|ninguna?|ni|y|o|una?|de|inversi[oó]n)")
_OGGETTI = (r"(?:recommendations?|advice|offers?|solicitations?|invitations?|inducements?|research|rating"
            r"|raccomandazion\w*|sollecitazion\w*|offert\w*|consulenz\w*|consigli\w*|invit\w*"
            r"|\w*empfehlung\w*|angebot\w*|aufforderung\w*|beratung\w*|anlageberatung\w*|finanzanalyse\w*"
            r"|recommandation\w*|offre\w*|sollicitation\w*|conseil\w*|invitation\w*"
            r"|recomendaci\w*|ofertas?|solicitud\w*|asesoramiento\w*|invitaci\w*)")
_DISCLAIMER_RX = re.compile(
    r"\b(?:" + _FORMULE + r")(?:\s+" + _RIEMPITIVI + r"\b){0,5}\s*" + _OGGETTI + r"\b", re.I)
# Fine della frase del disclaimer: punto seguito da maiuscola, ! ? ;  — oppure una congiunzione avversativa
_FINE_FRASE_RX = re.compile(
    r"[!?;]|\.(?=\s+[A-ZÀ-Ý«\"(])|\.\s*$"
    r"|,?\s+\b(?:but|however|while|ma|tuttavia|per[oò]|aber|jedoch|sondern|mais|cependant|pero|sino|sin\s+embargo)\b")
_TETTO_DISCLAIMER = 600


def _normalizza(testo: str) -> str:
    # NFKC: legature e spazi speciali dei PDF; gli a-capo restano (le frasi PDF vanno a capo ovunque, il
    # disclaimer non si chiude a fine riga)
    return unicodedata.normalize("NFKC", testo or "")


def _tratti_disclaimer(testo: str) -> list[tuple[int, int]]:
    tratti = []
    for m in _DISCLAIMER_RX.finditer(testo):
        coda = testo[m.end():m.end() + _TETTO_DISCLAIMER]
        f = _FINE_FRASE_RX.search(coda)
        fine = m.end() + (f.start() if f else len(coda))
        tratti.append((m.start(), fine))
    return tratti


def _dentro(pos: int, fine: int, tratti) -> bool:
    return any(a <= pos and fine <= b for a, b in tratti)


def _rating_creditizio(testo: str, m: re.Match) -> bool:
    """La corrispondenza e' «sostantivo + giudizio» e il sostantivo e' qualificato come creditizio/ESG."""
    s = _SOSTANTIVO_RX.search(m.group(0))
    if not s:
        return False
    inizio = m.start() + s.start()
    return bool(_CREDITIZIO_RX.search(testo[max(0, inizio - 40):inizio]))


def motivo_nota_ricerca(testo: str) -> str | None:
    """Motivo leggibile se `testo` (copertina / prime pagine) e' una nota di ricerca o di un broker, None
    altrimenti. Solo i disclaimer chiusi di `_DISCLAIMER_RX` annullano il lessico; ogni altra negazione no."""
    t = _normalizza(testo)
    if not t.strip():
        return None
    tratti = _tratti_disclaimer(t)
    m = _AUTORE_RX.search(t[:_TESTA_AUTORE])
    if m and not _dentro(m.start(), m.end(), tratti):
        return (f"nota di ricerca o di un broker («{m.group(0)}» come autore in intestazione): "
                "non e' un documento dell'emittente")
    for m in _PAROLE_RX.finditer(t):
        if _dentro(m.start(), m.end(), tratti) or _rating_creditizio(t, m):
            continue
        parola = " ".join(m.group(0).split()).rstrip(":-– ")
        return f"nota di ricerca o di un broker («{parola}»): non e' un documento dell'emittente"
    return None
