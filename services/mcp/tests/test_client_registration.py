import asyncio
import tempfile
import unittest
from pathlib import Path
from starlette.testclient import TestClient
from mcp.server.auth.provider import RegistrationError
from mcp.shared.auth import OAuthClientInformationFull
from baidu_ads_mcp.auth import OAuthProvider, SCOPES
from baidu_ads_mcp.client_registry import ClientRegistry
from baidu_ads_mcp.changes import ChangeStore
from baidu_ads_mcp.query import ReleaseQuery
from baidu_ads_mcp.server import create_server
from fixtures import make_release
from test_auth_web import FakeBridge, GRANTS


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.query=ReleaseQuery(make_release(self.tmp.name))
        self.store=ChangeStore(Path(self.tmp.name)/'control.sqlite3', self.query)
        self.bridge=FakeBridge(); self.bridge.service_token='a'*43
        self.provider=OAuthProvider(self.store,self.bridge,'https://mcp.example.com',[],GRANTS,dynamic_registration=True)

    def metadata(self, **extra):
        return dict(redirect_uris=['http://127.0.0.1:12345/callback'],grant_types=['authorization_code','refresh_token'],response_types=['code'],token_endpoint_auth_method='none',**extra)

    def test_http_registration_discovery_and_restart(self):
        client=TestClient(create_server(self.query,store=self.store,provider=self.provider).streamable_http_app(),base_url=self.provider.base)
        discovery=client.get('/.well-known/oauth-authorization-server').json()
        self.assertEqual(discovery['registration_endpoint'], self.provider.base+'/register')
        result=client.post('/register',json=self.metadata())
        self.assertEqual(result.status_code,201,result.text)
        item=result.json(); self.assertEqual(item['scope'],'analytics:read settings:read')
        restored=OAuthProvider(self.store,self.bridge,self.provider.base,[],GRANTS,dynamic_registration=True)
        self.assertEqual(asyncio.run(restored.get_client(item['client_id'])).model_dump(mode='json')['redirect_uris'],item['redirect_uris'])
        self.assertEqual(client.post('/mcp',json={}).status_code,401)

    def test_secret_encrypted_and_rotation_fails_closed(self):
        meta=self.metadata(); meta['token_endpoint_auth_method']='client_secret_post'
        info=OAuthClientInformationFull(client_id='confidential',client_secret='secret-value',scope='analytics:read',**meta)
        asyncio.run(self.provider.register_client(info))
        with self.store.db() as db:
            raw=db.execute('SELECT body FROM oauth_clients').fetchone()[0]
        self.assertNotIn(b'secret-value',raw)
        self.assertEqual(self.provider.client('confidential').client_secret,'secret-value')
        self.assertIsNone(ClientRegistry(self.store,'b'*43,SCOPES).get('confidential'))

    def test_bad_redirect_scope_and_overwrite_rejected(self):
        for uri in ['http://evil.example/callback','https://user:password@evil.example/callback','https://evil.example/callback#fragment']:
            meta=self.metadata();meta['redirect_uris']=[uri]
            info=OAuthClientInformationFull(client_id='bad',scope='analytics:read',**meta)
            with self.assertRaises(RegistrationError):asyncio.run(self.provider.register_client(info))
        info=OAuthClientInformationFull(client_id='one',scope='analytics:read',**self.metadata())
        asyncio.run(self.provider.register_client(info))
        with self.assertRaises(RegistrationError):asyncio.run(self.provider.register_client(info))
        info=info.model_copy(update={'client_id':'two','scope':'administrator'})
        with self.assertRaises(RegistrationError):asyncio.run(self.provider.register_client(info))

    def test_registration_rate_bound(self):
        for i in range(30):
            asyncio.run(self.provider.register_client(OAuthClientInformationFull(client_id=str(i),scope='analytics:read',**self.metadata())))
        with self.assertRaises(RegistrationError):
            asyncio.run(self.provider.register_client(OAuthClientInformationFull(client_id='overflow',scope='analytics:read',**self.metadata())))

    def test_dynamic_client_login_refresh_and_revocation(self):
        from urllib.parse import parse_qs, urlsplit
        from mcp.server.auth.provider import AuthorizationParams
        from test_auth_web import CHALLENGE
        async def flow():
            info=OAuthClientInformationFull(client_id='agent',scope='analytics:read',**self.metadata())
            await self.provider.register_client(info)
            params=AuthorizationParams(state='test-state',scopes=['analytics:read'],code_challenge=CHALLENGE,
                redirect_uri=str(info.redirect_uris[0]),redirect_uri_provided_explicitly=True,resource=self.provider.resource)
            url=await self.provider.authorize(info,params)
            pending=parse_qs(urlsplit(url).query)['request'][0]
            redirect=await self.provider.consent(pending,'alice','test-password')
            code=await self.provider.load_authorization_code(info,parse_qs(urlsplit(redirect).query)['code'][0])
            tokens=await self.provider.exchange_authorization_code(info,code)
            self.assertEqual((await self.provider.load_access_token(tokens.access_token)).subject,'alice')
            refresh=await self.provider.load_refresh_token(info,tokens.refresh_token)
            renewed=await self.provider.exchange_refresh_token(info,refresh,['analytics:read'])
            self.assertIsNone(await self.provider.load_access_token(tokens.access_token))
            self.assertIsNotNone(await self.provider.load_access_token(renewed.access_token))
            self.bridge.versions['alice']='new-password-version'
            self.assertIsNone(await self.provider.load_access_token(renewed.access_token))
        asyncio.run(flow())
