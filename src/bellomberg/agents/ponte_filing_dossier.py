"""Ponte archivio Filing -> dossier sigillato della run settimanale (modalita' ricerca).

Prima di R0, per ogni titolo in perimetro, i documenti che l'archivio Filing ha GIA'
scaricato e verificato (SEC 10-K/10-Q/20-F/6-K, ESEF, PDF dal sito dell'emittente)
entrano nel dossier con una ricevuta: nessuna rete, nessun modello, nessun costo.

Regole (PM 05/10):
- niente fallback silenziosi: ogni titolo ha un esito dichiarato (ammesso, nessun profilo,
  nessun run riuscito, nessun documento verificato, archivio in errore); ogni documento
  scartato o non selezionato porta il suo motivo;
- l'ultimo run Filing in errore o non aggiornato in tempo resta dichiarato; se si usano i
  documenti di un run riuscito precedente, data del run e periodo del documento sono scritti;
- determinismo: la selezione si fa UNA volta e si persiste in un checkpoint immutabile della
  run (``filing-dossier-bridge``); la ripresa riusa il checkpoint, mai ricalcola dal DB Filing;
- compatibilita': una run senza checkpoint del ponte (codice precedente) ha il dossier di prima,
  byte per byte. Il canale e' separato dalla ResearchSession (che richiede identita'/profilo
  dalla rete e citazioni PM): i documenti portano origin 'filing_archive' e la loro base.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

CONTRATTO = 'filing-dossier-bridge/1'
CHECKPOINT = 'filing-dossier-bridge'
CHIAVE_BOARD = '_filing_bridge'
REGOLA_SELEZIONE = ("ultimo documento verificato per periodo (poi data di deposito) e, se non e' "
                    "annuale, l'ultimo annuale verificato; gli altri verificati restano dichiarati come non selezionati")
# Soglie di eta' decise dal PM (soglia PM 06/10): oltre queste un documento resta AMMESSO ma
# e' dichiarato «non corrente» (flag strutturato + issue nel dossier e nell'indice del prompt).
SOGLIA_ANNUALE_MESI = 16        # soglia PM 06/10: annuale non corrente oltre 16 mesi dalla fine del periodo
SOGLIA_INFRANNUALE_MESI = 5     # soglia PM 06/10: semestrale/trimestrale non corrente oltre 5 mesi
SOGLIE_ETA = {'annuale_mesi': SOGLIA_ANNUALE_MESI, 'infrannuale_mesi': SOGLIA_INFRANNUALE_MESI,
              'fonte': 'soglia PM 06/10',
              'convenzione': ("mesi di calendario; periodo chiuso a fine mese -> scadenza a fine mese "
                              "(EOMONTH: 30/06 + 16 mesi = 31/10); altrimenti stesso giorno, limitato a fine mese; "
                              "il giorno di scadenza e' ancora corrente")}
_TIPI_PERIODO = {'annuale': 'annuale', 'semestrale': 'semestrale', 'trimestrale': 'trimestrale',
                 'nove_mesi': 'nove_mesi'}
_FORMA_PERIODO = {'10-K': 'annuale', '10-K/A': 'annuale', '20-F': 'annuale', '20-F/A': 'annuale',
                  '40-F': 'annuale', '10-Q': 'trimestrale', '10-Q/A': 'trimestrale'}
_CAMPI_CANDIDATO = ('url', 'path', 'sha256', 'filed_date', 'report_date', 'form', 'fonte', 'origine',
                    'metadati', 'filing_verification')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


def _digest(value):
    return sha256(_json(value).encode('utf-8')).hexdigest()


def _sigilla(corpo):
    return {**corpo, 'sha256': _digest(corpo)}


def base_origine(candidato):
    """Etichetta d'origine dichiarata del documento (mai mescolare sito e archivio ufficiale)."""
    fonte = candidato.get('fonte')
    origine = candidato.get('origine') or ((candidato.get('metadati') or {}).get('origine'))
    if fonte == 'IR' or origine == 'sito_emittente':
        return 'sito_emittente_non_archivio_ufficiale'
    if fonte == 'SEC EDGAR':
        return 'archivio_ufficiale_sec_edgar'
    if fonte == 'ESEF':
        return 'pacchetto_esef_dal_sito_emittente' if origine == 'sito' else 'repository_esef_filings_xbrl'
    return 'fonte_filing_non_classificata:' + str(fonte)


