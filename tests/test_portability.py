import importlib.util,json,os,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from vault import encrypt,unpack,restore
from publish_data import export
from i18n import english_template
from cryptography.exceptions import InvalidTag
class PortabilityTests(unittest.TestCase):
 def test_backup_roundtrip_and_tamper_detection(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'.credentials.json').write_text('{"access_token":"private-example"}')
   key=os.urandom(32);archive=root/'vault.enc'
   encrypt(root,archive,key)
   self.assertNotIn(b'private-example',archive.read_bytes())
   self.assertEqual(json.loads(unpack(archive.read_bytes(),key)['.credentials.json'])['access_token'],'private-example')
   other=root/'restored';restore(archive,other,key)
   self.assertEqual((root/'.credentials.json').read_bytes(),(other/'.credentials.json').read_bytes())
   changed=bytearray(archive.read_bytes());changed[-1]^=1
   with self.assertRaises(InvalidTag):unpack(bytes(changed),key)
   with self.assertRaises(InvalidTag):unpack(archive.read_bytes(),os.urandom(32))
 def test_restore_refuses_to_overwrite_different_credentials(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'source').mkdir();(root/'source/.credentials.json').write_text('original')
   key=os.urandom(32);archive=root/'vault.enc';encrypt(root/'source',archive,key)
   target=root/'target';target.mkdir();(target/'.credentials.json').write_text('different')
   with self.assertRaises(FileExistsError):restore(archive,target,key)
   self.assertEqual((target/'.credentials.json').read_text(),'different')
 def test_public_invalid_clicks_preserve_totals_without_ips(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);db=sqlite3.connect(root/'baidu.sqlite3')
   db.executescript('CREATE TABLE report_rows(report,row_key,date,payload,fetched_at);CREATE TABLE report_coverage(report,start_date,end_date);CREATE TABLE inventory_snapshots(kind,snapshot_date,payload);')
   for i,ip in enumerate(['192.0.2.1','192.0.2.2']):
    row={'date':'2026-10-01','campaignId':1,'campaignName':'Example','device':'Mobile','formatIP':ip,'sumClicks':5,'freeClicks':1,'validClicks':4,'sumCost':10,'freeCost':2,'validCost':8}
    db.execute('INSERT INTO report_rows VALUES(?,?,?,?,?)',('invalid_clicks',str(i),'2026-10-01',json.dumps(row),'now'))
   db.commit();db.close();(root/'status.json').write_text('{"ok":true}')
   export(root);dest=sqlite3.connect(root/'data/public.sqlite3');rows=[json.loads(r[0]) for r in dest.execute('SELECT payload FROM report_rows')];dest.close()
   self.assertEqual(len(rows),1);self.assertEqual(rows[0]['validClicks'],8);self.assertEqual(rows[0]['validCost'],16)
   self.assertNotIn('formatIP',rows[0]);self.assertNotIn(b'192.0.2.',(root/'data/public.sqlite3').read_bytes())
 def test_publication_text_and_english_data_logic(self):
  from publication import public_template
  source=(ROOT/'dashboard.template.html').read_text(encoding='utf-8');public=public_template(source);english=english_template(public)
  self.assertIn('本页及所展示统计数据可公开访问',public)
  self.assertNotIn('数据与分析均保存在本机',public)
  self.assertIn("x.name==='文档管理系统'",english)
  self.assertIn("r.device==='移动设备'",english)
  self.assertIn('/*__DATA__*/',english)
  self.assertIn('GitHub Actions',english)
if __name__=='__main__':unittest.main()
