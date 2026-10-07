"""Trade Idea su un titolo NUOVO (fuori dal book): profilo Filing attivato, run Filing e documenti nel dossier.

Richiesta PM 06/10 sera: «da domani quando lancio una run devo avere SEMPRE dati e bilanci nuovi, per QUALSIASI
societa' io metta». Vincolo PM: la fase documenti NON blocca MAI la run (rete giu', timeout, DB Filing
illeggibile, attivazione che solleva: la run arriva al PDF con la lacuna dichiarata).

Tutto sintetico e offline: ticker ZZTEST/ZZETF/SYNTH-EXT, CIK inventati, rete SEC finta, DB e archivio in tmp.
"""
from copy import deepcopy
from functools import partial
from pathlib import Path
from threading import Event, RLock
from types import SimpleNamespace
import json
import sqlite3
import time

import pytest

from bellomberg.agents import ponte_filing_dossier as ponte
from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
from bellomberg.market_data import filing_titolo_nuovo as fase
from bellomberg.market_data import sec_edgar
from bellomberg.market_data.filing_pipeline import esegui_profilo
from bellomberg.market_data.filing_service import FilingService
from bellomberg.storage.filing_store import FilingStore, ensure_schema
from tests.filing_sec_sintetici import documento_10k, documento_10q
from tests.test_filing_pipeline import rete

CIK = '0009990093'
NOME = 'Zztest Holdings Inc.'
AS_OF = '2026-09-10'
LUNGO = 'Synthetic demand drove results. ' * 90
REGISTRO = {'origine': 'sintetico', 'motivo': None,
            'veicoli': {'ZZTEST': {'tipo': 'operating'}, 'SYNTH-EXT': {'tipo': 'operating'}}}


# Le funzioni VERE di produzione, salvate prima che l'autouse le sostituisca (test di cablaggio sotto).
_VERI = {'passi_predefiniti': fase.passi_predefiniti, 'tipo_yahoo': fase._tipo_yahoo,
         'registro_fase': fase._registro_predefinito, 'registro_ponte': ponte._registro_predefinito}


@pytest.fixture(autouse=True)
def _isolamento(monkeypatch, tmp_path):
    """Mai rete vera, mai registro veicoli vero, mai passi extra veri (cascata di un altro agente)."""
    monkeypatch.setattr(fase, '_tipo_yahoo', lambda ticker: pytest.fail('Yahoo vero chiamato per ' + ticker))
    monkeypatch.setattr(fase, '_registro_predefinito', lambda: deepcopy(REGISTRO))
    monkeypatch.setattr(ponte, '_registro_predefinito', lambda: deepcopy(REGISTRO))
    monkeypatch.setattr(fase, 'passi_predefiniti', lambda: [])


def _archivio(tmp_path, nome='filing'):
    db = tmp_path / (nome + '.db')
    with sqlite3.connect(db) as conn:
        ensure_schema(conn)
    store = FilingStore(db)
    servizio = FilingService(store, tmp_path / (nome + '_archive'), pipeline=partial(esegui_profilo, numeri_fn=None),
                             indexer=lambda *a: {'status': 'skipped', 'reason': 'test'})
    return servizio


def sec_finta(monkeypatch, ticker, *, nome=NOME, cik=CIK, giu=None):
    """SEC sintetica: elenco emittenti, catalogo (10-K 2025/2024, 10-Q Q2 2026/2025) e documenti.

    ``giu``: eccezione sollevata da OGNI chiamata di rete SEC (elenco, catalogo, documenti)."""
    base = 'https://www.sec.gov/Archives/edgar/data/%d/' % int(cik)
    chiamate = {'elenco': 0, 'catalogo': 0}
    righe, pagine = [], {}
    for form, anno, trimestre, fine, depositato in (('10-K', 2025, None, '2025-12-31', '2026-02-20'),
                                                     ('10-K', 2024, None, '2024-12-31', '2025-02-20'),
                                                     ('10-Q', 2026, 2, '2026-06-30', '2026-08-05'),
                                                     ('10-Q', 2025, 2, '2025-06-30', '2025-08-05')):
        acc = '%s-%02d-%06d' % (cik, anno % 100, (trimestre or 9))
        url = base + acc.replace('-', '') + '/doc.htm'
        righe.append({'ticker': ticker, 'form': form, 'filed_date': depositato, 'accession': acc, 'url': url,
                      'emittente_id': 'CIK:' + cik, 'issuer': nome, 'report_date': fine, 'items': [],
                      'fonte': 'SEC EDGAR'})
        rischio = ('New synthetic rules apply. ' if anno >= 2026 or (form == '10-K' and anno == 2025)
                   else 'Demand may weaken. ') * 80
        pagine[url] = (documento_10q(anno, trimestre, cik=cik, nome=nome, rischio=rischio, gestione=LUNGO)
                       if form == '10-Q' else documento_10k(anno, cik=cik, nome=nome, rischio=rischio, gestione=LUNGO))

    def elenco(*a, **k):
        chiamate['elenco'] += 1
        if giu is not None:
            raise giu
        return {'origine': 'rete', 'motivo': None, 'righe': [{'cik': cik, 'ticker': ticker, 'nome': nome}]}

    def catalogo(*a, **k):
        chiamate['catalogo'] += 1
        if giu is not None:
            raise giu
        return {'stato': 'ok', 'motivi': [], 'documenti': righe}
    monkeypatch.setattr(sec_edgar, 'elenco_emittenti_sec', elenco)
    monkeypatch.setattr(sec_edgar, '_alias_sec', lambda: {})
    monkeypatch.setattr(sec_edgar, 'get_filing_catalog', catalogo)
    chiamate['documenti'] = rete(monkeypatch, {u: giu for u in pagine} if giu is not None else pagine)
    return chiamate


class _Board:
    def __init__(self, ticker='ZZTEST', **data):
        self.data, self._lock = dict(data), RLock()
        self.analysis_mode, self.run_scope = RESEARCH_ANALYSIS_MODE, 'trade_idea'
        self.target_ticker = ticker


def _fase_e_ponte(board, servizio_factory, **kw):
    record = fase.fase_documenti_trade_idea(board, as_of=AS_OF, servizio_factory=servizio_factory, nome=NOME,
                                            preferenze_predefinite=False, **kw)
    ricevuta = ponte.ammetti_per_trade_idea(board, as_of=AS_OF, servizio_factory=servizio_factory,
                                            aggiornamento=fase.aggiornamento_per_ponte(record),
                                            registro_factory=fase.registro_con_tipo(record, kw.get('registro_factory')))
    return record, ricevuta


# ---------------------------------------------------------------------------------------------
# Fase documenti (modulo) con l'attivazione e il run Filing VERI su rete finta
# ---------------------------------------------------------------------------------------------

def test_titolo_nuovo_profilo_attivato_run_filing_e_documento_nel_ponte(monkeypatch, tmp_path):
    servizio = _archivio(tmp_path)
    chiamate = sec_finta(monkeypatch, 'ZZTEST')
    assert servizio.store.get_profile('ZZTEST') is None
    record, ricevuta = _fase_e_ponte(_Board(), lambda: servizio)
    assert record["azione"] == "attivazione" and record["stato"] == "aggiornato", json.dumps(record, indent=1)
    assert record['attivazione']['esito'] == 'attivato'
    assert record['attivazione']['motivo'].startswith('attivato dalla Trade Idea del ' + AS_OF)
    profilo = servizio.store.get_profile('ZZTEST')
    assert profilo is not None and profilo['profile']['cik'] == CIK and profilo['enabled'] is True
    # stessa scrittura dell'attivazione dall'app: esito nelle preferenze (qui accanto al DB isolato)
    pref = json.loads((Path(servizio.store.db_path).parent / 'filing_preferenze.json').read_text(encoding='utf-8'))
    assert 'attivato dalla Trade Idea del ' + AS_OF in json.dumps(pref, ensure_ascii=False)
    run = record['run_filing']
    assert run['status'] in ('ok', 'parziale') and run['ultimo_periodo'] == '2026-06-30', run
    assert record['al_periodo_atteso'] is True and 'SOTTO il periodo atteso' not in record['motivo']
    assert servizio.store.get_run(run['id'])['trigger'] == 'scheduled'     # mai il giudice AI del pulsante
    voce = ricevuta['tickers']['ZZTEST']
    assert voce['esito'] == 'ammesso', voce
    assert voce['periodo_piu_recente'] == '2026-06-30'
    assert {d['form'] for d in voce['documenti']} >= {'10-Q'}
    assert voce['aggiornamento_pre_run']['stato'] == 'aggiornato'
    assert 'profilo attivato dalla Trade Idea del ' + AS_OF in voce['aggiornamento_pre_run']['motivo']
    assert chiamate['catalogo'] >= 1 and chiamate['documenti']


def test_rete_giu_lacuna_dichiarata_e_nessuna_eccezione(monkeypatch, tmp_path):
    servizio = _archivio(tmp_path)
    sec_finta(monkeypatch, 'ZZTEST', giu=ConnectionError('rete finta giu'))
    record, ricevuta = _fase_e_ponte(_Board(), lambda: servizio)
    assert record['stato'] == 'non_aggiornato'
    assert 'profilo Filing non attivato (errore)' in record['motivo'] and 'ConnectionError' in record['motivo']
    assert servizio.store.get_profile('ZZTEST') is None
    voce = ricevuta['tickers']['ZZTEST']
    assert voce['esito'] == 'nessun_profilo'
    assert any(m.startswith('aggiornamento prima della run non riuscito: Trade Idea, fase documenti prima '
                            'della run: profilo Filing non attivato') for m in voce['motivi']), voce['motivi']