def _periodo(candidato):
    return (candidato.get('report_date') or (candidato.get('metadati') or {}).get('periodo_fine')
            or candidato.get('period_end'))


def _ordine(candidato):
    return (str(_periodo(candidato) or ''), str(candidato.get('filed_date') or ''),
            str(candidato.get('url') or ''), str(candidato.get('sha256') or ''))


def _seleziona(candidati):
    """(selezionati, non_selezionati, esclusi) in ordine deterministico."""
    validi, esclusi = [], []
    for c in candidati if isinstance(candidati, list) else []:
        if not isinstance(c, dict):
            esclusi.append({'url': None, 'sha256': None, 'motivo': 'riga candidato non strutturata'})
            continue
        riga = {k: deepcopy(c[k]) for k in _CAMPI_CANDIDATO if k in c}
        if c.get('stato') != 'verificato':
            esclusi.append({'url': c.get('url'), 'sha256': c.get('sha256'), 'periodo': _periodo(c),
                            'motivo': 'non verificato dall\'archivio Filing (stato ' + str(c.get('stato')) + ')'
                            + (': ' + '; '.join(map(str, c.get('motivi') or []))[:300] if c.get('motivi') else '')})
        elif c.get('motivi'):
            esclusi.append({'url': c.get('url'), 'sha256': c.get('sha256'), 'periodo': _periodo(c),
                            'motivo': 'verificato con riserve: ' + '; '.join(map(str, c['motivi']))[:300]})
        elif not _periodo(c) or not c.get('url') or not c.get('path') or not c.get('sha256'):
            esclusi.append({'url': c.get('url'), 'sha256': c.get('sha256'), 'periodo': _periodo(c),
                            'motivo': 'periodo, URL, percorso o impronta assenti nella riga dell\'archivio'})
        else:
            validi.append(riga)
    validi.sort(key=_ordine, reverse=True)
    if not validi:
        return [], [], esclusi
    scelti = [validi[0]]
    if (validi[0].get('metadati') or {}).get('tipo') != 'annuale':
        annuale = next((v for v in validi[1:] if (v.get('metadati') or {}).get('tipo') == 'annuale'), None)
        if annuale is not None:
            scelti.append(annuale)
    altri = [{'url': v['url'], 'sha256': v['sha256'], 'periodo': _periodo(v),
              'motivo': 'verificato ma non selezionato (regola del ponte)'} for v in validi if v not in scelti]
    return scelti, altri, esclusi


def _tipo_strumento(registro, ticker):
    """(tipo dal registro dei veicoli o None, frase dichiarata quando il tipo non e' noto)."""
    if not isinstance(registro, dict):
        return None, 'tipo strumento n.d.: registro veicoli non consultato'
    if registro.get('origine') in ('assente', 'illeggibile'):
        return None, 'tipo strumento n.d.: registro veicoli ' + str(registro['origine'])
    voce = (registro.get('veicoli') or {}).get(ticker)
    if not voce or not voce.get('tipo'):
        return None, 'tipo strumento n.d.: titolo assente dal registro veicoli'
    return voce['tipo'], None


def _verificati_non_usati(filing_store, ticker, tipo):
    """Conteggio dichiarato dei documenti verificati di uno strumento non societario (mai ammessi)."""
    try:
        if filing_store.get_profile(ticker) is None:
            return {'conteggio': 0, 'motivo': 'archivio Filing: nessun profilo per questo titolo', 'documenti': []}
        riuscito = filing_store.ultimo_run_completo(ticker, senza_errori=True)
    except Exception as exc:
        return {'conteggio': None, 'motivo': 'archivio Filing illeggibile: ' + type(exc).__name__, 'documenti': []}
    candidati = ((riuscito or {}).get('result') or {}).get('candidati') if isinstance(riuscito, dict) else None
    righe = [c for c in candidati or [] if isinstance(c, dict) and c.get('stato') == 'verificato' and not c.get('motivi')]
    righe.sort(key=_ordine, reverse=True)
    return {'conteggio': len(righe), 'motivo': 'verificati ma non usati: strumento ' + tipo,
            **({'run_filing': riuscito.get('id')} if riuscito else {}),
            'documenti': [{'url': c.get('url'), 'sha256': c.get('sha256'), 'periodo': _periodo(c)} for c in righe]}


