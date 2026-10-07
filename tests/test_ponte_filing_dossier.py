"""V1-PONTE: archivio Filing -> dossier sigillato della weekly in modalita' ricerca.

Archivio Filing SINTETICO in tmp_path (ticker ZZTEST, QQSYN.MI, ...), documenti finti,
nessuna rete, nessuna AI. Le attese sono scritte a mano qui (oracolo fuori dal codice).
"""
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
import json
import sqlite3

import pytest

from bellomberg.agents import ponte_filing_dossier as ponte
from bellomberg.core.research_analysis import (RESEARCH_ANALYSIS_MODE, research_digest,
                                               research_reference, seal_research_thesis)
from bellomberg.storage.filing_store import FilingStore, ensure_schema

AS_OF_CONTEXT = '2030-01-15T09:00:00+00:00'
DESKS = ('macro', 'fundamentals')
BASE = {"lingua": "en", "perimetro": "consolidato", "sezioni": {}, "sezioni_intero": True,
        "verifica": {"lingua": "English", "tipo": "report", "perimetro": "consolidated"}}


def _doc(root, ticker, name, body):
    folder = root / ticker / 'documents'
    folder.mkdir(parents=True, exist_ok=True)
    raw = body.encode('utf-8')
    path = folder / name
    path.write_bytes(raw)
    return str(path), sha256(raw).hexdigest()


def _cand(path, digest, url, *, periodo, tipo, filed=None, form=None, fonte='SEC EDGAR', stato='verificato',
          motivi=(), **extra):
    row = {'fonte': fonte, 'path': path, 'sha256': digest, 'url': url, 'stato': stato, 'motivi': list(motivi),
           'metadati': {'emittente_id': 'CIK:0009990001', 'lingua': 'en', 'perimetro': 'consolidato',
                        'periodo_fine': periodo, 'periodo_inizio': periodo[:4] + '-01-01', 'tipo': tipo}}
    if filed is not None:
        row.update(filed_date=filed, report_date=periodo, form=form)
    row.update(extra)
    return row


def _run(store, ticker, status, result, reason=None):
    run = store.start_run(ticker, 'manual', language='it')
    store.claim_execution(run['id'])
    return store.finish_run(run['id'], status=status, reason=reason, result=result)


@pytest.fixture
def archivio(tmp_path):
    """DB Filing + archivio sintetici: un titolo per ogni esito da dichiarare."""
    db = tmp_path / 'filing.db'
    with sqlite3.connect(db) as conn:
        ensure_schema(conn)
    store = FilingStore(db)
    root = tmp_path / 'filing_archive'
    sec = {**BASE, 'tipo': 'trimestrale', 'emittente_id': 'CIK:0009990001'}
    esef = {**BASE, 'tipo': 'annuale', 'emittente_id': 'LEI:999900QQSYN0000001'}
    for ticker in ('ZZTEST', 'ZZERR', 'ZZVUOTO'):
        store.set_profile(ticker, {**sec, 'ticker': ticker})
    store.set_profile('QQSYN.MI', {**esef, 'ticker': 'QQSYN.MI'})

    q2 = _doc(root, 'ZZTEST', 'zz-q2.htm', '<html><body>FORM 10-Q ZZTEST Corp quarter ended 2029-06-30. '
              'Revenue 123.45 million.</body></html>')
    k = _doc(root, 'ZZTEST', 'zz-k.htm', '<html><body>FORM 10-K ZZTEST Corp year ended 2028-12-31. '
             'Revenue 456.78 million.</body></html>')
    q1 = _doc(root, 'ZZTEST', 'zz-q1.htm', '<html><body>FORM 10-Q ZZTEST Corp quarter ended 2029-03-31.</body></html>')
    bad = _doc(root, 'ZZTEST', 'zz-press.htm', '<html><body>ZZTEST press release</body></html>')
    dup = _doc(root, 'ZZTEST', 'zz-dup.htm', '<html><body>FORM 10-Q ZZTEST Corp copia duplicata</body></html>')
    base_url = 'https://www.sec.gov/Archives/edgar/data/9990001/000999000129000%03d/%s'
    _run(store, 'ZZTEST', 'ok', {'stato': 'ok', 'motivi': [], 'freschezza': {'stato': 'n.d.', 'ultimo_periodo': '2029-06-30'},
        'candidati': [
            _cand(*q1, base_url % (2, 'zz-q1.htm'), periodo='2029-03-31', tipo='trimestrale', filed='2029-05-02', form='10-Q'),
            _cand(*bad, base_url % (4, 'zz-press.htm'), periodo='2029-07-20', tipo='trimestrale', filed='2029-07-21',
                  form='6-K', stato='non_applicabile', motivi=['ValueError: emittente: prova testuale assente']),
            # stato non 'verificato' SENZA motivi e col periodo piu' recente: se entrasse, sarebbe il primo
            _cand(*dup, base_url % (5, 'zz-dup.htm'), periodo='2029-09-30', tipo='trimestrale', filed='2029-11-02',
                  form='10-Q', stato='duplicato'),
            _cand(*q2, base_url % (3, 'zz-q2.htm'), periodo='2029-06-30', tipo='trimestrale', filed='2029-08-01', form='10-Q'),
            _cand(*k, base_url % (1, 'zz-k.htm'), periodo='2028-12-31', tipo='annuale', filed='2029-02-20', form='10-K')]})

    annual = _doc(root, 'QQSYN.MI', 'qqsyn-2028.json', json.dumps(
        {'facts': {'f1': {'value': 'QQSYN SpA bilancio consolidato 2028, ricavi 789.01'}}}))
    _run(store, 'QQSYN.MI', 'parziale', {'stato': 'parziale', 'motivi': [], 'candidati': [
        _cand(*annual, 'https://filings.xbrl.org/999900QQSYN0000001/2028-12-31/ESEF/IT/0/qqsyn.json',
              periodo='2028-12-31', tipo='annuale', fonte='ESEF', language=None, period_end='2028-12-31')]},
        reason="ESEF: repository fermo all'esercizio FY2028 (finto)")

    old = _doc(root, 'ZZERR', 'zzerr-q.htm', '<html><body>FORM 10-Q ZZERR Inc quarter ended 2029-03-31.</body></html>')
    _run(store, 'ZZERR', 'ok', {'stato': 'ok', 'motivi': [], 'candidati': [
        _cand(*old, base_url % (9, 'zzerr-q.htm'), periodo='2029-03-31', tipo='trimestrale', filed='2029-05-03', form='10-Q')]})
    _run(store, 'ZZERR', 'errore', None, reason='controllo SEC finto in errore')

    junk = _doc(root, 'ZZVUOTO', 'zzv.htm', '<html><body>ZZVUOTO</body></html>')
    _run(store, 'ZZVUOTO', 'parziale', {'stato': 'parziale', 'motivi': [], 'candidati': [
        _cand(*junk, base_url % (7, 'zzv.htm'), periodo='2029-06-30', tipo='trimestrale', filed='2029-08-02',
              form='6-K', stato='non_applicabile',
              motivi=['lingua catalogo de diversa dal profilo en: documento non scaricato, nessuna traduzione'])]})
    return SimpleNamespace(store=store, archive_root=root, db=db)


