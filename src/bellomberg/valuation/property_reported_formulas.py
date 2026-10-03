"""Live, source-bound formulas for the reported property NAV variant."""
from .linked_model import LinkedModel, SCENARIOS, total
from .documented_presentation import _line, PRICE, PERCENT
from .property_reported_nav import ASSETS, LIABILITIES
from bellomberg.core.language import text as tr


def apply_reported_property_formulas(wb, payload):
    m = LinkedModel(wb, payload); m.quotation_checks()
    a = {k:m.ref('model','balance_sheet','assets',k) for k in ASSETS}
    l = {k:m.ref('model','balance_sheet','liabilities',k) for k in LIABILITIES}
    q = m.ref('model','balance_sheet','money_precision')
    shares = m.ref('model','shares')
    p = {k:m.ref('model','property_reconciliation',k) for k in
        ('standing','development','land','held_for_sale','jv_property_memorandum','portfolio_fair_value','leasehold_rights_in_standing')}
    e = {k:m.ref('model','epra_bridge','adjustments',k) for k in
        ('deferred_tax_property','financial_instruments','intangibles')}
    for s in SCENARIOS:
        ws = m.sheet('Reported NAV '+s,tr('NAV patrimoniale riportato — ','Reported property NAV — ')+s,['Snapshot'])
        _line(ws,4,tr('Sensibilita del NAV NTA: nessuna previsione di canoni, FFO/AFFO o compliance futura. JV immobiliari e costo residuo sono memorandum.',
            'NTA sensitivities: no rent, FFO/AFFO or future compliance forecast. JV property and cost to come are memorandum.'),6,height=42)
        for k,ref in {**{'asset '+k:v for k,v in a.items()},**{'liability '+k:v for k,v in l.items()}}.items():
            m.check(s,k,ref+'>=0')
        for key in ('reported_assets','reported_liabilities','reported_parent_equity','reported_minority_equity'):
            m.check(s,'Reported balance '+key,m.ref('model','balance_sheet',key)+'>=0')
        for key,ref in p.items():
            m.check(s,'Reported property '+key,ref+'>=0')
        m.check(s,'Reported precision',f'OR({q}=0.000001,{q}=0.001,{q}=1)')
        m.check(s,'Shares',shares+'>0')
        calc = lambda row,label,expr,fmt=None: m.calc(ws,row,4,label,expr,**({'fmt':fmt} if fmt else {}))
        assets = calc(9,tr('Attivita: riconto completo','Assets: complete ledger'),total(a.values()))
        liabilities = calc(10,tr('Passivita: riconto completo','Liabilities: complete ledger'),total(l.values()))
        minority = m.ref('model','balance_sheet','reported_minority_equity')
        ifrs = calc(11,tr('Patrimonio ordinario IFRS ricostruito','Reconstructed IFRS parent equity'),f'{assets}-{liabilities}-{minority}')
        for name,actual,reported,n in (
            ('Assets',assets,m.ref('model','balance_sheet','reported_assets'),len(a)),
            ('Liabilities',liabilities,m.ref('model','balance_sheet','reported_liabilities'),len(l)),
            ('Parent equity',ifrs,m.ref('model','balance_sheet','reported_parent_equity'),len(a)+len(l)+1)):
            m.check(s,name+' rounding',f'ABS({actual}-{reported})<=({n}+1)*{q}/2+0.000000001')
        for name,actual,reported,n in (
            ('Property',total(p[k] for k in ('standing','development','land')),a['investment_property'],3),
            ('Held for sale',p['held_for_sale'],a['held_for_sale'],1),
            ('Portfolio memorandum',total((a['investment_property'],a['held_for_sale'],p['jv_property_memorandum'])),p['portfolio_fair_value'],3)):
            m.check(s,name+' reconciliation',f'ABS({actual}-{reported})<=({n}+1)*{q}/2+0.000000001')
        m.check(s,'Leasehold subset',f'AND({p["leasehold_rights_in_standing"]}>=0,{p["leasehold_rights_in_standing"]}<={p["standing"]})')
        m.check(s,'Intangibles NTA deduction',f'ABS({e["intangibles"]}+{a["intangibles"]})<={q}')
        nta = calc(13,tr('NTA ricostruito dalle rettifiche riportate','NTA reconstructed from reported adjustments'),ifrs+'+'+total(e.values()))
        m.check(s,'NTA rounding',f'ABS({nta}-{m.ref("model","epra_bridge","reported_nav")})<=({len(a)+len(l)+6})*{q}/2+0.000000001')
        for key in ('entitled_shares_m','epra_diluted_shares_m'):
            m.check(s,key,f'ABS({m.ref("model","claims",key)}-{shares})<=0.0000005')
        for key in ('reported_diluted_ifrs_nav','hybrid_claims'):
            m.check(s,'Reported claim '+key,m.ref('model','claims',key)+'>=0')
        m.check(s,'Hybrid claims require separate bridge',m.ref('model','claims','hybrid_claims')+'=0')
        m.check(s,'Diluted IFRS NAV',f'ABS({m.ref("model","claims","reported_diluted_ifrs_nav")}-{m.ref("model","balance_sheet","reported_parent_equity")})<={q}')
        decimals = m.ref('model','epra_bridge','per_share_decimals')
        m.check(s,'Per-share published precision',f'AND({decimals}>=0,{decimals}<=6,{decimals}=INT({decimals}))')
        for key in ('reported_nav','reported_per_share'):
            m.check(s,'Reported NTA '+key,m.ref('model','epra_bridge',key)+'>=0')
        m.check(s,'Published NTA per share',f'ABS({m.ref("model","epra_bridge","reported_nav")}/{shares}-{m.ref("model","epra_bridge","reported_per_share")})<=0.5*10^(-{decimals})+0.000000001')
        for name,covenant in m.value('model','restrictions')['covenants'].items():
            m.check(s,'Observed covenant '+name,m.ref('model','restrictions','covenants',name,'observed')+covenant['operator']+m.ref('model','restrictions','covenants',name,'threshold'))
        for name in m.value('model','restrictions')['guarantees']:
            m.check(s,'Guarantee '+name,m.ref('model','restrictions','guarantees',name)+'>=0')
        for key in ('pipeline_total','invested','cost_to_come','undrawn_confirmed_lines','debt_maturities_to_end_next_year'):
            m.check(s,'Development / funding '+key,m.ref('model','development',key)+'>=0')
        dev = {k:m.ref('model','development',k) for k in ('pipeline_total','invested','cost_to_come')}
        m.check(s,'Reported pipeline rounding',f'ABS({dev["invested"]}+{dev["cost_to_come"]}-{dev["pipeline_total"]})<=1.5')
        impacts=[];row=16
        for k in ('standing','development','land','held_for_sale','participations'):
            shock=m.ref(s,'asset_shocks',k);value=a[k] if k=='participations' else p[k]
            m.check(s,'Asset sensitivity '+k,shock+'>-1')
            impacts.append(calc(row,k+' '+tr('variazione di valore','value change'),value+'*'+shock));row+=1
        overrun_rate=m.ref(s,'asset_shocks','development_cost_overrun')
        m.check(s,'Development cost overrun',overrun_rate+'>=0')
        overrun=calc(22,tr('Deduzione costo eccedente (ipotesi)','Cost overrun deduction (assumption)'),dev['cost_to_come']+'*'+overrun_rate)
        adjusted=calc(23,tr('NTA rettificato di scenario','Scenario adjusted NTA'),nta+'+'+total(impacts)+'-'+overrun)
        target=m.ref(s,'nav_target');m.check(s,'Explicit NAV target',target+'>0');m.check(s,'Positive adjusted equity',f'AND(ISNUMBER({adjusted}),{adjusted}>0)')
        m.results[s]=calc(24,tr('Valore per azione della sensibilita','Sensitivity value per share'),adjusted+'*'+target+'/'+shares,PRICE)
        calc(27,tr('JV immobiliari: esclusi dal secondo conteggio','JV property: excluded from second addition'),p['jv_property_memorandum'])
        calc(28,tr('Costo residuo: memorandum, nessun profitto futuro','Cost to come: memorandum, no future profit'),dev['cost_to_come'])
    m.finish()
    return True
