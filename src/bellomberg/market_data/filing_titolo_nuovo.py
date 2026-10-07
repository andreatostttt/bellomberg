"""Trade Idea su un titolo NUOVO: profilo Filing attivato e documenti scaricati PRIMA di R0.

Richiesta PM 06/10 sera: «quando lancio una run devo avere SEMPRE dati e bilanci nuovi, per
QUALSIASI societa' io metta». Prima l'archivio Filing aveva profili solo per i titoli del book:
una Trade Idea su un titolo nuovo trovava il ponte vuoto.

La fase (deterministica, nessuna AI, solo fonti gratuite ufficiali e sito IR):
1. tipo dello strumento (registro dei veicoli, poi quoteType di Yahoo): ETF/fondi/crypto =
   «bilancio societario non applicabile», nessuna attivazione;
2. titolo senza profilo: ``filing_attivazione.attiva`` (la stessa dell'app: SEC, ESEF, sito
   dell'emittente) + variante 6-K + esito nelle preferenze «attivato dalla Trade Idea del <data>»;
   profilo gia' presente: si aggiorna solo se l'ultimo periodo archiviato e' piu' vecchio del
   periodo atteso (REGOLA_FRESCHEZZA);
3. run Filing del solo titolo (``FilingService.run_programmato``: trigger «scheduled», il giudice
   AI non si chiama mai);
4. passi extra (cascata di freschezza, altro agente) nel tempo che resta.

Vincoli PM: la fase NON blocca mai la run. Tempo massimo complessivo DURO (TEMPO_MAX_S): oltre,
si prosegue e la lacuna e' dichiarata; ogni guasto diventa un motivo dichiarato (tipo + testo),
nessuna eccezione esce verso la run. L'esito vive nei dati della run (checkpoint): la ripresa lo
RIUSA, nessun secondo download. (Opus 5.5, 06/10)
"""
from copy import deepcopy
from datetime import date, timedelta
import json
import threading
import time
from pathlib import Path

CONTRATTO = 'filing-titolo-nuovo/1'
CHIAVE_BOARD = '_filing_titolo_nuovo'
TEMPO_MAX_S = 360          # tempo massimo complessivo della fase documenti (main 07/10: 6 minuti)
RISERVA_PASSI_S = 60       # riservati ai passi extra (cascata di freschezza): 300 s a Filing + sito
GIORNI_DEPOSITO = 60       # un trimestre si considera «atteso» 60 giorni dopo la chiusura
TEMPO_MAX_PONTE_S = 120    # R-FASE B1: tetto SEPARATO e dichiarato del ponte (rilettura + riverifica dei byte)
MINUTI_FRESCO = 10         # R-FASE M3: un controllo Filing di meno di 10 minuti fa non si ripete (deduplica)
GIUDIZIO_NON_RICHIESTO = ("controllo Filing forzato dalla Trade Idea: giudizio qualitativo AI non richiesto "
                          "(resta del solo pulsante «Verifica ora»)")
INDICE_RIMANDATO = ("fase documenti della Trade Idea scaduta o fermata: indice di ricerca non aggiornato, "
                    "lo rifa' il prossimo controllo")
CUTOFF = ("cutoff = data di accettazione della Trade Idea (riproducibilita'): un deposito con data successiva resta "
          "fuori dal ponte anche se scaricato ora, e serve alla run successiva")
REGOLA_FRESCHEZZA = ("profilo esistente aggiornato prima della run se l'ultimo periodo archiviato e' "
                     "anteriore al periodo atteso (ultimo trimestre solare chiuso da almeno %d giorni "
                     "alla data della run)" % GIORNI_DEPOSITO)
NON_IN_TEMPO = ("documenti non scaricati in tempo: la fase documenti ha superato %d s (fase in corso: %s); "
                "la run prosegue con l'archivio com'era, il download continua in background e servira' "
                "alla prossima run")
NON_APPLICABILE = 'bilancio societario non applicabile'
# quoteType di Yahoo -> tipo del registro dei veicoli (TIPI_NON_OPERATIVI del Filing) o natura dichiarata.
_QUOTE_TYPE = {'ETF': 'etf', 'CRYPTOCURRENCY': 'crypto', 'FUTURE': 'commodity',
               'MUTUALFUND': 'fondo comune', 'INDEX': 'indice', 'CURRENCY': 'valuta'}


def _motivo_eccezione(exc):
    return type(exc).__name__ + ': ' + str(exc)[:200]


def _json(valore):
    """Copia serializzabile (il record va nel checkpoint JSON della run)."""
    return json.loads(json.dumps(valore, ensure_ascii=False, default=str))


# ------------------------------------------------------------------ tipo dello strumento

def _registro_predefinito():
    from bellomberg.storage.classificazione import carica_veicoli
    return carica_veicoli()


