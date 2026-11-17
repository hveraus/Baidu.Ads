import asyncio
import base64
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import parse_qs,urlsplit

from mcp.server.auth.provider import AuthorizationParams,TokenError
from starlette.testclient import TestClient
from starlette.middleware.trustedhost import TrustedHostMiddleware
from baidu_ads_mcp.auth import OAuthProvider
from baidu_ads_mcp.changes import ChangeStore
from baidu_ads_mcp.query import ReleaseQuery,QueryError
from baidu_ads_mcp.server import create_server
from baidu_ads_mcp.web import BoundaryMiddleware
from fixtures import make_release
from test_analytics_changes import enrich,BUDGET,OPERATOR

BASE='http://127.0.0.1:8797'
VERIFIER='v'*64
CHALLENGE=base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip('=')
CLIENTS=[{'client_id':'test-client','client_name':'Test Client','redirect_uris':['http://127.0.0.1:9999/callback'],
          'grant_types':['authorization_code','refresh_token'],'response_types':['code'],
          'token_endpoint_auth_method':'none','scope':'analytics:read settings:read changes:propose changes:execute'}]
GRANTS={name:{'role':role,'scopes':scopes} for name,role,scopes in [
    ('kenshi','owner',['analytics:read','settings:read','changes:propose','changes:execute']),
    ('alice','operator',['analytics:read','settings:read','changes:propose','changes:execute']),
    ('bob','viewer',['analytics:read','settings:read'])]}


class FakeBridge:
    def __init__(self):self.versions={'kenshi':'v1','alice':'v1','bob':'v1'};self.down=False
    async def authenticate(self,subject,password):
        if self.down or subject not in self.versions or password!='test-password':raise QueryError('UNAUTHORIZED')
        return {'subject':subject,'version':self.versions[subject]}
    async def verify(self,identity):
        if self.down or self.versions.get(identity['subject'])!=identity['version']:raise QueryError('IDENTITY_CHANGED')
        return identity
    async def call(self,action,**payload):
        if action=='private':
            await self.verify({k:payload[k] for k in ('subject','version')})
            return {'release_id':payload['release_id'],'source':payload['source'],'through':'2026-11-14','rows':[{'date':'2026-11-14','started':2,'finished':1,'customers':0,'messages':3}]}
        raise QueryError('UNSUPPORTED_OPERATION')