def test_tempo_massimo_duro_lacuna_documenti_non_scaricati_in_tempo(monkeypatch, tmp_path):
    servizio = _archivio(tmp_path)
    sec_finta(monkeypatch, 'ZZTEST')
    sblocca = Event()

    def lento():
        sblocca.wait(30)              # archivio appeso (rete o lock): la run non aspetta
        return servizio
    inizio = time.monotonic()
    record = fase.esegui_fase('ZZTEST', as_of=AS_OF, nome=NOME, servizio_factory=lento,
                              preferenze_predefinite=False, tempo_max_s=1.0, riserva_passi_s=0.4)
    durata = time.monotonic() - inizio
    sblocca.set()
    assert durata < 3.0
    assert record['stato'] == 'non_aggiornato' and record['tempo_scaduto'] is True
    assert record['motivo'].startswith('documenti non scaricati in tempo')
    assert "apertura dell'archivio Filing" in record['motivo']


def test_guasto_di_attivazione_dichiarato_col_tipo(monkeypatch, tmp_path):
    from bellomberg.market_data import filing_attivazione

    def rotta(*a, **k):
        raise RuntimeError('attivazione finta rotta')
    monkeypatch.setattr(filing_attivazione, 'attiva', rotta)
    servizio = _archivio(tmp_path)
    record, ricevuta = _fase_e_ponte(_Board(), lambda: servizio)
    assert record['stato'] == 'non_aggiornato'
    assert record['motivo'] == ("fase documenti in errore durante «attivazione del profilo»: "
                                "RuntimeError: attivazione finta rotta")
    assert ricevuta['tickers']['ZZTEST']['esito'] == 'nessun_profilo'


def test_db_filing_illeggibile_dichiarato(tmp_path):
    def illeggibile():
        raise sqlite3.DatabaseError('file is not a database')
    record, ricevuta = _fase_e_ponte(_Board(), illeggibile)
    assert record['stato'] == 'non_aggiornato' and 'DatabaseError' in record['motivo']
    assert ricevuta['tickers']['ZZTEST']['esito'] == 'archivio_in_errore'


def test_etf_dal_registro_nessuna_attivazione(tmp_path):
    aperture, servizio = [], _archivio(tmp_path)
    record, ricevuta = _fase_e_ponte(_Board('ZZETF'), lambda: aperture.append(1) or servizio,
                                     registro_factory=lambda: {'origine': 'sintetico', 'motivo': None,
                                                               'veicoli': {'ZZETF': {'tipo': 'etf'}}})
    assert record['stato'] == 'non_applicabile'
    assert record['motivo'] == 'bilancio societario non applicabile: etf (registro dei veicoli)'
    assert record['azione'] is None
    assert aperture == [1]                     # solo il ponte apre l'archivio (lettura), la fase mai
    assert ricevuta['tickers']['ZZETF']['esito'] == 'non_applicabile'
    assert servizio.store.get_profile('ZZETF') is None


def test_etf_fuori_registro_riconosciuto_da_yahoo_e_non_applicabile_anche_nel_ponte(monkeypatch, tmp_path):
    monkeypatch.setattr(fase, '_tipo_yahoo', lambda ticker: 'ETF')
    aperture, servizio = [], _archivio(tmp_path)
    servizio.run_programmato = lambda ticker: pytest.fail('run Filing per un ETF')
    record, ricevuta = _fase_e_ponte(_Board('ZZETF'), lambda: aperture.append(1) or servizio)
    assert servizio.store.get_profile('ZZETF') is None     # nessuna attivazione (archivio solo letto)
    assert record['stato'] == 'non_applicabile'
    assert record['motivo'] == 'bilancio societario non applicabile: etf (Yahoo quoteType ETF)'
    voce = ricevuta['tickers']['ZZETF']
    assert voce['esito'] == 'non_applicabile' and voce['motivi'] == ['bilancio societario non applicabile: etf']


@pytest.mark.parametrize('quote_type, tipo', [('CRYPTOCURRENCY', 'crypto'), ('MUTUALFUND', 'fondo comune')])
def test_crypto_e_fondi_non_applicabili(monkeypatch, tmp_path, quote_type, tipo):
    monkeypatch.setattr(fase, '_tipo_yahoo', lambda ticker: quote_type)
    servizio = _archivio(tmp_path)
    servizio.run_programmato = lambda ticker: pytest.fail('run Filing per un non societario')
    record = fase.esegui_fase('ZZFUND', as_of=AS_OF, servizio_factory=lambda: servizio)
    assert record['stato'] == 'non_applicabile' and record['tipo_strumento']['tipo'] == tipo
    assert servizio.store.get_profile('ZZFUND') is None


def test_db_isolato_assente_nessuna_rete_per_il_tipo(monkeypatch):
    """Fuori registro e DB alternativo senza archivio isolato: la fase si ferma PRIMA di Yahoo (nessuna rete)."""
    def assente():
        raise RuntimeError('DB alternativo: archivio Filing isolato non fornito (filing_service_factory)')
    record = fase.esegui_fase('ZZNEW', as_of=AS_OF, servizio_factory=assente)    # _tipo_yahoo vero = pytest.fail
    assert record['stato'] == 'non_aggiornato' and 'archivio Filing isolato non fornito' in record['motivo']
    assert "apertura dell'archivio Filing" in record['motivo']


def test_tipo_ignoto_dichiarato_e_attivazione_tentata(monkeypatch, tmp_path):
    def giu(ticker):
        raise ConnectionError('yahoo finto giu')
    monkeypatch.setattr(fase, '_tipo_yahoo', giu)
    servizio = _archivio(tmp_path)
    sec_finta(monkeypatch, 'ZZNEW')
    record = fase.esegui_fase('ZZNEW', as_of=AS_OF, nome=NOME, servizio_factory=lambda: servizio,
                              preferenze_predefinite=False)
    assert record['tipo_strumento'] == {'tipo': None, 'origine': None,
                                        'motivo': 'tipo strumento n.d.: ricerca Yahoo non disponibile (ConnectionError)'}
    assert record['stato'] == 'aggiornato'


def test_ripresa_riusa_il_record_e_non_riscarica(monkeypatch, tmp_path):
    servizio = _archivio(tmp_path)
    chiamate = sec_finta(monkeypatch, 'ZZTEST')
    board = _Board()
    primo = fase.fase_documenti_trade_idea(board, as_of=AS_OF, servizio_factory=lambda: servizio, nome=NOME,
                                           preferenze_predefinite=False)
    salvato = json.loads(json.dumps(board.data[fase.CHIAVE_BOARD]))     # come nel checkpoint
    prima = dict(chiamate, documenti=len(chiamate['documenti']))
    ripresa = _Board(**{fase.CHIAVE_BOARD: salvato})
    di_nuovo = fase.fase_documenti_trade_idea(ripresa, as_of=AS_OF, nome=NOME,
                                              servizio_factory=lambda: pytest.fail('archivio riaperto in ripresa'))
    assert di_nuovo == salvato == primo
    assert dict(chiamate, documenti=len(chiamate['documenti'])) == prima


def test_run_gia_oltre_il_ponte_non_riesegue_la_fase(tmp_path):
    board = _Board(_filing_bridge={'contract': ponte.CONTRATTO})
    assert fase.fase_documenti_trade_idea(board, as_of=AS_OF,
                                          servizio_factory=lambda: pytest.fail('fase dopo il ponte')) is None
    assert fase.CHIAVE_BOARD not in board.data


def test_profilo_esistente_al_periodo_atteso_nessun_download(monkeypatch, tmp_path):
    servizio = _archivio(tmp_path)
    sec_finta(monkeypatch, 'ZZTEST')
    fase.esegui_fase('ZZTEST', as_of=AS_OF, nome=NOME, servizio_factory=lambda: servizio,
                     preferenze_predefinite=False)
    servizio.run_programmato = lambda ticker: pytest.fail('run Filing con archivio gia\' aggiornato')
    record = fase.esegui_fase('ZZTEST', as_of=AS_OF, nome=NOME, servizio_factory=lambda: servizio,
                              preferenze_predefinite=False)
    assert record['azione'] == 'nessuna' and record['stato'] == 'aggiornato'
    assert record['freschezza']['ultimo_periodo'] == '2026-06-30' and record['freschezza']['periodo_atteso'] == '2026-06-30'


