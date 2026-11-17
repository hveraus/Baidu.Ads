"""Real HTTP MCP calls -> persisted worker -> authenticated browser download."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import re
import time
import unittest

from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.testclient import TestClient
from baidu_ads_mcp.auth import OAuthProvider
from baidu_ads_mcp.server import create_server
from baidu_ads_mcp.query import QueryError
from baidu_ads_mcp.web import BoundaryMiddleware
from test_auth_web import Setup, BASE, CLIENTS, GRANTS


class OperationsHTTPTests(Setup, unittest.TestCase):
    def setUp(self):
        self.setup()
        clients=copy.deepcopy(CLIENTS)
        clients[0]['scope'] += ' exports:create subscriptions:manage jobs:refresh'
        grants=copy.deepcopy(GRANTS)
        for grant in grants.values():
            grant['scopes'] += ['exports:create','subscriptions:manage']
        grants['alice']['scopes'] += ['jobs:refresh']
        self.provider=OAuthProvider(self.store,self.bridge,BASE,clients,grants)
        original=self.bridge.call
        async def call(action,**payload):
            if action=='current':return {'release_id':self.query.release_id}
            if action=='report':
                relative='report/index.html' if payload['account']=='tinacms' else 'report/'+payload['account']+'/index.html'
                raw=(Path(self.tmp.name)/self.query.release_id/relative).read_bytes()
                return {'release_id':self.query.release_id,'html':raw.decode(),'sha256':hashlib.sha256(raw).hexdigest()}
            return await original(action,**payload)
        self.bridge.call=call
        self.server=create_server(self.query,self.store,provider=self.provider,cache=Path(self.tmp.name)/'cache')
        app=self.server.streamable_http_app()
        app=TrustedHostMiddleware(BoundaryMiddleware(app,BASE),allowed_hosts=['127.0.0.1'])
        self.client=TestClient(app,base_url=BASE)
        self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
        self.tokens=asyncio.run(self.grant('alice',['analytics:read','exports:create','subscriptions:manage','jobs:refresh']))

    def tool(self,name,params):
        response=self.client.post('/mcp',headers={'Authorization':'Bearer '+self.tokens.access_token,
            'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-11-25'},
            json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':{'params':params}}})
        self.assertEqual(response.status_code,200,response.text)
        result=response.json()['result']
        self.assertFalse(result.get('isError'),str(result))
        return result['structuredContent']

    def completed(self,job_id):
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            job=self.tool('baidu_ads_get_job',{'job_id':job_id})
            if job['state'] in ('completed','failed','cancelled'):return job
            time.sleep(.05)
        self.fail('Background job did not finish')

    def login(self,subject='alice'):
        response=self.client.get('/login')
        csrf=re.search('name="csrf" value="([^"]+)"',response.text)[1]
        response=self.client.post('/login',headers={'Origin':BASE},data={'csrf':csrf,'subject':subject,'password':'test-password'},follow_redirects=False)
        self.assertEqual(response.status_code,303)

    def test_mcp_export_background_job_and_browser_download(self):
        queued=self.tool('baidu_ads_create_export',{'account':'tinacms','start':'2026-11-14','end':'2026-11-16','idempotency_key':'http-export-0123456789'})
        job=self.completed(queued['job_id'])
        self.assertEqual(job['state'],'completed',str(job))
        path=job['result']['download_path']
        self.assertEqual(self.client.get(path).status_code,403)
        self.login('bob')
        self.assertEqual(self.client.get(path).status_code,403)
        self.login()
        response=self.client.get(path)
        self.assertEqual(response.status_code,200)
        self.assertIn('attachment;',response.headers['content-disposition'])
        self.assertIn('no-store',response.headers['cache-control'])
        self.assertEqual(response.json()['observed_totals']['cost'],'0.300000')
        self.bridge.versions['alice']='reset'
        self.assertEqual(self.client.get(path).status_code,403)

    def test_mcp_refresh_is_queued_and_completed_without_core_collection(self):
        first=self.tool('baidu_ads_request_refresh',{'idempotency_key':'http-refresh-0123456789'})
        again=self.tool('baidu_ads_request_refresh',{'idempotency_key':'http-refresh-0123456789'})
        self.assertEqual(first['job_id'],again['job_id'])
        result=self.completed(first['job_id'])
        self.assertEqual(result['state'],'completed',str(result))
        self.assertEqual(result['result']['release_id'],self.query.release_id)

    def test_restart_with_expired_cache_still_authenticates_and_refreshes(self):
        from datetime import datetime,timezone
        from baidu_ads_mcp.cache_reader import load_runtime_query
        from baidu_ads_mcp.sync import import_release
        cache=Path(self.tmp.name)/'cache'
        asyncio.run(import_release(self.bridge,{'subject':'alice','version':'v1'},cache))
        pointer=cache/'current.json';manifest=json.loads(pointer.read_text())
        expired_at=time.time()-7200
        manifest['fetched_at']=datetime.fromtimestamp(expired_at,timezone.utc).isoformat()
        pointer.write_text(json.dumps(manifest))
        query=load_runtime_query({'cache_directory':str(cache)},allow_expired=True)
        self.assertAlmostEqual(query.loaded_at,expired_at,places=5)
        # Stop the old HTTP runtime. The restarted service starts from precisely
        # the expired pointer, with no background runtime available to rescue it.
        self.client.__exit__(None,None,None)
        self.store.query=query
        self.server=create_server(query,self.store,provider=self.provider,cache=cache)
        app=TrustedHostMiddleware(BoundaryMiddleware(self.server.streamable_http_app(),BASE),allowed_hosts=['127.0.0.1'])
        self.client=TestClient(app,base_url=BASE)
        self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
        response=self.client.post('/mcp',headers={'Authorization':'Bearer '+self.tokens.access_token,
            'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-11-25'},
            json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'baidu_ads_get_summary','arguments':{'params':{'account':'tinacms','start':'2026-11-14','end':'2026-11-16'}}}})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()['result']['isError'])
        self.assertIn('SNAPSHOT_EXPIRED',response.text)
        self.login()
        queued=self.tool('baidu_ads_request_refresh',{'idempotency_key':'expired-restart-refresh-key'})
        result=self.completed(queued['job_id'])
        self.assertEqual(result['state'],'completed',str(result))
        summary=self.tool('baidu_ads_get_summary',{'account':'tinacms','start':'2026-11-14','end':'2026-11-16'})
        self.assertEqual(summary['observed_totals']['cost'],'0.300000')
        self.assertGreater(self.store.query.loaded_at,expired_at)

    def test_subscription_runs_notice_and_web_pause(self):
        value=self.tool('baidu_ads_create_subscription',{'kind':'daily_summary','account':'tinacms','at_beijing':'09:15'})
        with self.store.db() as db:
            db.execute('UPDATE subscriptions SET next_due=? WHERE id=?',(self.store.clock()-1,value['subscription_id']))
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            notices=self.tool('baidu_ads_get_notifications',{'limit':50})['items']
            if notices:break
            time.sleep(.05)
        self.assertTrue(notices)
        self.login()
        response=self.client.get('/notifications')
        self.assertEqual(response.status_code,200)
        response=self.client.get('/subscriptions')
        csrf=re.search('name="csrf" value="([^"]+)"',response.text)[1]
        rejected=self.client.post('/subscriptions',headers={'Origin':BASE},data={'csrf':'bad','subscription_id':value['subscription_id']})
        self.assertEqual(rejected.status_code,403)
        paused=self.client.post('/subscriptions',headers={'Origin':BASE},data={'csrf':csrf,'subscription_id':value['subscription_id']},follow_redirects=False)
        self.assertEqual(paused.status_code,303)
        self.assertTrue(self.tool('baidu_ads_get_subscription',{'subscription_id':value['subscription_id']})['paused'])

    def test_background_reference_revocation_and_no_raw_token(self):
        self.provider.remember_background(self.tokens.access_token)
        actor=asyncio.run(self.provider.resolve_background('alice'))
        self.assertEqual(actor.subject,'alice')
        raw=self.store.path.read_bytes()
        self.assertNotIn(self.tokens.access_token.encode(),raw)
        self.assertNotIn(self.tokens.refresh_token.encode(),raw)
        token=asyncio.run(self.provider.load_access_token(self.tokens.access_token))
        asyncio.run(self.provider.revoke_token(token))
        with self.assertRaises(QueryError):asyncio.run(self.provider.resolve_background('alice'))
        with self.assertRaises(QueryError):asyncio.run(self.provider.resolve_actor('alice'))

    def test_refresh_scope_narrowing_stops_prior_background_grant(self):
        self.provider.remember_background(self.tokens.access_token)
        client=asyncio.run(self.provider.get_client('test-client'))
        refresh=asyncio.run(self.provider.load_refresh_token(client,self.tokens.refresh_token))
        asyncio.run(self.provider.exchange_refresh_token(client,refresh,['analytics:read']))
        with self.assertRaises(QueryError):asyncio.run(self.provider.resolve_background('alice'))

    def test_grants_change_invalidates_background_reference(self):
        self.provider.remember_background(self.tokens.access_token)
        self.provider.grants['alice']['accounts']=['ssw']
        with self.assertRaises(QueryError):asyncio.run(self.provider.resolve_background('alice'))