def selezione_ponte(filing_store, tickers, *, as_of, esiti=None, archive_root, registro_veicoli=None):
    """Funzione pura (sola lettura dell'archivio Filing): esito dichiarato per ogni titolo.

    ``esiti`` = esiti dell'aggiornamento pre-run ({ticker: {stato, motivo}}) o None se non avviato.
    ``registro_veicoli`` = esito di classificazione.carica_veicoli (None = non consultato, dichiarato):
    ETF, ETN, fondi chiusi, materie prime e crypto (TIPI_NON_OPERATIVI del Filing) sono «non applicabile».
    """
    from bellomberg.market_data.filing_identita import TIPI_NON_OPERATIVI
    voci = {}
    for ticker in sorted(set(tickers)):
        voce = {'ticker': ticker, 'documenti': [], 'non_selezionati': [], 'esclusi': [], 'motivi': []}
        agg = (esiti or {}).get(ticker) if isinstance(esiti, dict) else None
        voce['aggiornamento_pre_run'] = (deepcopy(agg) if isinstance(agg, dict) else
            {'stato': 'non_misurato', 'motivo': 'aggiornamento pre-run non avviato o titolo non seguito'})
        tipo, tipo_nd = _tipo_strumento(registro_veicoli, ticker)
        voce['tipo_strumento'] = tipo
        if tipo in TIPI_NON_OPERATIVI:
            # Decisione PM 06/10: strumento non societario, il bilancio societario non e' il documento giusto.
            # I documenti verificati che l'archivio avesse comunque restano DICHIARATI (non ammessi).
            voce.update(esito='non_applicabile', motivi=['bilancio societario non applicabile: ' + tipo],
                        verificati_non_usati=_verificati_non_usati(filing_store, ticker, tipo))
            voci[ticker] = voce
            continue
        try:
            profilo = filing_store.get_profile(ticker)
            if profilo is None:
                voce.update(esito='nessun_profilo', motivi=['archivio Filing: nessun profilo per questo titolo']
                            + ([tipo_nd] if tipo_nd else []))
                voci[ticker] = voce
                continue
            voce['profilo_attivo'] = bool(profilo.get('enabled'))
            ultimo = filing_store.ultimo_run_completo(ticker)
            riuscito = filing_store.ultimo_run_completo(ticker, senza_errori=True)
        except Exception as exc:
            voce.update(esito='archivio_in_errore',
                        motivi=['archivio Filing illeggibile: ' + type(exc).__name__ + ': ' + str(exc)[:200]])
            voci[ticker] = voce
            continue
        if ultimo is not None:
            voce['ultimo_run'] = {k: ultimo.get(k) for k in ('id', 'status', 'finished_at', 'reason')}
        if riuscito is None or not isinstance(riuscito.get('result'), dict):
            voce.update(esito='nessun_run_riuscito', motivi=['archivio Filing: nessun run concluso con documenti'
                        + ('; ultimo run in ' + str(ultimo.get('status')) + ': ' + str(ultimo.get('reason'))[:200]
                           if ultimo is not None else '')])
            voci[ticker] = voce
            continue
        voce['run_usato'] = {k: riuscito.get(k) for k in ('id', 'status', 'finished_at', 'reason')}
        if ultimo is not None and ultimo.get('id') != riuscito.get('id'):
            voce['motivi'].append('ultimo run Filing (id %s) in %s: %s; documenti dal run riuscito precedente '
                                  '(id %s, concluso il %s), NON dal controllo piu\' recente'
                                  % (ultimo.get('id'), ultimo.get('status'), str(ultimo.get('reason'))[:200],
                                     riuscito.get('id'), riuscito.get('finished_at')))
        if voce['aggiornamento_pre_run'].get('stato') == 'non_aggiornato':
            voce['motivi'].append('archivio NON AGGIORNATO per questa run: ' + str(
                voce['aggiornamento_pre_run'].get('motivo')) + '; documenti dal run Filing concluso il '
                + str(riuscito.get('finished_at')))
        risultato = riuscito['result']
        if riuscito.get('status') != 'ok' and riuscito.get('reason'):
            voce['motivi'].append('run Filing ' + str(riuscito.get('status')) + ': ' + str(riuscito['reason'])[:300])
        if isinstance(risultato.get('freschezza'), dict):
            voce['freschezza'] = {k: deepcopy(risultato['freschezza'].get(k)) for k in ('stato', 'ultimo_periodo', 'checked_at')}
        scelti, altri, esclusi = _seleziona(risultato.get('candidati'))
        ammessi = []
        for doc in scelti:
            doc['base_origine'] = base_origine(doc)
            doc['run_filing'] = riuscito.get('id')
            doc['run_concluso_il'] = riuscito.get('finished_at')
            doc['periodo'] = _periodo(doc)
            try:
                # Byte ed estrazione verificati UNA volta qui (pre-R0): il sigillo tiene solo le impronte.
                ammessi.append(_verifica(doc, archive_root, as_of))
            except (ValueError, TypeError, KeyError, OSError) as exc:
                esclusi.append({'url': doc.get('url'), 'sha256': doc.get('sha256'), 'periodo': doc.get('periodo'),
                                'motivo': "verifica all'ammissione fallita: " + str(exc)[:300]})
        voce.update(documenti=ammessi, non_selezionati=altri, esclusi=esclusi)
        voce['esito'] = ('ammesso' if ammessi else 'verifica_fallita' if scelti
                         else 'nessun_documento_verificato')
        if not scelti:
            voce['motivi'].append('archivio Filing: nessun documento verificato e completo nel run usato')
        elif not ammessi:
            voce['motivi'].append('archivio Filing: i documenti selezionati non superano la verifica dei byte')
        else:
            voce['periodo_piu_recente'] = ammessi[0]['periodo']
        voci[ticker] = voce
    registro = ({'origine': 'non_consultato', 'motivo': None} if not isinstance(registro_veicoli, dict) else
                {'origine': (registro_veicoli.get('origine') if registro_veicoli.get('origine') in ('assente', 'illeggibile')
                             else Path(str(registro_veicoli.get('origine'))).name), 'motivo': registro_veicoli.get('motivo')})
    return _sigilla({'contract': CONTRATTO, 'as_of': as_of, 'archive_root': str(Path(archive_root).resolve()),
                     'regola_selezione': REGOLA_SELEZIONE, 'soglie_eta': dict(SOGLIE_ETA),
                     'registro_veicoli': registro, 'stato': 'ok', 'tickers': voci})


