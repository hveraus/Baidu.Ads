#!/usr/bin/env python3
"""Sync daily and date-range website traffic totals from Baidu Tongji."""
import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.publish_data import write_aggregate
from scripts.sync_website_conversions import BEIJING, SECRET, SITES, SSW_SITE_CONFIG, read_count, tongji, validate_site
from website_traffic import TRAFFIC_FILENAME, TRAFFIC_METRICS, load_traffic, validate_traffic


def read_metrics(values):
 if len(values)!=len(TRAFFIC_METRICS):
  raise ValueError('Unexpected number of Baidu Tongji traffic metrics')
 result={}
 for metric,value in zip(TRAFFIC_METRICS,values):
  if metric=='avg_visit_time' and isinstance(value,str) and re.fullmatch(r'\d+:\d{2}:\d{2}',value):
   hours,minutes,seconds=map(int,value.split(':'))
   value=3600*hours+60*minutes+seconds
  result[metric]=read_count(value)
 return result


def fetch_traffic(config, *, end, site_slug):
 name,hosts,_,_=SITES[site_slug]
 validate_site(config,hosts,name)
 first=dt.date.fromisoformat(config['start_date'])
 ranges=[]
 days=[]
 for start in sorted({max(first,end-dt.timedelta(days=length-1)) for length in (7,30,90)}):
  if start>end:
   break
  report=tongji(config,'getData',{
   'site_id':config['site_id'],'method':'trend/time/a','gran':'day','max_results':0,
   'start_date':start.strftime('%Y%m%d'),'end_date':end.strftime('%Y%m%d'),
   'metrics':','.join(TRAFFIC_METRICS),
  })['result']
  if tuple(report['fields'][1:])!=TRAFFIC_METRICS:
   raise ValueError('Unexpected metrics in Baidu Tongji traffic report')
  totals=report['sum']
  counts=read_metrics(totals[0] if totals and totals[0] else [None]*len(TRAFFIC_METRICS))
  ranges.append({'start':start.isoformat(),'end':end.isoformat(),**counts})
  if len(ranges)==1:
   dimensions,values=report['items'][:2]
   if len(dimensions)!=len(values):
    raise ValueError('Baidu Tongji traffic dates and values do not align')
   for dimension,value in zip(dimensions,values):
    day=dt.date.fromisoformat(dimension[0].replace('/','-'))
    if not start<=day<=end:
     raise ValueError('Unexpected date in Baidu Tongji traffic report')
    days.append({'date':day.isoformat(),**read_metrics(value)})
 return validate_traffic({'ranges':ranges,'days':days})


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--site',choices=('tinacms','ssw'),default='tinacms')
 args=parser.parse_args()
 root=ROOT/'accounts'/'ssw' if args.site=='ssw' else ROOT
 path=root/'data'/TRAFFIC_FILENAME
 try:
  config=json.loads(os.environ[SECRET])
  if args.site=='ssw':
   settings=json.loads(os.environ[SSW_SITE_CONFIG])
   config={**{key:config[key] for key in ('user_name','access_token')},
           **{key:settings[key] for key in ('site_id','start_date')}}
  traffic=fetch_traffic(config,end=dt.datetime.now(BEIJING).date()-dt.timedelta(days=1),site_slug=args.site)
  if traffic!=load_traffic(path):
   write_aggregate(path,traffic)
 except (ValueError,KeyError,IndexError,TypeError,OSError):
  sys.exit('Baidu Tongji website traffic could not sync. Check site, date, access, and report format.')
 print(f'Website traffic synced for {args.site}: {len(traffic["ranges"])} ranges, {len(traffic["days"])} daily rows')


if __name__=='__main__':
 main()
