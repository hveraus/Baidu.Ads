import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.publish_data import export_website_conversions
from scripts.sync_website_conversions import fetch_rows, read_csv
from website_conversions import load_rows


class WebsiteConversionTests(unittest.TestCase):
 def test_import_keeps_measured_zero_and_missing_separate(self):
  with tempfile.TemporaryDirectory() as folder:
   source=Path(folder)/'input.csv'
   dest=Path(folder)/'website-conversions.json'
   source.write_text('date,sessions,install_copy_sessions,email_click_sessions\n2026-10-03,12,0,\n')
   export_website_conversions(dest,read_csv(source))
   saved=load_rows(dest)
   self.assertEqual(saved,[{'date':'2026-10-03','sessions':12,'install_copy_sessions':0,'email_click_sessions':None}])
   for invalid in ['2026-10-03,12,13,0\n','2026-10-03,12,0,0,extra\n','2026-10-03,12,0,0\n2026-10-03,10,0,0\n']:
    source.write_text('date,sessions,install_copy_sessions,email_click_sessions\n'+invalid)
    with self.assertRaises(ValueError):
     read_csv(source)
   self.assertEqual(load_rows(dest),saved)

 def test_separate_goals_are_joined_by_date_and_wrong_site_is_rejected(self):
  config={'site_id':1,'start_date':'2026-10-01','install_copy_target':10,'email_click_target':20}
  def request(config, method, body=None):
   if method=='getSiteList':
    return {'list':[{'site_id':1,'domain':'www.tinacms.com.cn','status':0}]}
   self.assertEqual(body['site_id'],1)
   self.assertEqual(body['gran'],'day')
   target=body.get('target')
   dates,counts={None:(['2026/10/02','2026/10/01'],[5,10]),
                 10:(['2026/10/01','2026/10/02'],[2,0]),
                 20:(['2026/10/02'],[1])}[target]
   return {'result':{'fields':['simple_date_title',body['metrics']],
                     'items':[[[d] for d in dates],[[n] for n in counts],[],[]]}}
  with patch('scripts.sync_website_conversions.tongji',side_effect=request):
   self.assertEqual(fetch_rows(config,[],end=dt.date(2026,10,2)),[
    {'date':'2026-10-01','sessions':10,'install_copy_sessions':2,'email_click_sessions':None},
    {'date':'2026-10-02','sessions':5,'install_copy_sessions':0,'email_click_sessions':1},
   ])
   with self.assertRaisesRegex(ValueError,'not an active TinaCMS China site'):
    fetch_rows({**config,'site_id':2},[],end=dt.date(2026,10,2))

 def test_ssw_uses_only_its_contact_target(self):
  config={'site_id':12,'start_date':'2026-11-11','contact_click_target':34}
  date=dt.date(2026,11,11)
  sites={'list':[{'site_id':12,'domain':'ssw.com.cn','status':0}]}
  def result(metric,count):
   return {'result':{'fields':['simple_date_title',metric],'items':[[['2026/11/11']],[[count]]]}}
  with patch('scripts.sync_website_conversions.tongji',side_effect=[sites,result('visit_count',8),result('trans_count',1)]) as request:
   self.assertEqual(fetch_rows(config,[],end=date,site_slug='ssw'),[
    {'date':'2026-11-11','sessions':8,'contact_click_sessions':1},
   ])
   requests=[call.args[2] for call in request.call_args_list[1:]]
   self.assertEqual([(body['site_id'],body.get('target')) for body in requests],[(12,None),(12,34)])
  with patch('scripts.sync_website_conversions.tongji',return_value=sites):
   with self.assertRaisesRegex(ValueError,'not an active TinaCMS China site'):
    fetch_rows({**config,'install_copy_target':10,'email_click_target':20},[],end=date)
