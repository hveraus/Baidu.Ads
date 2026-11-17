from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from baidu_ads_mcp.query import ReleaseQuery, QueryError
from baidu_ads_mcp.analytics import Analytics
from baidu_ads_mcp.changes import ChangeStore, Principal
from fixtures import make_release


OPERATOR=Principal('alice','v1','operator',frozenset({'analytics:read','settings:read','changes:propose','changes:execute'}))
OWNER=Principal('kenshi','v1','owner',OPERATOR.scopes)
VIEWER=Principal('bob','v1','viewer',frozenset({'analytics:read'}))
BUDGET={'op':'set_campaign_budget','entity_id':'1','amount':'120.00'}


def enrich(release):
    for path in release.rglob('*.html'):
        text=path.read_text();data=json.loads(text.split('>',1)[1].split('</script>')[0])
        data['inventory']={'campaign_inventory':[{'campaignId':1,'campaignName':'<script>test</script>','budget':'100.00','pause':False,'negativeWords':['原词'],'exactNegativeWords':[],'schedule':[{'weekDay':1,'startHour':0,'endHour':9}]}],
            'keyword_inventory':[{'keywordId':9,'keyword':'原词','getTemp':0,'price':'1.00'}, {'keywordId':9,'keyword':'原词','getTemp':1,'price':'2.00'}]}
        data['website_traffic']={'days':[{'date':'2026-11-14','pv_count':10,'visitor_count':8,'ip_count':7,'avg_visit_time':10,'trans_count':0},
            {'date':'2026-11-15','pv_count':20,'visitor_count':9,'ip_count':8,'avg_visit_time':20,'trans_count':2}],
            'ranges':[{'start':'2026-11-14','end':'2026-11-15','pv_count':30,'visitor_count':12,'ip_count':10,'avg_visit_time':15,'trans_count':2}]}
        data['tina_paid_website_conversions']=[{'date':'2026-11-14','paid_sessions':10,'paid_email_click_sessions':0},
            {'date':'2026-11-15','paid_sessions':20,'paid_email_click_sessions':2,'paid_install_copy_sessions':3}]
        path.write_text('<script id="report-data" type="application/json">'+json.dumps(data).replace('<',r'\u003c')+'</script>')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        release=make_release(self.tmp.name);enrich(release)
        self.query=ReleaseQuery(release)
        self.analytics=Analytics(self.query)
        self.now=1000
        self.store=ChangeStore(Path(self.tmp.name)/'control.sqlite3',self.query,clock=lambda:self.now)


class AnalyticsTests(Base):
    def test_exact_unique_range(self):
        result=self.analytics.website('tinacms','2026-11-14','2026-11-15')
        self.assertEqual(result['totals']['visitor_count'],12)
        self.assertEqual(result['aggregation'],'exact_range')

    def test_custom_unique_range_not_summed(self):
        result=self.analytics.website('tinacms','2026-11-14','2026-11-16')
        self.assertIsNone(result['totals']['visitor_count'])
        self.assertIsNone(result['totals']['pv_count'])
        self.assertTrue(result['is_partial'])

    def test_paid_coverage_is_per_field(self):
        result=self.analytics.website('tinacms','2026-11-14','2026-11-15','baidu_paid')
        self.assertEqual(result['totals']['paid_email_click_sessions'],2)
        self.assertIsNone(result['totals']['paid_install_copy_sessions'])
        self.assertEqual(result['missing_dates_by_metric']['paid_install_copy_sessions'],['2026-11-14'])
        self.assertIn('ALL_CAMPAIGNS_ACCOUNT_SCOPE',result['warnings'])

    def test_single_day_zero_and_scope(self):
        result=self.analytics.website('tinacms','2026-11-14','2026-11-14','baidu_paid')
        self.assertEqual(result['totals']['paid_email_click_sessions'],0)
        with self.assertRaisesRegex(QueryError,'UNSUPPORTED_SCOPE'):
            self.analytics.website('tinacms','2026-11-14','2026-11-14','keyword')

    def test_entity_shadow_and_ids(self):
        result=self.analytics.settings('tinacms','keyword','9')
        self.assertTrue(result['ambiguous'])
        self.assertEqual([r['getTemp'] for r in result['items']],[0,1])
        self.assertEqual(result['items'][0]['keywordId'],'9')

    def test_comparison_missing_refuses_change_claim(self):
        result=self.analytics.compare('tinacms','2026-11-14','2026-11-16','2026-11-14','2026-11-16')
        self.assertFalse(result['comparable']);self.assertEqual(result['changes'],{})

    def test_comparison_complete(self):
        result=self.analytics.compare('tinacms','2026-11-16','2026-11-16','2026-11-14','2026-11-14')
        self.assertTrue(result['comparable'])
        self.assertEqual(result['changes']['cost']['absolute'],'0.100000')

    def test_analysis_does_not_invent_leads(self):
        result=self.analytics.analyze('tinacms','2026-11-14','2026-11-16')
        self.assertIn('COST_RANKING_IS_NOT_PROOF_OF_WASTE',result['warnings'])