def test_profilo_esistente_vecchio_aggiornato_prima_della_run(monkeypatch, tmp_path):
    servizio = _archivio(tmp_path)
    sec_finta(monkeypatch, 'ZZTEST')
    fase.esegui_fase('ZZTEST', as_of=AS_OF, nome=NOME, servizio_factory=lambda: servizio,
                     preferenze_predefinite=False)
    chiamati = []
    originale = servizio.run_programmato
    servizio.run_programmato = lambda ticker: chiamati.append(ticker) or originale(ticker)
    record = fase.esegui_fase('ZZTEST', as_of='2027-01-20', nome=NOME, servizio_factory=lambda: servizio,
                              preferenze_predefinite=False)
    assert chiamati == ['ZZTEST'] and record['azione'] == 'aggiornamento'
    assert record['freschezza'] == {'ultimo_periodo': '2026-06-30', 'periodo_atteso': '2026-09-30',
                                    'regola': fase.REGOLA_FRESCHEZZA}
    # appena controllato (intervallo 24 h): dichiarato, nessun secondo download
    assert record['run_filing']['status'] == 'non_dovuto' and record['stato'] == 'non_aggiornato'
    # R-FASE M3: controllo di pochi minuti fa -> deduplicato, dichiarato (nessun secondo download)
    assert record['motivo'].startswith("run Filing non_dovuto: controllo Filing gia' eseguito 0 minuti fa (meno di 10)")
    # titolo SEC sotto il periodo atteso: il sito dell'emittente non sostituisce mai la fonte ufficiale
    assert record['sito'] == {'esito': 'non_applicabile',
                              'motivo': "profilo SEC: il sito dell'emittente non sostituisce la fonte ufficiale"}


def test_run_eseguito_ma_sotto_il_periodo_atteso_dichiarato(monkeypatch, tmp_path):
    servizio = _archivio(tmp_path)
    sec_finta(monkeypatch, 'ZZTEST')
    record = fase.esegui_fase('ZZTEST', as_of='2027-01-20', nome=NOME, servizio_factory=lambda: servizio,
                              preferenze_predefinite=False)
    assert record['stato'] == 'aggiornato' and record['al_periodo_atteso'] is False
    assert ("ultimo periodo 2026-06-30 (SOTTO il periodo atteso 2026-09-30: documento piu' recente non trovato "
            "dalle fonti del profilo)") in record['motivo']


# ---------------------------------------------------------------------------------------------
# main 07/10: archivio ufficiale ESEF piu' vecchio del periodo atteso -> semestrale dal sito dell'emittente
# ---------------------------------------------------------------------------------------------

from test_attivazione_sito_emittente import (FILE, NOME as NOME_EU, REPORTS, SITO, _pagina, annuale,  # noqa: E402
                                             rete as rete_sito, semestrale)

EU = 'ZZTEST.DE'
LEI_EU = 'ZZTEST00000000000099'


def _servizio_esef(tmp_path):
    """Profilo ESEF a blocchi (annuale 2024 fermo) e pipeline: ESEF finto, variante IR con la pipeline VERA."""
    from bellomberg.market_data.filing_profili_auto import profilo_esef
    from bellomberg.storage.filing_store import unisci_variante
    db = tmp_path / 'filing-eu.db'
    with sqlite3.connect(db) as conn:
        ensure_schema(conn)
    store = FilingStore(db)
    store.set_profile(EU, profilo_esef(EU, lei=LEI_EU, nome=NOME_EU, origine='nome', lingua='en'), enabled=True,
                      interval_hours=24)
    root = tmp_path / 'filing_archive_eu'
    cartella = root / EU / 'documents'
    cartella.mkdir(parents=True)
    grezzo = ('<html><body>' + NOME_EU + ' annual financial report year ended 31 December 2024. '
              'Consolidated statements.</body></html>').encode()
    (cartella / 'esef-2024.htm').write_bytes(grezzo)
    from hashlib import sha256
    esef_c = {'fonte': 'ESEF', 'path': str(cartella / 'esef-2024.htm'), 'sha256': sha256(grezzo).hexdigest(),
              'url': 'https://filings.xbrl.org/' + LEI_EU + '/2024-12-31/ESEF/x.htm', 'stato': 'verificato',
              'motivi': [], 'report_date': '2024-12-31', 'filed_date': '2025-04-01', 'form': None,
              'metadati': {'emittente_id': 'LEI:' + LEI_EU, 'lingua': 'en', 'perimetro': 'consolidato',
                           'periodo_fine': '2024-12-31', 'periodo_inizio': '2024-01-01', 'tipo': 'annuale'}}

    def pipeline(profilo, *, archivio):
        ir = [v for v in profilo.get('varianti') or [] if v.get('fonti') == ['ir']]
        if not ir:
            return {'stato': 'parziale', 'motivi': ['ESEF: repository fermo all\'esercizio FY2024'],
                    'candidati': [esef_c]}
        vero = esegui_profilo(unisci_variante(profilo, ir[0]), archivio=archivio, oggi='2026-09-10')
        return {**vero, 'candidati_varianti': [esef_c]}
    return FilingService(store, root, pipeline=pipeline, indexer=lambda *a: {'status': 'skipped'})


def _sito_con_semestrale(rete):
    rete.update({SITO: _pagina((REPORTS, 'Financial reports')),
                 REPORTS: _pagina((FILE + 'Zztest-Half-Year-Financial-Report-2026.pdf', 'Half-Year Report 2026'),
                                  (FILE + 'Zztest-Half-Year-Financial-Report-2025.pdf', 'Half-Year Report 2025'),
                                  (FILE + 'Zztest-Annual-Report-2025.pdf', 'Annual Report 2025')),
                 FILE + 'Zztest-Half-Year-Financial-Report-2026.pdf': semestrale(2026, NOME_EU),
                 FILE + 'Zztest-Half-Year-Financial-Report-2025.pdf': semestrale(2025, NOME_EU),
                 FILE + 'Zztest-Annual-Report-2025.pdf': annuale(2025, NOME_EU)})


def _registro_eu():
    return {'origine': 'sintetico', 'motivo': None, 'veicoli': {EU: {'tipo': 'operating'}}}


def test_esef_fermo_semestrale_dal_sito_come_variante_e_nel_ponte(rete_sito, tmp_path):
    _sito_con_semestrale(rete_sito)
    servizio = _servizio_esef(tmp_path)
    board = _Board(EU)
    record = fase.fase_documenti_trade_idea(board, as_of=AS_OF, servizio_factory=lambda: servizio, nome=NOME_EU,
                                            preferenze_predefinite=False, registro_factory=_registro_eu)
    sito = record['sito']
    assert sito['esito'] == 'variante_aggiunta', sito
    assert sito['tipo'] == 'semestrale' and sito['periodo'] == '2026-06-30'
    assert sito['url'] == FILE + 'Zztest-Half-Year-Financial-Report-2026.pdf'
    assert "aggiunta accanto all'annuale ESEF" in sito['motivo']
    p = servizio.store.get_profile(EU)['profile']
    assert p['esef_modo'] == 'blocchi' and [v['tipo'] for v in p['varianti']] == ['annuale', 'semestrale']
    assert p['varianti'][1]['fonti'] == ['ir'] and p['varianti'][1]['sezioni_intero'] is True
    # etichette del profilo dal sito: alla base, per tipo, mai perse
    etichette = p['varianti_sito']['semestrale']
    assert etichette['origine_documenti'] == sito['origine'] and 'verificato' in etichette
    assert 'controlli_non_superati' in etichette and etichette['periodo_stato'] == sito['periodo_stato']
    assert record['run_filing_sito']['ultimo_periodo'] == '2026-06-30'
    assert record['stato'] == 'aggiornato' and record['al_periodo_atteso'] is True, record['motivo']
    assert "sito dell'emittente: " in record['motivo']
    # cutoff del ponte = oggi: il run Filing (e quindi la disponibilita' osservata del PDF) e' di adesso
    from datetime import date
    ricevuta = ponte.ammetti_per_trade_idea(board, as_of=date.today().isoformat(), servizio_factory=lambda: servizio,
                                            aggiornamento=fase.aggiornamento_per_ponte(record),
                                            registro_factory=fase.registro_con_tipo(record, _registro_eu))
    voce = ricevuta['tickers'][EU]
    assert voce['esito'] == 'ammesso' and voce['periodo_piu_recente'] == '2026-06-30', voce
    semestre = [d for d in voce['documenti'] if d['periodo'] == '2026-06-30']
    assert semestre and semestre[0]['base_origine'] == 'sito_emittente_non_archivio_ufficiale'
    assert any(d['periodo'] == '2024-12-31' for d in voce['documenti'])      # l'annuale ESEF resta
    # il desk rilegge il PDF del sito: byte e verifica del filing rifatti alla lettura
    documenti, _ = ponte.documenti_ponte(board, EU)
    pdf_sito = next(d for d in documenti if d['metadata']['filing_bridge']['periodo'] == '2026-06-30')
    ponte._TESTI.clear()
    assert 'Half-Year Financial Report 2026' in ponte.testo_documento(board, pdf_sito)


def test_esef_fermo_sito_senza_relazioni_nuove_dichiarato(rete_sito, tmp_path):
    rete_sito.update({SITO: _pagina((REPORTS, 'Financial reports')), REPORTS: _pagina()})
    servizio = _servizio_esef(tmp_path)
    record = fase.esegui_fase(EU, as_of=AS_OF, nome=NOME_EU, servizio_factory=lambda: servizio,
                              preferenze_predefinite=False, registro_factory=_registro_eu, passi_extra=[])
    assert record['sito']['esito'] == 'invariato'
    assert record['stato'] == 'aggiornato' and record['al_periodo_atteso'] is False
    assert 'SOTTO il periodo atteso 2026-06-30' in record['motivo'] and "sito dell'emittente: " in record['motivo']
    assert 'run_filing_sito' not in record
    assert len(servizio.store.get_profile(EU)['profile'].get('varianti') or []) == 0