TICKERS = ('QQSYN.MI', 'ZZERR', 'ZZNONE', 'ZZTEST', 'ZZVUOTO')


class _Store:
    """Checkpoint immutabili come WeeklyRunStore.complete (stessa regola: diverso = errore)."""

    def __init__(self):
        self.context = {'research_started_at': AS_OF_CONTEXT}
        self.rows = {}

    def get(self, key):
        return deepcopy(self.rows.get(key))

    def complete(self, key, payload, bb=None):
        encoded = json.dumps(payload, sort_keys=True)
        if key in self.rows and json.dumps(self.rows[key], sort_keys=True) != encoded:
            raise RuntimeError('Checkpoint completo immutabile: ' + key)
        self.rows.setdefault(key, json.loads(encoded))


class _Board:
    def __init__(self, session=None):
        self.data, self._lock = {}, RLock()
        self.analysis_mode, self.run_scope = RESEARCH_ANALYSIS_MODE, 'weekly'
        self.research_tickers = list(TICKERS)
        self.tool_receipts = []
        self.company_source_session_if_known = lambda ticker: (session or {}).get(ticker)

    def read(self, desk, round_n):
        return 'Report sintetico ' + desk


def _ammetti(archivio, board=None, store=None, esiti=None, registro=None):
    board, store = board or _Board(), store or _Store()
    agg = SimpleNamespace(esiti=lambda: esiti) if esiti is not None else None
    record = ponte.ammetti_archivio_filing(board, store, aggiornamento=agg,
                                           servizio_factory=lambda: archivio,
                                           **({'registro_factory': lambda: registro} if registro else {}))
    return board, store, record


def test_selezione_dichiara_un_esito_per_ogni_titolo(archivio):
    esiti = {'ZZTEST': {'stato': 'non_aggiornato', 'motivo': 'oltre i 60 s di attesa (finto)'}}
    _, store, record = _ammetti(archivio, esiti=esiti)
    assert store.get(ponte.CHECKPOINT) == record and record['stato'] == 'ok'
    voci = record['tickers']
    assert sorted(voci) == sorted(TICKERS)
    assert {t: voci[t]['esito'] for t in voci} == {
        'ZZTEST': 'ammesso', 'QQSYN.MI': 'ammesso', 'ZZERR': 'ammesso',
        'ZZNONE': 'nessun_profilo', 'ZZVUOTO': 'nessun_documento_verificato'}
    zz = voci['ZZTEST']
    # ultimo periodo + ultimo annuale; il trimestre vecchio dichiarato come non selezionato
    assert [d['periodo'] for d in zz['documenti']] == ['2029-06-30', '2028-12-31']
    assert [d['periodo'] for d in zz['non_selezionati']] == ['2029-03-31']
    assert [e['url'].rsplit('/', 1)[1] for e in zz['esclusi']] == ['zz-press.htm', 'zz-dup.htm']
    assert "identita'" in zz['esclusi'][0]['motivo'] and 'emittente' in zz['esclusi'][0]['motivo']
    assert 'stato duplicato' in zz['esclusi'][1]['motivo']
    assert any('NON AGGIORNATO' in m and 'oltre i 60 s' in m for m in zz['motivi'])
    err = voci['ZZERR']
    assert err['ultimo_run']['status'] == 'errore' and err['run_usato']['status'] == 'ok'
    assert any('controllo SEC finto in errore' in m and 'NON dal controllo piu' in m for m in err['motivi'])
    assert voci['ZZNONE']['motivi'] == ['archivio Filing: nessun profilo per questo titolo',
                                       'tipo strumento n.d.: titolo assente dal registro veicoli']
    assert voci['ZZVUOTO']['esclusi'] and voci['ZZVUOTO']['documenti'] == []
    assert any('repository fermo' in m for m in voci['QQSYN.MI']['motivi'])
    # ricevute senza percorsi assoluti, con eta' strutturata al cutoff (2030-01-15)
    assert all(not Path(d['path_relativo']).is_absolute() and 'text' not in d for d in zz['documenti'])
    assert [(d['eta_giorni_al_cutoff'], d['esercizio_successivo_chiuso']) for d in zz['documenti']] == [
        (199, False), (380, True)]


