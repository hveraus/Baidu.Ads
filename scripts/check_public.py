#!/usr/bin/env python3
"""Fail closed if credentials, visitor IPs or local private files are staged or published."""
import argparse,json,re,sqlite3,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from website_conversions import FILENAME, SSW_FIELDS, SSW_FILENAME, TINA_PAID_FIELDS, TINA_PAID_FILENAME, load_rows
from scripts.sync_posthog_cli import ENCRYPTED_SNAPSHOT, read_encrypted_snapshot
JWT=re.compile(rb'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}')
FORBIDDEN={'.credentials.json','.backup-key','baidu.sqlite3','status.json','runs.jsonl'}
def check():
 root=ROOT
 for db in [root/'data/public.sqlite3',*root.glob('accounts/*/data/public.sqlite3')]:
  check_database(db)
 load_rows(root/'data'/FILENAME)
 load_rows(root/'data'/TINA_PAID_FILENAME,TINA_PAID_FIELDS)
 load_rows(root/'accounts'/'ssw'/'data'/SSW_FILENAME,SSW_FIELDS)
 read_encrypted_snapshot(root/'data'/ENCRYPTED_SNAPSHOT)
 check_files(root)
 print('Public data and tracked-file checks passed')
def check_database(db):
 if db.exists():
  c=sqlite3.connect(db)
  assert c.execute('PRAGMA integrity_check').fetchone()==('ok',)
  for payload, in c.execute('SELECT payload FROM report_rows UNION ALL SELECT payload FROM inventory_snapshots'):
   assert not re.search(r'"(?:formatIP|accessToken|refreshToken|secretKey|authCode|password)"\s*:',payload,re.I),'Sensitive public field'
   assert not JWT.search(payload.encode()),'Token-shaped public value'
  c.close()
def check_files(root):
 paths=list((root/'report').rglob('*.html'))+list((root/'data').rglob('*'))+list((root/'accounts').rglob('*'))
 for p in paths:
  if p.is_file() and p.suffix!='.enc':
   content=p.read_bytes()
   assert not JWT.search(content),'Token-shaped value in '+str(p.relative_to(root))
   assert b'"private_posthog_cli":' not in content,'Private PostHog data in '+str(p.relative_to(root))
 if (root/'.git').exists():
  files=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
  for name in filter(None,files):
   assert (Path(name).name not in FORBIDDEN or '/data/' in '/'+name) and not name.startswith(('csv/','work/','.venv/','.private/')),'Private tracked file: '+name
   if not name.endswith('.enc'):assert not JWT.search((root/name).read_bytes()),'Token-shaped tracked value: '+name
if __name__=='__main__':check()
