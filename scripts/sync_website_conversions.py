#!/usr/bin/env python3
"""Pull or import daily website totals and publish only numeric aggregates."""
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
from website_conversions import FIELDS, FILENAME, SSW_FIELDS, SSW_FILENAME, SSW_HOMEPAGE_FIELDS, SSW_HOMEPAGE_FILENAME, TINA_PAID_FIELDS, TINA_PAID_FILENAME, load_rows, validate_rows
from scripts.publish_data import export_website_conversions

SECRET='BAIDU_TONGJI_CREDENTIALS_JSON'
SSW_SITE_CONFIG='BAIDU_TONGJI_SSW_SITE_JSON'
SERVICE='https://api.baidu.com/json/tongji/v1/ReportService/'
TINA_HOSTS={'tinacms.com.cn','www.tinacms.com.cn'}
SSW_HOSTS={'ssw.com.cn','www.ssw.com.cn'}
SITES={
 'tinacms':('TinaCMS China',TINA_HOSTS,FIELDS,FILENAME),
 'tinacms-paid':('TinaCMS China paid',TINA_HOSTS,TINA_PAID_FIELDS,TINA_PAID_FILENAME),
 'ssw':('SSW China',SSW_HOSTS,SSW_FIELDS,SSW_FILENAME),
 'ssw-homepage':('SSW China homepage',SSW_HOSTS,SSW_HOMEPAGE_FIELDS,SSW_HOMEPAGE_FILENAME),
}
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


def read_count(raw):
 if raw is None or raw in ('--','—',''):
  return None
 if type(raw) is int or (isinstance(raw,str) and raw.isascii() and raw.isdecimal()):
  return int(raw)
 raise ValueError('Unexpected count in Baidu Tongji report')


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
  days[date]=read_count(raw)
 return days


def fetch_paid_day(config, day, goal_field, target):
 date=day.strftime('%Y%m%d')
 base={'site_id':config['site_id'],'method':'pro/hour/a','adProduct':'0,0',
       'start_date':date,'end_date':date}

 def total(metric, **filters):
  report=tongji(config,'getData',{**base,**filters,'metrics':metric})['result']
  if report['fields'][1:]!=[metric]:
   raise ValueError('Unexpected metric in Baidu Tongji paid promotion report')
  totals=report['sum']
  if (not totals or not totals[0]) and report.get('total')==0:
   return None
  raw,=totals[0]
  return read_count(raw)

 visits=total('visit_count')
 if visits is None:
  return None
 return {'date':day.isoformat(),'paid_sessions':visits,
         goal_field:total('trans_count',target=target)}


def fetch_homepage_day(config, day):
 date=day.strftime('%Y%m%d')
 report=tongji(config,'getData',{'site_id':config['site_id'],'method':'visit/toppage/a',
                                'start_date':date,'end_date':date,'metrics':'pv_count','max_results':0})['result']
 if report['fields'][1:]!=['pv_count']:
  raise ValueError('Unexpected metric in Baidu Tongji visited pages report')
 if report['total']==0:
  return None
 dimensions,values=report['items'][:2]
 if len(dimensions)!=len(values) or len(dimensions)!=report['total']:
  raise ValueError('Incomplete Baidu Tongji visited pages report')
 views=0
 for dimension,value in zip(dimensions,values):
  page=dimension[0]
  urls=(page.get('id'),page.get('name')) if isinstance(page,dict) else (page,)
  for url in urls:
   if not isinstance(url,str):
    continue
   parsed=urlsplit(url if '://' in url else '//'+url.removeprefix('//'))
   if parsed.scheme in ('','http','https') and parsed.hostname in SSW_HOSTS and parsed.path in ('','/'):
    raw,=value
    count=read_count(raw)
    if count is None:
     return None
    views+=count
    break
 return {'date':day.isoformat(),'homepage_views':views}