def _leggi(board, ticker, document_id):
    from bellomberg.valuation.company_dossier import read_company_dossier
    return read_company_dossier(board, {'ticker': ticker, 'section': 'document', 'document_id': document_id},
                                max_chars=12000)


def test_il_sigillo_vede_i_documenti_per_riferimento_e_le_lacune_dichiarate(archivio):
    board, _, _ = _ammetti(archivio)
    sealed = seal_research_thesis(board, desks=DESKS)
    dossiers = sealed['dossiers']
    zz = dossiers['ZZTEST']
    assert zz['status'] == 'research'          # non piu' «unavailable / No company documents were acquired»
    assert [d['metadata']['report_date'] for d in zz['documents']] == ['2029-06-30', '2028-12-31']
    assert {d['metadata']['filing_bridge']['base_origine'] for d in zz['documents']} == {'archivio_ufficiale_sec_edgar'}
    # il sigillo tiene SOLO riferimenti: nessun testo, nessun percorso assoluto
    sigillo = json.dumps(sealed, ensure_ascii=False)
    assert all('text' not in d for x in dossiers.values() for d in x.get('documents') or [])
    assert 'Revenue' not in sigillo and str(archivio.archive_root) not in sigillo
    # il testo si legge su richiesta, riverificato al momento
    letto = _leggi(board, 'ZZTEST', zz['documents'][0]['id'])
    assert letto['ok'] is True and 'Revenue 123.45' in letto['text']
    assert zz['filing_bridge']['esito'] == 'ammesso' and zz['source_fingerprint'] == zz['filing_bridge']['entry_sha256']
    esef = dossiers['QQSYN.MI']['documents'][0]
    assert esef['published_at'] is None and esef['availability_basis'] == 'observed_download'
    assert esef['metadata']['filing_bridge']['base_origine'] == 'repository_esef_filings_xbrl'
    assert 'non nota' in esef['metadata']['filing_bridge']['data_pubblicazione']
    assert any(i['code'] == 'filing_archive_limitation' and '2028-12-31' in i['reason']
               for i in dossiers['QQSYN.MI']['issues'])
    # eta': esercizio successivo chiuso dichiarato per documento (fatto di calendario, nessuna soglia)
    vecchi = {(t, i['document_id']) for t, x in dossiers.items() for i in x['issues']
              if i.get('code') == 'filing_archive_esercizio_successivo_chiuso'}
    assert vecchi == {('ZZTEST', zz['documents'][1]['id']), ('QQSYN.MI', esef['id'])}
    err = dossiers['ZZERR']
    assert err['status'] == 'research'
    assert any('controllo SEC finto in errore' in i['reason'] and '2029-03-31' in i['reason'] for i in err['issues'])
    none = dossiers['ZZNONE']
    assert none['status'] == 'research_required' and none['documents'] == []
    assert none['issues'][-1]['code'] == 'filing_archive_nessun_profilo'
    assert dossiers['ZZVUOTO']['filing_bridge']['esclusi'][0]['motivo'].startswith('altra lingua')
    assert research_reference(board)['dossier_sha256'] == sealed['dossier_sha256']


def test_prompt_riceve_solo_un_indice_compatto(archivio):
    board, _, _ = _ammetti(archivio)
    seal_research_thesis(board, desks=DESKS)
    from bellomberg.core.research_analysis import research_context
    indice = research_context(board)['dossiers']
    testo = json.dumps(indice, ensure_ascii=False)
    assert 'filing_archive' not in testo.replace('filing_archive_', '') and 'regola_selezione' not in testo
    assert str(archivio.archive_root) not in testo and 'path_relativo' not in testo and 'Revenue' not in testo
    assert indice['ZZTEST']['documenti'][0] == {'id': indice['ZZTEST']['documenti'][0]['id'], 'tipo': '10-Q',
        'periodo': '2029-06-30', 'eta_giorni': 199, 'esercizio_successivo_chiuso': False, 'corrente': False,
        'pubblicato': '2029-08-01', 'origine': 'archivio_ufficiale_sec_edgar'}
    assert indice['ZZTEST']['esclusi'] == 2 and indice['ZZTEST']['non_selezionati'] == 1
    # la stessa lacuna non compare due volte (codice generico assorbito dal codice dell'archivio)
    assert [l['code'] for l in indice['ZZNONE']['lacune']] == ['filing_archive_nessun_profilo']
    assert all(len(l['reason']) <= 200 + len(' [troncato: testo intero con read_company_dossier section=catalog]')
               for x in indice.values() for l in x['lacune'])


def test_stessi_input_stesso_dossier_e_stessa_impronta(archivio, tmp_path):
    first = seal_research_thesis(_ammetti(archivio)[0], desks=DESKS)
    second = seal_research_thesis(_ammetti(archivio)[0], desks=DESKS)
    assert first['dossier_sha256'] == second['dossier_sha256']
    assert first['thesis_sha256'] == second['thesis_sha256']
    # ordine diverso dei candidati nel DB -> stessa selezione (ordinamento esplicito)
    shuffled = ponte.selezione_ponte(archivio.store, list(reversed(TICKERS)), as_of='2030-01-15',
                                     archive_root=archivio.archive_root)
    straight = ponte.selezione_ponte(archivio.store, list(TICKERS), as_of='2030-01-15',
                                     archive_root=archivio.archive_root)
    assert shuffled == straight