def test_esef_fermo_pdf_del_sito_con_lei_discorde_mai_ammesso(rete_sito, tmp_path):
    """Identita' mai rilassata: il LEI del profilo ESEF arriva alla verifica del sito."""
    from test_attivazione_sito_emittente import _pdf
    _sito_con_semestrale(rete_sito)
    for anno in (2026, 2025):
        rete_sito[FILE + 'Zztest-Half-Year-Financial-Report-%d.pdf' % anno] = _pdf(
            NOME_EU, 'Half-Year Financial Report %d' % anno, 'LEI: ZZOTHER0000000000001',
            'Consolidated interim financial statements of the group', 'for the six months ended 30 June %d' % anno,
            'The group and the management of the company report on the results of the half-year.')
    servizio = _servizio_esef(tmp_path)
    record = fase.esegui_fase(EU, as_of=AS_OF, nome=NOME_EU, servizio_factory=lambda: servizio,
                              preferenze_predefinite=False, registro_factory=_registro_eu, passi_extra=[])
    assert record['sito']['esito'] == 'invariato', record['sito']
    assert 'ZZOTHER0000000000001' in record['sito']['motivo'] and 'altro soggetto' in record['sito']['motivo']
    assert not servizio.store.get_profile(EU)['profile'].get('varianti')


def _semestrale_con_copertine(anno, copertine, pagine_testo=1):
    """PDF sintetico: `copertine` pagine-immagine (solo grafica, nessun testo) + una pagina di testo."""
    import io
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for _ in range(copertine):
        c.setFillColorRGB(0.1, 0.3, 0.6)
        c.rect(50, 300, 500, 400, fill=1)       # copertina/divisoria grafica
        c.showPage()
    y = 800
    for riga in (NOME_EU, 'Half-Year Financial Report %d' % anno,
                 'Consolidated interim financial statements of the group',
                 'for the six months ended 30 June %d' % anno,
                 'The group and the management of the company report on the results of the half-year.',
                 'Risks of the business in %d and the outlook of the group are described below.' % anno):
        c.drawString(60, y, riga)
        y -= 20
    c.showPage()
    for n in range(pagine_testo - 1):
        c.drawString(60, 800, 'Notes to the interim statements, page %d: demand and order book remain stable.' % n)
        c.showPage()
    c.save()
    return buf.getvalue()


def _ponte_eu(record, servizio, board):
    from datetime import date
    return ponte.ammetti_per_trade_idea(board, as_of=date.today().isoformat(), servizio_factory=lambda: servizio,
                                        aggiornamento=fase.aggiornamento_per_ponte(record),
                                        registro_factory=fase.registro_con_tipo(record, _registro_eu))


@pytest.mark.parametrize('copertine, pagine_testo, verificato', [(1, 1, True), (2, 3, True), (2, 1, False)])
def test_pdf_verificato_con_copertine_immagine_resta_verificato_con_nota(rete_sito, tmp_path, copertine, pagine_testo,
                                                                        verificato):
    """Decisione PM 07/10: pagine senza testo = NOTA se le prove stanno nelle pagine col testo; oltre il 50% no."""
    _sito_con_semestrale(rete_sito)
    for anno in (2026, 2025):
        rete_sito[FILE + 'Zztest-Half-Year-Financial-Report-%d.pdf' % anno] = _semestrale_con_copertine(anno, copertine,
                                                                                                    pagine_testo)
    servizio = _servizio_esef(tmp_path)
    board = _Board(EU)
    record = fase.fase_documenti_trade_idea(board, as_of=AS_OF, servizio_factory=lambda: servizio, nome=NOME_EU,
                                            preferenze_predefinite=False, registro_factory=_registro_eu)
    assert record['sito']['esito'] == 'variante_aggiunta', record['sito']
    voce = _ponte_eu(record, servizio, board)['tickers'][EU]
    doc = next(d for d in voce['documenti'] if d['periodo'] == '2026-06-30')
    if verificato:
        assert doc.get('verifica', 'verificato') == 'verificato' and 'etichetta_verifica' not in doc, doc
        assert doc['nota_pagine_senza_testo'] == ('%d pagine senza testo (immagini) su %d: %s; prove della verifica '
                                                  'nelle pagine con testo' % (copertine, copertine + pagine_testo,
                                                                               list(range(1, copertine + 1))))
        documenti, dichiarazione = ponte.documenti_ponte(board, EU)
        sem = next(d for d in documenti if d['metadata']['filing_bridge']['periodo'] == '2026-06-30')
        assert 'fonte_primaria_verificata' not in sem['metadata']['filing_bridge']
        assert sem['metadata']['filing_bridge']['nota_pagine_senza_testo'] == doc['nota_pagine_senza_testo']
        indice = ponte.indice_compatto({'ticker': EU, 'documents': documenti, 'filing_bridge': dichiarazione,
                                        'issues': []})       # la nota arriva a Capo/R2/Red Team
        voce_indice = next(d for d in indice['documenti'] if d['id'] == sem['id'])
        assert voce_indice['nota_pagine_senza_testo'] == doc['nota_pagine_senza_testo']
        assert 'verificato' not in voce_indice
        ponte._TESTI.clear()
        assert 'Half-Year Financial Report 2026' in ponte.testo_documento(board, sem)   # rilettura del desk
    else:
        assert doc['verifica'] == 'non_verificato'
        assert '(2 su 3, oltre la soglia del 50%)' in doc['etichetta_verifica'], doc['etichetta_verifica']
    # R-FASE mutante D: il catalogo del Red Team dichiara verificato / non verificato con l'etichetta
    from bellomberg.agents import trade_idea
    board.tool_receipts, board.source_qualification = [], {}
    catalogo = {r['id']: r for r in trade_idea.review_evidence_catalog(board)}
    voce_rt = catalogo[doc['id']]
    if verificato:
        assert voce_rt['verified'] is True and 'label' not in voce_rt
    else:
        assert voce_rt['verified'] is False and voce_rt['label'] == doc['etichetta_verifica']
        assert voce_rt['scope'].startswith('Unverified archived issuer document')


def test_pdf_con_copertina_e_prove_non_riverificate_resta_non_verificato(rete_sito, tmp_path, monkeypatch):
    from bellomberg.valuation import filing_pdf_evidence
    _sito_con_semestrale(rete_sito)
    for anno in (2026, 2025):
        rete_sito[FILE + 'Zztest-Half-Year-Financial-Report-%d.pdf' % anno] = _semestrale_con_copertine(anno, 1)
    servizio = _servizio_esef(tmp_path)
    board = _Board(EU)
    record = fase.fase_documenti_trade_idea(board, as_of=AS_OF, servizio_factory=lambda: servizio, nome=NOME_EU,
                                            preferenze_predefinite=False, registro_factory=_registro_eu)

    def prove_fuori(*a, **k):
        raise ValueError('PDF filing verification invalid: proof does not belong to one primary PDF page')
    monkeypatch.setattr(filing_pdf_evidence, 'verify_filing_pdf', prove_fuori)
    doc = next(d for d in _ponte_eu(record, servizio, board)['tickers'][EU]['documenti'] if d['periodo'] == '2026-06-30')
    assert doc['verifica'] == 'non_verificato'
    assert 'verifica PDF non superata: PDF filing verification invalid: proof does not belong' in doc['etichetta_verifica']


def test_esef_fermo_sito_guasto_non_cancella_il_run(monkeypatch, tmp_path):
    from bellomberg.market_data import esef_sito

    def giu(*a, **k):
        raise ConnectionError('sito finto giu')
    monkeypatch.setattr(esef_sito, 'scopri', giu)
    servizio = _servizio_esef(tmp_path)
    record = fase.esegui_fase(EU, as_of=AS_OF, nome=NOME_EU, servizio_factory=lambda: servizio,
                              preferenze_predefinite=False, registro_factory=_registro_eu, passi_extra=[])
    assert record['sito'] == {'esito': 'errore', 'motivo': ("durante «esplorazione del sito dell'emittente»: "
                                                            "ConnectionError: sito finto giu")}
    assert record['stato'] == 'aggiornato' and record['run_filing']['status'] == 'parziale'


def test_tempo_massimo_dichiarato_360_con_300_a_filing_e_sito():
    assert (fase.TEMPO_MAX_S, fase.RISERVA_PASSI_S) == (360, 60)
    record = fase.esegui_fase('ZZETF', as_of=AS_OF, servizio_factory=lambda: None, passi_extra=[],
                              registro_factory=lambda: {'origine': 's', 'motivo': None,
                                                        'veicoli': {'ZZETF': {'tipo': 'etf'}}})
    assert (record['tempo_max_s'], record['riserva_passi_s']) == (360, 60)


def test_profilo_disattivato_dall_utente_rispettato(monkeypatch, tmp_path):
    servizio = _archivio(tmp_path)
    sec_finta(monkeypatch, 'ZZTEST')
    fase.esegui_fase('ZZTEST', as_of=AS_OF, nome=NOME, servizio_factory=lambda: servizio,
                     preferenze_predefinite=False)
    riga = servizio.store.get_profile('ZZTEST')
    servizio.store.set_profile('ZZTEST', riga['profile'], enabled=False, interval_hours=riga['interval_hours'])
    record = fase.esegui_fase('ZZTEST', as_of='2027-01-20', servizio_factory=lambda: servizio,
                              preferenze_predefinite=False)
    assert record['stato'] == 'non_aggiornato' and 'disattivato' in record['motivo']


