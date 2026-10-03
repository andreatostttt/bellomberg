"""Bounded compiler of the archived WDP H1 2026 primary into typed NAV facts.

Version 1 supports this exact publicly released PDF and table layout. It never
accepts an analyst ledger or infers future income, missing rights or zero claims.
New editions require their own reviewed parser and source pins.
"""
from datetime import date
from hashlib import sha256
from pathlib import Path
import json
import re

NORMALIZER = 'wdp_reported_property_nav/1'
SOURCE_URL = 'https://wdp.eu/en/actions/site-module/asset-download/download?id=336401'
RAW_SHA = '1ad0ef0c520acf64547eb92eda6d5f8118e195eea2457cf4b183fc77821faf42'
ENTITY = 'Warehouses De Pauw NV'
ON = '2026-06-30'
PUBLISHED = '2026-07-31'


def normalize_reported_property(primary, *, entity, on, as_of):
    """Recompile original raw bytes and ordinary extracted text at every proof read."""
    try:
        from bellomberg.market_data.lettore_trimestrali import estrai_testo
        from .property_nav_requirements import REPORTED_SCHEMA
        if (entity != ENTITY or on != ON or date.fromisoformat(as_of) < date.fromisoformat(PUBLISHED)
            or primary.get('url') != SOURCE_URL or primary.get('published_at') != PUBLISHED
            or primary.get('document_sha256') != RAW_SHA or primary.get('id') != RAW_SHA
            or primary.get('sha256') != sha256(primary['text'].encode()).hexdigest()
            or (primary.get('metadata') or {}).get('report_date') != ON):
            raise ValueError('Exact WDP legal entity, edition, period, publication and source hashes required')
        path = Path(primary['archive_path'])
        if path.name != RAW_SHA+'.pdf' or path.stat().st_size != 5_997_526:
            raise ValueError('Bounded archived public PDF required')
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != RAW_SHA:
            raise ValueError('Original public PDF bytes changed')
        extracted = estrai_testo(str(path),contenuto=raw)
        if extracted.get('stato') != 'ok' or extracted.get('testo') != primary['text']:
            raise ValueError('Primary text differs from the common extraction of sealed public bytes')
        import pymupdf
        with pymupdf.open(stream=raw,filetype='pdf') as pdf:
            if len(pdf) != 103: raise ValueError('Unexpected primary page inventory')
            pages = {n:pdf[n-1].get_text() for n in (1,20,41,66,75,77,85,87,88,89,91,94,95,102,103)}
            # The quoted closing-price table and hybrid ledger are located by
            # unique primary headings, rather than assigning an assumed page.
            for page in pdf:
                text=page.get_text()
                if 'Number of shares in circulation on closing date' in text: pages['quotation']=text;pages['quotation_page']=page.number+1
                if 'Hybrids (including convertibles' in text: pages['hybrids']=text;pages['hybrids_page']=page.number+1
                if 'As of 30 June 2026, WDP has a total investment pipeline' in text: pages['pipeline']=text;pages['pipeline_page']=page.number+1
                if 'total undrawn and confirmed long-term credit lines' in text: pages['funding']=text;pages['funding_page']=page.number+1
                if 'Including a right of use of 117 million euros' in text: pages['leasehold']=text;pages['leasehold_page']=page.number+1
                if 'friendly all-share merger' in text and 'merger' not in pages:
                    pages['merger']=text;pages['merger_page']=page.number+1
        proofs={}
        def pin(driver,page,quote):
            text=pages[page] if isinstance(page,int) else pages[page]
            if quote not in text: raise ValueError('Primary quote not found for '+driver)
            page_number=page if isinstance(page,int) else pages[page+'_page']
            proofs.setdefault(driver,[]).append({'page':page_number,'quote':quote,
                'page_text_sha256':sha256(text.encode()).hexdigest(),'source_document_sha256':RAW_SHA})
        def row(driver,page,label,next_label,count,index=0,scale=1000):
            text=pages[page]
            if text.count(label)!=1: raise ValueError('Ambiguous primary row: '+label)
            section=text.split(label,1)[1].split(next_label,1)[0]
            if next_label not in text.split(label,1)[1]: raise ValueError('Missing row boundary: '+next_label)
            cells=[]
            number=re.compile(r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?')
            for line in section.splitlines():
                tokens=line.split()
                if tokens and all(number.fullmatch(token) for token in tokens):cells.extend(tokens)
            if len(cells)<count or len(cells)>count+1: raise ValueError('Unexpected table width for '+label)
            cells=cells[-count:]
            pin(driver,page,label+section)
            return float(cells[index].replace(',',''))/scale
        asset_labels=[('intangibles','Intangible fixed assets','Investment property'),
            ('investment_property','Investment property','Other tangible fixed assets'),
            ('other_tangible','Other tangible fixed assets (energy assets inclusive)','Financial fixed assets'),
            ('financial_fixed','Financial fixed assets','Trade receivables and other fixed assets'),
            ('other_fixed_receivables','Trade receivables and other fixed assets','Participations in associated companies and joint ventures'),
            ('participations','Participations in associated companies and joint ventures','Current assets'),
            ('held_for_sale','Assets held for sale','Trade receivables'),
            ('trade_receivables','\nTrade receivables \n','Tax receivables and other current assets'),
            ('current_tax_other','Tax receivables and other current assets','Cash and cash equivalents'),
            ('cash','Cash and cash equivalents','Accruals and deferrals'),
            ('current_accruals','Accruals and deferrals','Total assets')]
        assets={key:row('balance_sheet',66,label,end,3) for key,label,end in asset_labels}
        liability_labels=[('noncurrent_provisions','\nProvisions \n','Non-current financial debt'),
            ('noncurrent_debt','Non-current financial debt','Other non-current financial liabilities'),
            ('noncurrent_other_financial','Other non-current financial liabilities','Trade payables and other non-current liabilities'),
            ('noncurrent_payables','Trade payables and other non-current liabilities','Deferred taxes - liabilities'),
            ('deferred_tax','Deferred taxes - liabilities','II. Current liabilities'),
            ('current_debt','\nCurrent financial debt \n','Other current financial liabilities'),
            ('current_other_financial','Other current financial liabilities','Trade payables and other current debts'),
            ('current_payables','Trade payables and other current debts','Other current liabilities'),
            ('current_other_liabilities','Other current liabilities','Accrued charges and deferred income'),
            ('current_accruals','Accrued charges and deferred income','Total liabilities')]
        liabilities={key:row('balance_sheet',66,label,end,3) for key,label,end in liability_labels}
        balance={'assets':assets,'liabilities':liabilities,
            'reported_assets':row('balance_sheet',66,'Total assets','Shareholders\' equity',3),
            'reported_liabilities':row('balance_sheet',66,'\nLiabilities \n','I. Non-current liabilities',3),
            'reported_parent_equity':row('balance_sheet',66,'I. Shareholders\' equity attributable to the parent company shareholders','\nCapital',3),
            'reported_minority_equity':row('balance_sheet',66,'II. Minority interests','\nLiabilities',3),
            'money_precision':0.001}
        if '(in euros x 1,000)' not in pages[66] or '30.06.2026 31.12.2025 30.06.2025' not in pages[66]:
            raise ValueError('Currency scale and ordered comparative dates absent')
        pin('balance_sheet',66,'(in euros x 1,000) \nNote 30.06.2026 31.12.2025 30.06.2025')
        # Segment rows include current and prior-period tables; take the first
        # nine dated cells before the next label, never a value from FY 2025.
        def segment(label,end,index):
            text=pages[75].split('30.06.2026',1)[1].split('31.12.2025',1)[0]
            section=text.split(label,1)[1].split(end,1)[0]
            cells=re.findall(r'^\s*([\d,]+)\s*$',section,re.M)
            if len(cells)!=9: raise ValueError('Nine current-period geographical cells required: '+label)
            pin('property_reconciliation',75,label+section)
            return int(cells[index].replace(',',''))/1000
        property_values={key:segment(label,end,6) for key,label,end in (
            ('standing','Existing buildings','Projects under'),('development','Projects under','Land reserves'),
            ('land','Land reserves','Assets held for sale'))}
        property_values.update(held_for_sale=assets['held_for_sale'],
            jv_property_memorandum=segment('Investment \nproperties','Existing buildings',8))
        appraisal=re.search(r'amounted to ([\d,]+) euros',pages[87])
        if not appraisal: raise ValueError('Independent appraisal memorandum amount absent')
        property_values['portfolio_fair_value']=int(appraisal[1].replace(',',''))/1_000_000
        pin('property_reconciliation',87,appraisal.group(0))
        if 'Including a right of use of 117 million euros' not in pages['leasehold']: raise ValueError('Leasehold disclosure missing')
        leasequote=re.search(r'Including a right of use of ([\d.]+) million euros[^\n]*\n[^\n]*',pages['leasehold']).group(0)
        pin('property_reconciliation','leasehold',leasequote)
        property_values['leasehold_rights_in_standing']=float(re.search(r'([\d.]+) million',leasequote)[1])
        property_values.update(jv_basis='equity_method_property_memorandum_only',jv_ownership={
            'WDPort of Ghent Big Box NV':'50% equity method at 2026-06-30',
            'Gosselin-WDP NV':'29% equity method at 2026-06-30'})
        for name,percentage in (('WDPort of Ghent Big Box NV','50%'),('Gosselin-WDP NV','29%')):
            segment_quote=pages[77].split(name,1)[1].split('Equity method',1)[0]
            if percentage not in segment_quote: raise ValueError('Current JV ownership not proved')
            pin('property_reconciliation',77,name+segment_quote+'Equity method')
        epra={'basis':'EPRA_NTA','adjustments':{
            'deferred_tax_property':row('epra_bridge',91,'(V) Deferred tax in relation','(VI) Fair value',4),
            'financial_instruments':row('epra_bridge',91,'(VI) Fair value of financial instruments','(VIII.b)',4),
            'intangibles':row('epra_bridge',91,'(VIII.b) Intangibles','Subtotal',2)},
            'reported_nav':row('epra_bridge',91,'\nNAV \n','Number of shares',6,index=1),
            'reported_per_share':row('epra_bridge',91,'\nNAV/share (in euros)','\n \n',6,index=1,scale=1),
            'per_share_decimals':1}
        shares=row('claims',91,'Number of shares','NAV/share (in euros)',6,index=1,scale=1_000_000)
        entitled=row('claims','quotation','Number of shares in circulation on closing date','Free float',3,scale=1_000_000)
        hybrid=row('claims','hybrids','Hybrids (including convertibles','Bond loans',4)
        claims={'entitled_shares_m':entitled,'epra_diluted_shares_m':shares,
            'reported_diluted_ifrs_nav':row('claims',91,'Diluted NAV at fair value','Exclude:',6,index=1),
            'hybrid_claims':hybrid,'dilution_basis':'reported_EPRA_after_options_convertibles_other_equity',
            'post_balance_events':'Pending ARGAN all-share merger and Ghent interest increase after 30 June; excluded from this dated standalone snapshot.'}
        if 'ARGAN' not in pages.get('merger','') or 'At the beginning of \nJuly 2026, the shareholding was increased from 50% to 80%.' not in pages[77]:
            raise ValueError('Post-balance merger and ownership events must remain visible')
        pin('claims','merger',pages['merger'])
        pin('claims',77,'At the beginning of \nJuly 2026, the shareholding was increased from 50% to 80%.')
        price=row('quotation','quotation','       closing','IFRS NAV',3,scale=1)
        quotation={'financial_currency':'EUR','quote_currency':'EUR','quote_unit':'EUR','quote_units_per_currency':1,
            'financial_to_quote_rate':1,'shares_per_quote':1,'share_class':'shares entitled to dividend','price':price,'price_as_of':ON}
        text85=' '.join(pages[85].split())
        cov_patterns={'interest_coverage':(r'Interest Coverage of at least ([\d.]+)x.*?this is ([\d.]+)x','>=','reported operating result / interest charges'),
            'statutory_gearing':(r'gearing ratio below ([\d.]+)%.*?these are ([\d.]+)% and','<','statutory GVV/SIR gearing'),
            'consolidated_gearing':(r'gearing ratio below ([\d.]+)%.*?and ([\d.]+)% respectively','<','consolidated proportional GVV/SIR gearing'),
            'non_prelet_development':(r'development property ratio\) to ([\d.]+)%.*?this ratio is ([\d.]+)%','<=','non pre-let development / book portfolio excluding land'),
            'subsidiary_debt':(r'maximum of ([\d.]+)%.*?subsidiary financial debt ratio is ([\d.]+)%','<=','subsidiary financial debt / group financial debt')}
        covenants={}
        for name,(pattern,operator,basis) in cov_patterns.items():
            match=re.search(pattern,text85,re.I)
            if not match: raise ValueError('Complete observed covenant inventory missing: '+name)
            scale=1 if name=='interest_coverage' else 100
            covenants[name]={'threshold':float(match[1])/scale,'observed':float(match[2])/scale,'operator':operator,'basis':basis}
        pin('restrictions',85,pages[85])
        if 'no mortgages or other collateral securities are outstanding' not in text85 or 'change of control' not in text85:
            raise ValueError('Encumbrance and change-control clauses missing')
        guarantees={}
        for name,pattern in {'NL_facility_limit':r'amounting to ([\d.]+) million',
            'NL_drawn_within_limit':r'([\d.]+) million euros of which has been drawn',
            'Luxembourg':r'commitments of (\d+) (\d+) million',
            'Gosselin':r'commitments of Gosselin-WDP NV/SA for ([\d.]+) million'}.items():
            match=re.search(pattern,text85)
            if not match: raise ValueError('Guarantee limit/drawn disclosure absent: '+name)
            guarantees[name]=float(match[1]+'.'+match[2]) if name=='Luxembourg' else float(match[1])
        restrictions={'covenants':covenants,'guarantees':guarantees,
            'encumbrance':'Reported no mortgages/collateral at 2026-06-30; negative pledge, GVV/SIR qualification and conditional change-of-control repayment remain.',
            'compliance_date':ON}
        pipeline_text=' '.join(pages['pipeline'].split());funding_text=' '.join(pages['funding'].split())
        total_match=re.search(r'total investment pipeline in execution of ([\d.]+) million',pipeline_text)
        cost_match=re.search(r'cost to come is ([\d.]+) million',pipeline_text)
        lines_match=re.search(r'total undrawn and confirmed long-term credit lines are ([\d.]+) billion',funding_text)
        maturity_match=re.search(r'debt maturities until the end of 2027 \(([\d.]+) million',funding_text)
        if not all((total_match,cost_match,lines_match,maturity_match)): raise ValueError('Pipeline/funding source coverage missing')
        pipeline_total=float(total_match[1]);cost=float(cost_match[1])
        development={'pipeline_total':pipeline_total,'invested':pipeline_total-cost,'cost_to_come':cost,
            'undrawn_confirmed_lines':float(lines_match[1])*1000,'debt_maturities_to_end_next_year':float(maturity_match[1]),
            'funding_basis':'reported_aggregate_not_project_financing_or_future_profit',
            'pipeline_scope':'reported_projects_excluding_energy_and_land_reserves'}
        if 'Excludes projects in energy and land reserves.' not in pages['pipeline']:
            raise ValueError('Pipeline exclusions must remain explicit')
        pin('development','pipeline',pages['pipeline']);pin('development','funding',pages['funding'])
        values={'quotation':quotation,'shares':shares,'balance_sheet':balance,'property_reconciliation':property_values,
            'epra_bridge':epra,'claims':claims,'restrictions':restrictions,'development':development}
        observations=[]
        for driver,value in values.items():
            field,unit,basis,*_=REPORTED_SCHEMA[driver]
            if driver=='shares':proofs[driver]=proofs['claims']
            observations.append({'field':field,'driver':driver,'value':value,'entity':entity,'period':on,'unit':unit,'accounting_basis':basis})
        legal_name = re.search(r'\b(Warehouses De Pauw) (NV)/(SA), abbreviated WDP, having its registered office[^.]+\.', pages[102])
        registration = re.search(r'WDP NV/SA[^\n]*Company number\s+(0417\.199\.869) \(RPR Brussels, Dutch-speaking section\)', pages[103])
        if (not legal_name or not registration or pages[102].count(legal_name.group(0)) != 1
                or pages[103].count(registration.group(0)) != 1):
            raise ValueError('Exact bilingual legal name and company registration require primary page proof')
        pin('legal_identity',102,legal_name.group(0))
        pin('legal_identity',103,registration.group(0))
        legal_identity = {'contract':'primary_legal_identity/1','primary_document_id':primary['id'],
            'primary_document_sha256':RAW_SHA,'primary_text_sha256':primary['sha256'],'entity':entity,
            'names':[legal_name[1]+' '+legal_name[2],legal_name[1]+' '+legal_name[3]],
            'explicit_name':legal_name[1]+' '+legal_name[2]+'/'+legal_name[3],
            'registration':{'jurisdiction':'BE','number':registration[1]},'proofs':proofs['legal_identity']}
        text=json.dumps({'observations':observations,'proofs':proofs,'source_document_sha256':RAW_SHA,
            'source_text_sha256':primary['sha256'],'legal_identity':legal_identity,
            'scope':'Reported facts only; analytical contracts and sensitivities are supplied separately.'},
            sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
        digest=sha256(text.encode()).hexdigest()
        document={'id':'reported-property-'+digest,'url':SOURCE_URL+'#reported-property-nav-1','published_at':PUBLISHED,
            'sha256':digest,'text':text,'origin':'verified_primary_normalization',
            'metadata':{'normalizer':NORMALIZER,'source_document_id':primary['id'],'entity':entity,'on':on,'report_date':on,'as_of':as_of}}
        return {'status':'ready','documents':[document],'issues':[]}
    except (ValueError,KeyError,TypeError,OSError,IndexError,AttributeError) as exc:
        return {'status':'incomplete','documents':[],'issues':[str(exc)]}
