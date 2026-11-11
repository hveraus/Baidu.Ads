#!/usr/bin/env python3
"""Produce allowlisted public data from private collector output."""
import argparse,datetime as dt,hashlib,importlib.util,json,re,sqlite3,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tracker import write_csv,summary
from website_conversions import load_rows,validate_rows
SENSITIVE={'formatip','ip','ipaddress','accesstoken','refreshtoken','secretkey','authcode','signature','password','authorization'}

def export_website_conversions(path,rows):
 previous=load_rows(path)
 merged={row['date']:row for row in previous}
 merged.update((row['date'],row) for row in validate_rows(rows))
 clean=validate_rows(list(merged.values()))
 if clean==previous:return
 with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as file:
  temp=Path(file.name)
  try:
   file.write(json.dumps(clean,indent=2)+'\n')
   file.close()
   temp.replace(path)
  finally:
   temp.unlink(missing_ok=True)

def clean(value):
 if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if re.sub('[^a-z]','',k.lower()) not in SENSITIVE}
 if isinstance(value,list):return [clean(v) for v in value]
 if isinstance(value,str):
  # Source URLs sometimes contain tracking identifiers; never publish auth parameters.
  return re.sub(r'(?i)([?&](?:access_?token|refresh_?token|secret_?key|auth_?code|password|signature)=)[^&#\s]*',r'\1[REDACTED]',value)
 return value

def export(root=ROOT):
 source=sqlite3.connect(root/'baidu.sqlite3');out=root/'data';out.mkdir(exist_ok=True)
 temp=out/'public.sqlite3.tmp';temp.unlink(missing_ok=True);dest=sqlite3.connect(temp)
 dest.execute('CREATE TABLE report_rows(report TEXT,row_key TEXT,date TEXT,payload TEXT,fetched_at TEXT,PRIMARY KEY(report,row_key))')
 dest.execute('CREATE TABLE report_coverage(report TEXT,start_date TEXT,end_date TEXT,PRIMARY KEY(report,start_date,end_date))')
 dest.execute('CREATE TABLE inventory_snapshots(kind TEXT,snapshot_date TEXT,payload TEXT,PRIMARY KEY(kind,snapshot_date))')
 reports={};invalid={}
 for report,date,payload,fetched in source.execute('SELECT report,date,payload,fetched_at FROM report_rows ORDER BY report,date'):
  row=clean(json.loads(payload))
  if report=='invalid_clicks':
   key=(row.get('date'),row.get('campaignId'),row.get('device'))
   aggregate=invalid.setdefault(key,{k:row.get(k) for k in ['date','campaignId','campaignName','device']})
   for k in ['sumClicks','sumCost','freeClicks','freeCost','validClicks','validCost']:aggregate[k]=aggregate.get(k,0)+float(row.get(k) or 0)
  else:reports.setdefault(report,[]).append(row)
 if invalid:reports['invalid_clicks']=list(invalid.values())
 for name,rows in reports.items():
  for row in rows:
   payload=json.dumps(row,ensure_ascii=False,sort_keys=True);key=hashlib.sha256(payload.encode()).hexdigest()
   dest.execute('INSERT INTO report_rows VALUES(?,?,?,?,?)',(name,key,str(row.get('date','')),payload,'public-export'))
  write_csv(out/'csv'/f'{name}.csv',rows)
 inventory={}
 for kind,date,payload in source.execute('SELECT kind,snapshot_date,payload FROM inventory_snapshots ORDER BY kind,snapshot_date'):
  rows=clean(json.loads(payload));dest.execute('INSERT INTO inventory_snapshots VALUES(?,?,?)',(kind,date,json.dumps(rows,ensure_ascii=False)))
  inventory[kind]=rows
  write_csv(out/'csv'/f'{kind}.csv',rows)
 coverage=list(source.execute('SELECT report,start_date,end_date FROM report_coverage'));dest.executemany('INSERT INTO report_coverage VALUES(?,?,?)',coverage)
 for name,rows in reports.items():
  keys=sorted({key for row in rows for key in row});columns=','.join('json_extract(payload,\'$.'+key+'\') AS "'+key+'"' for key in keys)
  if columns:dest.execute('CREATE VIEW "'+name+'" AS SELECT '+columns+" FROM report_rows WHERE report='"+name+"'")
 bounds=dest.execute("SELECT MIN(start_date),MAX(end_date) FROM report_coverage WHERE report='keyword'").fetchone()
 if bounds[0]:summary(dest,inventory,*bounds,output_dir=out/'csv')
 dest.commit();assert dest.execute('PRAGMA integrity_check').fetchone()==('ok',)
 source.close();dest.close();temp.replace(out/'public.sqlite3')
 status=clean(json.loads((root/'status.json').read_text()));(out/'status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2))
 snapshot={'publication':'public','ip_addresses':'omitted; invalid clicks aggregated by date/campaign/device','report_counts':{k:len(v) for k,v in reports.items()},'coverage':coverage}
 (out/'manifest.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2))
 print('Public export:',len(reports),'reports; no visitor IP addresses or credentials')

def hydrate(root=ROOT):
 """Allow a fresh clone to build the site without credentials or private data."""
 target=root/'baidu.sqlite3'
 if target.exists():raise FileExistsError('Local private database already exists; do not overwrite it with public data')
 target.write_bytes((root/'data/public.sqlite3').read_bytes())
 if not (root/'status.json').exists():(root/'status.json').write_bytes((root/'data/status.json').read_bytes())
 print('Public snapshot copied to local data files')
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--hydrate',action='store_true');args=parser.parse_args();hydrate() if args.hydrate else export()