def _piu_mesi(giorno, mesi):
    import calendar
    anno, mese = divmod(giorno.month - 1 + mesi, 12)
    anno, mese = giorno.year + anno, mese + 1
    ultimo = calendar.monthrange(anno, mese)[1]
    if giorno.day == calendar.monthrange(giorno.year, giorno.month)[1]:
        return date(anno, mese, ultimo)     # fine mese -> fine mese (convenzione in SOGLIE_ETA)
    return date(anno, mese, min(giorno.day, ultimo))


def _eta(periodo, as_of, *, tipo=None, form=None):
    """Eta' strutturata al cutoff: giorni dalla fine del periodo e chiusura dell'esercizio successivo.

    Nessuna soglia di giudizio: «esercizio successivo chiuso» e' un fatto di calendario (fine periodo
    + 1 anno <= cutoff), non un verdetto di obsolescenza (la soglia la decide il PM)."""
    fine, cutoff = date.fromisoformat(periodo), date.fromisoformat(as_of)
    try:
        omologo = fine.replace(year=fine.year + 1)
    except ValueError:      # 29 febbraio
        omologo = fine.replace(year=fine.year + 1, day=28)
    tipo_periodo = _TIPI_PERIODO.get(tipo) or _FORMA_PERIODO.get(form)
    soglia = (None if tipo_periodo is None else
              SOGLIA_ANNUALE_MESI if tipo_periodo == 'annuale' else SOGLIA_INFRANNUALE_MESI)
    fino_al = _piu_mesi(fine, soglia) if soglia is not None else None
    return {'periodo_fine': periodo, 'eta_giorni_al_cutoff': (cutoff - fine).days,
            'esercizio_successivo_chiuso': omologo <= cutoff,
            'fine_esercizio_successivo': omologo.isoformat(),
            'tipo_periodo': tipo_periodo, 'soglia_mesi': soglia,
            'corrente_fino_al': fino_al.isoformat() if fino_al else None,
            # None = tipo di periodo non noto: soglia PM non applicabile (dichiarato nel dossier)
            'corrente': None if fino_al is None else cutoff <= fino_al}


def _estrai(percorso, grezzo):
    from bellomberg.market_data.lettore_trimestrali import estrai_testo
    return estrai_testo(str(percorso), contenuto=grezzo)


