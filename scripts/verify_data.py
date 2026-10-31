#!/usr/bin/env python3
"""Check API report totals against the same fully covered period before publication."""
import json,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def verify(path=None):
 c=sqlite3.connect(path or ROOT/'baidu.sqlite3')
 windows={r:(s,e) for r,s,e in c.execute('SELECT report,MIN(start_date),MAX(end_date) FROM report_coverage GROUP BY report')}
 baseline=None
 for report in ['account','campaign','adgroup','keyword','keyword_device','keyword_region','keyword_match','creative','search_terms','landing_page','channels']:
  if report not in windows:continue
  start,end=windows[report]
  if 'account' in windows and windows[report]!=windows['account']:continue
  spend,clicks=c.execute("SELECT COALESCE(SUM(json_extract(payload,'$.cost')),0),COALESCE(SUM(json_extract(payload,'$.click')),0) FROM report_rows WHERE report=? AND substr(date,1,10) BETWEEN ? AND ?",(report,start,end)).fetchone()
  value=(round(spend,2),int(clicks))
  if baseline is None:baseline=value
  if abs(value[0]-baseline[0])>.02 or value[1]!=baseline[1]:raise ValueError('Report reconciliation requires review: '+report)
 invalid=c.execute("SELECT SUM(json_extract(payload,'$.sumClicks')),SUM(json_extract(payload,'$.freeClicks')),SUM(json_extract(payload,'$.validClicks')),SUM(json_extract(payload,'$.sumCost')),SUM(json_extract(payload,'$.freeCost')),SUM(json_extract(payload,'$.validCost')) FROM report_rows WHERE report='invalid_clicks'").fetchone()
 if invalid[0] is not None:
  total,free,valid,amount,free_amount,billed=invalid
  assert total==free+valid and abs(amount-free_amount-billed)<.02,'Billing totals do not reconcile'
 c.close();print('Report totals reconciled:',baseline)
if __name__=='__main__':verify()