def _tipo_yahoo(ticker):
    """quoteType del simbolo ESATTO dalla ricerca pubblica di Yahoo (GET gratuita); None se assente."""
    import requests
    r = requests.get('https://query2.finance.yahoo.com/v1/finance/search',
                     params={'q': ticker, 'quotesCount': 10, 'newsCount': 0},
                     headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
    r.raise_for_status()
    tipi = {str(v.get('quoteType') or '').upper() for v in (r.json().get('quotes') or [])
            if isinstance(v, dict) and str(v.get('symbol') or '').upper() == ticker.upper()}
    tipi.discard('')
    return next(iter(tipi)) if len(tipi) == 1 else None


def tipo_dal_registro(ticker, registro):
    """{'tipo', 'origine', 'motivo'} dal registro dei veicoli (locale, nessuna rete) o None."""
    voce = ((registro or {}).get('veicoli') or {}).get(ticker) if isinstance(registro, dict) else None
    if voce and voce.get('tipo'):
        return {'tipo': voce['tipo'], 'origine': 'registro dei veicoli', 'motivo': None}
    return None


def tipo_strumento(ticker, registro):
    """{'tipo', 'origine', 'motivo'}: registro dei veicoli, poi quoteType di Yahoo; n.d. dichiarato."""
    dal_registro = tipo_dal_registro(ticker, registro)
    if dal_registro is not None:
        return dal_registro
    try:
        qt = _tipo_yahoo(ticker)
    except Exception as exc:
        return {'tipo': None, 'origine': None,
                'motivo': 'tipo strumento n.d.: ricerca Yahoo non disponibile (' + type(exc).__name__ + ')'}
    if qt is None:
        return {'tipo': None, 'origine': None, 'motivo': 'tipo strumento n.d.: simbolo non univoco su Yahoo'}
    if qt == 'EQUITY':
        return {'tipo': 'operating', 'origine': 'Yahoo quoteType EQUITY', 'motivo': None}
    return {'tipo': _QUOTE_TYPE.get(qt, qt.lower()), 'origine': 'Yahoo quoteType ' + qt, 'motivo': None}


def _non_societario(tipo):
    from bellomberg.market_data.filing_identita import TIPI_NON_OPERATIVI
    return tipo in TIPI_NON_OPERATIVI or tipo in ('fondo comune', 'indice', 'valuta')


# ------------------------------------------------------------------ freschezza del profilo

def periodo_atteso(as_of):
    """Ultimo trimestre solare chiuso da almeno GIORNI_DEPOSITO giorni alla data della run (ISO)."""
    giorno = date.fromisoformat(str(as_of)[:10]) - timedelta(days=GIORNI_DEPOSITO)
    for mese, fine in ((12, 31), (9, 30), (6, 30), (3, 31)):
        chiusura = date(giorno.year, mese, fine)
        if chiusura <= giorno:
            return chiusura.isoformat()
    return date(giorno.year - 1, 12, 31).isoformat()


def ultimo_periodo(risultato):
    """Periodo piu' recente fra i documenti VERIFICATI senza riserve (byte presenti) di un risultato Filing.

    R-FASE M2: un non verificato (identita', lingua, periodo da confermare) non puo' far dire «gia' al periodo
    atteso»: altrimenti la fase salta il download mentre il ponte esclude proprio quel documento."""
    if not isinstance(risultato, dict):
        return None
    periodi = []
    for c in list(risultato.get('candidati') or []) + list(risultato.get('candidati_varianti') or []):
        if (not isinstance(c, dict) or c.get('stato') != 'verificato' or c.get('motivi')
                or not (c.get('path') and c.get('sha256'))):
            continue
        p = (c.get('metadati') or {}).get('periodo_fine') or c.get('report_date')
        if isinstance(p, str) and len(p) >= 10:
            periodi.append(p[:10])
    return max(periodi, default=None)


# ------------------------------------------------------------------ passi della fase

def _registra_esito(ticker, esito, motivo, pref_path, candidati=None):
    """Esito nelle preferenze come l'attivazione dall'app; un errore di scrittura e' dichiarato, mai bloccante."""
    from bellomberg.storage import filing_preferenze
    try:
        filing_preferenze.registra_esito(ticker, esito, motivo=motivo, candidati=candidati, path=pref_path)
        return None
    except Exception as exc:
        return 'esito non salvato nelle preferenze: ' + type(exc).__name__


def _proponi_con_nome(nome):
    from bellomberg.market_data import filing_identita

    def proponi_fn(ticker, **kw):
        return filing_identita.proponi(ticker, nome, **kw)
    return proponi_fn


def _attiva(store, ticker, *, nome, as_of, pref_path, stato):
    from bellomberg.market_data import filing_attivazione as attivazione
    stato['passo'] = 'attivazione del profilo'
    kw = {'proponi_fn': _proponi_con_nome(nome)} if nome else {}
    esito = attivazione.attiva(store, ticker, pref_path=pref_path, **kw)
    attivato = esito.get('esito') == 'attivato'
    motivo = ('attivato dalla Trade Idea del %s: ' % as_of if attivato
              else 'Trade Idea del %s: ' % as_of) + str(esito.get('motivo'))
    out = {'esito': esito.get('esito'), 'motivo': motivo,
           **{k: esito.get(k) for k in ('profilo_versione', 'forme', 'fonte', 'origine', 'verificato',
                                         'controlli_non_superati', 'tipo_documento', 'periodo_stato') if k in esito}}
    if esito.get('esito') != 'gia_attivo':
        proposta = esito.get('proposta')   # stesso conteggio di filing_routes._candidati (SEC + ESEF)
        candidati = (sum(len((proposta.get(k) or {}).get('candidati') or []) for k in ('sec', 'esef'))
                     if isinstance(proposta, dict) else None)
        nota = _registra_esito(ticker, esito.get('esito'), motivo, pref_path, candidati)
        if nota:
            out['preferenze'] = nota
    if attivato and stato['fermo'].is_set():
        # R-FASE 07/10: dopo lo stop nessuna scrittura del profilo; la variante 6-K la prova il controllo successivo
        out['variante_6k'] = {'esito': 'non_eseguito',
                              'motivo': 'fase documenti scaduta o fermata: variante 6-K al prossimo controllo'}
    elif attivato:
        stato['passo'] = 'variante 6-K'
        try:
            sei_k = attivazione.completa_6k(store, ticker)
        except Exception as exc:
            sei_k = {'esito': 'errore', 'motivo': _motivo_eccezione(exc)}
        out['variante_6k'] = {'esito': sei_k.get('esito'), 'motivo': sei_k.get('motivo')}
    return out


def _servizio_della_fase(servizio, stato, *, senza_giudice=False):
    """Copia del servizio Filing per la fase: dopo lo stop (tempo scaduto o PM) l'indice Chroma non parte;
    il run in corso si chiude comunque (FilingService.execute chiude sempre il run: mai orfano)."""
    import copy
    from bellomberg.market_data.filing_service import FilingService
    if not isinstance(servizio, FilingService):
        return servizio
    fermo, indicizza = stato['fermo'], servizio.indexer
    copia = copy.copy(servizio)

    def indice(run, result, judgment):
        if fermo.is_set():
            return {'status': 'skipped', 'reason': INDICE_RIMANDATO}
        return indicizza(run, result, judgment)
    copia.indexer = indice
    if senza_giudice:
        copia.judge = lambda result, **_kw: {'status': 'skipped', 'findings': [], 'reason': GIUDIZIO_NON_RICHIESTO,
                                             'model': None, 'usage': None}
        copia._default_judge = False
    return copia


def _minuti_dall_ultimo_controllo(store, ticker):
    from datetime import datetime, timezone
    try:
        runs = store.list_runs(ticker, limit=1)
        quando = datetime.fromisoformat(str(runs[0].get('finished_at') or runs[0]['started_at']))
        if quando.tzinfo is None:
            return None
        return (datetime.now(timezone.utc) - quando).total_seconds() / 60
    except Exception:
        return None


def _run_filing(servizio, ticker, *, as_of, stato):
    from bellomberg.storage.filing_store import RunAlreadyActive, RunNotDue
    stato['passo'] = 'run Filing del titolo'
    if stato['fermo'].is_set():
        return {'status': 'non_eseguito', 'reason': 'fase documenti scaduta o fermata: run Filing non avviato'}
    forzato = None
    try:
        run = _servizio_della_fase(servizio, stato).run_programmato(ticker)
    except RunNotDue as exc:
        # R-FASE M3: la Trade Idea arriva qui solo se manca il periodo atteso: il controllo «non dovuto» per
        # l'intervallo del profilo (24 h o 168 h) non basta. Si forza un controllo su richiesta, senza AI,
        # salvo un controllo di pochi minuti fa (deduplica).
        minuti = _minuti_dall_ultimo_controllo(servizio.store, ticker)
        if minuti is not None and minuti < MINUTI_FRESCO:
            return {'status': 'non_dovuto', 'reason': 'controllo Filing gia\' eseguito %.0f minuti fa (meno di %d): '
                    'nessun secondo download; %s' % (minuti, MINUTI_FRESCO, str(exc)[:120])}
        if stato['fermo'].is_set():
            return {'status': 'non_eseguito', 'reason': 'fase documenti scaduta o fermata: run Filing non avviato'}
        stato['passo'] = 'run Filing forzato del titolo'
        forzato = ('controllo programmato non dovuto (%s) ma periodo atteso mancante: controllo forzato dalla Trade '
                   'Idea, senza giudizio AI' % str(exc)[:120])
        try:
            copia = _servizio_della_fase(servizio, stato, senza_giudice=True)
            run = copia.execute(copia.queue(ticker, 'manual')['id'])
        except RunAlreadyActive as exc2:
            return {'status': 'gia_attivo', 'reason': 'run Filing gia\' in corso (altro processo): ' + str(exc2)[:160]}
    except RunAlreadyActive as exc:
        return {'status': 'gia_attivo', 'reason': 'run Filing gia\' in corso (altro processo): ' + str(exc)[:160]}
    result = (run or {}).get('result') or {}
    if result.get('controllo_leggero'):
        try:
            riuscito = servizio.store.ultimo_run_completo(ticker, senza_errori=True)
        except Exception:
            riuscito = None
        result = (riuscito or {}).get('result') or {}
    return {'id': (run or {}).get('id'), 'status': (run or {}).get('status'), 'reason': (run or {}).get('reason'),
            'controllo_leggero': bool(((run or {}).get('result') or {}).get('controllo_leggero')),
            'ultimo_periodo': ultimo_periodo(result), 'periodo_atteso': periodo_atteso(as_of),
            **({'forzato': forzato} if forzato else {})}


# Chiavi di una variante IR ammesse dal negozio nei profili ESEF a blocchi (filing_store._CHIAVI_VARIANTE[_IR]).
_CHIAVI_VARIANTE_SITO = ('tipo', 'fonti', 'ir_urls', 'lingua', 'sezioni', 'sezioni_intero', 'verifica',
                         'periodo_regola')
_ETICHETTE_SITO = ('verificato', 'controlli_non_superati', 'origine_documenti', 'periodo_stato', 'periodi_visti',
                   'periodo_sito', 'tipo_documento', 'via_host', 'sito_ir', 'sito_ir_origine', 'host_documenti')


def _variante_sito(attuale, ir):
    """Profilo ESEF a blocchi + variante infrannuale dal sito (stesso tipo: sostituita); l'annuale ESEF resta."""
    base = {k: v for k, v in attuale.items() if k != 'varianti'}
    varianti = [dict(v) for v in attuale.get('varianti') or [{'tipo': 'annuale'}] if v.get('tipo') != ir['tipo']]
    varianti.append({k: deepcopy(ir[k]) for k in _CHIAVI_VARIANTE_SITO if k in ir})
    if ir.get('host_documenti'):
        base['host_documenti'] = sorted(set(base.get('host_documenti') or []) | set(ir['host_documenti']))
    # Il negozio non ammette queste chiavi nella variante: restano alla base, per tipo, mai perse in silenzio
    # (verificato / controlli non superati / etichetta / stato del periodo / sito IR scoperto).
    etichette = dict(base.get('varianti_sito') or {})
    etichette[ir['tipo']] = {k: deepcopy(ir.get(k)) for k in _ETICHETTE_SITO if k in ir}
    base['varianti_sito'] = etichette
    return {**base, 'varianti': varianti}


def _dal_sito(store, ticker, *, nome, dopo, stato):
    """Sito dell'emittente quando l'archivio ufficiale e' indietro (solo profili ESEF o «sito dell'emittente»;
    i titoli SEC mai). Stesse funzioni dell'attivazione dal sito (robots/403 rispettati, identita' mai
    rilassata). Esito dichiarato: variante_aggiunta | aggiornato | invariato | non_applicabile | errore."""
    from bellomberg.market_data import esef_sito
    from bellomberg.market_data import filing_attivazione as attivazione
    stato['passo'] = "sito dell'emittente"
    riga = store.get_profile(ticker)
    p = (riga or {}).get('profile') or {}
    if p.get('origine_collegamento') == attivazione.ORIGINE_COLLEGAMENTO_SITO:
        esito = attivazione.aggiorna_dal_sito(store, ticker)
        return {'esito': esito.get('esito'), 'motivo': esito.get('motivo'),
                **{k: esito[k] for k in ('verificato', 'controlli_non_superati', 'tipo_documento', 'periodo_stato')
                   if k in esito}}
    if p.get('esef_modo') != 'blocchi':
        return {'esito': 'non_applicabile', 'motivo': 'profilo %s: il sito dell\'emittente non sostituisce la fonte '
                'ufficiale' % ('SEC' if p.get('cik') else 'non ESEF')}
    if esef_sito.consigliere_in_corso():
        return {'esito': 'non_applicabile', 'motivo': 'sito non esplorato: run del Consigliere in corso'}
    stato['passo'] = "esplorazione del sito dell'emittente"
    # forza=True: la Trade Idea vuole il sito di oggi (cache di 7 giorni scavalcata, max 1 esplorazione l'ora)
    trovato, perche = attivazione._trovato_con_accesso(ticker, esef_sito.scopri(ticker, forza=True))
    if perche:
        return {'esito': 'errore', 'motivo': perche}
    accesso = trovato.get('accesso') or {}
    if accesso.get('stato') != 'ok':
        return {'esito': 'errore', 'motivo': 'sito dell\'emittente: ' + str(accesso.get('motivo'))}
    stato['passo'] = "verifica dei PDF del sito dell'emittente"
    esito, scelta, scarti = attivazione._prova_scelte(ticker, p.get('nome') or nome, trovato, dopo=dopo,
                                                      lei=p.get('lei'))
    if esito is None:
        return {'esito': 'invariato', 'motivo': ('nessuna relazione piu\' recente di %s sul sito' % dopo
                                                 if scelta is None else 'relazione del sito non ammessa: '
                                                 + '; '.join(scarti))[:600]}
    ir = esito['profilo']
    out = {'periodo': esito['periodo'], 'tipo': ir['tipo'], 'url': (ir.get('ir_urls') or [None])[0],
           'verificato': ir.get('verificato'), 'controlli_non_superati': ir.get('controlli_non_superati'),
           'tipo_documento': ir.get('tipo_documento'), 'periodo_stato': ir.get('periodo_stato'),
           'origine': ir.get('origine_documenti'), 'via_host': ir.get('via_host'), 'sito_ir': ir.get('sito_ir'),
           'sito_ir_origine': ir.get('sito_ir_origine')}
    if ir['tipo'] == 'annuale':
        return {**out, 'esito': 'invariato', 'motivo': ('relazione annuale %s sul sito piu\' recente dell\'ESEF: '
                'una variante IR puo\' essere solo infrannuale (l\'annuale resta ESEF), dichiarata' % esito['periodo']
                + ('; scartate prima: ' + '; '.join(scarti) if scarti else ''))[:900]}
    salvato = store.set_profile(ticker, _variante_sito(p, ir), enabled=riga['enabled'],
                                interval_hours=riga['interval_hours'], qualitative_enabled=riga['qualitative_enabled'])
    return {**out, 'esito': 'variante_aggiunta', 'profilo_versione': salvato['version'],
            'motivo': '%s %s al %s dal %s (%s) aggiunta accanto all\'annuale ESEF' % (
                ir.get('tipo_documento') or 'relazione', ir['tipo'], esito['periodo'], ir.get('origine_documenti'),
                'verificata sul testo' if ir.get('verificato') else 'NON verificata: '
                + '; '.join(ir.get('controlli_non_superati') or []))
            + ('; scartate prima: ' + '; '.join(scarti) if scarti else '')}


def _intervallo_auto():
    from bellomberg.market_data.filing_attivazione import INTERVALLO_AUTO_ORE
    return INTERVALLO_AUTO_ORE


def _profilo_nel_record(rec, store, ticker):
    """Identita' del profilo Filing usato (CIK SEC / LEI / sito): serve ai passi extra (es. estere con suffisso)."""
    try:
        riga = store.get_profile(ticker)
    except Exception as exc:
        rec['profilo'] = {'errore': type(exc).__name__}
        return
    if riga is None:
        return
    p = riga.get('profile') or {}
    rec['profilo'] = {'versione': riga.get('version'), 'enabled': riga.get('enabled'), 'cik': p.get('cik'),
                      'lei': p.get('lei'), 'emittente_id': p.get('emittente_id'), 'fonti': p.get('fonti'),
                      'origine_collegamento': p.get('origine_collegamento'), 'sec_ticker': p.get('sec_ticker')}


def _fase_filing(ticker, *, as_of, nome, servizio_factory, registro, pref_path, preferenze_predefinite, stato):
    """Corpo della fase Filing (thread): aggiorna ``stato['record']`` passo per passo; puo' sollevare."""
    rec = stato['record']
    stato['passo'] = 'tipo dello strumento'
    tipo = tipo_dal_registro(ticker, registro)
    if tipo is None:
        # Fuori registro: prima l'archivio (locale; DB isolato assente = si ferma qui, senza rete), poi Yahoo.
        stato['passo'] = 'apertura dell\'archivio Filing'
        servizio = servizio_factory()
        stato['passo'] = 'tipo dello strumento'
        tipo = tipo_strumento(ticker, registro)
    else:
        servizio = None
    rec['tipo_strumento'] = tipo
    if _non_societario(tipo['tipo']):
        rec.update(stato='non_applicabile', motivo='%s: %s (%s)' % (NON_APPLICABILE, tipo['tipo'], tipo['origine']))
        return
    if servizio is None:
        stato['passo'] = 'apertura dell\'archivio Filing'
        servizio = servizio_factory()
    store = servizio.store
    if pref_path is None and not preferenze_predefinite:
        # DB alternativo (isolato): preferenze accanto al DB Filing isolato, mai quelle vere.
        pref_path = Path(store.db_path).resolve().parent / 'filing_preferenze.json'
    profilo = store.get_profile(ticker)
    if profilo is None:
        rec['azione'] = 'attivazione'
        if stato['fermo'].is_set():
            return
        rec['attivazione'] = _attiva(store, ticker, nome=nome, as_of=as_of, pref_path=pref_path, stato=stato)
        if rec['attivazione']['esito'] == 'attivato':
            rec['profilo_creato'] = ('profilo Filing CREATO dalla Trade Idea del %s: resta attivo, controllo periodico '
                                     'ogni %d h come ogni profilo attivato (si scollega dalla pagina Filing)'
                                     % (as_of, _intervallo_auto()))
        _profilo_nel_record(rec, store, ticker)
        if rec['attivazione']['esito'] != 'attivato':
            rec.update(stato='non_aggiornato', motivo='profilo Filing non attivato (%s): %s'
                       % (rec['attivazione']['esito'], rec['attivazione']['motivo']))
            return
    else:
        _profilo_nel_record(rec, store, ticker)
        if not profilo.get('enabled'):
            rec.update(azione='nessuna', stato='non_aggiornato',
                       motivo='profilo Filing disattivato dall\'utente: nessun aggiornamento prima della run')
            return
        riuscito = store.ultimo_run_completo(ticker, senza_errori=True)
        ultimo = ultimo_periodo((riuscito or {}).get('result'))
        atteso = periodo_atteso(as_of)
        rec['freschezza'] = {'ultimo_periodo': ultimo, 'periodo_atteso': atteso, 'regola': REGOLA_FRESCHEZZA}
        if ultimo is not None and ultimo >= atteso:
            rec.update(azione='nessuna', stato='aggiornato',
                       motivo='archivio Filing gia\' al periodo atteso (%s >= %s): nessun download' % (ultimo, atteso))
            return
        rec['azione'] = 'aggiornamento'
    run = _run_filing(servizio, ticker, as_of=as_of, stato=stato)
    rec['run_filing'] = run
    # main 07/10: archivio ufficiale (ESEF/OAM) piu' vecchio del periodo atteso -> anche il sito dell'emittente.
    ultimo = run.get('ultimo_periodo') or (rec.get('freschezza') or {}).get('ultimo_periodo')
    if (not ultimo or ultimo < periodo_atteso(as_of)) and stato['fermo'].is_set():
        rec['sito'] = {'esito': 'non_eseguito', 'motivo': 'fase documenti scaduta o fermata: sito non esplorato'}
    elif not ultimo or ultimo < periodo_atteso(as_of):
        try:
            rec['sito'] = _dal_sito(store, ticker, nome=nome, dopo=ultimo, stato=stato)
        except Exception as exc:     # un guasto del sito non cancella il run Filing gia' fatto: dichiarato
            rec['sito'] = {'esito': 'errore', 'motivo': 'durante «%s»: %s' % (stato['passo'], _motivo_eccezione(exc))}
        if rec['sito'].get('esito') in ('variante_aggiunta', 'aggiornato'):
            run = _run_filing(servizio, ticker, as_of=as_of, stato=stato)
            rec['run_filing_sito'] = run
            rec['run_filing'] = run
    if run.get('status') in ('ok', 'parziale') and run.get('id') is not None:
        ultimo, atteso = run.get('ultimo_periodo'), periodo_atteso(as_of)
        # «Aggiornato» = run Filing eseguito ora; se l'archivio resta sotto il periodo atteso lo si DICHIARA.
        rec['al_periodo_atteso'] = bool(ultimo and ultimo >= atteso)
        rec.update(stato='aggiornato', motivo=('profilo attivato dalla Trade Idea del %s e ' % as_of
                   if rec['azione'] == 'attivazione' else '') + 'run Filing %s (id %s): ultimo periodo %s'
                   % (run['status'], run['id'], ultimo or 'n.d.')
                   + ('' if rec['al_periodo_atteso'] else ' (SOTTO il periodo atteso %s: documento piu\' '
                      'recente non trovato dalle fonti del profilo)' % atteso)
                   + ('; motivi: ' + str(run['reason'])[:200] if run.get('reason') else '')
                   + ('; sito dell\'emittente: ' + str(rec['sito'].get('motivo'))[:300] if rec.get('sito') else ''))
    else:
        rec.update(stato='non_aggiornato', motivo='run Filing %s: %s' % (run.get('status'), str(run.get('reason'))[:300])
                   + ('; sito dell\'emittente: ' + str(rec['sito'].get('motivo'))[:300] if rec.get('sito') else ''))


def passi_predefiniti():
    """Passi extra della fase (cascata di freschezza dell'agente FRESCHEZZA); assente = dichiarato."""
    try:
        from bellomberg.market_data.freschezza_trimestrale import passo_freschezza
    except Exception as exc:
        motivo = 'passo non disponibile: ' + type(exc).__name__

        def passo_assente(ticker, **_kw):
            return {'stato': 'non_disponibile', 'motivo': motivo}
        return [('freschezza', passo_assente)]
    return [('freschezza', passo_freschezza)]


def passi_isolati():
    """Run con DB alternativo (isolata): mai fonti vive implicite per i passi extra, lacuna dichiarata."""
    def non_eseguito(ticker, **_kw):
        return {'stato': 'non_eseguito',
                'motivo': 'DB alternativo: fonti vive dei passi extra non fornite (isolamento)'}
    return [('freschezza', non_eseguito)]


def _fermato_dal_pm(stop_fn):
    if stop_fn is None:
        return False
    try:
        return bool(stop_fn())
    except Exception:
        return False          # stop illeggibile: decide il controllo della run subito dopo la fase


def _in_thread(nome, corpo, scadenza, stop_fn=None, fermo=None):
    """Esegue ``corpo()`` in un thread daemon; (finito, eccezione, fermato_dal_pm) entro la scadenza.

    Lo stop del PM e' ascoltato ogni mezzo secondo (R-FASE B4). Scadenza o stop: ``fermo`` viene alzato, il
    thread chiude il passo in corso e non ne avvia altri (R-FASE M4)."""
    esito = {}

    def bersaglio():
        try:
            corpo()
        except BaseException as exc:          # anche SystemExit/KeyboardInterrupt nel thread: dichiarati
            esito['eccezione'] = exc
    filo = threading.Thread(target=bersaglio, name=nome, daemon=True)
    filo.start()
    while filo.is_alive():
        resto = scadenza - time.monotonic()
        if resto <= 0:
            break
        filo.join(min(0.5, resto))
        if filo.is_alive() and _fermato_dal_pm(stop_fn):
            if fermo is not None:
                fermo.set()
            return False, None, True
    if filo.is_alive() and fermo is not None:
        fermo.set()
    return not filo.is_alive(), esito.get('eccezione'), False


def esegui_fase(ticker, *, as_of, nome=None, servizio_factory, registro_factory=None, pref_path=None,
                preferenze_predefinite=True, tempo_max_s=None, riserva_passi_s=None,
                passi_extra=None, orologio=time.monotonic, stop_fn=None):
    """Fase documenti di UN titolo con tempo massimo duro. Mai solleva: ritorna il record dichiarato."""
    tempo_max_s = TEMPO_MAX_S if tempo_max_s is None else tempo_max_s
    riserva_passi_s = min(RISERVA_PASSI_S if riserva_passi_s is None else riserva_passi_s, tempo_max_s)
    inizio = orologio()
    scadenza = inizio + tempo_max_s
    rec = {'contract': CONTRATTO, 'ticker': ticker, 'as_of': as_of, 'tempo_max_s': tempo_max_s,
           'riserva_passi_s': riserva_passi_s, 'regola_freschezza': REGOLA_FRESCHEZZA,
           'stato': 'non_aggiornato', 'motivo': 'fase documenti non conclusa', 'azione': None, 'passi': {},
           'cutoff': {'as_of': as_of, 'regola': CUTOFF}}
    stato = {'record': rec, 'passo': 'avvio', 'fermo': threading.Event()}
    try:
        try:
            registro = (registro_factory or _registro_predefinito)()
        except Exception as exc:
            registro = {'origine': 'illeggibile', 'veicoli': {}, 'motivo': type(exc).__name__}
        finito, exc, dal_pm = _in_thread('trade-idea-filing-' + ticker, lambda: _fase_filing(
            ticker, as_of=as_of, nome=nome, servizio_factory=servizio_factory, registro=registro,
            pref_path=pref_path, preferenze_predefinite=preferenze_predefinite, stato=stato),
            max(inizio, scadenza - riserva_passi_s), stop_fn=stop_fn, fermo=stato['fermo'])
        if not finito:
            # Il thread appeso continua a scrivere nel SUO record: la run tiene una copia staccata.
            passo_in_corso = stato['passo']
            try:
                rec = deepcopy(rec)
            except Exception:       # copia durante una scrittura del thread: record minimo dichiarato
                rec = {k: rec.get(k) for k in ('contract', 'ticker', 'as_of', 'tempo_max_s', 'riserva_passi_s',
                                               'regola_freschezza', 'azione')}
                rec['passi'] = {}
            if dal_pm:
                rec.update(stato='non_aggiornato', fermato_dal_pm=True,
                           motivo='fase documenti interrotta: stop del PM durante «%s» (il passo in corso si chiude '
                                  'in background, nessun passo nuovo)' % passo_in_corso)
            else:
                rec.update(stato='non_aggiornato', tempo_scaduto=True,
                           motivo=NON_IN_TEMPO % (tempo_max_s - riserva_passi_s, passo_in_corso))
        elif exc is not None:
            rec.update(stato='non_aggiornato', motivo='fase documenti in errore durante «%s»: %s'
                       % (stato['passo'], _motivo_eccezione(exc)))
        istantanea = _json(rec)
        for nome_passo, passo in (passi_predefiniti() if passi_extra is None else passi_extra):
            if rec.get('fermato_dal_pm') or _fermato_dal_pm(stop_fn):
                rec['passi'][nome_passo] = {'stato': 'non_eseguito', 'motivo': 'stop del PM durante la fase documenti'}
                continue
            if orologio() >= scadenza:
                rec['passi'][nome_passo] = {'stato': 'tempo_scaduto', 'motivo': 'tempo della fase documenti esaurito'}
                continue
            uscita = {}

            def corpo(passo=passo, uscita=uscita):
                uscita['esito'] = passo(ticker, as_of=as_of, scadenza_monotonic=scadenza, contesto=istantanea)
            finito, exc, dal_pm = _in_thread('trade-idea-passo-' + nome_passo, corpo, scadenza, stop_fn=stop_fn)
            if dal_pm:
                rec['passi'][nome_passo] = {'stato': 'non_eseguito', 'motivo': 'stop del PM durante il passo'}
            elif not finito:
                rec['passi'][nome_passo] = {'stato': 'tempo_scaduto',
                                            'motivo': 'passo oltre il tempo massimo della fase (%d s)' % tempo_max_s}
            elif exc is not None:
                rec['passi'][nome_passo] = {'stato': 'errore', 'motivo': _motivo_eccezione(exc)}
            else:
                rec['passi'][nome_passo] = uscita.get('esito') if isinstance(uscita.get('esito'), dict) else {
                    'stato': 'errore', 'motivo': 'esito del passo non e\' un dizionario'}
    except Exception as exc:
        rec.update(stato='non_aggiornato', motivo='fase documenti non eseguita: ' + _motivo_eccezione(exc))
    rec['durata_s'] = round(orologio() - inizio, 1)
    try:
        return _json(rec)
    except Exception as exc:
        return {'contract': CONTRATTO, 'ticker': ticker, 'as_of': as_of, 'stato': 'non_aggiornato',
                'motivo': 'esito della fase non serializzabile: ' + type(exc).__name__, 'passi': {}}


# ------------------------------------------------------------------ aggancio alla Trade Idea

def fase_documenti_trade_idea(bb, *, as_of, servizio_factory, nome=None, preferenze_predefinite=True,
                              registro_factory=None, tempo_max_s=None, passi_extra=None, log=None, **kw):
    """Passo pre-R0 della Trade Idea, PRIMA del ponte. Ripresa: record gia' nei dati della run = riuso.

    Ricerca gia' sigillata o ponte gia' ammesso (run del codice precedente): nessuna fase, None.
    Mai solleva."""
    try:
        with bb._lock:
            esistente = bb.data.get(CHIAVE_BOARD)
            gia_fatto = bb.data.get('_research_thesis') is not None or bb.data.get('_filing_bridge') is not None
        if esistente is not None:
            if log is not None:
                log('  Fase documenti (Trade Idea): ripresa, esito riusato (%s)' % esistente.get('stato'))
            return esistente
        if gia_fatto:
            return None
        ticker = getattr(bb, 'target_ticker', None)
        if not isinstance(ticker, str) or not ticker:
            record = {'contract': CONTRATTO, 'ticker': None, 'as_of': as_of, 'stato': 'non_aggiornato',
                      'motivo': 'fase documenti non eseguita: titolo della Trade Idea assente', 'passi': {}}
        else:
            record = esegui_fase(ticker, as_of=as_of, nome=nome, servizio_factory=servizio_factory,
                                 registro_factory=registro_factory, preferenze_predefinite=preferenze_predefinite,
                                 tempo_max_s=tempo_max_s, passi_extra=passi_extra, **kw)
    except Exception as exc:
        record = {'contract': CONTRATTO, 'ticker': getattr(bb, 'target_ticker', None), 'as_of': as_of,
                  'stato': 'non_aggiornato', 'motivo': 'fase documenti non eseguita: ' + _motivo_eccezione(exc),
                  'passi': {}}
    try:
        with bb._lock:
            bb.data[CHIAVE_BOARD] = deepcopy(record)
    except Exception:
        pass
    if log is not None:
        log('  Fase documenti (Trade Idea): %s - %s' % (record.get('stato'), str(record.get('motivo'))[:200]))
        if record.get('profilo_creato'):
            log('  [i] ' + record['ticker'] + ': ' + record['profilo_creato'])
    return record


def aggiornamento_per_ponte(record):
    """{stato, motivo} per la ricevuta del ponte (``aggiornamento_pre_run``), None senza fase."""
    if not isinstance(record, dict):
        return None
    stato = 'aggiornato' if record.get('stato') in ('aggiornato', 'non_applicabile') else 'non_aggiornato'
    return {'stato': stato, 'motivo': 'Trade Idea, fase documenti prima della run: ' + str(record.get('motivo'))}


def registro_con_tipo(record, registro_factory=None):
    """Registro dei veicoli per il ponte con il tipo trovato da Yahoo per un titolo fuori registro.

    Cosi' un ETF/crypto nuovo e' «non applicabile» anche nel ponte (e il dossier lo dice), invece di
    «nessun profilo». Il registro vero non viene mai scritto."""
    def fabbrica():
        registro = (registro_factory or _registro_predefinito)()
        tipo = (record or {}).get('tipo_strumento') or {}
        ticker = (record or {}).get('ticker')
        from bellomberg.market_data.filing_identita import TIPI_NON_OPERATIVI
        if (isinstance(registro, dict) and ticker and tipo.get('tipo') in TIPI_NON_OPERATIVI
                and str(tipo.get('origine') or '').startswith('Yahoo') and ticker not in (registro.get('veicoli') or {})):
            registro = {**registro, 'veicoli': {**(registro.get('veicoli') or {}),
                                                ticker: {'tipo': tipo['tipo'], 'provenienza': tipo['origine']}}}
        return registro
    return fabbrica
