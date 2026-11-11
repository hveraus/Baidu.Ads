#!/usr/bin/env python3
"""Pull or import daily TinaCMS website totals and publish only numeric aggregates."""
import argparse
import csv
import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from website_conversions import FIELDS, FILENAME, METRICS, load_rows, validate_rows
from scripts.publish_data import export_website_conversions

SECRET='BAIDU_TONGJI_CREDENTIALS_JSON'
SERVICE='https://api.baidu.com/json/tongji/v1/ReportService/'
HOSTS={'tinacms.com.cn','www.tinacms.com.cn'}
BEIJING=dt.timezone(dt.timedelta(hours=8))


class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self, req, fp, code, msg, headers, newurl):
  return None


def tongji(config, method, body=None):
 payload={'header':{'userName':config['user_name'],'accessToken':config['access_token']}}
 if body is not None:
  payload['body']=body
 request=urllib.request.Request(SERVICE+method,data=json.dumps(payload).encode(),
                                headers={'Content-Type':'application/json'})
 try:
  with urllib.request.build_opener(NoRedirect()).open(request,timeout=20) as response:
   result=json.load(response)
  if result['header']['status']!=0 or result['header'].get('failures'):
   raise ValueError
  return result['body']['data'][0]
 except (OSError, ValueError, KeyError, IndexError, TypeError):
  # Do not include provider errors or response bodies, which can echo credentials.
  raise ValueError(f'Baidu Tongji {method} failed; check access and configuration') from None


def read_daily(result, metric, start, end):
 fields=result['fields']
 if fields[1:]!=[metric]:
  raise ValueError('Unexpected metric in Baidu Tongji report')
 dimensions,values=result['items'][:2]
 days={}
 if len(dimensions)!=len(values):
  raise ValueError('Baidu Tongji report dates and values do not align')
 for dimension,value in zip(dimensions,values):
  date=dt.date.fromisoformat(dimension[0].replace('/','-'))
  if date in days or not start<=date<=end:
   raise ValueError('Unexpected date in Baidu Tongji report')
  raw,=value
  if raw is None or raw in ('--','—',''):
   days[date]=None
  elif type(raw) is int or (isinstance(raw,str) and raw.isascii() and raw.isdecimal()):
   days[date]=int(raw)
  else:
   raise ValueError('Unexpected count in Baidu Tongji report')
 return days


def fetch_rows(config, previous, *, end):
 site=config['site_id']
 targets=[config[key] for key in ('install_copy_target','email_click_target')]
 if any(type(x) is not int or x<=0 for x in [site,*targets]) or len(set(targets))!=2:
  raise ValueError('Configure the TinaCMS site ID and two distinct individual target IDs')
 first=dt.date.fromisoformat(config['start_date'])
 if first>end:
  return []
 sites=tongji(config,'getSiteList')['list']
 if not any(row.get('site_id')==site and row.get('status')==0 and
            urlsplit(row['domain'] if '://' in row['domain'] else '//'+row['domain']).hostname in HOSTS
            for row in sites):
  raise ValueError('The configured Baidu Tongji site is not an active TinaCMS China site')
 latest=max((dt.date.fromisoformat(r['date']) for r in previous if r['date']<=end.isoformat()),default=first)
 start=max(first,latest-dt.timedelta(days=6))
 all_rows=[]
 while start<=end:
  stop=min(end,start+dt.timedelta(days=30))
  base={'site_id':site,'method':'trend/time/a','start_date':start.strftime('%Y%m%d'),
        'end_date':stop.strftime('%Y%m%d'),'gran':'day','max_results':0}
  def get(metric, target=None):
   filters={} if target is None else {'target':target}
   report=tongji(config,'getData',{**base,**filters,'metrics':metric})['result']
   return read_daily(report,metric,start,stop)
  visits=get('visit_count')
  copy,email=(get('trans_count',target) for target in targets)
  all_rows.extend({'date':d.isoformat(),'sessions':count,
                   'install_copy_sessions':copy.get(d),'email_click_sessions':email.get(d)}
                  for d,count in visits.items() if count is not None)
  start=stop+dt.timedelta(days=1)
 return validate_rows(all_rows)


def read_csv(path):
 with path.open(encoding='utf-8-sig',newline='') as file:
  reader=csv.DictReader(file)
  if tuple(reader.fieldnames or ())!=FIELDS:
   raise ValueError('CSV headers must match the four documented aggregate fields')
  rows=[]
  for row in reader:
   if set(row)!=set(FIELDS) or any(v is None for v in row.values()):
    raise ValueError('CSV row does not match its headers')
   for metric in METRICS:
    raw=row[metric].strip()
    if raw and (not raw.isascii() or not raw.isdecimal()):
     raise ValueError('CSV counts must be integers or blank for unmeasured goals')
    row[metric]=int(raw) if raw else None
   rows.append(row)
 return validate_rows(rows)


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--csv',type=Path,help='Import the documented daily aggregate CSV instead of querying Tongji')
 args=parser.parse_args()
 path=ROOT/'data'/FILENAME
 secret=os.environ.get(SECRET)
 if not args.csv and not secret:
  print('TinaCMS website statistics: no data source configured')
  return
 try:
  if args.csv:
   rows=read_csv(args.csv)
  else:
   config=json.loads(secret)
   rows=fetch_rows(config,load_rows(path),end=dt.datetime.now(BEIJING).date()-dt.timedelta(days=1))
  export_website_conversions(path,rows)
 except (ValueError, KeyError, IndexError, TypeError, OSError):
  sys.exit('TinaCMS website statistics were not updated. Check site, date, goal IDs, access, and aggregate format.')
 print(f'TinaCMS website statistics: {len(rows)} daily aggregates received')


if __name__=='__main__':
 main()