class ChangeTests(Base):
    def create(self,actor=OPERATOR,operations=None):
        return self.store.create(actor,'tinacms',operations or [BUDGET],'用户明确提出的预算方案')

    def test_preview_retains_before_after(self):
        plan=self.create()
        change=plan['proposal']['changes'][0]
        self.assertEqual(change['changed_fields']['budget'],{'before':'100.00','after':'120.00'})
        self.assertFalse(plan['execution_enabled'])
        self.assertEqual(self.store.history(OPERATOR,plan['id'])['events'][0]['action'],'created')

    def test_viewer_cannot_propose_or_approve(self):
        with self.assertRaises(QueryError):self.create(VIEWER)
        plan=self.create();self.store.request_approval(OPERATOR,plan['id'],1)
        with self.assertRaisesRegex(QueryError,'INSUFFICIENT_ROLE'):
            self.store.approve_from_web(OPERATOR,plan['id'],1,plan['hash'])

    def test_other_operator_cannot_read(self):
        plan=self.create()
        with self.assertRaisesRegex(QueryError,'PLAN_NOT_FOUND'):
            self.store.get(Principal('eve','v1','operator',OPERATOR.scopes),plan['id'])

    def test_stale_version_and_hash(self):
        plan=self.create();self.store.request_approval(OPERATOR,plan['id'],1)
        for version,value in [(2,plan['hash']),(1,'bad')]:
            with self.assertRaisesRegex(QueryError,'STALE_PLAN_VERSION'):
                self.store.approve_from_web(OWNER,plan['id'],version,value)

    def test_edit_invalidates_approval(self):
        plan=self.create();self.store.request_approval(OPERATOR,plan['id'],1)
        self.store.approve_from_web(OWNER,plan['id'],1,plan['hash'])
        updated=self.store.revise(OPERATOR,plan['id'],1,[{**BUDGET,'amount':'130.00'}],'新方案')
        self.assertEqual(updated['version'],2);self.assertIsNone(updated['approval_actor'])
        with self.assertRaisesRegex(QueryError,'APPROVAL_REQUIRED'):
            self.store.execute(OPERATOR,plan['id'],2,'test-idempotency-123')

    def test_approved_never_executes_upstream(self):
        plan=self.create();self.store.request_approval(OPERATOR,plan['id'],1)
        self.store.approve_from_web(OWNER,plan['id'],1,plan['hash'])
        with self.assertRaisesRegex(QueryError,'MUTATIONS_DISABLED'):
            self.store.execute(OPERATOR,plan['id'],1,'test-idempotency-123')
        self.now+=1800
        with self.assertRaisesRegex(QueryError,'APPROVAL_EXPIRED'):
            self.store.execute(OPERATOR,plan['id'],1,'test-idempotency-123')

    def test_cancel_and_account_scope(self):
        plan=self.create();self.store.cancel(OPERATOR,plan['id'],1)
        with self.assertRaisesRegex(QueryError,'STALE_PLAN_VERSION'):
            self.store.request_approval(OPERATOR,plan['id'],1)
        actor=Principal('alice','v1','operator',OPERATOR.scopes,frozenset({'ssw'}))
        with self.assertRaisesRegex(QueryError,'INSUFFICIENT_SCOPE'):self.create(actor)

    def test_unknown_operation_fields_rejected(self):
        for value in ({**BUDGET,'confirmed':True},{**BUDGET,'amount':'NaN'},{'op':'raw_api','url':'https://example.com'}):
            with self.assertRaisesRegex(QueryError,'INVALID_OPERATION'):self.create(operations=[value])
        with self.assertRaisesRegex(QueryError,'DUPLICATE_ENTITY'):
            self.create(operations=[BUDGET,BUDGET])

    def test_negative_words_preserved_and_deduplicated(self):
        plan=self.create(operations=[{'op':'add_negative_keywords','kind':'campaign','entity_id':'1','match':'phrase','words':['原词','新增','新增']}])
        self.assertEqual(plan['proposal']['changes'][0]['after']['negativeWords'],['原词','新增'])

    def test_foreign_database_untouched(self):
        path=Path(self.tmp.name)/'core.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:db.execute('CREATE TABLE users(id INTEGER)')
        before=path.read_bytes()
        with self.assertRaisesRegex(QueryError,'FOREIGN_DATABASE'):ChangeStore(path,self.query)
        self.assertEqual(before,path.read_bytes())