def test_ripresa_riusa_il_checkpoint_e_non_rilegge_il_db_filing(archivio):
    board, store, record = _ammetti(archivio)
    before = seal_research_thesis(board, desks=DESKS)['dossier_sha256']
    newer = _doc(archivio.archive_root, 'ZZTEST', 'zz-q3.htm', '<html><body>FORM 10-Q ZZTEST Corp Q3</body></html>')
    _run(archivio.store, 'ZZTEST', 'ok', {'stato': 'ok', 'motivi': [], 'candidati': [_cand(
        *newer, 'https://www.sec.gov/Archives/edgar/data/9990001/000999000129000010/zz-q3.htm',
        periodo='2029-09-30', tipo='trimestrale', filed='2029-11-01', form='10-Q')]})
    resumed = _Board()
    again = ponte.ammetti_archivio_filing(resumed, store, servizio_factory=lambda: pytest.fail('DB Filing riletto'))
    assert again == record
    assert research_digest(seal_research_thesis(resumed, desks=DESKS)['dossiers']) == research_digest(
        board.data['_research_thesis']['dossiers'])
    assert seal_research_thesis(resumed, desks=DESKS)['dossier_sha256'] == before


def test_run_sigillata_col_codice_vecchio_resta_identica(archivio):
    """Nessun checkpoint del ponte (run vecchia): dossier byte-identico al formato precedente."""
    board = _Board()
    sealed = seal_research_thesis(board, desks=DESKS)
    expected = {t: {'ticker': t, 'status': 'unavailable', 'documents': [], 'records': [], 'issues': [
        {'reason': 'No company documents were acquired for this ticker; coverage is unavailable'}]} for t in TICKERS}
    assert sealed['dossiers'] == expected
    assert sealed['dossier_sha256'] == research_digest({'dossiers': expected, 'tool_receipts': []})
    assert research_reference(board)['dossier_sha256'] == sealed['dossier_sha256']
    from bellomberg.core.research_analysis import research_context
    assert research_context(board)['dossiers'] == expected      # prompt di una run vecchia invariato


@pytest.mark.parametrize('guasto', ['byte_cambiati', 'file_sparito'])
def test_archivio_cambiato_dopo_il_sigillo_e_lacuna_dichiarata_la_run_prosegue(archivio, guasto):
    board, _, record = _ammetti(archivio)
    sealed = seal_research_thesis(board, desks=DESKS)
    doc = record['tickers']['ZZTEST']['documenti'][0]
    path = Path(archivio.archive_root) / doc['path_relativo']
    if guasto == 'byte_cambiati':
        path.write_bytes(path.read_bytes() + b' ')
    else:
        path.unlink()
    # il sigillo non rilegge l'archivio vivo: nessun blocco
    assert research_reference(board)['dossier_sha256'] == sealed['dossier_sha256']
    gap = _leggi(board, 'ZZTEST', doc['id'])
    assert gap['ok'] is False and gap['declared_gap'] is True and gap['expected_sha256'] == doc['id']
    assert 'cambiato dopo il sigillo' in gap['reason']
    # l'altro documento resta leggibile
    assert _leggi(board, 'ZZTEST', record['tickers']['ZZTEST']['documenti'][1]['id'])['ok'] is True


def test_estrattore_cambiato_dopo_il_sigillo_non_blocca_il_sigillo(archivio, monkeypatch):
    board, _, record = _ammetti(archivio)
    sealed = seal_research_thesis(board, desks=DESKS)
    monkeypatch.setattr(ponte, '_TESTI', {})
    monkeypatch.setattr(ponte, '_estrai', lambda *a: {'stato': 'ok', 'testo': 'estrattore diverso', 'formato': 'html'})
    assert research_reference(board)['dossier_sha256'] == sealed['dossier_sha256']
    gap = _leggi(board, 'ZZTEST', record['tickers']['ZZTEST']['documenti'][0]['id'])
    assert gap['ok'] is False and 'testo estratto diverso' in gap['reason']


def test_ricevuta_del_ponte_manomessa_non_passa(archivio):
    board, _, _ = _ammetti(archivio)
    board.data['_filing_bridge']['tickers']['ZZNONE']['esito'] = 'ammesso'
    from bellomberg.valuation.company_dossier import dossier_for_board
    with pytest.raises(ValueError, match='ponte Filing'):
        dossier_for_board(board, 'ZZNONE')


def test_archivio_filing_in_errore_resta_dichiarato(archivio):
    def rotto():
        raise RuntimeError('schema filing assente (finto)')
    board, store = _Board(), _Store()
    record = ponte.ammetti_archivio_filing(board, store, servizio_factory=rotto)
    assert record['stato'] == 'errore' and 'schema filing assente' in record['motivo']
    dossier = seal_research_thesis(board, desks=DESKS)['dossiers']['ZZTEST']
    assert dossier['status'] == 'research_required'
    assert dossier['issues'][-1]['code'] == 'filing_archive_archivio_in_errore'
    assert dossier['filing_bridge']['motivo_archivio'].startswith('archivio Filing non disponibile')


