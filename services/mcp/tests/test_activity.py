import asyncio
import json
import re
import unittest
from starlette.testclient import TestClient
from baidu_ads_mcp.activity import ActivityLog
from baidu_ads_mcp.server import create_server
from baidu_ads_mcp.web import BoundaryMiddleware
from test_auth_web import Setup, BASE


class ActivityTests(Setup, unittest.TestCase):
    def setUp(self):
        self.setup()
        self.server = create_server(self.query, self.store, provider=self.provider)
        self.client = TestClient(BoundaryMiddleware(self.server.streamable_http_app(), BASE), base_url=BASE)
        self.client.__enter__(); self.addCleanup(self.client.__exit__, None, None, None)

    def login(self, subject):
        page = self.client.get('/login')
        csrf = re.search('name="csrf" value="([^"]+)"', page.text)[1]
        result = self.client.post('/login', data={'csrf':csrf,'subject':subject,'password':'test-password'},
                                  headers={'Origin':BASE}, follow_redirects=False)
        self.assertEqual(result.status_code, 303)

    def tool(self, name, params):
        token = asyncio.run(self.grant('alice'))
        response = self.client.post('/mcp', headers={'Authorization':'Bearer '+token.access_token,
            'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-11-25'},
            json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':{'params':params}}})
        self.assertEqual(response.status_code, 200)
        return response.json()['result']

    def test_http_calls_record_success_failure_without_secrets(self):
        params={'account':'tinacms','start':'2026-11-14','end':'2026-11-16'}
        self.assertFalse(self.tool('baidu_ads_get_summary',params)['isError'])
        self.assertTrue(self.tool('baidu_ads_get_summary',{**params,'password':'SENTINEL-SECRET'})['isError'])
        with self.store.db() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM activity ORDER BY seq')]
        self.assertEqual([r['status'] for r in rows], ['succeeded','failed'])
        self.assertEqual({r['subject'] for r in rows}, {'alice'})
        self.assertNotIn('SENTINEL',json.dumps(rows))
        self.assertNotIn('observed_totals',json.dumps(rows))

    def test_history_is_private_escaped_and_paginated(self):
        self.assertEqual(self.client.get('/activity',follow_redirects=False).status_code,303)
        log=self.server.activity
        for _ in range(52):log.finish(log.begin('alice','query'), 'succeeded', 2)
        log.finish(log.begin('bob','<script>other-user-action</script>'), 'failed', 1)
        self.login('alice')
        response=self.client.get('/activity')
        self.assertNotIn('other-user-action',response.text)
        self.assertIn('更早记录',response.text)
        self.assertIn('no-store',response.headers['cache-control'])
        self.login('kenshi')
        response=self.client.get('/activity')
        self.assertIn('&lt;script&gt;other-user-action',response.text)
        self.assertNotIn('<script>',response.text)
        self.bridge.versions['kenshi']='reset'
        self.assertEqual(self.client.get('/activity',follow_redirects=False).status_code,303)

    def test_retention_and_unfinished_call_survive_restart(self):
        log=ActivityLog(self.store,clock=lambda:0)
        log.begin('alice','old')
        log=ActivityLog(self.store,clock=lambda:91*86400)
        log.begin('alice','pending',{'params':{'account':{},'token':'SECRET'}})
        log=ActivityLog(self.store)
        actor=self.provider.principal({'subject':'alice','version':'v1'})
        rows=log.list(actor)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['status'],'started')
        self.assertEqual(rows[0]['context'],'{}')

    def test_web_mutation_records_result_without_csrf(self):
        self.login('alice')
        self.client.post('/logout',data={'csrf':'SENTINEL'},headers={'Origin':BASE})
        with self.store.db() as db:
            row=dict(db.execute("SELECT * FROM activity WHERE action='web:/logout'").fetchone())
        self.assertEqual(row['status'],'failed')
        self.assertEqual(row['subject'],'alice')
        self.assertNotIn('SENTINEL',json.dumps(row))

    def test_failed_login_does_not_record_unverified_username(self):
        page=self.client.get('/login')
        csrf=re.search('name="csrf" value="([^"]+)"',page.text)[1]
        self.client.post('/login',data={'csrf':csrf,'subject':'UNVERIFIED-SENTINEL','password':'SECRET'},headers={'Origin':BASE})
        with self.store.db() as db:
            row=dict(db.execute("SELECT * FROM activity WHERE action='web_login'").fetchone())
        self.assertEqual(row['subject'],'anonymous')
        self.assertEqual(row['status'],'failed')
        self.assertNotIn('SENTINEL',json.dumps(row))
