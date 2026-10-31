#!/usr/bin/env python3
"""Fail closed if credentials, visitor IPs or local private files are staged or published."""
import argparse,json,re,sqlite3,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
JWT=re.compile(rb'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}')
FORBIDDEN={'.credentials.json','.backup-key','baidu.sqlite3','status.json','runs.jsonl'}
def check():
 root=ROOT;db=root/'data/public.sqlite3'
 if db.exists():
  c=sqlite3.connect(db)
  assert c.execute('PRAGMA integrity_check').fetchone()==('ok',)
  for payload, in c.execute('SELECT payload FROM report_rows UNION ALL SELECT payload FROM inventory_snapshots'):
   assert not re.search(r'"(?:formatIP|accessToken|refreshToken|secretKey|authCode|password)"\s*:',payload,re.I),'Sensitive public field'
   assert not JWT.search(payload.encode()),'Token-shaped public value'
  c.close()
 paths=list((root/'report').glob('*.html'))+list((root/'data').rglob('*'))
 for p in paths:
  if p.is_file():assert not JWT.search(p.read_bytes()),'Token-shaped value in '+str(p.relative_to(root))
 if (root/'.git').exists():
  files=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
  for name in filter(None,files):
   assert name not in FORBIDDEN and not name.startswith(('csv/','work/','.venv/','.private/')),'Private tracked file: '+name
   if not name.endswith('.enc'):assert not JWT.search((root/name).read_bytes()),'Token-shaped tracked value: '+name
 print('Public data and tracked-file checks passed')
if __name__=='__main__':check()