def test_guasto_del_ponte_e_lacuna_dichiarata(archivio):
    class StoreRotto(_Store):
        def complete(self, key, payload, bb=None):
            raise OSError('disco finto pieno')
    board = _Board()
    record = ponte.ammetti_archivio_filing(board, StoreRotto(), servizio_factory=lambda: archivio)
    assert record['stato'] == 'non_eseguito' and record['motivo'] == 'ponte non eseguito: OSError'
    dossier = seal_research_thesis(board, desks=DESKS)['dossiers']['ZZTEST']
    assert dossier['issues'][-1]['code'] == 'filing_archive_ponte_non_eseguito'


def test_esiti_del_pre_run_letti_una_volta(archivio):
    letture = []
    agg = SimpleNamespace(esiti=lambda: letture.append(1) or {})
    board, store = _Board(), _Store()
    record = ponte.ammetti_archivio_filing(board, store, aggiornamento=agg, servizio_factory=lambda: archivio,
        esiti={'ZZTEST': {'stato': 'non_aggiornato', 'motivo': 'gia letto dal contesto (finto)'}})
    assert letture == []
    assert record['tickers']['ZZTEST']['aggiornamento_pre_run']['motivo'] == 'gia letto dal contesto (finto)'


def test_documento_gia_acquisito_dal_desk_non_si_duplica(archivio):
    board, _, record = _ammetti(archivio)
    from bellomberg.valuation.company_dossier import dossier_for_board
    bridged = dossier_for_board(board, 'ZZTEST')['documents'][0]
    desk_doc = {k: deepcopy(bridged[k]) for k in ('id', 'url', 'published_at', 'sha256', 'document_sha256')}
    desk_doc.update(text=ponte.testo_documento(board, bridged), origin='SEC',
                    metadata={'report_date': '2029-06-30', 'form': '10-Q'})
    session = SimpleNamespace(snapshot=lambda **_: {'ticker': 'ZZTEST', 'as_of': '2030-01-15', 'documents': [desk_doc],
        'revision_id': 'r' * 64, 'revision_sha256': 'r' * 64, 'parent_grant_fingerprint': 'p' * 64,
        'failures': [], 'pending_requests': []})
    board.company_source_session_if_known = lambda t: session if t == 'ZZTEST' else None
    dossier = dossier_for_board(board, 'ZZTEST')
    assert [d['id'] for d in dossier['documents']].count(bridged['id']) == 1
    tenuto = next(d for d in dossier['documents'] if d['id'] == bridged['id'])
    assert 'filing_bridge' not in tenuto['metadata'] and 'Revenue 123.45' in tenuto['text']   # resta quello del desk
    assert dossier['filing_bridge']['gia_acquisiti_dal_desk'] == [bridged['id']]
    assert len(dossier['documents']) == 2 and dossier['source_fingerprint'] == 'r' * 64


def test_cutoff_diverso_fra_ponte_e_sessione_si_rifiuta(archivio):
    board, _, _ = _ammetti(archivio)
    session = SimpleNamespace(snapshot=lambda **_: {'ticker': 'ZZTEST', 'as_of': '2030-01-14', 'documents': [],
        'revision_id': None, 'revision_sha256': None, 'parent_grant_fingerprint': 'p' * 64,
        'failures': [], 'pending_requests': []})
    board.company_source_session_if_known = lambda t: session if t == 'ZZTEST' else None
    from bellomberg.valuation.company_dossier import dossier_for_board
    with pytest.raises(ValueError, match='cutoff differs'):
        dossier_for_board(board, 'ZZTEST')


@pytest.mark.parametrize('row,expected', [
    ({'fonte': 'IR'}, 'sito_emittente_non_archivio_ufficiale'),
    ({'fonte': 'IR', 'origine': 'sito_emittente'}, 'sito_emittente_non_archivio_ufficiale'),
    ({'fonte': 'ESEF', 'origine': 'sito'}, 'pacchetto_esef_dal_sito_emittente'),
    ({'fonte': 'ESEF'}, 'repository_esef_filings_xbrl'),
    ({'fonte': 'SEC EDGAR'}, 'archivio_ufficiale_sec_edgar'),
    ({'fonte': 'ALTRO'}, 'fonte_filing_non_classificata:ALTRO')])
def test_etichetta_d_origine_dichiarata(row, expected):
    assert ponte.base_origine(row) == expected


def _pdf(path, pagine):
    from reportlab.pdfgen import canvas
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path))
    for testo in pagine:
        if testo:
            c.drawString(72, 720, testo)
        c.showPage()
    c.save()
    return str(path), sha256(path.read_bytes()).hexdigest()