def _leggi_byte(archive_root, relativo, atteso):
    radice = Path(archive_root).resolve()
    percorso = (radice / relativo).resolve()
    if not percorso.is_relative_to(radice):
        raise ValueError("percorso fuori dalla radice dell'archivio Filing")
    try:
        grezzo = percorso.read_bytes()
    except OSError as exc:
        raise ValueError('documento archiviato assente o illeggibile (' + type(exc).__name__ + ')') from exc
    if sha256(grezzo).hexdigest() != atteso:
        raise ValueError('impronta dei byte diversa da quella verificata (sha256 atteso ' + str(atteso) + ')')
    return percorso, grezzo


def _testo_verificato(doc, percorso, grezzo):
    estratto = _estrai(percorso, grezzo)
    testo = estratto.get('testo', '')
    if estratto.get('stato') not in ('ok', 'parziale') or not testo.strip():
        raise ValueError('testo non estraibile: ' + str(estratto.get('motivo')))
    if estratto.get('pagine_senza_testo'):
        raise ValueError('documento con pagine senza testo estraibile: ' + str(estratto['pagine_senza_testo'][:20]))
    if doc.get('filing_verification') is not None:
        from bellomberg.valuation.filing_pdf_evidence import verify_filing_pdf, SCHEMA
        if estratto.get('formato') != 'pdf':
            raise ValueError('verifica PDF del filing senza byte PDF originali')
        metadati = dict(doc.get('metadati') or {})
        metadati.update(report_start=metadati.get('periodo_inizio'), filing_verification=SCHEMA)
        verify_filing_pdf({'id': doc['sha256'], 'url': doc['url'], 'document_sha256': doc['sha256'], 'text': testo,
            'metadata': metadati, 'filing_verification': doc['filing_verification'],
            'page_references': [{'pagina': p['pagina'], 'inizio': p['inizio'], 'fine': p['fine'],
                'sha256': sha256(p['testo'].encode('utf-8')).hexdigest()} for p in estratto['riferimenti']]})
    return testo, estratto


def _disponibilita(doc):
    if doc.get('filed_date'):
        return {'published_at': doc['filed_date'], 'availability_basis': 'publication',
                'available_at': doc['filed_date']}
    concluso = datetime.fromisoformat(doc['run_concluso_il']).astimezone(timezone.utc)
    return {'published_at': None, 'availability_basis': 'observed_download',
            'available_at': concluso.date().isoformat(),
            'retrieval': {'url': doc['url'], 'document_sha256': doc['id'], 'retrieved_at': concluso.isoformat()}}


def _verifica(doc, archive_root, as_of):
    """Ricevuta del documento SENZA testo: impronte di byte e testo, periodo, eta', origine."""
    radice = Path(archive_root).resolve()
    assoluto = Path(doc['path']).resolve()
    if not assoluto.is_relative_to(radice):
        raise ValueError("percorso fuori dalla radice dell'archivio Filing")
    relativo = assoluto.relative_to(radice).as_posix()
    percorso, grezzo = _leggi_byte(radice, relativo, doc['sha256'])
    testo, estratto = _testo_verificato(doc, percorso, grezzo)
    ricevuta = {'id': doc['sha256'], 'url': doc['url'], 'path_relativo': relativo,
        'text_sha256': sha256(testo.encode('utf-8')).hexdigest(), 'text_chars': len(testo),
        'formato': estratto.get('formato'), 'pagine': estratto.get('pagine'),
        'filed_date': doc.get('filed_date'), 'form': doc.get('form'), 'fonte': doc.get('fonte'),
        'metadati': deepcopy(doc.get('metadati') or {}), 'periodo': doc['periodo'],
        'base_origine': doc['base_origine'], 'run_filing': doc['run_filing'],
        'run_concluso_il': doc['run_concluso_il'],
        **_eta(doc['periodo'], as_of, tipo=(doc.get('metadati') or {}).get('tipo'), form=doc.get('form'))}
    if doc.get('filing_verification') is not None:
        ricevuta['filing_verification'] = deepcopy(doc['filing_verification'])
    ricevuta.update(_disponibilita(ricevuta))
    from bellomberg.valuation.document_evidence import source_dates
    source_dates({'published_at': ricevuta['published_at'], 'availability_basis': ricevuta['availability_basis'],
                  'url': ricevuta['url'], 'document_sha256': ricevuta['id'],
                  **({'retrieval': ricevuta['retrieval']} if 'retrieval' in ricevuta else {})},
                 date.fromisoformat(as_of))      # data oltre il cutoff = motivo dichiarato
    return ricevuta


