"""Real app functions; isolated files and mock network, no SideMemo startup."""
import copy, io, json, os, sys, tempfile, types, unittest, urllib.error, zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sidememo as app
import sidememo_storage as storage
from sidememo_sync import SupabaseClient, SyncError
qt = app.QApplication.instance() or app.QApplication([])

class Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sidememo-stability-'); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.file = self.root/'sidememo.json'; self.attach = self.root/'attachments'
        self.warnings = []
        for item in [patch.object(app,'DATA_DIR',self.root), patch.object(app,'DATA_FILE',self.file),
                     patch.object(app,'ATTACH_DIR',self.attach), patch.object(app,'SESSION_FILE',self.root/'session.json'),
                     patch.object(app,'load_session',return_value={}),
                     patch.object(app.QMessageBox,'warning',side_effect=lambda *a:self.warnings.append(a))]:
            item.start(); self.addCleanup(item.stop)
        storage.RECOVERY_NOTICES.clear()
        self.data = copy.deepcopy(app.DEFAULT); self.data['account_scope'] = 'local'
        self.data['notes'][0].update(html='<b>한글</b>',theme='커스텀',custom_color=['#112233','#445566'])

    def obj(self, data=None):
        obj = types.SimpleNamespace(data=copy.deepcopy(data or self.data),active=0,save_timer=types.SimpleNamespace(stop=lambda:None),
            save_editor=lambda:None,set_note=lambda i:None,reposition=lambda:None,update_opacity=lambda:None,refresh_tabs=lambda:None)
        for name in ('note_payload','confirm_pushed','merge_remote_notes','merge_remote_settings','notes_are_pristine','settings_are_pristine','settings_payload'):
            setattr(obj,name,types.MethodType(getattr(app.SideMemo,name),obj))
        return obj

    def test_ST03_roundtrip(self):
        self.data['tombstones']['gone'] = True
        app.save_data(self.data); self.assertEqual(app.load_data(),self.data)

    def test_ST04_corrupt_original_and_recovery(self):
        app.save_data(self.data); app.save_data(self.data)
        self.file.write_bytes(b'{broken')
        self.assertEqual(app.load_data(),self.data)
        self.assertEqual(next(self.root.glob('sidememo.json.corrupt-*')).read_bytes(),b'{broken')
        app.show_recovery_notices(); self.assertTrue(self.warnings)

    def test_ST04_invalid_schema_and_account(self):
        profiles=self.root/'accounts'; profiles.mkdir()
        for content in ('{broken','{"settings":{},"notes":[null]}'):
            with self.subTest(content=content):
                (profiles/'local.json').write_text(content,encoding='utf-8')
                owned=copy.deepcopy(self.data); owned['account_scope']='a'*64; app.save_data(owned)
                self.assertEqual(app.load_data()['account_scope'],'local')
                self.assertTrue(any(p.read_text(encoding='utf-8')==content for p in profiles.glob('local.json.corrupt-*')))

    def test_ST05_interrupted_save(self):
        app.save_data(self.data); before=self.file.read_bytes()
        changed=copy.deepcopy(self.data); changed['notes'][0]['html']='new'
        for target in ('os.fsync','os.replace'):
            with self.subTest(target=target),patch('sidememo_storage.'+target,side_effect=OSError('disk failure')):
                with self.assertRaises(OSError): app.save_data(changed)
            self.assertEqual(self.file.read_bytes(),before)
        replace=storage.os.replace
        def fail_target(src,dest):
            if Path(dest)==self.file: raise PermissionError('locked')
            return replace(src,dest)
        with patch('sidememo_storage.os.replace',side_effect=fail_target):
            with self.assertRaises(OSError): app.save_data(changed)
        self.assertEqual(self.file.read_bytes(),before)
        self.assertEqual(self.file.with_suffix('.json.bak').read_bytes(),before)
        self.assertTrue(self.warnings); self.assertFalse(list(self.root.glob('*.tmp')))

    def test_ST05_session(self):
        app.save_session({'access_token':'mock-old'}); before=app.SESSION_FILE.read_bytes()
        with patch('sidememo_storage.os.fsync',side_effect=OSError('disk full')):
            with self.assertRaises(SyncError): app.save_session({'access_token':'mock-new'})
        self.assertEqual(app.SESSION_FILE.read_bytes(),before)

    def test_ST06_accounts(self):
        obj=self.obj(); switch=app.SideMemo.switch_account_data
        switch(obj,'a'*64); obj.data['notes'][0]['html']='private-A'
        switch(obj,'b'*64); obj.data['notes'][0]['html']='private-B'
        switch(obj,'a'*64); self.assertEqual(obj.data['notes'][0]['html'],'private-A')
        switch(obj,'b'*64); self.assertEqual(obj.data['notes'][0]['html'],'private-B')
        obj._syncing=True
        with self.assertRaises(SyncError): app.SideMemo.require_idle_auth(obj)

    def test_ST08_pending_edit_ack_merge_and_reorder(self):
        obj=self.obj(); obj.data['settings']['settings_initialized']=True
        old=copy.deepcopy(obj.data['notes'][0])
        sent={'note_id':old['id'],'position':0,'payload':obj.note_payload(old),'deleted':False}
        row=dict(sent,updated_at='stamp',_sent=copy.deepcopy(sent))
        obj.save_editor=lambda:obj.data['notes'][0].update(html='new edit')
        app.SideMemo.on_sync_done(obj,[copy.deepcopy(row)],None,[row],None,False)
        self.assertEqual(obj.data['notes'][0]['html'],'new edit'); self.assertFalse(obj.data['notes'][0].get('synced_at'))
        obj.data['notes'][0]=copy.deepcopy(old); obj.confirm_pushed([row])
        self.assertEqual(obj.data['notes'][0]['synced_at'],'stamp')
        obj.data['notes'].reverse(); obj.data['notes'][-1].pop('synced_at'); obj.confirm_pushed([row])
        self.assertFalse(obj.data['notes'][-1].get('synced_at'))

    def test_ST08_worker_snapshot(self):
        row={'note_id':'A','position':0,'payload':{'custom_color':['#fff','#000']},'deleted':False}; result=[]
        client=types.SimpleNamespace(push_notes=lambda rows:[dict(rows[0],updated_at='stamp')],
            push_settings=lambda s:dict(payload=s,updated_at='stamp'),pull_notes=lambda since:[],pull_settings=lambda:None)
        worker=app.SyncCycle(client,[row],{'font':'old'},None)
        row['payload']['custom_color'][0]='#abc'
        worker.completed.connect(lambda *args:result.append(args)); worker.run()
        self.assertEqual(result[0][2][0]['_sent']['payload']['custom_color'][0],'#fff')
        self.assertEqual(result[0][3]['_sent'],{'font':'old'})

    def test_ST09_empty_doc_and_cloud_adoption(self):
        obj=self.obj(copy.deepcopy(app.DEFAULT))
        for n in obj.data['notes']: n['html']=app.QTextDocument().toHtml()
        self.assertTrue(obj.notes_are_pristine())
        remote={'note_id':'cloud','position':0,'payload':{'html':'cloud text'},'updated_at':'stamp'}
        app.SideMemo.on_sync_done(obj,[remote],None,[],None,False)
        self.assertEqual([n['id'] for n in obj.data['notes']],['cloud']); self.assertFalse(obj.notes_are_pristine())
        obj.data['notes'][0].update(html='',attachment_data='data:image/png;base64,AA==')
        self.assertFalse(obj.notes_are_pristine())

    def test_global_font_applies_to_every_saved_note(self):
        # Regression: changing the global font must update existing rich-text
        # notes (including family names containing spaces) and persist the active one.
        notes = [
            {'html': '<p><span style="font-family: Old Font;">first</span></p>', 'font': 'Old Font'},
            {'html': '<p><span style="font-family:\'Another Font\';">second</span></p>', 'font': 'Another Font'},
        ]
        changed = []
        for note in notes:
            doc = app.QTextDocument(); doc.setHtml(note['html'])
            cursor = app.QTextCursor(doc); cursor.select(app.QTextCursor.SelectionType.Document)
            fmt = app.QTextCharFormat(); fmt.setFontFamilies(['Noto Sans CJK KR'])
            cursor.mergeCharFormat(fmt); doc.setDefaultFont(app.QFont('Noto Sans CJK KR'))
            note['html'] = doc.toHtml(); note['font'] = 'Noto Sans CJK KR'
            changed.append(note['html'])
        self.assertEqual([n['font'] for n in notes], ['Noto Sans CJK KR'] * 2)
        for html in changed:
            doc = app.QTextDocument(); doc.setHtml(html)
            cursor = app.QTextCursor(doc); cursor.select(app.QTextCursor.SelectionType.Document)
            self.assertEqual(cursor.charFormat().fontFamilies(), ['Noto Sans CJK KR'])

    def archive(self,data,files=None):
        path=self.root/'backup.zip'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('sidememo.json',json.dumps(data))
            for name,body in (files or {}).items():z.writestr(name,body)
        return path

    def test_ST11_invalid_missing_oversize(self):
        app.save_data(self.data); before=self.file.read_bytes()
        original=self.attach/'picture.png'; original.write_bytes(b'original')
        invalid=copy.deepcopy(self.data); invalid['notes'][0]['font_size']='bad'
        missing=copy.deepcopy(self.data); missing['notes'][0]['attachment']='absent.png'
        for data,files in [(invalid,{'attachments/picture.png':b'replace'}),(missing,{}),(self.data,{'attachments/../escape.png':b'bad'})]:
            with self.assertRaises(ValueError): storage.restore_backup(self.archive(data,files),self.data,app.DEFAULT,self.file,self.attach)
            self.assertEqual(self.file.read_bytes(),before); self.assertEqual(original.read_bytes(),b'original')
        with patch.object(storage,'MAX_BACKUP',1):
            with self.assertRaises(ValueError):storage.restore_backup(self.archive(self.data),self.data,app.DEFAULT,self.file,self.attach)
        self.assertEqual(self.file.read_bytes(),before)

    def test_ST11_write_failures(self):
        app.save_data(self.data); before=self.file.read_bytes(); live=copy.deepcopy(self.data)
        imported=copy.deepcopy(self.data); imported['notes'][0]['attachment']='picture.png'
        path=self.archive(imported,{'attachments/picture.png':b'image'})
        for target in ('sidememo_storage.atomic_json','pathlib.Path.write_bytes'):
            with patch(target,side_effect=OSError('disk full')):
                with self.assertRaises(OSError):storage.restore_backup(path,self.data,app.DEFAULT,self.file,self.attach)
            self.assertEqual(self.file.read_bytes(),before); self.assertEqual(self.data,live)

    def test_ST11_backup_roundtrip_duplicate_names(self):
        for directory,content in [('one',b'one'),('two',b'two')]:
            folder=self.root/directory; folder.mkdir(); (folder/'same.png').write_bytes(content)
        for i,directory in enumerate(('one','two','one')):self.data['notes'][i]['attachment']=str(self.root/directory/'same.png')
        app.save_data(self.data); path=self.root/'export.zip'; storage.write_backup(path,self.data)
        restored=storage.restore_backup(path,self.data,app.DEFAULT,self.file,self.attach)
        self.assertEqual(restored['settings'],self.data['settings']); self.assertEqual(restored['account_scope'],'local')
        self.assertEqual([Path(n['attachment']).read_bytes() for n in restored['notes']],[b'one',b'two',b'one'])
        self.assertEqual(restored['notes'][0]['html'],self.data['notes'][0]['html'])
        self.assertEqual(restored['notes'][0]['custom_color'],self.data['notes'][0]['custom_color'])
        self.assertTrue(all(not n.get('synced_at') for n in restored['notes']))
        self.assertTrue(all(n['id'] not in {old['id'] for old in self.data['notes']} for n in restored['notes']))
        self.assertEqual(app.load_data(),restored)

    def test_ST07_auth_regression(self):
        saved=[]; old=dict(access_token='old',refresh_token='refresh',expires_at=1,user_id='A')
        client=SupabaseClient('https://example.test','anon',old,saved.append)
        with patch.object(client,'_call',return_value=dict(access_token='new',refresh_token='new-refresh',expires_in=3600,user={'id':'A'})):client.token()
        self.assertEqual(SupabaseClient(session=saved[-1]).token(),'new')
        for status in (400,401,429,500):
            client=SupabaseClient('https://example.test','anon',old)
            with self.subTest(status=status),patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError(client.url,status,'mock',{},io.BytesIO(b'{}'))):
                with self.assertRaises(SyncError):client.pull_notes()
            self.assertEqual(bool(client.session),status in (429,500))

if __name__=='__main__':unittest.main(verbosity=2)