def test_pdf_dal_sito_dell_emittente(tmp_path):
    db = tmp_path / 'filing.db'
    with sqlite3.connect(db) as conn:
        ensure_schema(conn)
    store = FilingStore(db)
    root = tmp_path / 'filing_archive'
    store.set_profile('QQPDF.MI', {**BASE, 'tipo': 'semestrale', 'emittente_id': 'LEI:999900QQPDF0000001',
                                   'ticker': 'QQPDF.MI'})
    buono = _pdf(root / 'QQPDF.MI' / 'documents' / 'h1.pdf', ['QQPDF SpA relazione semestrale 2029', 'Ricavi 12.34'])
    vuoto = _pdf(root / 'QQPDF.MI' / 'documents' / 'h1b.pdf', ['QQPDF SpA relazione semestrale 2028', ''])
    falso = _pdf(root / 'QQPDF.MI' / 'documents' / 'h1c.pdf', ['QQPDF SpA relazione 2027', 'Ricavi 1.23'])
    _run(store, 'QQPDF.MI', 'ok', {'stato': 'ok', 'motivi': [], 'candidati': [
        _cand(*buono, 'https://ir.qqpdf.example/h1-2029.pdf', periodo='2029-06-30', tipo='semestrale', fonte='IR'),
        _cand(*vuoto, 'https://ir.qqpdf.example/h1-2028.pdf', periodo='2028-06-30', tipo='annuale', fonte='IR'),
        _cand(*falso, 'https://ir.qqpdf.example/h1-2027.pdf', periodo='2027-06-30', tipo='semestrale', fonte='IR',
              filing_verification={'schema': 'finto'})]})
    record = ponte.selezione_ponte(store, ['QQPDF.MI'], as_of='2030-01-15', archive_root=root)
    voce = record['tickers']['QQPDF.MI']
    # Regola nuova (PM 06/10, APERTO-TI): l'annuale col PDF a pagina muta ENTRA, dichiarato
    # «non verificato: ... pagine senza testo ... (testo incompleto)», mai fonte primaria verificata.
    assert [(d['periodo'], d.get('verifica', 'verificato')) for d in voce['documenti']] == [
        ('2029-06-30', 'verificato'), ('2028-06-30', 'non_verificato')]
    assert 'pagine senza testo' in voce['documenti'][1]['etichetta_verifica']
    assert 'testo incompleto' in voce['documenti'][1]['etichetta_verifica']
    doc = voce['documenti'][0]
    assert doc['formato'] == 'pdf' and doc['pagine'] == 2 and doc['base_origine'] == 'sito_emittente_non_archivio_ufficiale'
    assert doc['published_at'] is None and doc['availability_basis'] == 'observed_download'
    # il semestrale 2027 e' «non selezionato»; nessun documento escluso per pagine mute
    assert not any('pagine senza testo' in e['motivo'] for e in voce['esclusi'])
    # filing_verification falsa: la verifica del PDF lo rifiuta col motivo
    record2 = ponte.selezione_ponte(store, ['QQPDF.MI'], as_of='2030-01-15', archive_root=root)
    assert record2 == record
    falsa = {**deepcopy(voce['documenti'][0]), 'path': falso[0], 'sha256': falso[1], 'url': 'https://x.example/f.pdf',
             'filing_verification': {'schema': 'finto'}, 'metadati': {'periodo_fine': '2027-06-30'}}
    with pytest.raises(ValueError):
        ponte._verifica(falsa, root, '2030-01-15')


def test_run_settimanale_collega_il_ponte_prima_di_r0(research_weekly, monkeypatch, tmp_path):
    """Cablaggio: la run vera (offline) ammette i documenti Filing e il sigillo li contiene per riferimento."""
    from bellomberg.agents import consigliere_multi as cm
    from bellomberg.market_data.filing_service import FilingService
    from test_weekly_recovery import _store
    db = tmp_path / 'filing-e2e.db'
    with sqlite3.connect(db) as conn:
        ensure_schema(conn)
    fstore = FilingStore(db)
    fstore.set_profile('SYNTH-A', {**BASE, 'tipo': 'trimestrale', 'emittente_id': 'CIK:0009990003', 'ticker': 'SYNTH-A'})
    root = tmp_path / 'arch'
    doc = _doc(root, 'SYNTH-A', 'sa.htm', '<html><body>FORM 10-Q Synthetic issuer SYNTH-A quarter. '
               'Revenue 321.09 million.</body></html>')
    _run(fstore, 'SYNTH-A', 'ok', {'stato': 'ok', 'motivi': [], 'candidati': [_cand(
        *doc, 'https://www.sec.gov/Archives/edgar/data/9990003/000999000329000001/sa.htm',
        periodo='2026-06-30', tipo='trimestrale', filed='2026-08-01', form='10-Q')]})
    monkeypatch.setattr(cm, '_filing_service', lambda: FilingService(
        fstore, root, pipeline=lambda *a, **k: pytest.fail('download Filing nella run'),
        indexer=lambda *a: {'status': 'skipped'}))
    cm.run_multi_agent(send_email=False)
    store = _store()
    assert store.get(ponte.CHECKPOINT)['tickers']['SYNTH-A']['esito'] == 'ammesso'
    dossiers = store.get('research_dossier')['dossiers']
    assert dossiers['SYNTH-A']['status'] == 'research'
    documento = dossiers['SYNTH-A']['documents'][0]
    origine = documento['metadata']['filing_bridge']
    assert origine['contract'] == ponte.CONTRATTO and origine['base_origine'] == 'archivio_ufficiale_sec_edgar'
    assert 'text' not in documento and 'Revenue 321.09' not in json.dumps(dossiers)
    assert dossiers['SYNTH-B']['issues'][-1]['code'] == 'filing_archive_nessun_profilo'


def test_run_settimanale_guasto_del_ponte_non_ferma_la_run(research_weekly, monkeypatch):
    from bellomberg.agents import consigliere_multi as cm
    from test_weekly_recovery import _store

    def rotto(*a, **k):
        raise RuntimeError('ponte finto rotto')
    monkeypatch.setattr(ponte, 'ammetti_archivio_filing', rotto)
    result = cm.run_multi_agent(send_email=False)
    assert result['status'] == 'completed'
    dossiers = _store().get('research_dossier')['dossiers']
    assert dossiers['SYNTH-A']['filing_bridge']['esito'] == 'ponte_non_eseguito'
    assert dossiers['SYNTH-A']['issues'][-1]['reason'] == 'Archivio Filing: ponte non eseguito: RuntimeError'


# --- Decisioni PM 06/10: soglie di eta' e strumenti non societari -------------------------------

