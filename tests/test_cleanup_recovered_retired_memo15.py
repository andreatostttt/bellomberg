"""Retired destructive entry point; only TEMP fixtures and instrumented access."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT=Path(__file__).resolve().parents[1]/'tools/migrations/cleanup_recovered.py'


@pytest.mark.parametrize('apply',[False,True])
def test_direct_entry_refuses_before_any_target_access(tmp_path,monkeypatch,apply):
    spec=importlib.util.spec_from_file_location('cleanup_retired_test',SCRIPT)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    sentinel=tmp_path/'sentinel.db';sentinel.write_bytes(b'UNCHANGED')
    mod.DB=str(sentinel)
    touched=[]
    def forbidden(*a,**k):
        touched.append(str(a));raise RuntimeError('TARGET_ACCESS_FORBIDDEN')
    with monkeypatch.context() as m:
        m.setattr(mod.os.path,'exists',forbidden)
        m.setattr(mod.sqlite3,'connect',forbidden)
        m.setattr(mod.os,'remove',forbidden)
        m.setattr(mod.os,'listdir',forbidden)
        with pytest.raises(SystemExit) as caught:mod.main(apply=apply)
    assert caught.value.code not in (None,0)
    assert 'disabilitat' in str(caught.value).lower()
    assert touched==[]
    assert sentinel.read_bytes()==b'UNCHANGED'


@pytest.mark.parametrize('args',[[],['--apply'],['--memo-id','1'],['--apply','--memo-id','1']])
def test_cli_entry_refuses_without_sql_or_file_operations(tmp_path,args):
    env=dict(os.environ,BELLOMBERG_DATA_DIR=str(tmp_path),PYTHONDONTWRITEBYTECODE='1')
    env['PYTHONPATH']=str(SCRIPT.parents[2]/'src')
    # Execute the exact CLI code in a separate interpreter, with its real argv,
    # after preloading imports and instrumenting every dangerous dependency.
    harness=r'''
import json,os,sqlite3,sys
from pathlib import Path
from bellomberg.core import paths
script=sys.argv[1];args=sys.argv[2:]
code=compile(Path(script).read_text(encoding='utf-8'),script,'exec')
touched=[]
def forbidden(*a,**k):
    touched.append(str(a));raise RuntimeError('TARGET_ACCESS_FORBIDDEN')
sqlite3.connect=forbidden
os.path.exists=forbidden
os.remove=forbidden
os.listdir=forbidden
sys.argv=[script,*args]
try:
    exec(code,{'__name__':'__main__','__file__':script})
except SystemExit as e:
    print(json.dumps({'exit_code':e.code,'touched':touched}))
else:
    raise AssertionError('entry point did not refuse')
'''
    result=subprocess.run([sys.executable,'-B','-c',harness,str(SCRIPT),*args],
                          env=env,cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    output=json.loads(result.stdout)
    assert output['exit_code'] not in (None,0)
    assert 'disabilitat' in str(output['exit_code']).lower()
    assert output['touched']==[]


def test_actual_cli_exit_is_nonzero_in_temp(tmp_path):
    env=dict(os.environ,BELLOMBERG_DATA_DIR=str(tmp_path),PYTHONDONTWRITEBYTECODE='1',
             PYTHONPATH=str(SCRIPT.parents[2]/'src'))
    for args in ([],['--apply']):
        result=subprocess.run([sys.executable,'-B',str(SCRIPT),*args],env=env,cwd=tmp_path,
                              capture_output=True,text=True)
        assert result.returncode!=0
        assert 'disabilitat' in result.stderr.lower()
    assert not list(tmp_path.iterdir())
