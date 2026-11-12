import datetime as dt
import unittest
from unittest.mock import patch

from scripts.sync_website_traffic import fetch_traffic
from website_traffic import TRAFFIC_METRICS


class WebsiteTrafficTests(unittest.TestCase):
 def test_range_totals_use_baidu_summary_and_keep_daily_counts_separate(self):
  config={'site_id':12,'start_date':'2026-09-01'}
  calls=[]
  def request(config,method,body):
   calls.append(body)
   self.assertEqual(method,'getData')
   self.assertEqual(body['method'],'trend/time/a')
   self.assertEqual(body['metrics'],','.join(TRAFFIC_METRICS))
   self.assertNotIn('source',body)
   self.assertNotIn('target',body)
   return {'result':{
    'fields':['simple_date_title',*TRAFFIC_METRICS],
    'sum':[[100,50,42,'00:01:23',9],[]],
    'items':[[['2026/11/10'],['2026/11/11']],[[50,36,30,60,9],[50,28,24,'--',0]],[],[]],
   }}
  with patch('scripts.sync_website_traffic.validate_site') as site, patch('scripts.sync_website_traffic.tongji',side_effect=request):
   data=fetch_traffic(config,end=dt.date(2026,11,11),site_slug='ssw')
  site.assert_called_once()
  self.assertEqual([row['start_date'] for row in calls],['20260901','20261013','20261105'])
  self.assertEqual(data['ranges'][0],{
   'start':'2026-09-01','end':'2026-11-11','pv_count':100,'visitor_count':50,'ip_count':42,'avg_visit_time':83,'trans_count':9,
  })
  self.assertEqual(data['days'][1],{
   'date':'2026-11-11','pv_count':50,'visitor_count':28,'ip_count':24,'avg_visit_time':None,'trans_count':0,
  })

 def test_presets_share_one_query_for_a_new_site_and_preserve_missing_metrics(self):
  config={'site_id':4,'start_date':'2026-11-11'}
  result={'result':{'fields':['simple_date_title',*TRAFFIC_METRICS],'sum':[[],[]],'items':[[],[],[],[]]}}
  with patch('scripts.sync_website_traffic.validate_site'), patch('scripts.sync_website_traffic.tongji',return_value=result) as request:
   traffic=fetch_traffic(config,end=dt.date(2026,11,11),site_slug='tinacms')
  request.assert_called_once()
  self.assertEqual(traffic,{'ranges':[{'start':'2026-11-11','end':'2026-11-11',**dict.fromkeys(TRAFFIC_METRICS)}],'days':[]})
