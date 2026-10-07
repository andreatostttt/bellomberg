"""Synthetic weekly fact audit: isolated, pure, no provider or portfolio values."""
from copy import deepcopy
import hashlib
import json
import pytest
from bellomberg.reporting import memo_facts as mf


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def snapshot(text='ZZTEST revenue 123,45 EUR FY2026.', *, complete=True, role='user', kind='desk_report', payload=None, tool='get_financial_history'):
    part={'id':'p0','role':role,'kind':kind,'text':text,'sha256':hashlib.sha256(text.encode()).hexdigest(),'desk':'fundamentals'}
    snap={'version':mf.VERSION,'run_id':'synthetic-weekly','cutoff':'2026-10-07T10:00:00+00:00',
          'context':{'status':'COMPLETE' if complete else 'PARTIAL','attestation':{'status':'VERIFIED','basis':'wire_journal','request_ids':['request-synthetic']},'parts':[part],'missing_parts':[] if complete else ['attachment']},
          'receipts':[],'issues':[]}
    if payload is not None:
        receipt={'tool':tool,'input':{'ticker':'ZZTEST'},'success':True,'truncated':False,'output':json.dumps(payload),'source':'synthetic'}
        snap['receipts']=[{'id':'r0','receipt':receipt,'sha256':digest(receipt)}]
    return snap


def number(report):
    return next(f for f in report['findings'] if f['kind']=='number')


def test_numeric_presence_not_semantic_truth_and_input_immutable():
    snap=snapshot();raw=deepcopy(snap);memo='ZZTEST revenue 123,45 EUR FY2026.'
    report=mf.audit_memo_facts(memo,snap)
    assert number(report)['dimensions']['context_presence']=='MATCH'
    assert number(report)['dimensions']['semantic_scope']=='NOT_ASSESSED'
    assert number(report)['dimensions']['tool_attribution']=='UNAVAILABLE'
    assert snap==raw and 'fact_verified' not in json.dumps(report)
    assert report==mf.audit_memo_facts(memo,snap)
    assert report['counters']['findings']==len(report['findings'])


@pytest.mark.parametrize('complete,expected',[(True,'NOT_FOUND'),(False,'UNAVAILABLE')])
def test_absence_requires_complete_context(complete,expected):
    report=mf.audit_memo_facts('Revenue 997,75 EUR.',snapshot(complete=complete))
    assert number(report)['dimensions']['context_presence']==expected


@pytest.mark.parametrize('change',[lambda c:c.pop('attestation'),lambda c:c['parts'][0].update(sha256='bad'),lambda c:c.update(missing_parts=['nudge'])])
def test_unattested_or_partial_context_never_claims_absence(change):
    s=snapshot();change(s['context'])
    report=mf.audit_memo_facts('Revenue 997,75 EUR.',s)
    assert number(report)['dimensions']['context_presence']=='UNAVAILABLE'
    assert report['context_completeness']!='COMPLETE' and report['issues']


@pytest.mark.parametrize('role',['system','assistant'])
def test_policy_and_assistant_context_not_economic_evidence(role):
    r=mf.audit_memo_facts('Revenue 123,45 EUR.',snapshot(role=role,kind='instruction'))
    f=number(r);assert f['dimensions']['context_presence']=='MATCH'
    assert f['dimensions']['tool_attribution']=='UNAVAILABLE'
    assert f['dimensions']['desk_attribution']=='UNAVAILABLE'


def test_delivered_desk_exact_and_ambiguous_reports():
    s=snapshot();r=mf.audit_memo_facts('Revenue 123,45 EUR.',s)
    assert number(r)['dimensions']['desk_attribution']=='REPORT_MATCH'
    second=deepcopy(s['context']['parts'][0]);second.update(id='p1',desk='macro');s['context']['parts'].append(second)
    assert number(mf.audit_memo_facts('Revenue 123,45 EUR.',s))['dimensions']['desk_attribution']=='AMBIGUOUS'


@pytest.mark.parametrize('period,value,unit,expected',[('2026-06-30',123.45,'EUR','CONSISTENT_EXPLICIT'),('2025-06-30',123.45,'EUR','MISMATCH_EXPLICIT'),('2026-06-30',123.45,'USD','MISMATCH_EXPLICIT'),('2026-06-30',-123.45,'EUR','MISMATCH_EXPLICIT')])
def test_financial_scope_preserves_period_currency_sign(period,value,unit,expected):
    s=snapshot(payload={'data':{'period_end':period,'revenue':value,'currency':unit}})
    r=mf.audit_memo_facts('ZZTEST revenue 123,45 EUR 2026-06-30 [src: get_financial_history]',s)
    assert number(r)['dimensions']['semantic_scope']==expected