def test_periodo_atteso():
    assert fase.periodo_atteso('2026-09-10') == '2026-06-30'
    assert fase.periodo_atteso('2026-08-29') == '2026-06-30'
    assert fase.periodo_atteso('2026-05-29') == '2025-12-31'
    assert fase.periodo_atteso('2026-06-01') == '2026-03-31'
    assert fase.periodo_atteso('2026-02-15') == '2025-09-30'


def test_passi_extra_ricevono_il_contesto_e_ogni_guasto_e_dichiarato(monkeypatch, tmp_path):
    visto = {}

    def buono(ticker, *, as_of, scadenza_monotonic, contesto):
        visto.update(ticker=ticker, as_of=as_of, contesto=contesto, residuo=scadenza_monotonic - time.monotonic())
        return {'stato': 'ok', 'motivo': None}

    def rotto(ticker, **_kw):
        raise ValueError('passo finto rotto')

    def lento(ticker, **_kw):
        time.sleep(5)
        return {'stato': 'ok'}
    record = fase.esegui_fase('ZZETF', as_of=AS_OF, servizio_factory=lambda: pytest.fail('archivio'),
                              registro_factory=lambda: {'origine': 's', 'motivo': None, 'veicoli': {'ZZETF': {'tipo': 'etf'}}},
                              tempo_max_s=1.5, riserva_passi_s=0.5,
                              passi_extra=[('buono', buono), ('rotto', rotto), ('lento', lento), ('dopo', buono)])
    assert visto['ticker'] == 'ZZETF' and visto['contesto']['stato'] == 'non_applicabile' and visto['residuo'] > 0
    assert record['passi']['buono'] == {'stato': 'ok', 'motivo': None}
    assert record['passi']['rotto'] == {'stato': 'errore', 'motivo': 'ValueError: passo finto rotto'}
    assert record['passi']['lento']['stato'] == 'tempo_scaduto'
    assert record['passi']['dopo']['stato'] == 'tempo_scaduto'
    assert record['durata_s'] < 3


def test_passo_predefinito_assente_dichiarato(monkeypatch):
    import builtins
    vero = builtins.__import__

    def senza(name, *a, **k):
        if name.endswith('freschezza_trimestrale'):
            raise ImportError('modulo finto assente')
        return vero(name, *a, **k)
    monkeypatch.undo()          # passi_predefiniti vera (l'autouse la sostituisce)
    monkeypatch.setattr(builtins, '__import__', senza)
    passi = fase.passi_predefiniti()
    monkeypatch.undo()
    assert [n for n, _ in passi] == ['freschezza']
    assert passi[0][1]('ZZTEST', as_of=AS_OF, scadenza_monotonic=0, contesto={}) == {
        'stato': 'non_disponibile', 'motivo': 'passo non disponibile: ImportError'}


# ---------------------------------------------------------------------------------------------
# End-to-end Trade Idea offline (fixture no_workbook_case): titolo nuovo fino al Capo e al PDF
# ---------------------------------------------------------------------------------------------

from test_trade_idea_no_workbook_e2e import no_workbook_case  # noqa: E402,F401  (fixture)
from test_trade_idea_store import db_path, migrated, store  # noqa: E402,F401
from _smtp_cattura import smtp  # noqa: E402,F401
from test_trade_idea_economic import IDENTITY  # noqa: E402


def _con_servizio(monkeypatch, factory):
    from bellomberg.agents import trade_idea
    monkeypatch.setattr(trade_idea, 'execute_trade_idea',
                        partial(trade_idea.execute_trade_idea, filing_service_factory=factory))


def _pdf_pronto(detail):
    assert detail['run']['technical_status'] == 'completed', detail['run']['reason']
    manifest = detail['artifacts']
    assert manifest['complete_package_status'] == 'ready'
    pdf = Path(manifest['artifacts'][0]['path'])
    assert manifest['artifacts'][0]['kind'] == 'pdf' and pdf.read_bytes().startswith(b'%PDF-')


def _dossier(detail):
    return detail['progress']['checkpoint']['data']['_research_thesis']['dossiers'][IDENTITY['ticker']]


# ---------------------------------------------------------------------------------------------
# CABLAGGIO di produzione (revisione R-FASE 07/10): nessuna factory iniettata
# ---------------------------------------------------------------------------------------------

class _Fermo(Exception):
    pass


def test_cablaggio_produzione_execute_passa_none_e_db_predefinito(no_workbook_case, monkeypatch):
    """Col DB predefinito execute_trade_idea passa servizio_factory=None e preferenze predefinite: e' il
    valore che la fase deve risolvere da sola (il test sotto). Il run si ferma alla fase (nessuna rete)."""
    from bellomberg.agents import trade_idea
    from bellomberg.core import paths
    case = no_workbook_case
    visti = []

    def registra(blackboard, **kw):
        visti.append(kw)
        raise _Fermo('fermo dopo la registrazione')
    monkeypatch.setattr(paths, 'SQLITE_PATH', Path(case.current.db_path))       # = DB predefinito
    monkeypatch.setattr(trade_idea, '_fase_documenti_e_ponte', registra)
    run_id = case.current.create_run(case.request, idempotency_key='cablaggio-prod')['run']['id']
    try:
        case.execute(run_id, 'cablaggio-prod')
    except Exception:
        pass
    assert len(visti) == 1, visti
    assert visti[0]['servizio_factory'] is None and visti[0]['preferenze_predefinite'] is True
    assert visti[0]['stop_fn']() is False          # R-FASE B4: lo stop del PM arriva alla fase (qui non chiesto)


def test_cablaggio_produzione_factory_none_usa_default_service(monkeypatch, tmp_path):
    """servizio_factory=None (produzione) -> api.filing_routes.default_service, UNA volta, per fase e ponte."""
    from bellomberg.agents import trade_idea
    from bellomberg.api import filing_routes
    servizio = _archivio(tmp_path)
    sec_finta(monkeypatch, 'ZZTEST')
    chiamate = []
    monkeypatch.setattr(filing_routes, 'default_service', lambda: chiamate.append(1) or servizio)
    # preferenze «predefinite» = file sotto DATA_DIR (tmp della sessione di test)
    board = _Board()
    trade_idea._fase_documenti_e_ponte(board, as_of=AS_OF, servizio_factory=None, nome=NOME,
                                       preferenze_predefinite=True)
    assert chiamate == [1]
    record = board.data[fase.CHIAVE_BOARD]
    assert record['azione'] == 'attivazione' and record['stato'] == 'aggiornato', record['motivo']
    voce = board.data[ponte.CHIAVE_BOARD]['tickers']['ZZTEST']
    assert voce['esito'] == 'ammesso' and voce['periodo_piu_recente'] == '2026-06-30'


def test_cablaggio_produzione_default_service_non_importabile_dichiarato(monkeypatch):
    import builtins
    from bellomberg.agents import trade_idea
    vero = builtins.__import__

    def senza(name, *a, **k):
        if name == 'bellomberg.api.filing_routes':
            raise ImportError('modulo finto assente')
        return vero(name, *a, **k)
    monkeypatch.setattr(builtins, '__import__', senza)
    board = _Board()
    trade_idea._fase_documenti_e_ponte(board, as_of=AS_OF, servizio_factory=None, nome=NOME,
                                       preferenze_predefinite=True)
    monkeypatch.setattr(builtins, '__import__', vero)
    assert 'archivio Filing predefinito non importabile: ImportError' in board.data[fase.CHIAVE_BOARD]['motivo']
    assert board.data[ponte.CHIAVE_BOARD]['tickers']['ZZTEST']['esito'] == 'archivio_in_errore'


def test_cablaggio_passi_predefiniti_sono_la_cascata_vera_con_fonti_vive(monkeypatch):
    """In produzione (DB predefinito) il passo extra e' passo_freschezza SENZA fonti iniettate -> FontiVive."""
    from bellomberg.market_data import freschezza_trimestrale as ft
    passi = _VERI['passi_predefiniti']()
    assert [n for n, _ in passi] == ['freschezza'] and passi[0][1] is ft.passo_freschezza
    usate = []

    def cascata(ticker, *, fonti, **kw):
        usate.append(fonti)
        return {'stato': 'non_applicabile'}
    monkeypatch.setattr(ft, 'cascata', cascata)
    monkeypatch.setattr(ft, '_scrivi_cache', lambda esito: None)
    passi[0][1]('ZZTEST', as_of=AS_OF, scadenza_monotonic=time.monotonic() + 5, contesto={})
    assert usate == [None]      # fonti=None -> la cascata usa FontiVive (default di produzione)


def test_cablaggio_registro_predefinito_e_il_negozio_dei_veicoli(monkeypatch):
    from bellomberg.storage import classificazione
    letti = []
    monkeypatch.setattr(classificazione, 'carica_veicoli', lambda *a, **k: letti.append(1) or {'veicoli': {}})
    assert _VERI['registro_fase']() == {'veicoli': {}} and _VERI['registro_ponte']() == {'veicoli': {}}
    assert letti == [1, 1]


