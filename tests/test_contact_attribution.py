import datetime as dt
import unittest
from contact_attribution import parse_sources, fetch_rows, validate_rows, PAID_SOURCE


def report(rows, visits, goals):
 return {'fields':['source_type_title','visit_count','trans_count'],'total':len(rows),'sum':[[visits,goals],[]],
         'items':[[[{'source':key}] for key,_,_ in rows],[[visits,goals] for _,visits,goals in rows],[],[]]}


class ContactAttributionTests(unittest.TestCase):
 def test_exact_provider_paid_source(self):
  self.assertEqual(PAID_SOURCE,'searchBaiduProFc')

 def test_groups_match_user_definition(self):
  result=parse_sources(report([('through',8,2),('link',2,1),(PAID_SOURCE,3,2),('searchBaiduNature',4,1)],17,6))
  self.assertEqual(result,{'sessions':17,'contact_click_sessions':6,'paid_sessions':3,'paid_contact_click_sessions':2,'natural_contact_click_sessions':4})

 def test_new_source_goals_count_as_natural(self):
  result=parse_sources(report([('previouslyUnseenSource',2,1),(PAID_SOURCE,3,0)],5,1))
  self.assertEqual(result['paid_sessions'],3)
  self.assertEqual(result['paid_contact_click_sessions'],0)
  self.assertEqual(result['natural_contact_click_sessions'],1)

 def test_missing_paid_source_is_zero_only_in_complete_partition(self):
  result=parse_sources(report([('through',4,1),('link',1,'--')],5,1))
  self.assertEqual(result['paid_contact_click_sessions'],0)
  self.assertEqual(result['natural_contact_click_sessions'],1)
  result=parse_sources(report([('through','--',1)],'--',1))
  self.assertIsNone(result['paid_contact_click_sessions'])
  self.assertIsNone(result['natural_contact_click_sessions'])

 def test_dash_alone_is_unknown(self):
  result=parse_sources(report([('through',4,'--'),(PAID_SOURCE,1,'--')],5,'--'))
  self.assertIsNone(result['contact_click_sessions'])
  self.assertIsNone(result['paid_contact_click_sessions'])
  self.assertIsNone(result['natural_contact_click_sessions'])

 def test_numeric_partition_total_proves_remaining_zero(self):
  result=parse_sources(report([('through',4,2),(PAID_SOURCE,1,'--')],5,2))
  self.assertEqual(result['paid_contact_click_sessions'],0)
  self.assertEqual(result['natural_contact_click_sessions'],2)

 def test_pagination_invalid_source_and_inconsistent_counts_fail(self):
  r=report([('through',4,1)],4,1)
  for bad in [{**r,'total':2},report([(' ',4,1)],4,1),report([('through',4,2)],4,1),report([('through',4,1),('through',2,1)],6,2)]:
   with self.assertRaises(ValueError):parse_sources(bad)

 def test_complete_range_reconciles_dash_dates_to_zero(self):
  config={'site_id':12,'contact_click_target':34}
  def request(c,method,body):
   self.assertEqual((method,body['method'],body['target'],body['max_results']),('getData','source/all/a',34,0))
   if body['start_date']!=body['end_date']:return {'result':report([('through',8,1),(PAID_SOURCE,2,1)],10,2)}
   if body['start_date']=='20261124':return {'result':report([('through',4,1),(PAID_SOURCE,1,1)],5,2)}
   return {'result':report([('through',4,'--'),(PAID_SOURCE,1,'--')],5,'--')}
  rows=fetch_rows(config,dt.date(2026,11,24),dt.date(2026,11,25),request)
  self.assertEqual(rows[1]['contact_click_sessions'],0)
  self.assertEqual(rows[1]['paid_contact_click_sessions'],0)
  self.assertEqual(rows[1]['natural_contact_click_sessions'],0)

 def test_range_does_not_guess_missing_nonzero_residual(self):
  def request(c,m,b):
   if b['start_date']!=b['end_date']:return {'result':report([('through',8,2),(PAID_SOURCE,2,2)],10,4)}
   return {'result':report([('through',4,'--'),(PAID_SOURCE,1,'--')],5,'--')}
  rows=fetch_rows({'site_id':12,'contact_click_target':34},dt.date(2026,11,24),dt.date(2026,11,25),request)
  self.assertIsNone(rows[0]['paid_contact_click_sessions'])

 def test_non_reconciling_range_fails(self):
  def request(c,m,b):return {'result':report([('through',4,1)],4,1)}
  with self.assertRaises(ValueError):fetch_rows({'site_id':12,'contact_click_target':34},dt.date(2026,11,24),dt.date(2026,11,25),request)

 def test_invalid_schema_not_exposed(self):
  row={'date':'2026-11-24','sessions':5,'contact_click_sessions':2,'paid_sessions':3,'paid_contact_click_sessions':1,'natural_contact_click_sessions':1}
  self.assertEqual(validate_rows([row]),[row])
  for bad in [{**row,'visitor_ip':'test'},{**row,'natural_contact_click_sessions':2},{**row,'sessions':True}]:
   with self.assertRaises(ValueError):validate_rows([bad])
