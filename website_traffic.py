"""Validate the aggregate Baidu Tongji traffic snapshots used by the dashboard."""
import datetime as dt
import json

TRAFFIC_FILENAME='website-traffic.json'
TRAFFIC_METRICS=('pv_count','visitor_count','ip_count','avg_visit_time','trans_count')


def date_value(value):
 if not isinstance(value,str) or dt.date.fromisoformat(value).isoformat()!=value:
  raise ValueError('Website traffic dates must be ISO dates')
 return value


def validate_traffic(snapshot):
 if not isinstance(snapshot,dict) or set(snapshot)!={'ranges','days'}:
  raise ValueError('Invalid website traffic snapshot')
 for group,dates in (('ranges',('start','end')),('days',('date',))):
  rows=snapshot[group]
  if not isinstance(rows,list):
   raise ValueError('Website traffic rows must be a list')
  seen=set()
  for row in rows:
   if not isinstance(row,dict) or set(row)!=set(dates+TRAFFIC_METRICS):
    raise ValueError('Website traffic row has unexpected fields')
   key=tuple(date_value(row[date]) for date in dates)
   if key in seen or key[0]>key[-1]:
    raise ValueError('Invalid or duplicate website traffic dates')
   seen.add(key)
   for metric in TRAFFIC_METRICS:
    value=row[metric]
    if value is not None and (type(value) is not int or not 0<=value<=1_000_000_000):
     raise ValueError('Website traffic totals must be non-negative integers')
  snapshot[group]=sorted(rows,key=lambda row:tuple(row[date] for date in dates))
 return snapshot


def load_traffic(path):
 if not path.exists():
  return {'ranges':[],'days':[]}
 return validate_traffic(json.loads(path.read_text()))