def fetch_rows(config, previous, *, end, site_slug='tinacms'):
 name,hosts,fields,_=SITES[site_slug]
 site=config['site_id']
 targets={field:config[field.removeprefix('paid_').removesuffix('_sessions')+'_target'] for field in fields[2:]}
 if any(type(x) is not int or x<=0 for x in [site,*targets.values()]) or len(set(targets.values()))!=len(targets):
  raise ValueError(f'Configure the {name} site ID and its distinct individual target IDs')
 first=dt.date.fromisoformat(config['start_date'])
 if first>end:
  return []
 sites=tongji(config,'getSiteList')['list']
 if not any(row.get('site_id')==site and row.get('status')==0 and
            urlsplit(row['domain'] if '://' in row['domain'] else '//'+row['domain']).hostname in hosts
            for row in sites):
  raise ValueError(f'The configured Baidu Tongji site is not an active {name} site')
 latest=max((dt.date.fromisoformat(r['date']) for r in previous if r['date']<=end.isoformat()),default=first)
 start=max(first,latest-dt.timedelta(days=6))
 all_rows=[]
 if site_slug=='ssw-homepage':
  for offset in range((end-start).days+1):
   row=fetch_homepage_day(config,start+dt.timedelta(days=offset))
   if row is not None:
    all_rows.append(row)
  return validate_rows(all_rows,fields)
 if fields[1]=='paid_sessions':
  goal_field,=targets
  for offset in range((end-start).days+1):
   row=fetch_paid_day(config,start+dt.timedelta(days=offset),goal_field,targets[goal_field])
   if row is not None:
    all_rows.append(row)
  return validate_rows(all_rows,fields)
 while start<=end:
  stop=min(end,start+dt.timedelta(days=30))
  base={'site_id':site,'method':'trend/time/a','start_date':start.strftime('%Y%m%d'),
        'end_date':stop.strftime('%Y%m%d'),'gran':'day','max_results':0}
  def get(metric, target=None):
   filters={} if target is None else {'target':target}
   report=tongji(config,'getData',{**base,**filters,'metrics':metric})['result']
   return read_daily(report,metric,start,stop)
  visits=get('visit_count')
  goals={field:get('trans_count',target) for field,target in targets.items()}
  all_rows.extend({'date':d.isoformat(),'sessions':count,**{field:days.get(d) for field,days in goals.items()}}
                  for d,count in visits.items() if count is not None)
  start=stop+dt.timedelta(days=1)
 return validate_rows(all_rows,fields)


def read_csv(path,fields=FIELDS):
 with path.open(encoding='utf-8-sig',newline='') as file:
  reader=csv.DictReader(file)
  if tuple(reader.fieldnames or ())!=fields:
   raise ValueError('CSV headers must match the selected site’s aggregate fields')
  rows=[]
  for row in reader:
   if set(row)!=set(fields) or any(v is None for v in row.values()):
    raise ValueError('CSV row does not match its headers')
   for metric in fields[1:]:
    raw=row[metric].strip()
    if raw and (not raw.isascii() or not raw.isdecimal()):
     raise ValueError('CSV counts must be integers or blank for unmeasured goals')
    row[metric]=int(raw) if raw else None
   rows.append(row)
 return validate_rows(rows,fields)


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--csv',type=Path,help='Import the documented daily aggregate CSV instead of querying Tongji')
 parser.add_argument('--site',choices=SITES,default='tinacms')
 args=parser.parse_args()
 name,_,fields,filename=SITES[args.site]
 ssw=args.site in ('ssw','ssw-homepage')
 data_root=ROOT/'accounts'/'ssw' if ssw else ROOT
 path=data_root/'data'/filename
 secret=os.environ.get(SECRET)
 site_config=os.environ.get(SSW_SITE_CONFIG) if ssw else secret
 if not args.csv and not (secret and site_config):
  print(f'{name} website statistics: no data source configured')
  return
 try:
  if args.csv:
   rows=read_csv(args.csv,fields)
  else:
   config=json.loads(secret)
   if ssw:
    site_config=json.loads(site_config)
    config={key:config[key] for key in ('user_name','access_token')}
    config.update({key:site_config[key] for key in ('site_id','start_date')})
    if args.site=='ssw':
     config['contact_click_target']=site_config['contact_click_target']
   rows=fetch_rows(config,load_rows(path,fields),end=dt.datetime.now(BEIJING).date()-dt.timedelta(days=1),site_slug=args.site)
  export_website_conversions(path,rows,fields)
 except (ValueError, KeyError, IndexError, TypeError, OSError):
  sys.exit(f'{name} website statistics were not updated. Check site, date, goal IDs, access, and aggregate format.')
 print(f'{name} website statistics: {len(rows)} daily aggregates received')


if __name__=='__main__':
 main()