def test_old_number_does_not_support_new_period_or_other_ticker():
    s=snapshot(payload={'data':{'latest':{'period_end':'2026-06-30','revenue':12.5,'currency':'EUR'},'history':{'period_end':'2025-06-30','revenue':123.45,'currency':'EUR'}}})
    f=number(mf.audit_memo_facts('ZZTEST revenue 123,45 EUR 2026-06-30 [src: get_financial_history]',s))
    assert f['dimensions']['semantic_scope']=='MISMATCH_EXPLICIT'
    g=number(mf.audit_memo_facts('QQSYN revenue 123,45 EUR 2025-06-30 [src: get_financial_history]',s))
    assert g['dimensions']['tool_attribution']!='MATCH'


def test_metric_not_identified_from_equal_number():
    s=snapshot(payload={'period_end':'2026-06-30','eps':123.45,'currency':'EUR'})
    f=number(mf.audit_memo_facts('ZZTEST revenue 123,45 EUR 2026-06-30 [src: get_financial_history]',s))
    assert f['dimensions']['semantic_scope']=='MISMATCH_EXPLICIT'


@pytest.mark.parametrize('change',[{'success':False},{'truncated':True},{'output':'not-json'}])
def test_bad_receipt_is_visible_and_not_attributed(change):
    s=snapshot(payload={'period_end':'2026-06-30','revenue':123.45});row=s['receipts'][0];row['receipt'].update(change);row['sha256']=digest(row['receipt'])
    r=mf.audit_memo_facts('ZZTEST revenue 123,45 EUR [src: get_financial_history]',s)
    assert number(r)['dimensions']['tool_attribution']!='MATCH'
    assert r['counters']['receipts_excluded']==1 and r['issues']


def test_hash_corruption_rejected():
    s=snapshot(payload={'revenue':123.45});s['receipts'][0]['sha256']='bad'
    assert mf.audit_memo_facts('123,45 EUR [src: get_financial_history]',s)['counters']['receipts_excluded']==1


def test_guidance_mixed_scopes_and_future_forecast_not_future_source():
    payload={'ticker':'ZZTEST','as_of':'2026-10-07','active':[
      {'metric':'revenue','period':'FY2027','value_mid':123.45,'unit':'musd','status':'active','source_date':'2026-10-06','stale':False},
      {'metric':'eps','period':'FY2027','value_mid':8.75,'unit':'eps','status':'active','source_date':'2024-01-01','stale':True}],
      'history':[{'metric':'revenue','value_mid':997.75,'period':'FY2027'}]}
    s=snapshot(payload=payload,tool='get_guidance')
    r=mf.audit_memo_facts('ZZTEST revenue 123,45 milioni USD FY2027 [src: get_guidance]',s)
    assert number(r)['dimensions']['semantic_scope']=='CONSISTENT_EXPLICIT'
    assert r['counters']['scopes_excluded']>=2
    payload['active'][0]['source_date']='2026-10-08';s=snapshot(payload=payload,tool='get_guidance')
    assert number(mf.audit_memo_facts('ZZTEST revenue 123,45 milioni USD FY2027 [src: get_guidance]',s))['dimensions']['tool_attribution']!='MATCH'


def test_receipts_with_duplicate_ids_and_multiple_sources_are_ambiguous():
    s=snapshot(payload={'revenue':123.45});s['receipts'].append(deepcopy(s['receipts'][0]))
    r=mf.audit_memo_facts('ZZTEST revenue 123,45 EUR [src: get_financial_history]',s)
    assert number(r)['dimensions']['tool_attribution']!='MATCH' and r['issues']


def test_sources_do_not_cross_line_or_semicolon():
    s=snapshot(payload={'revenue':123.45})
    r=mf.audit_memo_facts('[src: get_financial_history]\nZZTEST revenue 123,45 EUR; other 123,45 EUR.',s)
    assert all(f['dimensions']['tool_attribution']=='UNAVAILABLE' for f in r['findings'] if f['kind']=='number')