def _ricevuta_errore(as_of, tickers, stato, motivo, registro=None):
    from bellomberg.market_data.filing_identita import TIPI_NON_OPERATIVI
    voci = {}
    for t in sorted(set(tickers)):
        tipo = _tipo_strumento(registro, t)[0] if registro is not None else None
        if tipo in TIPI_NON_OPERATIVI:
            # L'ETF resta «non applicabile» anche con l'archivio Filing guasto (non dipende dall'archivio).
            voci[t] = {'ticker': t, 'esito': 'non_applicabile', 'tipo_strumento': tipo, 'documenti': [],
                       'non_selezionati': [], 'esclusi': [], 'motivi': ['bilancio societario non applicabile: ' + tipo]}
        else:
            voci[t] = {'ticker': t, 'esito': 'archivio_in_errore' if stato == 'errore' else 'ponte_non_eseguito',
                       'documenti': [], 'non_selezionati': [], 'esclusi': [], 'motivi': [motivo]}
    return _sigilla({'contract': CONTRATTO, 'as_of': as_of, 'archive_root': None,
        'regola_selezione': REGOLA_SELEZIONE, 'stato': stato, 'motivo': motivo, 'tickers': voci})


_NON_LETTI = object()


def _registro_predefinito():
    from bellomberg.storage.classificazione import carica_veicoli
    return carica_veicoli()


def ammetti_archivio_filing(bb, store, *, esiti=_NON_LETTI, aggiornamento=None, servizio_factory=None, log=None,
                            registro_factory=None):
    """Passo pre-R0: persiste (una volta) la ricevuta del ponte e la aggancia alla blackboard.

    ``esiti`` = esiti dell'aggiornamento pre-run gia' letti per il contesto Filing (letti UNA volta);
    ``aggiornamento`` si interroga solo se gli esiti non sono stati letti. Mai solleva: ogni guasto
    diventa una ricevuta dichiarata («archivio in errore» o «ponte non eseguito»)."""
    tickers = sorted(getattr(bb, 'research_tickers', ()) or ())
    try:
        esistente = store.get(CHECKPOINT)
        as_of = store.context['research_started_at'][:10]
    except Exception as exc:
        return ponte_non_eseguito(bb, exc, log=log)
    if esistente is not None:
        record = esistente     # ripresa: mai ricalcolo dal DB Filing
    else:
        try:
            registro = (registro_factory or _registro_predefinito)()
        except Exception as exc:
            registro = {'origine': 'illeggibile', 'veicoli': {}, 'motivo': type(exc).__name__}
        try:
            if servizio_factory is None:
                from bellomberg.api.filing_routes import default_service as servizio_factory
            servizio = servizio_factory()
            if esiti is _NON_LETTI:
                esiti = None
                if aggiornamento is not None:
                    try:
                        esiti = aggiornamento.esiti()
                    except Exception as exc:
                        esiti = {t: {'stato': 'non_aggiornato', 'motivo': 'esiti non disponibili: ' + type(exc).__name__}
                                 for t in tickers}
            record = selezione_ponte(servizio.store, tickers, as_of=as_of, esiti=esiti,
                                     archive_root=servizio.archive_root, registro_veicoli=registro)
        except Exception as exc:
            record = _ricevuta_errore(as_of, tickers, 'errore',
                'archivio Filing non disponibile: ' + type(exc).__name__ + ': ' + str(exc)[:200], registro)
        try:
            with bb._lock:
                bb.data[CHIAVE_BOARD] = deepcopy(record)
            store.complete(CHECKPOINT, record, bb)
        except Exception as exc:
            return ponte_non_eseguito(bb, exc, log=log)
    with bb._lock:
        bb.data[CHIAVE_BOARD] = deepcopy(record)
    if log is not None:
        n = {t: len(v.get('documenti') or []) for t, v in (record.get('tickers') or {}).items()}
        log('  Ponte Filing -> dossier: %d documenti su %d titoli (%s)' % (
            sum(n.values()), sum(1 for x in n.values() if x), record.get('stato')))
    return record