def test_cablaggio_tipo_yahoo_legge_il_quote_type_del_simbolo_esatto(monkeypatch):
    import requests
    chieste = []

    class Risposta:
        def raise_for_status(self):
            pass

        def json(self):
            return {'quotes': [{'symbol': 'ZZETF', 'quoteType': 'ETF'}, {'symbol': 'ZZETF.L', 'quoteType': 'EQUITY'}]}
    monkeypatch.setattr(requests, 'get', lambda url, **kw: chieste.append((url, kw['params']['q'])) or Risposta())
    assert _VERI['tipo_yahoo']('ZZETF') == 'ETF'
    assert chieste == [('https://query2.finance.yahoo.com/v1/finance/search', 'ZZETF')]


# ---------------------------------------------------------------------------------------------
# Revisione R-FASE 07/10: M2, M3, M4, B1, B3, B4, mutante E
# ---------------------------------------------------------------------------------------------

def test_m2_ultimo_periodo_conta_solo_i_verificati_senza_riserve():
    base = {'path': '/x', 'sha256': 'a' * 64}
    risultato = {'candidati': [
        {**base, 'stato': 'non_verificato', 'report_date': '2026-06-30', 'motivi': ['emittente: prova testuale assente']},
        {**base, 'stato': 'periodo_da_confermare', 'report_date': '2026-09-30', 'motivi': []},
        {**base, 'stato': 'verificato', 'report_date': '2026-03-31', 'motivi': ['verificato con riserve']},
        {**base, 'stato': 'verificato', 'metadati': {'periodo_fine': '2025-12-31'}, 'motivi': []}]}
    assert fase.ultimo_periodo(risultato) == '2025-12-31'


def _gia_controllato(monkeypatch, tmp_path, *, qualitativo=False):
    servizio = _archivio(tmp_path)
    sec_finta(monkeypatch, 'ZZTEST')
    fase.esegui_fase('ZZTEST', as_of=AS_OF, nome=NOME, servizio_factory=lambda: servizio, preferenze_predefinite=False)
    if qualitativo:
        riga = servizio.store.get_profile('ZZTEST')
        servizio.store.set_profile('ZZTEST', riga['profile'], enabled=True, interval_hours=riga['interval_hours'],
                                   qualitative_enabled=True)   # stessa impronta del profilo: non dovuto
    return servizio


def test_m3_periodo_mancante_forza_un_controllo_senza_ai(monkeypatch, tmp_path):
    servizio = _gia_controllato(monkeypatch, tmp_path, qualitativo=True)
    servizio.judge = lambda *a, **k: pytest.fail('giudice AI chiamato dalla Trade Idea')
    monkeypatch.setattr(fase, '_minuti_dall_ultimo_controllo', lambda store, ticker: 180.0)
    prima = len(servizio.store.list_runs('ZZTEST', limit=50))
    record = fase.esegui_fase('ZZTEST', as_of='2027-01-20', nome=NOME, servizio_factory=lambda: servizio,
                              preferenze_predefinite=False, passi_extra=[])
    run = record['run_filing']
    assert run['forzato'].startswith('controllo programmato non dovuto') and run['status'] in ('ok', 'parziale')
    salvato = servizio.store.get_run(run['id'])
    assert salvato['trigger'] == 'manual' and len(servizio.store.list_runs('ZZTEST', limit=50)) == prima + 1
    assert salvato['judgment']['status'] == 'skipped'
    assert salvato['judgment']['reason'] in (fase.GIUDIZIO_NON_RICHIESTO, 'diff non confrontabile',
                                             'nessun cambiamento testuale')


def test_m3_controllo_di_pochi_minuti_fa_non_si_ripete(monkeypatch, tmp_path):
    servizio = _gia_controllato(monkeypatch, tmp_path)
    monkeypatch.setattr(fase, '_minuti_dall_ultimo_controllo', lambda store, ticker: 3.0)
    prima = len(servizio.store.list_runs('ZZTEST', limit=50))
    record = fase.esegui_fase('ZZTEST', as_of='2027-01-20', nome=NOME, servizio_factory=lambda: servizio,
                              preferenze_predefinite=False, passi_extra=[])
    assert len(servizio.store.list_runs('ZZTEST', limit=50)) == prima          # nessun secondo download
    assert record['run_filing']['status'] == 'non_dovuto' and '3 minuti fa (meno di 10)' in record['motivo']


def test_m4_dopo_il_tempo_il_thread_chiude_il_run_e_non_avvia_altro(monkeypatch, tmp_path):
    import threading as th
    servizio = _archivio(tmp_path)
    sec_finta(monkeypatch, 'ZZTEST')
    sblocca, indicizzati, siti = th.Event(), [], []
    vera = servizio.pipeline

    def lenta(profilo, **kw):
        sblocca.wait(20)
        return vera(profilo, **kw)
    servizio.pipeline = lenta
    servizio.indexer = lambda *a: indicizzati.append(1) or {'status': 'ok'}
    monkeypatch.setattr(fase, '_dal_sito', lambda *a, **k: siti.append(1) or {'esito': 'invariato'})
    record = fase.esegui_fase('ZZTEST', as_of='2027-01-20', nome=NOME, servizio_factory=lambda: servizio,
                              preferenze_predefinite=False, tempo_max_s=1.5, riserva_passi_s=0.2, passi_extra=[])
    assert record['tempo_scaduto'] is True and 'run Filing del titolo' in record['motivo']
    sblocca.set()
    for filo in [t for t in th.enumerate() if t.name == 'trade-idea-filing-ZZTEST']:
        filo.join(20)
    runs = servizio.store.list_runs('ZZTEST', limit=10)
    assert len(runs) == 1 and runs[0]['status'] in ('ok', 'parziale')          # chiuso, mai orfano
    assert indicizzati == [] and siti == []                                     # niente Chroma, niente sito
    assert servizio.store.get_run(runs[0]['id'])['index']['reason'] == fase.INDICE_RIMANDATO


def test_b1_ponte_oltre_il_suo_tetto_dichiarato_e_mai_scritto_dopo(monkeypatch, tmp_path):
    from bellomberg.agents import trade_idea
    servizio = _archivio(tmp_path)
    sec_finta(monkeypatch, 'ZZTEST')
    monkeypatch.setattr(fase, 'TEMPO_MAX_PONTE_S', 0.5)
    vera = ponte.selezione_ponte

    def lenta(*a, **k):
        time.sleep(2)
        return vera(*a, **k)
    monkeypatch.setattr(ponte, 'selezione_ponte', lenta)
    board = _Board()
    trade_idea._fase_documenti_e_ponte(board, as_of=AS_OF, servizio_factory=lambda: servizio, nome=NOME,
                                       preferenze_predefinite=False)
    assert board.data['_filing_ponte_misura']['esito'] == 'tempo_scaduto'
    assert board.data['_filing_ponte_misura']['tetto_s'] == 0.5
    voce = board.data[ponte.CHIAVE_BOARD]['tickers']['ZZTEST']
    assert voce['esito'] == 'ponte_non_eseguito' and voce['motivi'] == ['ponte non eseguito: TempoPonteScaduto']
    time.sleep(2.5)                                    # il thread del ponte finisce: la lavagna vera NON cambia
    assert board.data[ponte.CHIAVE_BOARD]['tickers']['ZZTEST']['esito'] == 'ponte_non_eseguito'


def test_b1_durata_del_ponte_misurata(monkeypatch, tmp_path):
    from bellomberg.agents import trade_idea
    servizio = _archivio(tmp_path)
    sec_finta(monkeypatch, 'ZZTEST')
    board = _Board()
    trade_idea._fase_documenti_e_ponte(board, as_of=AS_OF, servizio_factory=lambda: servizio, nome=NOME,
                                       preferenze_predefinite=False)
    misura = board.data['_filing_ponte_misura']
    assert misura['esito'] == 'ok' and misura['tetto_s'] == fase.TEMPO_MAX_PONTE_S and misura['durata_s'] >= 0
    assert board.data[ponte.CHIAVE_BOARD]['tickers']['ZZTEST']['esito'] == 'ammesso'


def test_b3_avvisi_non_applicabili_non_rubano_il_posto_alla_relazione():
    def riga(n, stato, periodo, motivo):
        return {'url': 'https://www.sec.gov/x/%d.htm' % n, 'path': '/a/%d' % n, 'sha256': '%064d' % n,
                'stato': stato, 'report_date': periodo, 'filed_date': periodo, 'motivi': [motivo]}
    candidati = [
        {**riga(1, 'verificato', '2028-12-31', None), 'motivi': [],
         'metadati': {'periodo_fine': '2028-12-31', 'tipo': 'annuale'}},
        riga(2, 'non_verificato', '2029-06-30', 'ValueError: periodo testuale assente'),
        riga(3, 'non_applicabile', '2029-08-20', 'avviso di dividendo: non e\' la relazione cercata'),
        riga(4, 'non_applicabile', '2029-09-01', 'nomina del consiglio: non e\' la relazione cercata')]
    scelti, altri, _ = ponte._seleziona(candidati)
    urls = [c['url'] for c in scelti]
    assert 'https://www.sec.gov/x/2.htm' in urls and 'https://www.sec.gov/x/4.htm' in urls
    assert 'https://www.sec.gov/x/3.htm' not in urls
    assert any(a['url'].endswith('/3.htm') and 'oltre il tetto' in a['motivo'] for a in altri)