@pytest.mark.parametrize('expression,expected',[('(120-100)/100 = 20%','CONSISTENT_ARITHMETIC'),('(120-100)/100 = 25%','MISMATCH_ARITHMETIC'),('120/0 = 20%','INCOMPLETE'),('120**2 = 14400','INCOMPLETE'),('__import__("os") = 12','INCOMPLETE')])
def test_literal_calculations_no_eval(expression,expected):
    r=mf.audit_memo_facts('calc: '+expression,snapshot())
    f=next(f for f in r['findings'] if f['kind']=='calculation')
    assert f['dimensions']['calculation']==expected
    assert f['dimensions']['semantic_scope']=='NOT_ASSESSED'


@pytest.mark.parametrize('date,estimated,expected',[('2026-11-03',False,'MATCH_EXPLICIT'),('2026-11-04',False,'MISMATCH_EXPLICIT'),('2026-11-03',True,'ESTIMATED')])
def test_future_event_is_not_future_source(date,estimated,expected):
    s=snapshot(payload={'ticker':'ZZTEST','event_type':'earnings','event_date':date,'date_estimated':estimated,'published_at':'2026-10-06'},tool='get_earnings')
    r=mf.audit_memo_facts('ZZTEST earnings 2026-11-03 [src: get_earnings]',s)
    f=next(f for f in r['findings'] if f['kind']=='event')
    assert f['dimensions']['event_date']==expected


def test_event_without_year_or_wrong_identity_unavailable():
    s=snapshot(payload={'ticker':'ZZTEST','event_type':'fomc','event_date':'2026-11-03'},tool='get_earnings')
    r=mf.audit_memo_facts('ZZTEST earnings 3 November [src: get_earnings]',s)
    f=next(f for f in r['findings'] if f['kind']=='event')
    assert f['dimensions']['event_date']=='UNAVAILABLE'


@pytest.mark.parametrize('language',['it','en'])
def test_renderer_measured_counts_no_private_text(language):
    text='SECRET_PAYLOAD_MUST_NOT_APPEAR 777 EUR'
    report=mf.audit_memo_facts(text,snapshot(),language=language)
    rendered=mf.render_memo_facts(report)
    assert 'SECRET_PAYLOAD_MUST_NOT_APPEAR' not in rendered and '777' not in rendered
    assert 'MEMO FACTS' in rendered and 'CHECK_UNAVAILABLE' not in rendered
    assert str(report['counters']['findings']) in rendered
    assert ('non certifica' if language=='it' else 'does not certify') in rendered


def test_invalid_input_and_source_identity_declared():
    s=snapshot();s['source_memo_sha256']='bad'
    r=mf.audit_memo_facts('123 EUR',s)
    assert r['status']=='CHECK_UNAVAILABLE' and r['issues']
    assert 'CHECK_UNAVAILABLE' in mf.render_memo_facts(r)