def ponte_non_eseguito(bb, exc, *, log=None):
    """Guasto del ponte: lacuna dichiarata per ogni titolo, la run prosegue (non persistita: la ripresa ritenta)."""
    motivo = 'ponte non eseguito: ' + type(exc).__name__
    try:
        as_of = bb.weekly_store.context['research_started_at'][:10]
    except Exception:
        as_of = None
    record = _ricevuta_errore(as_of, getattr(bb, 'research_tickers', ()) or (), 'non_eseguito', motivo)
    with bb._lock:
        bb.data[CHIAVE_BOARD] = record
    if log is not None:
        log('  [!] Ponte Filing -> dossier: ' + motivo + ' (lacuna dichiarata nel dossier)')
    return record


def _ricevuta_board(board):
    record = (getattr(board, 'data', {}) or {}).get(CHIAVE_BOARD)
    if record is None:
        return None
    corpo = {k: v for k, v in record.items() if k != 'sha256'} if isinstance(record, dict) else None
    if corpo is None or record.get('contract') != CONTRATTO or record.get('sha256') != _digest(corpo):
        raise ValueError('Ricevuta del ponte Filing modificata o di contratto diverso')
    return record


def documenti_ponte(board, ticker):
    """(riferimenti ai documenti, dichiarazione) dalla SOLA ricevuta: nessuna lettura dell'archivio vivo.

    None se la run non ha il ponte (codice precedente) o il titolo e' fuori perimetro."""
    record = _ricevuta_board(board)
    if record is None:
        return None
    voce = (record.get('tickers') or {}).get(ticker)
    if voce is None:
        return None
    documenti = []
    for doc in voce.get('documenti') or []:
        metadati = dict(doc.get('metadati') or {})
        metadati.setdefault('report_date', doc['periodo'])
        for chiave in ('form', 'fonte'):
            if doc.get(chiave) and not metadati.get(chiave):
                metadati[chiave] = doc[chiave]
        metadati['filing_bridge'] = {'contract': CONTRATTO, 'base_origine': doc['base_origine'],
            'run_filing': doc['run_filing'], 'run_concluso_il': doc['run_concluso_il'], 'periodo': doc['periodo'],
            'path_relativo': doc['path_relativo'], 'formato': doc.get('formato'), 'pagine': doc.get('pagine'),
            'text_chars': doc['text_chars'], 'periodo_fine': doc['periodo_fine'],
            'eta_giorni_al_cutoff': doc['eta_giorni_al_cutoff'],
            'esercizio_successivo_chiuso': doc['esercizio_successivo_chiuso'],
            'fine_esercizio_successivo': doc['fine_esercizio_successivo'],
            **{k: doc[k] for k in ('tipo_periodo', 'soglia_mesi', 'corrente_fino_al', 'corrente') if k in doc},
            'data_pubblicazione': doc.get('filed_date') or ("non nota (disponibilita' = documento osservato "
                                                            "nell'archivio alla fine del run Filing)"),
            'testo': 'per riferimento: read_company_dossier section=document rilegge e riverifica i byte'}
        documenti.append({'id': doc['id'], 'url': doc['url'], 'published_at': doc['published_at'],
            'available_at': doc['available_at'], 'availability_basis': doc['availability_basis'],
            'sha256': doc['text_sha256'], 'document_sha256': doc['id'], 'metadata': metadati})
    dichiarazione = {'contract': CONTRATTO, 'receipt_sha256': record['sha256'],
        'entry_sha256': _digest(voce), 'stato_archivio': record.get('stato'),
        **({'motivo_archivio': record['motivo']} if record.get('motivo') else {}),
        'esito': voce.get('esito'), 'regola_selezione': record.get('regola_selezione'),
        **({'soglie_eta': deepcopy(record['soglie_eta'])} if 'soglie_eta' in record else {}),
        **({'tipo_strumento': voce['tipo_strumento']} if 'tipo_strumento' in voce else {}),
        **({'verificati_non_usati': deepcopy(voce['verificati_non_usati'])} if 'verificati_non_usati' in voce else {}),
        'aggiornamento_pre_run': deepcopy(voce.get('aggiornamento_pre_run')),
        'run_usato': deepcopy(voce.get('run_usato')), 'ultimo_run': deepcopy(voce.get('ultimo_run')),
        'periodo_piu_recente': voce.get('periodo_piu_recente'), 'freschezza': deepcopy(voce.get('freschezza')),
        'documenti': [{'id': d['id'], 'periodo': d['metadata']['filing_bridge']['periodo'],
                       'eta_giorni_al_cutoff': d['metadata']['filing_bridge']['eta_giorni_al_cutoff'],
                       'base_origine': d['metadata']['filing_bridge']['base_origine']} for d in documenti],
        'motivi': list(voce.get('motivi') or []), 'esclusi': deepcopy(voce.get('esclusi') or []),
        'non_selezionati': deepcopy(voce.get('non_selezionati') or [])}
    return documenti, dichiarazione