REGISTRO_VUOTO = {'origine': 'registro sintetico di prova', 'veicoli': {}, 'motivo': None}


@pytest.fixture(autouse=True)
def _registro_sintetico(monkeypatch):
    """Mai il registro veicoli vero nei test: ogni prova usa un registro sintetico."""
    monkeypatch.setattr(ponte, '_registro_predefinito', lambda: deepcopy(REGISTRO_VUOTO))


def _registro(**tipi):
    return {'origine': 'registro sintetico di prova', 'motivo': None,
            'veicoli': {t.replace('_', '.'): {'tipo': v} for t, v in tipi.items()}}


def test_soglie_pm_eta_corrente_e_issue_dichiarato(archivio):
    board, _, record = _ammetti(archivio)
    assert {k: v for k, v in record['soglie_eta'].items() if k != 'convenzione'} == {
        'annuale_mesi': 16, 'infrannuale_mesi': 5, 'fonte': 'soglia PM 06/10'}
    assert record['soglie_eta']['convenzione'].startswith('mesi di calendario; periodo chiuso a fine mese')
    docs = record['tickers']['ZZTEST']['documenti']
    # 10-Q chiuso il 2029-06-30: corrente fino al 2029-11-30 (5 mesi) -> al cutoff 2030-01-15 NON corrente
    assert [(d['tipo_periodo'], d['corrente'], d['corrente_fino_al']) for d in docs] == [
        ('trimestrale', False, '2029-11-30'), ('annuale', True, '2030-04-30')]
    dossiers = seal_research_thesis(board, desks=DESKS)['dossiers']
    non_correnti = {(t, i['document_id']) for t, x in dossiers.items() for i in x['issues']
                    if i.get('code') == 'filing_archive_documento_non_corrente'}
    assert non_correnti == {('ZZTEST', docs[0]['id']),
                            ('ZZERR', record['tickers']['ZZERR']['documenti'][0]['id'])}
    motivo = next(i['reason'] for i in dossiers['ZZTEST']['issues'] if i.get('code') == 'filing_archive_documento_non_corrente')
    assert '2029-06-30' in motivo and '5 mesi' in motivo and 'soglia PM 06/10' in motivo
    # il documento entra comunque
    assert dossiers['ZZTEST']['status'] == 'research' and len(dossiers['ZZTEST']['documents']) == 2
    from bellomberg.core.research_analysis import research_context
    indice = research_context(board)['dossiers']['ZZTEST']
    assert [d['corrente'] for d in indice['documenti']] == [False, True]
    assert 'filing_archive_documento_non_corrente' in [l['code'] for l in indice['lacune']]


@pytest.mark.parametrize('periodo,tipo,form,atteso', [
    ('2028-09-30', 'annuale', '20-F', True),      # +16 mesi = 2030-01-30 >= cutoff 2030-01-15
    ('2028-08-31', 'annuale', '20-F', False),     # +16 mesi = 2029-12-31 < cutoff
    ('2029-08-15', 'semestrale', None, True),     # +5 mesi = 2030-01-15 = cutoff: ancora corrente
    ('2029-08-14', 'nove_mesi', None, False),
    ('2029-09-30', None, '10-Q', True),           # tipo dal modulo SEC
    ('2029-06-30', None, '10-K', True),           # annuale dal modulo SEC: 16 mesi, non 5
    ('2029-09-30', None, '6-K', None)])           # tipo non noto: soglia non applicabile, dichiarato
def test_soglie_pm_confini(periodo, tipo, form, atteso):
    eta = ponte._eta(periodo, '2030-01-15', tipo=tipo, form=form)
    assert eta['corrente'] is atteso


def test_strumenti_non_societari_sono_non_applicabili(archivio):
    board = _Board()
    board.research_tickers = ['QQETF.MI', 'ZZFUND', 'ZZNONE', 'ZZTEST']
    registro = _registro(QQETF_MI='etf', ZZFUND='cef', ZZTEST='operating')
    _, _, record = _ammetti(archivio, board=board, registro=registro)
    voci = record['tickers']
    assert {t: voci[t]['esito'] for t in voci} == {'QQETF.MI': 'non_applicabile', 'ZZFUND': 'non_applicabile',
                                                   'ZZNONE': 'nessun_profilo', 'ZZTEST': 'ammesso'}
    assert voci['QQETF.MI']['motivi'] == ['bilancio societario non applicabile: etf']
    assert any('tipo strumento n.d.' in m for m in voci['ZZNONE']['motivi'])
    dossiers = seal_research_thesis(board, desks=DESKS)['dossiers']
    etf = dossiers['QQETF.MI']
    assert etf['status'] == 'non_applicabile' and etf['documents'] == []
    assert etf['issues'] == [{'field': 'documents', 'code': 'non_applicabile',
                              'reason': 'bilancio societario non applicabile: etf'}]
    assert dossiers['ZZNONE']['status'] == 'research_required'
    assert research_reference(board)['dossier_sha256']
    from bellomberg.core.research_analysis import research_context
    indice = research_context(board)['dossiers']['ZZFUND']
    assert indice['status'] == 'non_applicabile'
    assert indice['lacune'] == [{'code': 'non_applicabile', 'reason': 'bilancio societario non applicabile: cef'}]


def test_registro_veicoli_assente_resta_dichiarato(archivio):
    registro = {'origine': 'assente', 'veicoli': {}, 'motivo': 'negozio non trovato (finto)'}
    _, _, record = _ammetti(archivio, registro=registro)
    assert record['registro_veicoli'] == {'origine': 'assente', 'motivo': 'negozio non trovato (finto)'}
    assert any('registro veicoli assente' in m for m in record['tickers']['ZZNONE']['motivi'])

