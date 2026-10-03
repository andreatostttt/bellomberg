# Trade Idea ? percorso corrente dal 03/10/2026

Le nuove run usano la ricerca societaria senza generazione o compilazione Excel,
anche nei recuperi. I workbook storici rimangono in archivio. La ricerca raccoglie
bilanci, assunzioni e fonti, confronta il consensus di mercato quando disponibile
e produce un giudizio indipendente; le vecchie stime Excel non sono consensus.

Il comitato comprende sei desk, Red Team, confronto sulle obiezioni e Capo.
La nuova policy `trade-idea-research/2`, salvata all'accettazione, usa MEDIUM per
specialisti/Red Team e LOW per il Capo, con spazio massimo di output Capo di
32768 token. Gli identificativi modello e il tetto USD scelto dal PM restano
invariati. L'effort non garantisce un costo o una durata: ogni richiesta richiede
la prenotazione nel budget disponibile. Le run precedenti conservano il contratto
accettato originariamente; questa policy non riapre REY.

Il memo adotta la grafica del Consigliere: copertina con tesi e rischi, corpo
analitico continuo, tabelle/grafici solo con dati citati, fonti in appendice.
Non ha un minimo artificiale di pagine o parole. Deve sviluppare tutte le undici
sezioni richieste, preservare le obiezioni e rendere il testo integralmente nel PDF.
Un output troncato, vuoto o incompleto non autorizza l'email finale. Fonti mancanti
possono invece motivare un giudizio completo di osservazione o rigetto, con limiti
espliciti. La qualit? dell'argomentazione andr? valutata anche nel collaudo del PM.

La richiesta esatta al Capo viene salvata prima dell'invio insieme alla riserva.
Dopo un'interruzione una risposta completa gi? pagata si recupera senza una nuova
chiamata, solo se ricevuta, costo, contenuti e contesto economico sono coerenti.
Cambiamenti economici o costi incerti richiedono verifica; non scattano retry pagati
silenziosi. Il pacchetto PDF e l'invio conservano le garanzie di idempotenza.

Per il prossimo collaudo: a run ferme, chiudere e riaprire l'app per caricare il
codice; eseguire una nuova Verifica fonti, controllare MEDIUM/LOW nel riepilogo e
avviare una nuova Trade Idea con il tetto desiderato. Il collaudo live resta al PM.
Le prove di sviluppo sono offline con DB temporanei, provider/SMTP simulati e
blocco dei percorsi di compilazione Excel.

---

# Riferimento storico ? precedente percorso con modello Excel

Le sezioni seguenti documentano il vecchio contratto. Le istruzioni di compilazione,
preparazione, effort MAX e minimo dieci pagine sono superate per le nuove run.

Trade Idea is a separate research workflow for one selected instrument. Open it from
Agents Live, Market or Favorites. Favorites can prefill the saved company note;
editing the run view does not edit that note. A blank view is supported.

Enter an explicit positive USD spending limit. The preflight resolves the exact
instrument, economic method, current observed quote, configuration and the free
provider model catalog. It admits primary-document research even when the internal
filing archive is empty or historical balances have not been normalized.
Catalog availability is not proof of account access.
The displayed model prices are not an estimate of the final run cost.

The committee uses Macro, EventDesk, Crypto, Fundamentals, Quant and Options,
followed by the mandatory Red Team and chair. The PM view is a hypothesis to
challenge. Ordinary weekly committee configuration is separate.

| Trade Idea role | Model ID | Reasoning effort |
| --- | --- | --- |
| Specialists, Red Team, auxiliary preparation | `meta/muse-spark-1.3` | `max` |
| Chair | `anthropic/claude-opus-5.5` | `max` |