class Setup:
    def setup(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        release=make_release(self.tmp.name);enrich(release)
        self.query=ReleaseQuery(release)
        self.store=ChangeStore(Path(self.tmp.name)/'mcp.sqlite3',self.query)
        self.bridge=FakeBridge()
        self.provider=OAuthProvider(self.store,self.bridge,BASE,CLIENTS,GRANTS)

    async def grant(self,subject='alice',scopes=None):
        provider=self.provider
        client=await provider.get_client('test-client')
        params=AuthorizationParams(state='state-test',scopes=scopes or ['analytics:read'],code_challenge=CHALLENGE,
             redirect_uri=CLIENTS[0]['redirect_uris'][0],redirect_uri_provided_explicitly=True,resource=BASE+'/mcp')
        url=await provider.authorize(client,params)
        pending=parse_qs(urlsplit(url).query)['request'][0]
        redirect=await provider.consent(pending,subject,'test-password')
        code=parse_qs(urlsplit(redirect).query)['code'][0]
        model=await provider.load_authorization_code(client,code)
        return await provider.exchange_authorization_code(client,model)


class AuthTests(Setup,unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.setup()

    async def test_access_explicit_identity_scopes_and_reset(self):
        tokens=await self.grant()
        access=await self.provider.load_access_token(tokens.access_token)
        self.assertEqual(access.subject,'alice')
        self.assertEqual(access.scopes,['analytics:read'])
        self.bridge.versions['alice']='v2'
        self.assertIsNone(await self.provider.load_access_token(tokens.access_token))

    async def test_bridge_failure_closes_access(self):
        tokens=await self.grant();self.bridge.down=True
        self.assertIsNone(await self.provider.load_access_token(tokens.access_token))

    async def test_refresh_rotation_reuse_revokes_family(self):
        tokens=await self.grant()
        client=await self.provider.get_client('test-client')
        refresh=await self.provider.load_refresh_token(client,tokens.refresh_token)
        second=await self.provider.exchange_refresh_token(client,refresh,['analytics:read'])
        self.assertIsNone(await self.provider.load_access_token(tokens.access_token))
        self.assertIsNotNone(await self.provider.load_access_token(second.access_token))
        self.assertIsNone(await self.provider.load_refresh_token(client,tokens.refresh_token))
        self.assertIsNone(await self.provider.load_access_token(second.access_token))

    async def test_scope_escalation_rejected(self):
        with self.assertRaisesRegex(QueryError,'INSUFFICIENT_SCOPE'):
            await self.grant('bob',['changes:execute'])
        tokens=await self.grant();client=await self.provider.get_client('test-client')
        refresh=await self.provider.load_refresh_token(client,tokens.refresh_token)
        with self.assertRaises(TokenError):await self.provider.exchange_refresh_token(client,refresh,['changes:execute'])

    async def test_revoke_and_no_plain_tokens_in_database(self):
        tokens=await self.grant();access=await self.provider.load_access_token(tokens.access_token)
        raw=self.store.path.read_bytes()
        self.assertNotIn(tokens.access_token.encode(),raw);self.assertNotIn(tokens.refresh_token.encode(),raw)
        await self.provider.revoke_token(access)
        self.assertIsNone(await self.provider.load_access_token(tokens.access_token))

    async def test_exact_redirect_resource_pkce(self):
        client=await self.provider.get_client('test-client')
        base=dict(state='s',scopes=['analytics:read'],code_challenge=CHALLENGE,redirect_uri=CLIENTS[0]['redirect_uris'][0],redirect_uri_provided_explicitly=True,resource=BASE+'/mcp')
        for changes in ({'redirect_uri':'http://127.0.0.1:9998/callback'},{'resource':'https://wrong.test/mcp'},{'code_challenge':'bad'}):
            with self.assertRaises(Exception):await self.provider.authorize(client,AuthorizationParams(**{**base,**changes}))

    async def test_authorization_code_single_use(self):
        client=await self.provider.get_client('test-client')
        params=AuthorizationParams(state='s',scopes=['analytics:read'],code_challenge=CHALLENGE,redirect_uri=CLIENTS[0]['redirect_uris'][0],redirect_uri_provided_explicitly=True,resource=BASE+'/mcp')
        url=await self.provider.authorize(client,params)
        pending=parse_qs(urlsplit(url).query)['request'][0]
        redirect=await self.provider.consent(pending,'alice','test-password')
        code=parse_qs(urlsplit(redirect).query)['code'][0]
        model=await self.provider.load_authorization_code(client,code)
        await self.provider.exchange_authorization_code(client,model)
        with self.assertRaises(TokenError):await self.provider.exchange_authorization_code(client,model)
        with self.assertRaises(QueryError):await self.provider.consent(pending,'alice','test-password')


class WebTests(Setup,unittest.TestCase):
    def setUp(self):
        self.setup()
        server=create_server(self.query,self.store,provider=self.provider)
        app=server.streamable_http_app()
        app=TrustedHostMiddleware(BoundaryMiddleware(app,BASE),allowed_hosts=['127.0.0.1'])
        self.client=TestClient(app,base_url=BASE)
        self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)

    def login(self,name='kenshi'):
        response=self.client.get('/login')
        csrf=re.search('name="csrf" value="([^"]+)"',response.text)[1]
        response=self.client.post('/login',data={'csrf':csrf,'subject':name,'password':'test-password'},headers={'Origin':BASE},follow_redirects=False)
        self.assertEqual(response.status_code,303)

    def test_web_approval_csrf_xss_and_stale_version(self):
        actor=self.provider.principal({'subject':'alice','version':'v1'})
        plan=self.store.create(actor,'tinacms',[BUDGET],'<script>alert(1)</script>')
        self.store.request_approval(actor,plan['id'],1)
        url='/plans/'+plan['id'];self.login()
        response=self.client.get(url)
        self.assertNotIn('<script>',response.text);self.assertIn('&lt;script&gt;',response.text)
        csrf=re.search('name="csrf" value="([^"]+)"',response.text)[1]
        data={'csrf':csrf,'action':'approve','version':'1','hash':plan['hash']}
        self.assertEqual(self.client.post(url,data={**data,'csrf':'bad'},headers={'Origin':BASE}).status_code,403)
        self.assertEqual(self.client.post(url,data=data,headers={'Origin':'https://evil.test'}).status_code,403)
        self.assertEqual(self.client.post(url,data=data,headers={'Origin':BASE},follow_redirects=False).status_code,303)
        self.assertEqual(self.store.get(actor,plan['id'])['state'],'approved')
        self.assertEqual(self.client.post(url,data=data,headers={'Origin':BASE}).status_code,403)

    def test_mcp_proposal_to_human_approval_execution_disabled(self):
        token=asyncio.run(self.grant('alice',['analytics:read','changes:propose','changes:execute']))
        headers={'Authorization':'Bearer '+token.access_token,'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-11-25'}
        def tool(name,arguments):
            response=self.client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':{'params':arguments}}},headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            return response.json()['result']
        result=tool('baidu_ads_create_change_plan',{'account':'tinacms','operations':[BUDGET],'reason':'调预算方案'})
        self.assertFalse(result['isError'])
        plan=result['structuredContent']
        pending=tool('baidu_ads_request_change_approval',{'plan_id':plan['id'],'version':1})
        self.assertEqual(pending['structuredContent']['approval_url'],BASE+'/plans/'+plan['id'])
        self.login()
        page=self.client.get('/plans/'+plan['id'])
        csrf=re.search('name="csrf" value="([^"]+)"',page.text)[1]
        response=self.client.post('/plans/'+plan['id'],data={'csrf':csrf,'action':'approve','version':'1','hash':plan['hash']},headers={'Origin':BASE},follow_redirects=False)
        self.assertEqual(response.status_code,303)
        blocked=tool('baidu_ads_execute_change_plan',{'plan_id':plan['id'],'version':1,'idempotency_key':'synthetic-idempotency'})
        self.assertTrue(blocked['isError'])
        self.assertIn('MUTATIONS_DISABLED',blocked['content'][0]['text'])

    def test_anonymous_wrong_host_and_body_limit(self):
        self.assertEqual(self.client.get('/plans',follow_redirects=False).status_code,303)
        self.assertEqual(self.client.get('/plans',headers={'Host':'evil.test'}).status_code,400)
        self.assertEqual(self.client.post('/login',content=b'a'*70000,headers={'Origin':BASE}).status_code,413)
        self.assertEqual(self.client.post('/mcp',json={}).status_code,401)

    def test_oauth_pkce_http_and_mcp_call(self):
        query={'client_id':'test-client','redirect_uri':CLIENTS[0]['redirect_uris'][0],
               'response_type':'code','state':'saved-state','scope':'analytics:read',
               'code_challenge':CHALLENGE,'code_challenge_method':'S256','resource':BASE+'/mcp'}
        response=self.client.get('/authorize',params=query,follow_redirects=False)
        self.assertIn(response.status_code,(302,303,307))
        consent=response.headers['location']
        page=self.client.get(consent)
        csrf=re.search('name="csrf" value="([^"]+)"',page.text)[1]
        response=self.client.post(consent,data={'csrf':csrf,'subject':'alice','password':'test-password'},headers={'Origin':BASE},follow_redirects=False)
        self.assertEqual(response.status_code,303)
        params=parse_qs(urlsplit(response.headers['location']).query)
        self.assertEqual(params['state'],['saved-state'])
        token_data={'grant_type':'authorization_code','client_id':'test-client','code':params['code'][0],
                    'redirect_uri':CLIENTS[0]['redirect_uris'][0],'code_verifier':VERIFIER,'resource':BASE+'/mcp'}
        wrong_target=self.client.post('/token',data={**token_data,'resource':'https://evil.test/mcp'})
        self.assertEqual(wrong_target.status_code,400)
        bad=self.client.post('/token',data={**token_data,'code_verifier':'wrong'*13})
        self.assertEqual(bad.status_code,400)
        response=self.client.post('/token',data=token_data)
        self.assertEqual(response.status_code,200,response.text)
        token=response.json()['access_token']
        headers={'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-11-25'}
        response=self.client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'baidu_ads_get_cli_metrics','arguments':{'params':{'account':'tinacms','start':'2026-11-14','end':'2026-11-14'}}}},headers=headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['result']['structuredContent']['totals']['started'],2)
        response=self.client.post('/mcp',json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'baidu_ads_create_change_plan','arguments':{'params':{'account':'tinacms','operations':[BUDGET],'reason':'proposal'}}}},headers=headers)
        self.assertTrue(response.json()['result']['isError'])
        self.bridge.versions['alice']='reset'
        self.assertEqual(self.client.post('/mcp',json={'jsonrpc':'2.0','id':3,'method':'tools/list'},headers=headers).status_code,401)
        metadata=self.client.get('/.well-known/oauth-authorization-server').json()
        self.assertEqual(metadata['issuer'],BASE+'/')
