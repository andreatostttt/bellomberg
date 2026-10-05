# Loghi dei titoli

La dashboard mostra accanto a ogni posizione un'icona tonda. L'icona viene cercata in quest'ordine:

1. **Un file in questa cartella.** Il nome del file è il ticker com'è nel portafoglio, oppure solo la sua radice (la parte prima del punto).
   - `ALFA.DE.svg` vale solo per `ALFA.DE`.
   - `ALFA.svg` vale per `ALFA.DE`, `ALFA.MI` e per ogni altra sede dello stesso titolo.
2. **Il logo della società dal backend** (`GET /market/logos`, al massimo 40 ticker per richiesta), scaricato una volta e tenuto in `data/loghi/`. Il backend lo prende dal profilo Finnhub (serve un simbolo USA: il ticker stesso o l'alias dichiarato nella sezione `finnhub` di `data/alias_fonti.json`) oppure dall'icona del sito dell'emittente. Quando non trova nulla risponde `null` con il motivo, che l'icona conserva nell'attributo `data-logo-motivo`.
3. **Il ripiego dichiarato: le iniziali del nome** dell'emittente in un cerchio, con un colore che resta sempre lo stesso per quel ticker.

L'app non contiene elenchi di ticker o di marchi: nessun titolo ha un logo «di serie» nel codice.

I file `.svg` e `.png` di questa cartella sono personali: `.gitignore` li tiene fuori dal repository (sono marchi di terzi e descrivono il tuo portafoglio). Servono soprattutto per i titoli che né Finnhub né il sito dell'emittente coprono, o per sostituire un logo scaricato che nel cerchio rende male.

## Come aggiungere un logo

1. Procurati un logo che hai il diritto di usare, in formato `.svg` (preferito) o `.png`. Va bene un quadrato da almeno 128×128 px con lo sfondo trasparente.
2. Salvalo qui con il nome del ticker: `ALFA.MI.svg`, `BETA.png`, eccetera. Maiuscole e minuscole non contano.
3. In sviluppo il logo compare da solo. Per l'app installata va ricompilata (`npm run build`).

Il file viene incluso nel pacchetto dell'app che compili tu e vince sul logo scaricato.