These approved IDs have dedicated `TRADE_IDEA_SPECIALIST_MODEL`,
`TRADE_IDEA_RED_TEAM_MODEL`, `TRADE_IDEA_AUX_MODEL` and `TRADE_IDEA_CAPO_MODEL`
configuration checks. A conflicting override fails explicitly. The existing
OpenRouter account and email configuration are reused; no weekly model variables
are rewritten. Each Trade Idea has an explicit run-specific grant covering model
preparation, the committee and a bounded model revision within the displayed
limit. It does not enlarge a separate legacy valuation grant or activate other
tickers, scheduled work or recurring spending.

## Sources and the common model

Research admission happens before any paid request; economic qualification happens
after the agents acquire and analyze the primary documents. During R0/R1, the
ordinary specialist tools `search_company_sources`, `open_company_source` and
`acquire_company_source` navigate official issuer/IR pages, download HTML/PDF
statements and earnings, and share verified bytes through `read_company_dossier`.
Optional public issuer-document URLs can
be added under **Add documentary sources**; dates and exact quotations are optional
verification aids. A URL or a number entered by the user is not verified evidence.
The server checks the publisher, document bytes, publication and reporting dates.
Document identity and economic sufficiency are separate checks.

An external PDF link is accepted only through an exact link observed in a verified
official issuer page; it does not grant general access to the external host.
Navigation pages and snippets remain research material, never accounting evidence.
The original run grant stays immutable. Acquired documents create sealed revisions
with their URLs, dates, hashes and parent grant. Continuation rechecks these
receipts; an interrupted GET without a durable response remains explicitly
unresolved, rather than being silently repeated.

In the workbook, **Assumptions** links to **Sources**, which identifies consumed
historical facts, guidance and analyst estimates, with rationales and documentary
references. The raw provider snapshot sheets remain separately labelled. Missing
proof is explicit; neither a wide valuation/price gap nor an empty archive alone
invalidates a financial model.

The source check also verifies a separate current observed market price, with its
observation time, acquisition date, currency and instrument identity. The displayed
time is local to the browser. An old model price cannot satisfy this check, and a
new download cannot change the economic date of the historical model. The current
freshness contract accepts the previous weekday and does not model exchange
holidays. An observed quote does not guarantee an official close or an intraday
feed. Fair-value and current-FX comparison remain separate qualifications.

AI research and assumptions are paid phases of the accepted grant. During the
first analysis round, Fundamentals consults the other five desks and supplies
explicit model inputs. The common compiler creates the Excel workbook during
that round. The Red Team then raises objections; all six desks discuss the exact
verified workbook in the final round, before the chair reviews the final version.
Insufficient sources or an invalid workbook produce an explicit incomplete state.
Even a rejected investment requires a valid workbook to count as completed research.

Model reviews may change supported analyst assumptions. Historical observations
and published company guidance retain their values, identity and source evidence;
correcting them requires a verified source refresh. A document ID or a new review
rationale alone cannot validate a changed fact. Explicit paid review requests
must refer to existing input slots, and the resulting model is checked again.

The method registry distinguishes operating businesses, banks, managed care,
insurance, regulated assets, NAV-based instruments, property, resources,
development and mixed businesses. Exposure instruments have an exposure workbook,
not an invented intrinsic fair value. A registered method or a passing synthetic
test is not proof that every real issuer in that family can be prepared.

## Results and recovery

A favorable judgment becomes a pending Decision only after evidence, research
depth, valuation, mandate, sizing, book and PM-history checks succeed. Buying an
existing holding is normalized to ADD. Unsupported sizing, stale inputs, changed
book or unresolved risk assumptions keep the candidate in Research, with a
reason. HOLD/HEDGE do not fabricate executable orders. Analysis does not change
holdings, cash, saved notes or previous PM feedback.