def test_run_settimanale_etf_non_applicabile_fino_al_memo(research_weekly, monkeypatch, tmp_path):
    """Decisione PM 06/10 end-to-end: l'ETF esce «non_applicabile», la run arriva a PDF e mail, e il
    contesto che ricevono R2/Red Team/Capo (research_context) lo dichiara col motivo."""
    from bellomberg.agents import consigliere_multi as cm
    from bellomberg.core.research_analysis import research_context
    from test_weekly_recovery import _store
    observed, _ = research_weekly

    def rotto():
        raise FileNotFoundError('archivio filing di prova assente')
    monkeypatch.setattr(cm, '_filing_service', rotto)
    monkeypatch.setattr(ponte, '_registro_predefinito', lambda: {'origine': 'registro sintetico di prova',
                        'motivo': None, 'veicoli': {'SYNTH-B': {'tipo': 'etf'}}})
    result = cm.run_multi_agent(send_email=True)
    assert result['status'] == 'completed' and result['delivery_status'] == 'sent'
    assert {row['kind'] for row in result['artifacts']} == {'Markdown', 'PDF'}
    dossiers = _store().get('research_dossier')['dossiers']
    assert dossiers['SYNTH-B']['status'] == 'non_applicabile'
    assert dossiers['SYNTH-B']['issues'] == [{'field': 'documents', 'code': 'non_applicabile',
                                              'reason': 'bilancio societario non applicabile: etf'}]
    # l'archivio Filing assente resta dichiarato per il titolo societario
    assert dossiers['SYNTH-A']['issues'][-1]['code'] == 'filing_archive_archivio_in_errore'
    contesto = research_context(observed.catturato['bb'])['dossiers']['SYNTH-B']
    assert contesto['status'] == 'non_applicabile'
    assert contesto['lacune'] == [{'code': 'non_applicabile', 'reason': 'bilancio societario non applicabile: etf'}]

@pytest.mark.parametrize('periodo,tipo,atteso', [
    ('2024-06-30', 'annuale', '2025-10-31'),        # fine mese -> fine mese (non il 30/10)
    ('2024-09-30', 'annuale', '2026-01-31'),
    ('2024-02-29', 'annuale', '2025-06-30'),        # bisestile a fine febbraio
    ('2025-02-28', 'trimestrale', '2025-07-31'),    # fine febbraio non bisestile
    ('2025-06-30', 'trimestrale', '2025-11-30'),
    ('2025-06-15', 'trimestrale', '2025-11-15'),    # meta' mese: stesso giorno
    ('2024-01-31', 'trimestrale', '2024-06-30')])
def test_soglia_aggancia_la_fine_del_mese(periodo, tipo, atteso):
    eta = ponte._eta(periodo, atteso, tipo=tipo)
    assert eta['corrente_fino_al'] == atteso and eta['corrente'] is True
    giorno_dopo = (date.fromisoformat(atteso) + timedelta(days=1)).isoformat()
    assert ponte._eta(periodo, giorno_dopo, tipo=tipo)['corrente'] is False
    assert 'fine mese' in ponte.SOGLIE_ETA['convenzione']


def test_strumento_non_societario_con_documenti_verificati_li_dichiara(archivio):
    """Un fondo chiuso con profilo Filing attivo: documenti NON ammessi ma dichiarati con conteggio."""
    board = _Board()
    board.research_tickers = ['ZZTEST']
    _, _, record = _ammetti(archivio, board=board, registro=_registro(ZZTEST='cef'))
    voce = record['tickers']['ZZTEST']
    assert voce['esito'] == 'non_applicabile' and voce['documenti'] == []
    non_usati = voce['verificati_non_usati']
    assert non_usati['conteggio'] == 3 and non_usati['motivo'] == 'verificati ma non usati: strumento cef'
    assert sorted(d['periodo'] for d in non_usati['documenti']) == ['2028-12-31', '2029-03-31', '2029-06-30']
    dossier = seal_research_thesis(board, desks=DESKS)['dossiers']['ZZTEST']
    assert dossier['status'] == 'non_applicabile' and dossier['documents'] == []
    assert dossier['issues'] == [
        {'field': 'documents', 'code': 'non_applicabile', 'reason': 'bilancio societario non applicabile: cef'},
        {'field': 'documents', 'code': 'filing_archive_verificati_non_usati',
         'reason': "3 documenti verificati dall'archivio Filing ma non usati: strumento cef (scelta PM)"}]
    assert dossier['filing_bridge']['verificati_non_usati']['conteggio'] == 3
    from bellomberg.core.research_analysis import research_context
    assert [l['code'] for l in research_context(board)['dossiers']['ZZTEST']['lacune']] == [
        'non_applicabile', 'filing_archive_verificati_non_usati']


def test_strumento_non_societario_senza_profilo_non_dichiara_documenti(archivio):
    board = _Board()
    board.research_tickers = ['QQETF.MI']
    _, _, record = _ammetti(archivio, board=board, registro=_registro(QQETF_MI='etf'))
    assert record['tickers']['QQETF.MI']['verificati_non_usati'] == {
        'conteggio': 0, 'motivo': 'archivio Filing: nessun profilo per questo titolo', 'documenti': []}

from test_weekly_research_without_workbook import research_weekly  # noqa: E402,F401  (fixture condivisa)
from test_cablaggio_consigliere_multi import run_offline  # noqa: E402,F401