class DocumentoPonteNonDisponibile(ValueError):
    """Byte del ponte cambiati o spariti DOPO il sigillo: lacuna del documento, non blocco della run."""


_TESTI = {}


def testo_documento(board, documento):
    """Testo di un documento del ponte, riletto e riverificato (byte e testo) al momento della lettura."""
    record = _ricevuta_board(board)
    ponte = (documento.get('metadata') or {}).get('filing_bridge') or {}
    if record is None or not record.get('archive_root') or not ponte.get('path_relativo'):
        raise DocumentoPonteNonDisponibile('ricevuta del ponte Filing assente per questo documento')
    chiave = documento['document_sha256']
    try:
        percorso, grezzo = _leggi_byte(record['archive_root'], ponte['path_relativo'], chiave)
        testo = _TESTI.get(chiave)
        if testo is None:
            sorgente = next((d for v in (record.get('tickers') or {}).values() for d in v.get('documenti') or []
                             if d['id'] == chiave), {})
            testo, _ = _testo_verificato({'sha256': chiave, 'url': documento['url'],
                'metadati': sorgente.get('metadati'), 'filing_verification': sorgente.get('filing_verification')},
                percorso, grezzo)
        if sha256(testo.encode('utf-8')).hexdigest() != documento['sha256']:
            raise ValueError('testo estratto diverso da quello sigillato (sha256 atteso ' + documento['sha256'] + ')')
    except ValueError as exc:
        raise DocumentoPonteNonDisponibile("documento archiviato non piu' disponibile o cambiato dopo il sigillo: "
                                           + str(exc)[:300]) from exc
    if len(_TESTI) >= 8:
        _TESTI.clear()
    _TESTI[chiave] = testo
    return testo


_TRONCA = 200


def indice_compatto(dossier):
    """Vista per il prompt (R2, Red Team, Capo) di un dossier col ponte: solo indice, mai testo o percorsi."""
    ponte = dossier.get('filing_bridge') or {}
    codici = {i.get('code') for i in dossier.get('issues') or []}
    non_correnti = {i.get('document_id') for i in dossier.get('issues') or []
                    if i.get('code') == 'filing_archive_documento_non_corrente'}
    lacune = []
    for issue in dossier.get('issues') or []:
        if issue.get('code') == 'unavailable' and any(str(c).startswith('filing_archive_') for c in codici):
            continue    # la stessa lacuna e' gia' detta dal codice dell'archivio Filing
        if (issue.get('code') == 'filing_archive_esercizio_successivo_chiuso'
                and issue.get('document_id') in non_correnti):
            continue    # gia' detto dalla soglia PM sullo stesso documento
        motivo = str(issue.get('reason') or '')
        lacune.append({'code': issue.get('code'), 'reason': motivo if len(motivo) <= _TRONCA else
                       motivo[:_TRONCA] + ' [troncato: testo intero con read_company_dossier section=catalog]'})
    documenti = []
    for d in dossier.get('documents') or []:
        fb = (d.get('metadata') or {}).get('filing_bridge')
        if fb:
            documenti.append({'id': d['id'], 'tipo': d['metadata'].get('form') or d['metadata'].get('tipo'),
                'periodo': fb['periodo'], 'eta_giorni': fb['eta_giorni_al_cutoff'],
                'esercizio_successivo_chiuso': fb['esercizio_successivo_chiuso'],
                **({'corrente': fb['corrente']} if 'corrente' in fb else {}),
                'pubblicato': d.get('published_at') or 'n.d.', 'origine': fb['base_origine']})
        else:
            documenti.append({'id': d.get('id'), 'pubblicato': d.get('published_at') or 'n.d.',
                              'origine': 'acquisito_dal_desk'})
    out = {'ticker': dossier.get('ticker'), 'status': dossier.get('status'),
           'archivio_filing': ponte.get('esito'), 'documenti': documenti, 'lacune': lacune}
    if ponte.get('esclusi'):
        out['esclusi'] = len(ponte['esclusi'])
    if ponte.get('non_selezionati'):
        out['non_selezionati'] = len(ponte['non_selezionati'])
    return out