def test_b4_stop_del_pm_ascoltato_durante_la_fase(tmp_path):
    from threading import Event
    sblocca, chiamate = Event(), []

    def appeso():
        sblocca.wait(30)
        raise RuntimeError('fine')

    def stop():
        chiamate.append(1)
        return len(chiamate) >= 2
    inizio = time.monotonic()
    record = fase.esegui_fase('ZZNEW', as_of=AS_OF, servizio_factory=appeso, passi_extra=[('x', lambda *a, **k: {})],
                              stop_fn=stop)
    sblocca.set()
    assert time.monotonic() - inizio < 5
    assert record['fermato_dal_pm'] is True and record['motivo'].startswith('fase documenti interrotta: stop del PM')
    assert record['passi'] == {'x': {'stato': 'non_eseguito', 'motivo': 'stop del PM durante la fase documenti'}}


def test_b4_profilo_creato_dichiarato_nel_log_e_cutoff_nel_risultato(monkeypatch, tmp_path, capsys):
    from bellomberg.agents import trade_idea
    servizio = _archivio(tmp_path)
    sec_finta(monkeypatch, 'ZZTEST')
    board = _Board()
    trade_idea._fase_documenti_e_ponte(board, as_of=AS_OF, servizio_factory=lambda: servizio, nome=NOME,
                                       preferenze_predefinite=False)
    record = board.data[fase.CHIAVE_BOARD]
    assert record['cutoff'] == {'as_of': AS_OF, 'regola': fase.CUTOFF}
    assert record['profilo_creato'].startswith('profilo Filing CREATO dalla Trade Idea del ' + AS_OF)
    assert ('[TRADE_IDEA]  [i] ZZTEST: profilo Filing CREATO dalla Trade Idea del ' + AS_OF) in capsys.readouterr().out


def test_e_sito_non_esplorato_con_consigliere_in_corso(monkeypatch, tmp_path):
    from bellomberg.market_data import esef_sito
    monkeypatch.setattr(esef_sito, 'consigliere_in_corso', lambda: True)
    monkeypatch.setattr(esef_sito, 'scopri', lambda *a, **k: pytest.fail('sito esplorato durante la run'))
    servizio = _servizio_esef(tmp_path)
    record = fase.esegui_fase(EU, as_of=AS_OF, nome=NOME_EU, servizio_factory=lambda: servizio,
                              preferenze_predefinite=False, registro_factory=_registro_eu, passi_extra=[])
    assert record['sito'] == {'esito': 'non_applicabile', 'motivo': 'sito non esplorato: run del Consigliere in corso'}


# --- seconda verifica R-FASE 07/10: ripresa dopo il sigillo, completa_6k dopo lo stop -----------------------

def _board_in_ripresa():
    board = _Board()
    ricevuta = ponte._ricevuta_errore('2026-10-07', ['ZZTEST'], 'ok', 'ricevuta valida di prova')    # sigillata
    board.data.update({ponte.CHIAVE_BOARD: ricevuta, fase.CHIAVE_BOARD: {'stato': 'aggiornato', 'motivo': 'x',
                                                                         'ticker': 'ZZTEST'},
                       '_research_thesis': {'sigillo': 'finto'}})
    return board, json.dumps(ricevuta, sort_keys=True)


def test_ripresa_ricevuta_valida_riusata_anche_con_rilettura_lenta(monkeypatch):
    """Prova del revisore (verifica_ripresa.py) resa test: tetto 0,1 s e rilettura lenta non toccano la ricevuta."""
    from bellomberg.agents import trade_idea
    monkeypatch.setattr(fase, 'TEMPO_MAX_PONTE_S', 0.1)
    vera = ponte._ricevuta_board

    def lenta(b):
        time.sleep(0.5)
        return vera(b)
    monkeypatch.setattr(ponte, '_ricevuta_board', lenta)
    board, prima = _board_in_ripresa()
    trade_idea._fase_documenti_e_ponte(board, as_of='2026-10-07', nome=NOME, preferenze_predefinite=True,
                                       servizio_factory=lambda: pytest.fail('archivio aperto in ripresa'))
    assert json.dumps(board.data[ponte.CHIAVE_BOARD], sort_keys=True) == prima
    assert '_filing_ponte_misura' not in board.data              # nessun thread col tetto in ripresa


def test_ripresa_ponte_che_solleva_non_cancella_la_ricevuta(monkeypatch):
    from bellomberg.agents import trade_idea

    def rotto(*a, **k):
        raise RuntimeError('ponte finto rotto in ripresa')
    monkeypatch.setattr(ponte, 'ammetti_per_trade_idea', rotto)
    board, prima = _board_in_ripresa()
    trade_idea._fase_documenti_e_ponte(board, as_of='2026-10-07', nome=NOME, preferenze_predefinite=True,
                                       servizio_factory=lambda: pytest.fail('archivio aperto in ripresa'))
    assert json.dumps(board.data[ponte.CHIAVE_BOARD], sort_keys=True) == prima


def test_ponte_non_eseguito_mai_sopra_una_ricevuta_valida():
    board, prima = _board_in_ripresa()
    righe = []
    tenuta = ponte.ponte_non_eseguito(board, TimeoutError('x'), log=righe.append, tickers=['ZZTEST'],
                                      mantieni_esistente=True)
    assert json.dumps(tenuta, sort_keys=True) == prima == json.dumps(board.data[ponte.CHIAVE_BOARD], sort_keys=True)
    assert righe == ["  [!] Ponte Filing: TimeoutError dopo una ricevuta gia' valida: ricevuta mantenuta"]
    # ricevuta manomessa (impronta che non torna): si sostituisce con la lacuna dichiarata
    board.data[ponte.CHIAVE_BOARD] = {**board.data[ponte.CHIAVE_BOARD], 'stato': 'manomessa'}
    nuova = ponte.ponte_non_eseguito(board, TimeoutError('x'), tickers=['ZZTEST'], mantieni_esistente=True)
    assert nuova['tickers']['ZZTEST']['motivi'] == ['ponte non eseguito: TimeoutError']


def test_completa_6k_mai_dopo_lo_stop(monkeypatch, tmp_path):
    import threading as th
    from bellomberg.market_data import filing_attivazione
    servizio = _archivio(tmp_path)
    sei_k, run = [], []

    def attiva_lenta(store, ticker, **kw):
        time.sleep(1.5)                        # l'attivazione supera il tempo della fase
        return {'ticker': ticker, 'esito': 'attivato', 'motivo': 'finto', 'profilo_versione': 1}
    monkeypatch.setattr(filing_attivazione, 'attiva', attiva_lenta)
    monkeypatch.setattr(filing_attivazione, 'completa_6k', lambda *a, **k: sei_k.append(1) or {'esito': 'aggiunto'})
    servizio.run_programmato = lambda ticker: run.append(1)
    record = fase.esegui_fase('ZZTEST', as_of=AS_OF, nome=NOME, servizio_factory=lambda: servizio,
                              preferenze_predefinite=False, tempo_max_s=1.0, riserva_passi_s=0.2, passi_extra=[])
    assert record['tempo_scaduto'] is True
    for filo in [t for t in th.enumerate() if t.name == 'trade-idea-filing-ZZTEST']:
        filo.join(10)
    assert sei_k == [] and run == []          # nessuna scrittura del profilo e nessun run dopo lo stop


def test_e2e_titolo_nuovo_attivato_scaricato_e_visto_dai_desk_e_dal_capo(no_workbook_case, monkeypatch):
    case = no_workbook_case
    servizio = _archivio(case.root, 'filing-nuovo')
    sec_finta(monkeypatch, IDENTITY['ticker'], nome=IDENTITY['name'])
    _con_servizio(monkeypatch, lambda: servizio)
    assert servizio.store.get_profile(IDENTITY['ticker']) is None
    run_id = case.current.create_run(case.request, idempotency_key='nuovo-e2e')['run']['id']
    detail = case.execute(run_id, 'nuovo')
    _pdf_pronto(detail)
    data = detail['progress']['checkpoint']['data']
    record = data[fase.CHIAVE_BOARD]
    assert record["azione"] == "attivazione" and record["stato"] == "aggiornato", json.dumps(record, indent=1)
    assert record['profilo']['cik'] == CIK
    # DB alternativo: i passi extra non usano fonti vive implicite (lacuna dichiarata)
    assert record['passi'] == {'freschezza': {'stato': 'non_eseguito', 'motivo':
                               'DB alternativo: fonti vive dei passi extra non fornite (isolamento)'}}
    assert servizio.store.get_profile(IDENTITY['ticker'])['profile']['cik'] == CIK
    # DB alternativo: esito dell'attivazione nelle preferenze accanto al DB Filing isolato
    pref = (Path(servizio.store.db_path).parent / 'filing_preferenze.json').read_text(encoding='utf-8')
    assert 'attivato dalla Trade Idea del ' + record['as_of'] in pref
    dossier = _dossier(detail)
    docs = [d for d in dossier['documents'] if (d.get('metadata') or {}).get('filing_bridge')]
    assert docs and max(d['metadata']['filing_bridge']['periodo'] for d in docs) == '2026-06-30'
    assert dossier['filing_bridge']['aggiornamento_pre_run']['stato'] == 'aggiornato'
    # i desk (read_company_dossier, catalogo) e il Capo vedono gli ID citabili del documento nuovo
    capo = json.dumps(case.providers[-1], ensure_ascii=False, default=str)
    assert all(d['id'] in capo for d in docs)
    desk = [json.dumps(p, ensure_ascii=False, default=str) for p in case.providers[:-1]]
    assert any(docs[0]['id'] in testo for testo in desk)


