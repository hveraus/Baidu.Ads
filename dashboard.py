"""Generate a standalone, offline analysis page from the local database."""
import base64,datetime as dt,json,sqlite3
from pathlib import Path
from i18n import english_template
from website_conversions import FILENAME, SSW_FIELDS, SSW_FILENAME, TINA_PAID_FIELDS, TINA_PAID_FILENAME, load_rows
ROOT=Path(__file__).resolve().parent

def build_dashboard(public=False):
 c=sqlite3.connect(ROOT/('data/public.sqlite3' if public else 'baidu.sqlite3'))
 reports={}
 for name,payload in c.execute('select report,payload from report_rows order by date,row_key'):
  reports.setdefault(name,[]).append(json.loads(payload))
 record_counts={k:len(v) for k,v in reports.items()}
 # The browser report uses aggregate click-quality data, never raw visitor IPs.
 invalid={}
 for row in reports.get('invalid_clicks',[]):
  key=(row.get('date'),row.get('campaignId'),row.get('device'))
  g=invalid.setdefault(key,{k:row.get(k) for k in ['date','campaignId','campaignName','device']})
  for k in ['sumClicks','sumCost','freeClicks','freeCost','validClicks','validCost']: g[k]=g.get(k,0)+float(row.get(k) or 0)
 if 'invalid_clicks' in reports: reports['invalid_clicks']=list(invalid.values())
 inventory={}
 for kind,payload in c.execute('select kind,payload from inventory_snapshots s where snapshot_date=(select max(snapshot_date) from inventory_snapshots where kind=s.kind)'):
  inventory[kind]=json.loads(payload)
 coverage=[{'report':r,'start':s,'end':e} for r,s,e in c.execute('select report,min(start_date),max(end_date) from report_coverage group by report')]
 c.close()
 status=json.loads((ROOT/('data/status.json' if public else 'status.json')).read_text())
 profile=json.loads((ROOT/'profile.json').read_text()) if (ROOT/'profile.json').exists() else {'slug':'tinacms','name':'TinaCMS'}
 data={'reports':reports,'inventory':inventory,'coverage':coverage,'status':status,'generated':dt.datetime.now().isoformat(),'record_counts':record_counts,'website':json.loads((ROOT/'website-audit.json').read_text()) if (ROOT/'website-audit.json').exists() else {}}
 if profile['slug']=='tinacms':
  data['website_conversions']=load_rows(ROOT/'data'/FILENAME)
  data['tina_paid_website_conversions']=load_rows(ROOT/'data'/TINA_PAID_FILENAME,TINA_PAID_FIELDS)
  if public:
   from scripts.sync_posthog_cli import ENCRYPTED_SNAPSHOT, read_encrypted_snapshot
   encrypted=read_encrypted_snapshot(ROOT/'data'/ENCRYPTED_SNAPSHOT)
   if encrypted:
    data['encrypted_posthog_cli']=base64.b64encode(encrypted).decode('ascii')
 elif profile['slug']=='ssw':
  data['ssw_paid_website_conversions']=load_rows(ROOT/'data'/SSW_FILENAME,SSW_FIELDS)
 template=(ROOT/'dashboard.template.html').read_text()
 if public:
  from publication import public_template
  template=public_template(template)
 # Script-safe serialization: account-supplied names cannot break out of the JSON block.
 packed=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
 output=ROOT/'report'/'index.html'; output.parent.mkdir(exist_ok=True)
 from account_ui import account_template
 output.write_text(account_template(template,profile,'zh').replace('/*__DATA__*/',packed))
 output.chmod(0o600)
 english=output.with_name('en.html')
 english.write_text(account_template(english_template(template),profile,'en').replace('/*__DATA__*/',packed))
 english.chmod(0o600)
 return output
if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--public',action='store_true');args=parser.parse_args()
 print(build_dashboard(public=args.public))
