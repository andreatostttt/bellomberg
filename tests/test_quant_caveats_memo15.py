"""Quant appendix: real renderers must retain uncertainty and avoid false captions."""
import sys
import types
from pathlib import Path
import numpy as np
import pytest
from bellomberg.core.language import language_context
from bellomberg.reporting import charts_quant as cq

@pytest.mark.parametrize('language,forbidden,reference', [('it','code spesse','normale'),('en','fat tails','normal')])
@pytest.mark.parametrize('values', [np.linspace(-2,2,100),np.zeros(100)])
def test_distribution_caption_is_neutral_and_finite(tmp_path,monkeypatch,language,forbidden,reference,values):
    monkeypatch.setattr(cq,'_returns_from_nav',lambda _:values)
    monkeypatch.setattr(cq,'CHART_DIR',str(tmp_path))
    captured={}
    original=cq.st.titlebar
    def titlebar(fig,title,subtitle,*args,**kwargs):
        captured['subtitle']=subtitle
        return original(fig,title,subtitle,*args,**kwargs)
    monkeypatch.setattr(cq.st,'titlebar',titlebar)
    path=cq.chart_var_distribution({}, {}, language=language)
    assert path and Path(path).stat().st_size>1000
    text=captured['subtitle'].lower()
    assert forbidden not in text
    assert reference in text
    assert 'nan' not in text
    if np.std(values)>0:
        assert '1.8' in text or '1,8' in text
    else:
        assert 'n.d.' in text or 'n/a' in text


def rendered_text(flow):
    chunks=[]
    for item in flow:
        if hasattr(item,'getPlainText'):
            chunks.append(item.getPlainText())
        for row in getattr(item,'_cellvalues',[]):
            for cell in row:
                chunks.append(cell.getPlainText() if hasattr(cell,'getPlainText') else str(cell))
    return '\n'.join(chunks)

@pytest.mark.parametrize('language',['it','en'])
@pytest.mark.parametrize('level',['portfolio','root'])
@pytest.mark.parametrize('beta',[None,1.7])
def test_beta_caveat_survives_real_flowables(monkeypatch,language,level,beta):
    mod=types.ModuleType('bellomberg.portfolio.advanced_metrics')
    mod.portfolio_metrics=lambda:{}
    monkeypatch.setitem(sys.modules,mod.__name__,mod)
    marker='ONLY 12 observations < uncertain & provisional >'
    risk={'portfolio':{'beta_vs_spy':beta}}
    target=risk if level=='root' else risk['portfolio']
    target['beta_error' if beta is None else 'beta_note']=marker
    with language_context(language):
        text=rendered_text(cq._numeric_tables(risk,{},{}))
    assert marker in text
    assert 'amplifica' not in text and 'amplifies' not in text
    assert 'None' not in text
    if beta is not None:
        assert '1.70' in text or '1,70' in text
