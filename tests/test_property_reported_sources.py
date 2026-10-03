"""Real archived WDP primary reproof; skipped only when the external proof archive is absent."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import os
import socket
import sqlite3
import pytest
from bellomberg.valuation.property_reported_sources import normalize_reported_property, ENTITY, ON, RAW_SHA
from bellomberg.valuation.input_preparation import _catalog

@pytest.fixture(autouse=True)
def isolated_primary_reproof(monkeypatch):
    def deny(*a,**k):raise AssertionError('Source reproof must use sealed public bytes, no live network or DB')
    monkeypatch.setattr(socket,'getaddrinfo',deny)
    monkeypatch.setattr(socket.socket,'connect',deny)
    monkeypatch.setattr(sqlite3,'connect',deny)


@pytest.fixture
def primary():
    configured=os.environ.get('BELLOMBERG_TEST_WDP_PRIMARY_DOCUMENT')
    if not configured:
        pytest.skip('External real WDP fixture not configured: BELLOMBERG_TEST_WDP_PRIMARY_DOCUMENT')
    ARCHIVE=Path(configured)
    if not ARCHIVE.is_file():pytest.skip('External real WDP proof archive absent')
    result=json.loads(ARCHIVE.read_text(encoding='utf8'))
    if not Path(result['archive_path']).is_file():pytest.skip('Archived public WDP PDF absent')
    return result


def test_primary_compiler_values_are_actual_reported_facts(primary):
    result=normalize_reported_property(primary,entity=ENTITY,on=ON,as_of='2026-09-28')
    assert result['status']=='ready',result['issues']
    observed={r['driver']:r['value'] for r in json.loads(result['documents'][0]['text'])['observations']}
    assert observed['balance_sheet']['assets']['investment_property']==8436.827
    assert observed['property_reconciliation']['jv_property_memorandum']==88.963
    assert observed['claims']['epra_diluted_shares_m']==240.543824
    assert observed['epra_bridge']['reported_nav']==5149.327
    assert observed['restrictions']['guarantees']['Luxembourg']==44.1
    assert observed['development']['cost_to_come']==588
    assert observed['quotation']['price']==22.1
    assert 'policy' not in observed and 'asset_shocks' not in observed


def test_primary_legal_names_have_exact_registration_and_page_proofs(primary):
    normalized=normalize_reported_property(primary,entity=ENTITY,on=ON,as_of='2026-09-28')['documents'][0]
    body=json.loads(normalized['text']); identity=body['legal_identity']
    assert identity['contract']=='primary_legal_identity/1'
    assert identity['names']==['Warehouses De Pauw NV','Warehouses De Pauw SA']
    assert identity['explicit_name']=='Warehouses De Pauw NV/SA'
    assert identity['registration']=={'jurisdiction':'BE','number':'0417.199.869'}
    assert identity['primary_document_id']==primary['id']
    assert identity['primary_document_sha256']==RAW_SHA
    assert identity['primary_text_sha256']==primary['sha256']
    assert [proof['page'] for proof in identity['proofs']]==[102,103]
    assert all(proof['source_document_sha256']==RAW_SHA for proof in identity['proofs'])
    assert len(body['observations'])==8


@pytest.mark.parametrize('fault',['legal_name','company_registration'])
def test_rehashed_legal_identity_cannot_replace_original_primary_proof(primary,fault):
    normalized=normalize_reported_property(primary,entity=ENTITY,on=ON,as_of='2026-09-28')['documents'][0]
    body=json.loads(normalized['text'])
    if fault=='legal_name':body['legal_identity']['names'][1]='Warehouses De Pauw Holding SA'
    else:body['legal_identity']['registration']['number']='0417.199.860'
    normalized['text']=json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    normalized['sha256']=sha256(normalized['text'].encode()).hexdigest()
    normalized['id']='reported-property-'+normalized['sha256']
    catalog,issues,_=_catalog([primary,normalized],__import__('datetime').date(2026,9,28))
    assert normalized['id'] not in catalog and issues


@pytest.mark.parametrize('fault',['wrong_issuer','wrong_period','future_cutoff','wrong_url','text_tamper','raw_hash','wrong_path_name'])
def test_wrong_or_unverifiable_primary_cannot_compile(primary,fault,tmp_path):
    entity=ENTITY;on=ON;as_of='2026-09-28';doc=deepcopy(primary)
    if fault=='wrong_issuer':entity='Other WDP issuer'
    if fault=='wrong_period':on='2025-12-31'
    if fault=='future_cutoff':as_of='2026-07-30'
    if fault=='wrong_url':doc['url']='https://example.org/self-certified/statement.pdf'
    if fault=='text_tamper':doc['text']=doc['text'].replace('8,436,827','9,436,827');doc['sha256']=sha256(doc['text'].encode()).hexdigest()
    if fault=='raw_hash':doc['document_sha256']='f'*64
    if fault=='wrong_path_name':doc['archive_path']=str(tmp_path/'private.txt')
    result=normalize_reported_property(doc,entity=entity,on=on,as_of=as_of)
    assert result['status']=='incomplete' and result['documents']==[]


@pytest.mark.parametrize('fault',['normalized_only','forged_value_and_hash','future_normalization','quote_pin_tamper','wrong_source_dependency'])
def test_common_catalog_recompiles_primary_before_accepting_normalized_facts(primary,fault):
    normalized=normalize_reported_property(primary,entity=ENTITY,on=ON,as_of='2026-09-28')['documents'][0]
    docs=[deepcopy(primary),deepcopy(normalized)];doc=docs[-1]
    if fault=='normalized_only':docs=docs[1:]
    if fault in ('forged_value_and_hash','quote_pin_tamper'):
        body=json.loads(doc['text'])
        if fault=='forged_value_and_hash':body['observations'][2]['value']['assets']['investment_property']+=100
        else:body['proofs']['balance_sheet'][0]['quote']='A self-certified quote that was never published'
        doc['text']=json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        doc['sha256']=sha256(doc['text'].encode()).hexdigest();doc['id']='reported-property-'+doc['sha256']
    if fault=='future_normalization':doc['metadata']['as_of']='2026-10-01'
    if fault=='wrong_source_dependency':doc['metadata']['source_document_id']='wrong-id'
    catalog,issues,_=_catalog(docs,__import__('datetime').date(2026,9,28))
    assert doc['id'] not in catalog
    assert issues


def test_common_catalog_retains_verified_original_dependency(primary):
    normalized=normalize_reported_property(primary,entity=ENTITY,on=ON,as_of='2026-09-28')['documents'][0]
    catalog,issues,_=_catalog([primary,normalized],__import__('datetime').date(2026,9,28))
    assert not issues,issues
    assert normalized['id'] in catalog
    assert catalog[RAW_SHA]['archive_path']==primary['archive_path']


def test_changed_raw_bytes_with_original_pins_block_before_extraction(primary,tmp_path):
    raw=bytearray(Path(primary['archive_path']).read_bytes());raw[-2]=(raw[-2]+1)%256
    path=tmp_path/(RAW_SHA+'.pdf');path.write_bytes(raw)
    candidate=deepcopy(primary);candidate['archive_path']=str(path)
    result=normalize_reported_property(candidate,entity=ENTITY,on=ON,as_of='2026-09-28')
    assert result['status']=='incomplete'
    assert result['issues']==['Original public PDF bytes changed']


def test_url_only_actual_primary_metadata_preserves_confirmed_sa_and_proves_nv(primary,tmp_path):
    from bellomberg.agents import trade_idea_sources as sources
    from test_trade_idea_pm_sources import transport,Response
    raw=Path(primary['archive_path']).read_bytes()
    download,calls=transport(responses=[Response(raw,headers={'Content-Type':'application/pdf'})])
    identity={'ticker':'WDP.BR','name':'Warehouses De Pauw SA','exchange':'Brussels',
        'currency':'EUR','status':'confirmed'}
    result=sources.ingest_document_sources('WDP.BR',identity,'2026-09-28',
        [{'url':primary['url']}],archive_root=tmp_path,issuer_website='https://wdp.eu',download=download)
    document=result['documents'][0]
    verification=document['metadata']['pm_source_verification']
    assert document['published_at']=='2026-07-31'
    assert document['metadata']['report_date']=='2026-06-30'
    assert document['metadata']['issuer']==ENTITY
    assert verification['accepted_profile_name']=='Warehouses De Pauw SA'
    assert verification['issuer_basis']=='recompiled_primary_legal_identity'
    assert verification['primary_legal_identity']['primary_document_sha256']==RAW_SHA
    assert verification['primary_legal_identity']['registration']['number']=='0417.199.869'
    assert verification['claim_origins']['issuer']=='verified_primary_legal_identity'
    assert len([row for row in calls if row[0]=='request'])==1
    assert {key:value for key,value in result['receipt']['documents'][0]['request'].items()
            if value is not None}=={'url':primary['url']}
    assert identity['name']=='Warehouses De Pauw SA'
    assert sha256(Path(primary['archive_path']).read_bytes()).hexdigest()==RAW_SHA


def test_same_primary_cannot_bind_a_different_issuer_suffix_by_proximity(primary,tmp_path):
    from bellomberg.agents import trade_idea_sources as sources
    from test_trade_idea_pm_sources import transport,Response
    raw=Path(primary['archive_path']).read_bytes()
    download,calls=transport(responses=[Response(raw,headers={'Content-Type':'application/pdf'})])
    identity={'ticker':'WDP.BR','name':'Warehouses De Pauw Holding SA','exchange':'Brussels',
        'currency':'EUR','status':'confirmed'}
    with pytest.raises(sources.SourceIngestionError) as error:
        sources.ingest_document_sources('WDP.BR',identity,'2026-09-28',
            [{'url':primary['url']}],archive_root=tmp_path,issuer_website='https://wdp.eu',download=download)
    assert error.value.receipt['documents'][0]['status']=='needs_verification'
    assert len([row for row in calls if row[0]=='request'])==1
