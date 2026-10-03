# Recuperare una run Consigliere

## Percorso corrente: ricerca senza Excel (03/10/2026)

Le nuove run producono ricerca societaria, memo e PDF. Non generano o compilano
Excel, nemmeno durante un recupero. I workbook precedenti restano nell'archivio
storico in sola lettura e non forniscono il consensus corrente della pagina Fund.

In **Agents Live**, apri **Recupera una run Consigliere** e seleziona il memo
preciso. Sono mostrati lo stato analitico, la disponibilità dei file, la
consegna, la prima causa dell'errore, i costi e le fasi mancanti.

- **Continua le fasi mancanti** richiede una conferma perché può inviare nuove
  richieste AI. Riusa report, risposte e tool completi con checkpoint compatibili.
  Un costo incerto, un book o mandato cambiato, o un checkpoint alterato bloccano
  la ripresa. Non vengono inventati un budget o una previsione del costo finale.
- **Recupera memo e PDF** usa soltanto il materiale salvato. Non richiama
  l'AI e non invia email. I file devono conservare generazione e hash verificati;
  un originale modificato non viene sovrascritto.

Le due azioni mantengono la stessa identità del memo e registrano i tentativi.
Una seconda ripresa non ripete fasi già concluse. Le decisioni estratte sono
validate prima della sostituzione atomica; annotazioni e decisioni del PM sono
preservate anche quando estrazione o salvataggio falliscono.

Il recupero dei file resta disponibile quando il book corrente è cambiato,
perché conserva il contesto della ricerca originale. Non autorizza operazioni
sul portafoglio corrente. I memo precedenti all'introduzione dei checkpoint
nativi restano nell'archivio: se il contesto necessario manca, il sistema lo
dichiara e non ricostruisce una continuità fittizia.
Le vecchie run che prevedevano la compilazione Excel non possono riprendere
le fasi analitiche nel percorso corrente; avvia una nuova ricerca quando serve
un'analisi aggiornata. Gli artefatti storici già salvati e verificabili restano
recuperabili senza compilare nuovi workbook.

Il comando ordinario alternativo, dalla cartella reale del progetto, è:

```powershell
python -m bellomberg.cli.regenerate_memo --memo-id ID --delivery-only
```

Sostituisci `ID` con il memo scelto. Per riprendere le fasi analitiche, il comando
esplicito è `--memo-id ID --authorize-new-ai`: può generare spesa. La scelta
`--send-email` richiede la volontà di inviare la consegna; un invio dall'esito
incerto resta bloccato contro le duplicazioni. Questi comandi sono istruzioni
operative per il PM, non sono stati eseguiti su dati personali durante il goal.

Un codice di uscita nullo non certifica da solo una run:
l'API verifica l'esito riferito al task, il memo, l'analisi e gli artefatti.
L'email ha uno stato separato. Le verifiche di sviluppo sono esclusivamente
offline; non attestano disponibilità futura dei provider o qualità di nuove
analisi societarie.
## Fonti societarie nelle run ordinarie

Gli agenti possono aprire le pagine ufficiali Investor Relations e acquisire
bilanci ed earnings durante R0/R1, con gli stessi strumenti di Trade Idea.
I documenti verificati sono condivisi nel dossier della run e usati da
Fundamentals e dal comitato per discutere bilanci, ipotesi e giudizio indipendente.
Un archivio interno vuoto non sostituisce la ricerca; un dato non verificabile
rimane dichiarato. Il memo e il PDF conservano fonti, limiti della ricerca e
distinzione fra dati osservati e ipotesi.

Fonti, identita societaria e revisioni sono conservate per la ripresa; le ricevute
complete non causano nuovi download. Se un download si interrompe prima di una
ricevuta durevole, il relativo esito resta incerto e non viene ripetuto alla cieca.
Modelli, ragionamento e limiti di output seguono i parametri dichiarati
dall'applicazione e il contratto della run selezionata. Non applicare a una run
storica i parametri di una nuova analisi. Un limite di output maggiore non
garantisce una risposta completa: le troncature restano visibili e non diventano
report completi.

Queste modifiche richiedono che il backend carichi il nuovo codice. Sono state
collaudate con provider simulati, documenti congelati e renderer reale, senza
generare o compilare Excel nel percorso corrente;
nessuna nuova run personale e stata avviata durante lo sviluppo.

## Riferimento storico: percorso del 02/10/2026

Il percorso precedente prevedeva un preparatore del modello e un foglio
Sources nei workbook, con un limite allora specifico di 64000 token per
Fundamentals R1/R2. Queste indicazioni descrivono soltanto lo storico: sono
superate dal percorso di ricerca senza Excel. Ricevute, report e file già
salvati mantengono la propria identità originale; non vengono convertiti
silenziosamente in risultati di una nuova run.