@pytest.mark.parametrize('guasto', ['rete', 'timeout', 'db_illeggibile', 'attivazione'])
def test_e2e_ogni_fonte_giu_la_run_arriva_al_pdf_con_la_lacuna(no_workbook_case, monkeypatch, guasto):
    from bellomberg.market_data import filing_attivazione
    case = no_workbook_case
    servizio = _archivio(case.root, 'filing-giu')
    sblocca = Event()
    factory = lambda: servizio
    attesa = 'profilo Filing non attivato'
    if guasto == 'rete':
        sec_finta(monkeypatch, IDENTITY['ticker'], nome=IDENTITY['name'], giu=ConnectionError('rete finta giu'))
    elif guasto == 'timeout':
        monkeypatch.setattr(fase, 'TEMPO_MAX_S', 1.0)
        monkeypatch.setattr(fase, 'RISERVA_PASSI_S', 0.3)

        def factory():
            sblocca.wait(30)
            return servizio
        attesa = 'documenti non scaricati in tempo'
    elif guasto == 'db_illeggibile':
        def factory():
            raise sqlite3.DatabaseError('file is not a database')
        attesa = 'DatabaseError'
    else:
        def rotta(*a, **k):
            raise RuntimeError('attivazione finta rotta')
        monkeypatch.setattr(filing_attivazione, 'attiva', rotta)
        attesa = 'RuntimeError: attivazione finta rotta'
    _con_servizio(monkeypatch, factory)
    run_id = case.current.create_run(case.request, idempotency_key='giu-' + guasto)['run']['id']
    try:
        detail = case.execute(run_id, 'giu-' + guasto)
    finally:
        sblocca.set()
    _pdf_pronto(detail)
    record = detail['progress']['checkpoint']['data'][fase.CHIAVE_BOARD]
    assert record['stato'] == 'non_aggiornato' and attesa in record['motivo'], record
    dossier = _dossier(detail)
    lacune = json.dumps(dossier['issues'] + [dossier['filing_bridge']], ensure_ascii=False)
    assert attesa in lacune or guasto == 'db_illeggibile'
    if guasto == 'db_illeggibile':
        assert dossier['filing_bridge']['esito'] == 'archivio_in_errore'


def test_e2e_fase_che_solleva_non_ferma_la_run(no_workbook_case, monkeypatch):
    """Cattura ampia SOLO attorno alla fase: un'eccezione della fase stessa non esce verso la run."""
    case = no_workbook_case

    def esplode(*a, **k):
        raise RuntimeError('fase finta esplosa')
    monkeypatch.setattr(fase, 'fase_documenti_trade_idea', esplode)
    monkeypatch.setattr(ponte, 'ammetti_per_trade_idea', esplode)
    _con_servizio(monkeypatch, lambda: pytest.fail('archivio aperto'))
    run_id = case.current.create_run(case.request, idempotency_key='fase-esplosa')['run']['id']
    detail = case.execute(run_id, 'fase-esplosa')
    _pdf_pronto(detail)
    bridge = _dossier(detail)['filing_bridge']
    assert bridge['esito'] == 'ponte_non_eseguito' and bridge['motivi'] == ['ponte non eseguito: RuntimeError']


def test_e2e_ripresa_nessun_secondo_download(no_workbook_case, monkeypatch):
    case = no_workbook_case
    servizio = _archivio(case.root, 'filing-ripresa')
    chiamate = sec_finta(monkeypatch, IDENTITY['ticker'], nome=IDENTITY['name'])
    aperture = []
    _con_servizio(monkeypatch, lambda: aperture.append(1) or servizio)
    parent = case.current.create_run(case.request, idempotency_key='nuovo-ripresa')['run']['id']
    case.state['stop_after'] = 0
    first = case.execute(parent, 'r0')
    assert first['run']['technical_status'] == 'incomplete'
    salvato = first['progress']['checkpoint']['data'][fase.CHIAVE_BOARD]
    assert salvato['stato'] == 'aggiornato'
    prima = (chiamate['elenco'], chiamate['catalogo'], len(chiamate['documenti']), len(aperture))
    child = case.current.create_continuation(parent, idempotency_key='zz-rip-1',
                                             authorize_new_requests=True)['run']['id']
    case.state['stop_after'] = None
    final = case.execute(child, 'r1')
    _pdf_pronto(final)
    assert final['progress']['checkpoint']['data'][fase.CHIAVE_BOARD] == salvato
    assert (chiamate['elenco'], chiamate['catalogo'], len(chiamate['documenti']), len(aperture)) == prima


@pytest.mark.parametrize('origine', ['registro', 'yahoo'])
def test_e2e_etf_nessuna_attivazione(no_workbook_case, monkeypatch, origine):
    case = no_workbook_case
    if origine == 'registro':
        registro = {'origine': 'sintetico', 'motivo': None, 'veicoli': {IDENTITY['ticker']: {'tipo': 'etf'}}}
    else:   # titolo fuori registro: tipo da Yahoo, e il ponte lo vede come ETF (non «nessun profilo»)
        registro = {'origine': 'sintetico', 'motivo': None, 'veicoli': {}}
        monkeypatch.setattr(fase, '_tipo_yahoo', lambda ticker: 'ETF')
    monkeypatch.setattr(fase, '_registro_predefinito', lambda: deepcopy(registro))
    monkeypatch.setattr(ponte, '_registro_predefinito', lambda: deepcopy(registro))
    servizio = _archivio(case.root, 'filing-etf')
    servizio.run_programmato = lambda ticker: pytest.fail('run Filing per un ETF')
    _con_servizio(monkeypatch, lambda: servizio)
    run_id = case.current.create_run(case.request, idempotency_key='etf-e2e')['run']['id']
    detail = case.execute(run_id, 'etf')
    assert detail['run']['technical_status'] == 'completed', detail['run']['reason']
    record = detail['progress']['checkpoint']['data'][fase.CHIAVE_BOARD]
    assert record['stato'] == 'non_applicabile' and record['azione'] is None
    assert servizio.store.get_profile(IDENTITY['ticker']) is None
    assert _dossier(detail)['filing_bridge']['esito'] == 'non_applicabile'


# ---------------------------------------------------------------------------------------------
# Run settimanale: ponte che solleva -> run completata fino al PDF (vincolo PM «mai bloccare»)
# ---------------------------------------------------------------------------------------------

from test_weekly_research_without_workbook import research_weekly  # noqa: E402,F401  (fixture)
from test_cablaggio_consigliere_multi import run_offline  # noqa: E402,F401  (fixture)


def test_settimanale_anche_la_lacuna_del_ponte_che_solleva_e_dichiarata(research_weekly, monkeypatch, capsys):
    """main 07/10: niente `except: pass` attorno a ponte_non_eseguito: log col tipo e dichiarazione minima."""
    from bellomberg.agents import consigliere_multi as cm
    from test_weekly_recovery import _store

    def rotto(*a, **k):
        raise RuntimeError('ponte finto rotto')

    def lacuna_rotta(*a, **k):
        raise UnboundLocalError('lacuna finta rotta')
    monkeypatch.setattr(ponte, 'ammetti_archivio_filing', rotto)
    monkeypatch.setattr(ponte, 'ponte_non_eseguito', lacuna_rotta)
    result = cm.run_multi_agent(send_email=True)
    assert result['status'] == 'completed'
    pdf = next(Path(row['path']) for row in result['artifacts'] if row['kind'] == 'PDF')
    assert pdf.read_bytes().startswith(b'%PDF-')
    assert ("[!] Ponte Filing: lacuna non registrata dal ponte (UnboundLocalError): dichiarazione minima nel "
            "dossier") in capsys.readouterr().out
    bridge = _store().get('research_dossier')['dossiers']['SYNTH-A']['filing_bridge']
    assert bridge['esito'] == 'ponte_non_eseguito'
    assert bridge['motivi'] == ['ponte non eseguito: RuntimeError; lacuna registrata in forma minima '
                                '(UnboundLocalError)']


def test_settimanale_ponte_che_solleva_arriva_al_pdf(research_weekly, monkeypatch):
    from bellomberg.agents import consigliere_multi as cm
    from test_weekly_recovery import _store

    def rotto(*a, **k):
        raise RuntimeError('ponte finto rotto')
    monkeypatch.setattr(ponte, 'ammetti_archivio_filing', rotto)
    result = cm.run_multi_agent(send_email=True)
    assert result['status'] == 'completed'
    pdf = next(Path(row['path']) for row in result['artifacts'] if row['kind'] == 'PDF')
    assert pdf.read_bytes().startswith(b'%PDF-')
    dossiers = _store().get('research_dossier')['dossiers']
    assert dossiers['SYNTH-A']['filing_bridge']['esito'] == 'ponte_non_eseguito'
