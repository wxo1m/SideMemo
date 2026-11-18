"""Isolated checks: no application startup, real sessions or network access."""
import ast
import io
import json
import re
import tempfile
import types
import uuid
from pathlib import Path
from unittest.mock import patch
import sys
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sidememo_sync import SupabaseClient, SyncError
tree = ast.parse((ROOT / 'sidememo.py').read_text(encoding='utf-8-sig'))
klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SideMemo')

def bind(name, ns, method=False):
    fn = next(n for n in (klass.body if method else tree.body) if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'actual-app-function', 'exec'), ns)
    return ns[name]

def check(label, test):
    try:
        assert test()
        print('PASS', label)
    except Exception as exc:
        print('FAIL', label, type(exc).__name__)

with tempfile.TemporaryDirectory(prefix='sidememo-stability-') as folder:
    root = Path(folder)
    ns = dict(DATA_DIR=root, DATA_FILE=root/'sidememo.json', ATTACH_DIR=root/'attachments',
              DEFAULT={'settings':{'sync_url':'https://example.test','sync_key':'anon'},'notes':[{'id':'default','html':''}]},
              DEFAULT_SYNC_URL='https://example.test', load_session=lambda:{}, THEMES={},
              json=json,re=re,uuid=uuid,SyncError=SyncError)
    for name in ('data_path','save_data','load_data'):
        bind(name,ns)
    sample={'account_scope':'local','settings':{},'notes':[{'id':'A','html':'<b>한글</b>','theme':'커스텀','custom_color':['#112233','#445566']}],'tombstones':{'gone':True}}
    ns['save_data'](sample)
    check('ST-03 save/load roundtrip',lambda:ns['load_data']()==sample)
    ns['DATA_FILE'].write_text('{broken',encoding='utf-8')
    ns['load_data']()
    check('ST-04 corrupt original retained',lambda:ns['DATA_FILE'].read_text(encoding='utf-8')=='{broken')
    profile=root/'accounts'/'local.json'; profile.write_text('{broken',encoding='utf-8')
    ns['save_data']({'account_scope':'a'*64,'settings':{},'notes':[{'id':'A'}]})
    check('ST-04 corrupt account graceful recovery',lambda:bool(ns['load_data']()))
    profile.unlink()
    ns['save_data'](sample)
    original=Path.write_text
    def interrupted(path, text, *args, **kwargs):
        original(path,'',encoding='utf-8')
        raise OSError('simulated disk full')
    with patch.object(Path,'write_text',interrupted):
        try:ns['save_data'](sample)
        except OSError:pass
    check('ST-05 interrupted save preserves notes',lambda:ns['DATA_FILE'].read_text(encoding='utf-8')!='')
    ns['save_data'](sample)
    switch=bind('switch_account_data',ns,True)
    obj=types.SimpleNamespace(data=sample,save_timer=types.SimpleNamespace(stop=lambda:None),save_editor=lambda:None,set_note=lambda i:None)
    switch(obj,'a'*64);obj.data['notes']=[{'id':'private-A'}]
    switch(obj,'b'*64); obj.data['notes']=[{'id':'private-B'}]
    switch(obj,'a'*64)
    check('ST-06 restores A',lambda:obj.data['notes']==[{'id':'private-A'}])
    switch(obj,'b'*64)
    check('ST-06 restores B',lambda:obj.data['notes']==[{'id':'private-B'}])
    guard=bind('require_idle_auth',ns,True);obj._syncing=True
    try:guard(obj);blocked=False
    except SyncError:blocked=True
    check('ST-06 blocks busy transition',lambda:blocked)
    confirm=bind('confirm_pushed',ns,True)
    obj.data={'notes':[{'id':'A','html':'new edit'}]}
    confirm(obj,[{'note_id':'A','updated_at':'old-request'}])
    check('ST-08 new edit remains dirty',lambda:not obj.data['notes'][0].get('synced_at'))
    from PySide6.QtGui import QTextDocument
    pristine=bind('notes_are_pristine',ns,True)
    obj.data={'notes':[{'html':QTextDocument().toHtml()}]}
    check('ST-09 Qt empty document is pristine',lambda:pristine(obj))

saved=[]
old=dict(access_token='old',refresh_token='old-refresh',expires_at=1,user_id='A')
c=SupabaseClient('https://example.test','anon',old,saved.append)
with patch.object(c,'_call',return_value=dict(access_token='new',refresh_token='new-refresh',expires_in=3600,user={'id':'A'})):
    c.token()
check('ST-07 refresh restart',lambda:SupabaseClient(session=saved[-1]).token()=='new')
for status in (400,401,429,500):
    c=SupabaseClient('https://example.test','anon',old)
    with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError(c.url,status,'mock',{},io.BytesIO(b'{}'))):
        try:c.pull_notes()
        except SyncError:pass
    check('ST-07 HTTP '+str(status),lambda:bool(c.session)==(status in (429,500)))
print('DONE (FAIL entries are findings, not harness errors)')