def test_pure_module_does_not_import_provider_or_store(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    source = Path(mf.__file__).parents[2]
    script = """
import sys
sys.dont_write_bytecode=True
sys.path.insert(0, SOURCE)
def guard(event,args):
    if event in ('socket.connect','socket.getaddrinfo','sqlite3.connect'):
        raise AssertionError('side effect')
    if event=='open' and args and isinstance(args[0],str) and args[0].endswith('.env'):
        raise AssertionError('dotenv read')
sys.addaudithook(guard)
from bellomberg.reporting import memo_facts
assert 'bellomberg.core.llm_client' not in sys.modules
assert 'bellomberg.storage.memory_db' not in sys.modules
assert 'requests' not in sys.modules
""".replace('SOURCE', repr(str(source)))
    result = subprocess.run([sys.executable, '-I', '-B', '-c', script], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr



def test_filing_period_ranges_are_compared_using_own_pair():
    node={'voce':'ricavi','delta_pct':12.5,'valuta':'EUR','tipo_periodo':'durata',
          'periodi':{'dopo':{'inizio':'2025-01-01','fine':'2025-12-31'},'prima':{'inizio':'2024-01-01','fine':'2024-12-31'}}}
    s=snapshot(payload={'numeri':{'stato':'ok','voci':[node]}},tool='get_filing_changes')
    memo='ZZTEST ricavi crescita 12,5% 2025-01-01/2025-12-31 vs 2024-01-01/2024-12-31 [src: get_filing_changes]'
    f=number(mf.audit_memo_facts(memo,s))
    assert f['dimensions']['semantic_scope']=='CONSISTENT_EXPLICIT'
    f=number(mf.audit_memo_facts(memo.replace('2025-12-31','2025-06-30'),s))
    assert f['dimensions']['semantic_scope']=='MISMATCH_EXPLICIT'


def test_growth_is_not_level_even_when_value_matches():
    s=snapshot(payload={'period':'FY2026','revenue_growth_pct':12.5})
    r=mf.audit_memo_facts('ZZTEST revenue 12,5% FY2026 [src: get_financial_history]',s)
    assert number(r)['dimensions']['semantic_scope']!='CONSISTENT_EXPLICIT'
    r=mf.audit_memo_facts('ZZTEST revenue growth 12,5% FY2026 [src: get_financial_history]',s)
    assert number(r)['dimensions']['semantic_scope']=='CONSISTENT_EXPLICIT'


def test_unknown_period_never_inherits_parent_as_of():
    s=snapshot(payload={'as_of':'2026-10-07','data':{'revenue':123.45,'currency':'EUR'}})
    r=mf.audit_memo_facts('ZZTEST revenue 123,45 EUR 2026-10-07 [src: get_financial_history]',s)
    assert number(r)['dimensions']['semantic_scope']=='NOT_ASSESSED'


def test_other_desk_tool_receipt_not_inferred_from_list_position():
    s=snapshot(payload={'period':'FY2026','revenue':123.45,'currency':'EUR'},kind='other')
    s['receipts'][0].update(desk='quant',round=2,attribution='UNAVAILABLE')
    r=mf.audit_memo_facts('ZZTEST revenue 123,45 EUR FY2026 [src: get_financial_history]',s)
    assert number(r)['dimensions']['desk_attribution']=='UNAVAILABLE'


def test_multiple_receipts_and_periods_are_not_silently_selected():
    s=snapshot(payload={'period':'FY2026','revenue':123.45,'currency':'EUR'})
    row=deepcopy(s['receipts'][0]);row['id']='r1';s['receipts'].append(row)
    f=number(mf.audit_memo_facts('ZZTEST revenue 123,45 EUR FY2026 [src: get_financial_history]',s))
    assert f['dimensions']['semantic_scope']=='AMBIGUOUS'
    assert f['dimensions']['tool_attribution']=='AMBIGUOUS'


def test_invalid_language_is_not_silent_fallback():
    assert mf.audit_memo_facts('123 EUR',snapshot(),language='xx')['status']=='CHECK_UNAVAILABLE'


def test_renderer_exposes_unassessed_dimensions_and_partial_context():
    r=mf.audit_memo_facts('Revenue 123,45 EUR',snapshot(complete=False))
    rendered=mf.render_memo_facts(r)
    assert 'PARTIAL' in rendered and 'non valutati' in rendered


def test_locale_sign_scale_and_untrusted_renderer_payloads():
    s=snapshot('ZZTEST revenue -1.250,50 milioni EUR FY2026.')
    yes=mf.audit_memo_facts('ZZTEST revenue -1.250,50 milioni EUR FY2026',s)
    no=mf.audit_memo_facts('ZZTEST revenue 1.250,50 milioni EUR FY2026',s)
    assert number(yes)['dimensions']['context_presence']=='MATCH'
    assert number(no)['dimensions']['context_presence']=='NOT_FOUND'
    yes['findings'][0]['references']=[{'part_id':'PRIVATE_CREDENTIAL'}]
    yes['issues'].append({'code':'PRIVATE_CREDENTIAL'})
    assert 'PRIVATE_CREDENTIAL' not in mf.render_memo_facts(yes)


def test_event_and_receipt_input_unchanged_with_future_source_rejected():
    s=snapshot(payload={'ticker':'ZZTEST','event_type':'earnings','event_date':'2026-11-03','date_estimated':False,'published_at':'2026-10-08'},tool='get_earnings')
    before=deepcopy(s)
    r=mf.audit_memo_facts('ZZTEST earnings 2026-11-03 [src: get_earnings]',s)
    assert next(f for f in r['findings'] if f['kind']=='event')['dimensions']['event_date']=='UNAVAILABLE'
    assert s==before and r['counters']['scopes_excluded']==1


def test_calculation_rounding_matches_existing_half_up_convention():
    r=mf.audit_memo_facts('calc: 5/2 = 3',snapshot())
    assert r['findings'][0]['dimensions']['calculation']=='CONSISTENT_ARITHMETIC'


def test_event_time_not_silently_approved_by_date_match():
    s=snapshot(payload={'ticker':'ZZTEST','event_type':'earnings','event_date':'2026-11-03','date_estimated':False},tool='get_earnings')
    r=mf.audit_memo_facts('ZZTEST earnings 2026-11-03 15:30 CET [src: get_earnings]',s)
    assert next(f for f in r['findings'] if f['kind']=='event')['dimensions']['event_date']=='UNAVAILABLE'


def test_renderer_explicit_context_status_even_if_all_numbers_found():
    r=mf.audit_memo_facts('Revenue 123,45 EUR',snapshot(complete=False))
    assert 'Contesto: PARTIAL' in mf.render_memo_facts(r)


def test_real_guidance_producer_quarter_and_growth(tmp_path):
    from datetime import date
    from bellomberg.storage.memory_db import MemoryDB
    db=MemoryDB(db_path=str(tmp_path/'guidance.db'),chroma_path=str(tmp_path/'chroma'))
    for metric,value in [('revenue_abs',123.45),('revenue_growth',0.125)]:
        result=db.add_guidance('ZZTEST',metric,'Q3 2026',value,source_doc='Synthetic issuer release',
             source_date='2026-10-06',valid_until='2027-01-01',today=date(2026,10,7))
        assert result['ok'] is True
    payload=db.get_guidance('ZZTEST',today=date(2026,10,7))
    assert {row['period'] for row in payload['active']}=={'Q3-2026'}
    s=snapshot(payload=payload,tool='get_guidance')
    for memo in ['ZZTEST revenue 123,45 milioni USD Q3 2026 [src: get_guidance]',
                 'ZZTEST revenue growth 12,5% Q3 2026 [src: get_guidance]']:
        f=number(mf.audit_memo_facts(memo,s))
        assert f['dimensions']['semantic_scope']=='CONSISTENT_EXPLICIT'
    f=number(mf.audit_memo_facts('ZZTEST revenue 12,5% Q3 2026 [src: get_guidance]',s))
    assert f['dimensions']['semantic_scope']=='MISMATCH_EXPLICIT'


@pytest.mark.parametrize('field,good', [('revenue',True),('revenue_per_share',False),('revenue_adjusted',False)])
def test_review_metric_qualifiers_are_not_total(field,good):
    s=snapshot(payload={'period_end':'2026-06-30',field:123.45,'currency':'EUR'})
    d=number(mf.audit_memo_facts('ZZTEST revenue 123.45 EUR 2026-06-30 [src: get_financial_history]',s,language='en'))['dimensions']
    assert (d['semantic_scope']=='CONSISTENT_EXPLICIT') is good


@pytest.mark.parametrize('country,good',[('US',True),('Japan',False),('',False)])
def test_review_macro_country_identity(country,good):
    s=snapshot(payload={'country':'US','event_type':'cpi','event_date':'2026-11-03','date_estimated':False},tool='get_calendar')
    r=mf.audit_memo_facts(country+' CPI 2026-11-03 [src: get_calendar]',s,language='en')
    d=next(f for f in r['findings'] if f['kind']=='event')['dimensions']
    assert (d['event_date']=='MATCH_EXPLICIT') is good


@pytest.mark.parametrize('claim,source,good',[('annual','quarterly',False),('quarterly','quarterly',True),('annual','annual',True),('annual',None,False)])
def test_review_fiscal_duration(claim,source,good):
    s=snapshot(payload={'period_end':'2026-06-30','period_type':source,'revenue':123.45,'currency':'EUR'})
    d=number(mf.audit_memo_facts('ZZTEST revenue '+claim+' 123.45 EUR 2026-06-30 [src: get_financial_history]',s,language='en'))['dimensions']
    assert (d['semantic_scope']=='CONSISTENT_EXPLICIT') is good


@pytest.mark.parametrize('stamp,good',[('2026-10-07T20:00:00+00:00',False),('2026-10-07T09:00:00+00:00',True),('2026-10-07T11:00:00+02:00',True),('2026-10-07T09:00:00',False)])
def test_review_intraday_cutoff(stamp,good):
    s=snapshot(payload={'period_end':'2026-06-30','revenue':123.45,'currency':'EUR','available_at':stamp})
    d=number(mf.audit_memo_facts('ZZTEST revenue 123.45 EUR 2026-06-30 [src: get_financial_history]',s,language='en'))['dimensions']
    assert (d['semantic_scope']=='CONSISTENT_EXPLICIT') is good


@pytest.mark.parametrize('ending,good',[(' [src: get_financial_history].',True),('. Other assertion [src: get_financial_history]',False),('? Other assertion [src: get_financial_history]',False)])
def test_review_source_sentence_boundary(ending,good):
    s=snapshot(payload={'period_end':'2026-06-30','revenue':123.45,'currency':'EUR'})
    d=number(mf.audit_memo_facts('ZZTEST revenue 123.45 EUR 2026-06-30'+ending,s,language='en'))['dimensions']
    assert (d['tool_attribution']=='MATCH') is good


@pytest.mark.parametrize('desk,good',[('Fundamentals',True),('Options',False)])
def test_review_explicit_desk_attribution(desk,good):
    d=number(mf.audit_memo_facts(desk+' desk: ZZTEST revenue 123.45 EUR.',snapshot(text='123.45'),language='en'))['dimensions']
    assert (d['desk_attribution']=='REPORT_MATCH') is good


@pytest.mark.parametrize('value,claim,good',[(0.125,'12.5%',True),(0.125,'0.125%',False),(0.5,'50%',True)])
def test_review_guidance_pct_is_fraction(value,claim,good):
    payload={'active':[{'status':'active','metric':'revenue_growth','period':'FY2029','unit':'pct','value_mid':value}]}
    d=number(mf.audit_memo_facts('ZZTEST revenue growth '+claim+' FY2029 [src: get_guidance]',snapshot(payload=payload,tool='get_guidance'),language='en'))['dimensions']
    assert (d['semantic_scope']=='CONSISTENT_EXPLICIT') is good


@pytest.mark.parametrize('claim,good',[('123.45 EUR',False),('123.45 million EUR',True),('123450000 EUR',True)])
def test_review_explicit_field_scale_not_bare_value(claim,good):
    payload={'revenue_eur_m':123.45,'period_end':'2026-06-30'}
    d=number(mf.audit_memo_facts('ZZTEST revenue '+claim+' 2026-06-30 [src: get_financial_history]',snapshot(payload=payload),language='en'))['dimensions']
    assert (d['semantic_scope']=='CONSISTENT_EXPLICIT') is good


@pytest.mark.parametrize('value,good',[(0.125,False),(12.5,True)])
def test_review_generic_pct_is_percentage_points(value,good):
    payload={'period':'FY2029','revenue_growth_pct':value}
    d=number(mf.audit_memo_facts('ZZTEST revenue growth 12.5% FY2029 [src: get_financial_history]',snapshot(payload=payload),language='en'))['dimensions']
    assert (d['semantic_scope']=='CONSISTENT_EXPLICIT') is good


@pytest.mark.parametrize('metric,claim,good',[('debt','debt',True),('debt','net debt',False),('debt','gross debt',False),('debt','debito netto',False),('eps','EPS',True),('eps','diluted EPS',False),('eps','basic EPS',False),('eps','EPS diluito',False)])
def test_review_qualified_metric_not_unqualified(metric,claim,good):
    payload={'period_end':'2026-06-30',metric:123.45,'currency':'EUR'}
    d=number(mf.audit_memo_facts('ZZTEST '+claim+' 123.45 EUR 2026-06-30 [src: get_financial_history]',snapshot(payload=payload),language='en'))['dimensions']
    assert (d['semantic_scope']=='CONSISTENT_EXPLICIT') is good


@pytest.mark.parametrize('claim',['organic revenue','constant-currency revenue','ultraviolet revenue','revenue excluding one-off restructuring','revenue under an imaginary accounting standard','revenue adjusted for a secret denominator'])
def test_review_closed_grammar_rejects_unlisted_qualifiers(claim):
    payload={'period_end':'2026-06-30','revenue':123.45,'currency':'EUR'}
    d=number(mf.audit_memo_facts('ZZTEST '+claim+' 123.45 EUR 2026-06-30 [src: get_financial_history]',snapshot(payload=payload),language='en'))['dimensions']
    assert d['semantic_scope']=='NOT_ASSESSED'
    assert d['context_presence']=='MATCH'


@pytest.mark.parametrize('tail',[' excluding special items',' under pro forma accounting',' plus 123.45 EUR'])
def test_review_closed_grammar_rejects_extra_prose_and_second_quantity(tail):
    payload={'period_end':'2026-06-30','revenue':123.45,'currency':'EUR'}
    r=mf.audit_memo_facts('ZZTEST revenue 123.45 EUR 2026-06-30'+tail+' [src: get_financial_history]',snapshot(payload=payload),language='en')
    assert all(f['dimensions']['semantic_scope']=='NOT_ASSESSED' for f in r['findings'] if f['kind']=='number')
