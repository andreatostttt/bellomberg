"""Download con tetto e pulizia delle cartelle di lavoro (fase F, chiude il rinvio della fase C).

Tutti i documenti delle fonti automatiche passano da qui: solo HTTP(S) pubblico, host
ammessi controllati a ogni redirect (lettore_trimestrali.scarica_documento), corpo letto
a flusso con un tetto per tipo di documento, sha256 calcolato durante la lettura.
"""
import os
import time
from urllib.parse import urlsplit

MB = 1024 * 1024
MAX_PDF = 40 * MB
MAX_PACCHETTO_ESEF = 80 * MB
MAX_XHTML_ESEF = 200 * MB  # XHTML estratto dal pacchetto (lo controlla ixbrl_oim)

# cartella filing_archive/ai: file ricaricabili (la proposta AI tiene lo sha256, non il file)
GIORNI_AI = 30
MAX_CARTELLA_AI = 1024 * MB


def scarica_limitato(url, dest, max_bytes, hosts):
    """Snapshot di `url` in `dest`, al massimo `max_bytes`, solo verso `hosts` (anche nei
    redirect) e solo verso indirizzi pubblici. Restituisce l'esito di scarica_documento."""
    from bellomberg.market_data.lettore_trimestrali import scarica_documento
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError("max_bytes positivo richiesto")
    if urlsplit(str(url or "")).scheme != "https":
        return {"stato": "errore", "url": url, "motivo": "ValueError: solo HTTPS"}
    return scarica_documento(url, str(dest), host_consentiti=set(hosts), public_only=True, max_bytes=max_bytes,
                             solo_https=True)


def pulisci_cartella(cartella, *, giorni=GIORNI_AI, max_totale=MAX_CARTELLA_AI, tieni=(), adesso=None):
    """Toglie i file piu' vecchi di `giorni` e poi i piu' vecchi finche' la cartella sta sotto
    `max_totale` byte; mai quelli in `tieni` (percorsi appena scaricati). Restituisce i rimossi.
    Solo file regolari al primo livello: niente sottocartelle, niente link simbolici seguiti."""
    adesso = time.time() if adesso is None else adesso
    tieni = {os.path.realpath(p) for p in tieni}
    try:
        voci = [e for e in os.scandir(cartella) if e.is_file(follow_symlinks=False)]
    except OSError:
        return []
    file = sorted(((e.stat(follow_symlinks=False).st_mtime, e.stat(follow_symlinks=False).st_size, e.path)
                   for e in voci), key=lambda x: x[0])
    rimossi, totale = [], sum(f[1] for f in file)
    for mtime, size, path in file:
        if os.path.realpath(path) in tieni:
            continue
        if mtime < adesso - giorni * 86400 or totale > max_totale:
            try:
                os.unlink(path)
            except OSError:
                continue
            rimossi.append(path)
            totale -= size
    return rimossi