Trade Idea stress and sizing require dated historical prices converted to EUR
using aligned historical FX before computing returns. Missing series, dates or
unqualified proxies keep proposals in Research with the specific reason;
a current spot FX rate cannot establish the missing historical stress evidence.
Changing FX rates between accepted and final book snapshots also keeps the
proposal in Research until the personal sizing assumptions can be revalidated.
A proposal also stays unavailable to the trade form until final artifacts pass.
If final report validation fails, an untouched pending proposal moves to Research.
PM edits are preserved, while a separate technical block prevents using that
decision as a trade authorization.
The candidate price check uses an explicitly dated daily close and the existing
previous-weekday freshness policy. It is not an intraday quote or a guarantee
against price changes after the research cutoff.
Evidence dates must come from economic fields in the tool response; a request
timestamp alone cannot make an older financial observation current. Numeric
claims require a corresponding tool receipt or a declared assumption and formula.
These checks establish traceability, not independent semantic validation of every
claim made by the committee.

Negative, watch and incomplete results remain Research entries. A failed
preflight creates no run or Decision. Stop and provider failures preserve partial
reports without inventing an investment judgment. History, run identity and
original PM view survive page refreshes and backend restarts. Interrupted paid
work is never resumed automatically.

## Work with a saved research run

### Native continuation and file recovery

In **Agents Live → Trade Idea**, select the exact run in history and use its
**Run recovery** panel. The panel distinguishes analysis, artifacts, delivery
and operational status, preserves the original failure, and shows known costs,
uncertain reservations and remaining work. A reservation floor excludes future
input costs and is not a forecast or a guarantee of completion.

**Continue missing phases** requires explicit confirmation. It creates one linked
continuation; repeated requests open the same successor. Complete compatible
reports, tool receipts and reviewed workbook generations are reused. The
original run stays immutable and its costs remain part of the original ceiling.
Unknown billing, changed dependencies and missing native checkpoints are
explicit blocks; a dead worker does not grant permission to spend again.

**Recover memo, PDF and Excel** uses saved material without new AI or email. It
keeps original hashes and generations. Modified originals are preserved and
reported as unavailable, rather than silently overwritten. Historical material
without sufficient checkpoints or verified artifact copies can remain blocked.
File recovery does not create a new operational approval.

For the weekly flow, see [Consigliere recovery](CONSIGLIERE_RECOVERY.md).

Select a run, then use **Work with this research** and select the version to consult.

| Tab | Available actions |
| --- | --- |
| Questions and scenarios | Evidence-based questions with model cells; explicit personal input changes and common-engine recalculation; comparisons between versions and ideas, with incompatible bases identified. |
| Thesis and objections | Append-only PM objections and replies; explicit manually checked monitoring conditions; observed-price and verified-document refresh. Subsequent runs consume the preserved PM history. |
| Earnings and review | Freeze model expectations before an event; compare later primary-source results on the same entity, period and unit; explicitly revise a future assumption and inspect the value impact; document past errors without claiming causal performance improvement. |
| Costs and export | Actual phase charges, reservations, unresolved costs and known remaining budget; private package or explicitly acknowledged shareable extract. |

These workspace operations are deterministic and do not implicitly authorize
another LLM request. Missing evidence is shown explicitly. Frozen expectations
come from model outputs and are not market consensus; unsupported metrics are
identified. A published URL alone does not validate a reported earnings number.
Monitoring is manual and must be explicitly activated; no recurring spending is
enabled by saving a condition.

Version comparisons require documented matching entity, share class, quotation
basis and method version. Pence/pounds presentation can be normalized with an
explicit conversion; different ADR ratios, FX bases or missing contracts suppress
the numerical delta. Non-finite results are unavailable, including arithmetic
overflow from otherwise finite inputs.

A price refresh verifies the new observation's complete fields, exact venue and
timestamped acquisition receipt before generating a variant. It cannot inherit
missing quote fields from the previous price. The observation time must precede
acquisition and verification; historical financial dates remain unchanged.

Material simulations and new observations invalidate the older operational
proposal while preserving its result and every PM intervention. Personal variants
never overwrite the committee workbook. Exports use the reviewed committee
version, so a new scenario is not silently paired with an older PDF.

