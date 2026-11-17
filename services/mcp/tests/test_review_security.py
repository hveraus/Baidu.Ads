"""Regression coverage for the independent security/data review."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from mcp.server.auth.provider import TokenError
from baidu_ads_mcp.analytics import Analytics
from baidu_ads_mcp.query import ReleaseQuery, QueryError
from baidu_ads_mcp.auth import Bridge
from fixtures import make_release
import test_auth_web as auth_fixtures
from test_analytics_changes import enrich


class OAuthReviewTests(auth_fixtures.Setup,unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.setup()

    async def test_removed_client_revokes_existing_access_and_refresh(self):
        tokens=await self.grant()
        client=self.provider.clients.pop('test-client')
        self.assertIsNone(await self.provider.load_access_token(tokens.access_token))
        refresh=await self.provider.load_refresh_token(client,tokens.refresh_token)
        with self.assertRaises(TokenError):
            await self.provider.exchange_refresh_token(client,refresh,['analytics:read'])

    async def test_resource_change_invalidates_old_tokens(self):
        tokens=await self.grant()
        self.provider.resource='https://different.test/mcp'
        self.assertIsNone(await self.provider.load_access_token(tokens.access_token))

    async def test_refresh_loaded_before_rival_rotation_still_revokes_family(self):
        tokens=await self.grant()
        client=await self.provider.get_client('test-client')
        model=await self.provider.load_refresh_token(client,tokens.refresh_token)
        second=await self.provider.exchange_refresh_token(client,model,['analytics:read'])
        with self.assertRaises(TokenError):
            await self.provider.exchange_refresh_token(client,model,['analytics:read'])
        self.assertIsNone(await self.provider.load_access_token(second.access_token))

    async def test_revoke_while_identity_check_waits(self):
        tokens=await self.grant()
        access=await self.provider.load_access_token(tokens.access_token)
        entered,finish=asyncio.Event(),asyncio.Event()
        async def verify(identity):
            entered.set();await finish.wait();return identity
        self.bridge.verify=verify
        pending=asyncio.create_task(self.provider.load_access_token(tokens.access_token))
        await entered.wait()
        await self.provider.revoke_token(access)
        finish.set()
        self.assertIsNone(await pending)

    async def test_code_replay_revokes_grant_family(self):
        client=await self.provider.get_client('test-client')
        from mcp.server.auth.provider import AuthorizationParams
        from urllib.parse import urlsplit,parse_qs
        params=AuthorizationParams(state='state',scopes=['analytics:read'],code_challenge=auth_fixtures.CHALLENGE,
            redirect_uri=auth_fixtures.CLIENTS[0]['redirect_uris'][0],redirect_uri_provided_explicitly=True,resource=self.provider.resource)
        pending=parse_qs(urlsplit(await self.provider.authorize(client,params)).query)['request'][0]
        code=parse_qs(urlsplit(await self.provider.consent(pending,'alice','test-password')).query)['code'][0]
        model=await self.provider.load_authorization_code(client,code)
        tokens=await self.provider.exchange_authorization_code(client,model)
        self.assertIsNone(await self.provider.load_authorization_code(client,code))
        self.assertIsNone(await self.provider.load_access_token(tokens.access_token))


class DataReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.release=make_release(self.tmp.name);enrich(self.release)

    def alter(self,fn):
        path=self.release/'report/index.html'
        raw=path.read_text()
        data=json.loads(raw.split('>',1)[1].split('</script>')[0])
        fn(data)
        path.write_text('<script id="report-data" type="application/json">'+json.dumps(data).replace('<',r'\u003c')+'</script>')

    def test_exact_coverage_distinct_from_daily_gaps(self):
        self.alter(lambda d:d['website_traffic'].update(days=[]))
        result=Analytics(ReleaseQuery(self.release)).website('tinacms','2026-11-14','2026-11-15')
        self.assertFalse(result['is_partial'])
        self.assertEqual(result['missing_dates'],[])
        self.assertEqual(result['daily_coverage']['missing_dates'],['2026-11-14','2026-11-15'])
        self.assertEqual(result['covered_range'],{'start':'2026-11-14','end':'2026-11-15'})

    def test_non_finite_inventory_rejected(self):
        self.alter(lambda d:d['inventory']['campaign_inventory'][0].update(budget=float('nan')))
        with self.assertRaisesRegex(QueryError,'INVALID_SNAPSHOT'):ReleaseQuery(self.release)

    def test_invalid_pause_and_schedule_fail_closed(self):
        self.alter(lambda d:d['inventory']['campaign_inventory'][0].update(pause='false'))
        with self.assertRaisesRegex(QueryError,'INVALID_SNAPSHOT'):ReleaseQuery(self.release)
        self.alter(lambda d:d['inventory']['campaign_inventory'][0].update(pause=False,schedule=[{'weekDay':8,'startHour':0,'endHour':25}]))
        with self.assertRaisesRegex(QueryError,'INVALID_SNAPSHOT'):ReleaseQuery(self.release)

    def test_duplicate_json_keys_rejected(self):
        path=self.release/'report/index.html'
        path.write_text(path.read_text().replace('"reports":','"reports": {}, "reports":',1))
        with self.assertRaisesRegex(QueryError,'INVALID_SNAPSHOT'):ReleaseQuery(self.release)

    def test_unicode_entity_id_is_not_numeric_id(self):
        analytics=Analytics(ReleaseQuery(self.release))
        with self.assertRaisesRegex(QueryError,'INVALID_ENTITY_ID'):
            analytics.settings('tinacms','campaign','²')

    def test_bridge_config_rejects_missing_host_and_weak_credential(self):
        for url,token in [('https:///reader','s'*43),('https://bridge.test','short')]:
            with self.assertRaises(ValueError):Bridge(url,token)

class SummaryReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.query=ReleaseQuery(make_release(self.tmp.name))

    def test_impression_difference_preserves_account_primary(self):
        # Mirrors the real class of discrepancy, without publishing production data.
        self.query.data['tinacms']['reports']['account']=[{'date':'2026-11-14','cost':'100.00','click':20,'impression':1004}]
        self.query.data['tinacms']['reports']['keyword']=[{'date':'2026-11-14','cost':'100.00','click':20,'impression':1000,
            'campaignId':1,'adGroupId':2,'wInfoId':3,'wInfoNameStatus':'synthetic'}]
        result=self.query.summary('tinacms','2026-11-14','2026-11-14')
        self.assertEqual(result['source'],'baidu_ads.account')
        self.assertEqual(result['observed_totals']['impression'],'1004.000000')
        compare=result['reconciliation']
        self.assertEqual(compare['dashboard_overview_source'],'baidu_ads.keyword')
        self.assertEqual(compare['dashboard_observed_totals']['impression'],'1000.000000')
        self.assertEqual(compare['observed_difference_primary_minus_dashboard'],{'cost':'0.000000','click':'0.000000','impression':'4.000000'})
        self.assertTrue(compare['totals_differ']);self.assertTrue(compare['complete_comparison'])
        self.assertIn('ACCOUNT_AND_DASHBOARD_KEYWORD_TOTALS_DIFFER',result['warnings'])
        self.assertNotIn('reconciliation',self.query.performance('tinacms','2026-11-14','2026-11-14'))

    def test_missing_keyword_source_is_explicit(self):
        del self.query.data['tinacms']['reports']['keyword']
        result=self.query.summary('tinacms','2026-11-14','2026-11-14')
        self.assertFalse(result['reconciliation']['comparison_available'])
        self.assertIn('DASHBOARD_KEYWORD_COMPARISON_UNAVAILABLE',result['warnings'])

    def test_different_coverage_is_not_called_complete(self):
        result=self.query.summary('tinacms','2026-11-14','2026-11-16')
        self.assertFalse(result['reconciliation']['complete_comparison'])
        self.assertFalse(result['reconciliation']['coverage_comparable'])