An unfinished operation appears separately from completed history. **Recover the
saved result** publishes an already completed durable receipt after checking its
artifact hashes. It does not repeat the calculation. A crash before that receipt
remains explicitly interrupted and cannot automatically recalculate the same
request. Long calculations do not hold a SQLite write transaction.

One paid committee can run at a time. Each LLM request reserves its maximum
permitted cost before dispatch and records provider usage afterward. Unknown
billing blocks further spending; a delayed provider receipt can reconcile it
without resending the request. Stop prevents new phases and requests; an already
dispatched provider request may still incur cost.

## PDF, workbooks and email

Completed research requires at least ten substantive analytical PDF pages.
Cover, bibliography and quoted PM text do not satisfy that count. Insufficient
material stays explicitly partial; the renderer does not pad the document.

Excel files come from the common valuation engine and existing usability,
registration, sidecar and hash checks. The chair must identify the exact reviewed
snapshot, generation and valuation date. A negative investment judgment can still
have a valid attached model. Missing authorization or economic inputs are shown
explicitly; diagnostic or mismatched workbooks are excluded.

The same PDF and verified Excel bytes are available for download and included in
the automatic final email. Analysis, artifacts and email have separate states.
SMTP acceptance means the server accepted the message, not that the recipient
read it. A failed send can retry the original saved package without a new
analysis. An uncertain outcome requires a separate acknowledgement of possible
duplicate delivery. No automatic blind resend occurs.

The private export preserves the reviewed PDF and Excel bytes. The shareable
export is a newly built model extract with public source/version inventory,
constructed from an explicit inclusion list. Its preview excludes personal book,
sizing, notes, PM text, original committee prose and private metadata. Workbook
cells, hidden sheets, comments, properties, links and PDF metadata are checked.
It is not the complete committee dossier, and exporting never sends it to a third party.

## Installation and offline verification

The additive schema migration is explicit. Stop the Bellomberg backend after
checking active jobs, then run from the real project directory:

```powershell
python tools/migrations/migra_trade_idea.py --dry-run
python tools/migrations/migra_trade_idea.py --apply
```

Dry-run checks a transactional database copy. Apply requires a free backend port,
creates and verifies a backup, checks the source has not changed, and preserves
existing tables and rows. It inserts no example runs or research into the
personal database. Restart the normal application after a successful apply.

Automated coverage uses temporary SQLite databases, intercepted providers and
captured SMTP MIME. Electron coverage includes the real authenticated API with an
isolated database and synthetic analysis worker, plus controlled HTTP scenarios
for failure states and localization. Tests use separate desktop profiles.
These tests prove software contracts and artifact identity; they do not
claim a paid live model run, real inbox delivery, investment success or native
macOS desktop validation.

## Collaudo manuale del PM - consegna 01/10/2026

La consegna del codice e chiusa; il PM esegue la prova della run. Codex non avvia
altre run, prove a pagamento o tentativi sul checkpoint precedente.

1. Usa il progetto standard e riavvia backend e app per caricare
   il codice aggiornato. Non usare il worktree isolato della consegna per la prova.
2. Apri **Agents Live > Trade Idea**, oppure Trade Idea da **Market/Favoriti**.
3. Seleziona il ticker, inserisci una view solo se vuoi e imposta il limite USD
   della tua prova. Esegui il controllo delle fonti e leggi eventuali dati mancanti.
4. Quando decidi di effettuare la tua prova, premi **Avvia** e conferma la spesa.
5. Osserva la ricerca iniziale, Fundamentals e la costruzione del modello durante
   il comitato; poi Red Team, discussione finale e Capo. Dal risultato apri Excel
   e PDF; se la run e incompleta, conserva il motivo mostrato e i report disponibili.

La precedente prova e rimasta incompleta al Capo per esaurimento del limite
output senza testo pubblico. Questa consegna non dichiara superato quel problema
ne modifica i parametri dei modelli. Artefatti e spese precedenti sono conservati
nella delivery, separati dalla tua futura prova.
