"""OAuth provider with hashed opaque tokens and an independent identity bridge."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import sqlite3
from contextlib import closing
import time
from urllib.parse import urlsplit

import httpx
from mcp.server.auth.provider import AccessToken, AuthorizationCode, RefreshToken, AuthorizeError, TokenError, construct_redirect_uri
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from .changes import Principal, canonical, digest
from .query import ACCOUNTS, QueryError

SCOPES = {'analytics:read','settings:read','changes:propose','changes:execute','changes:delete','jobs:refresh','policies:manage','policies:automate','exports:create','subscriptions:manage'}


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Bridge:
    def __init__(self,url,service_token,*,allow_loopback=False):
        parsed=urlsplit(url)
        if parsed.scheme!='https' and not (allow_loopback and parsed.scheme=='http' and parsed.hostname=='127.0.0.1'):
            raise ValueError('Identity bridge requires HTTPS or explicit loopback reader mode')
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Invalid bridge URL')
        if not isinstance(service_token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43,256}',service_token):
            raise ValueError('Invalid bridge service credential')
        self.url,self.service_token=url.rstrip('/'),service_token

    async def call(self,action,**payload):
        try:
            async with asyncio.timeout(15 if action=='report' else 5), httpx.AsyncClient(timeout=5,trust_env=False,follow_redirects=False) as client:
                async with client.stream('POST',self.url+'/'+action,json=payload,
                                         headers={'Authorization':'Bearer '+self.service_token}) as response:
                    if response.status_code!=200:
                        raise QueryError('UNAUTHORIZED')
                    chunks=bytearray()
                    limit=40*1024*1024 if action=='report' else 65536
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks)>limit:
                            raise QueryError('INVALID_BRIDGE_RESPONSE')
                    result=json.loads(chunks)
                    if not isinstance(result,dict):
                        raise QueryError('INVALID_BRIDGE_RESPONSE')
                    return result
        except QueryError:
            raise
        except (httpx.HTTPError,ValueError,TimeoutError):
            raise QueryError('IDENTITY_BRIDGE_UNAVAILABLE') from None

    async def authenticate(self,subject,password):
        identity=await self.call('authenticate',subject=subject,password=password)
        validate_identity(identity)
        return identity

    async def verify(self,identity):
        validate_identity(identity)
        current=await self.call('identity',**identity)
        validate_identity(current)
        if current != identity:
            raise QueryError('IDENTITY_CHANGED')
        return current


def validate_identity(identity):
    if not isinstance(identity,dict) or set(identity)!={'subject','version'}:
        raise QueryError('INVALID_BRIDGE_RESPONSE')
    if not isinstance(identity['subject'],str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,64}',identity['subject']):
        raise QueryError('INVALID_BRIDGE_RESPONSE')
    if not isinstance(identity['version'],str) or not 1<=len(identity['version'])<=128:
        raise QueryError('INVALID_BRIDGE_RESPONSE')


class UserAccessToken(AccessToken):
    subject: str
    identity_version: str
    role: str
    accounts: list[str]


class OAuthProvider:
    def __init__(self,store,bridge,base_url,clients,grants,clock=time.time,*,dynamic_registration=False):
        self.store,self.bridge,self.base,self.clock=store,bridge,base_url.rstrip('/'),clock
        self.resource=self.base+'/mcp'
        self.clients={c['client_id']:OAuthClientInformationFull(**c) for c in clients}
        if len(self.clients)!=len(clients):
            raise ValueError('Duplicate OAuth client')
        for client in self.clients.values():
            if not client.redirect_uris or not set((client.scope or '').split())<=SCOPES:
                raise ValueError('Invalid OAuth client configuration')
            for redirect in client.redirect_uris:
                parsed=urlsplit(str(redirect))
                if (parsed.fragment or parsed.username or parsed.password or not parsed.hostname
                    or not (parsed.scheme=='https' or (parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','::1','localhost')))):
                    raise ValueError('HTTPS or loopback redirect required')
        self.dynamic_registration=dynamic_registration
        self.registry=None
        if dynamic_registration:
            from .client_registry import ClientRegistry
            self.registry=ClientRegistry(store,bridge.service_token,SCOPES,clock)
        self.grants=grants
        for name,grant in grants.items():
            if grant['role'] not in ('viewer','operator','owner') or (grant['role']=='owner' and name!='kenshi'):
                raise ValueError('Invalid role assignment')
            if not set(grant['scopes'])<=SCOPES or not set(grant.get('accounts',ACCOUNTS))<=set(ACCOUNTS):
                raise ValueError('Invalid grant')
        with store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS tokens(hash TEXT PRIMARY KEY,kind TEXT,body TEXT,expires REAL,used INTEGER DEFAULT 0,family TEXT);
            CREATE INDEX IF NOT EXISTS token_family ON tokens(family);
            CREATE INDEX IF NOT EXISTS token_expiry ON tokens(expires);
            CREATE TABLE IF NOT EXISTS revoked(family TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS background_identities(subject TEXT PRIMARY KEY,body TEXT,family TEXT,expires REAL);
            CREATE TABLE IF NOT EXISTS identity_references(subject TEXT,token_hash TEXT PRIMARY KEY,expires REAL);
            ''')

    def put(self,db,kind,body,expires,family=None):
        db.execute('DELETE FROM tokens WHERE expires<=?',(self.clock(),))
        db.execute('DELETE FROM identity_references WHERE expires<=?',(self.clock(),))
        if db.execute('SELECT count(*) FROM tokens').fetchone()[0]>=50000:
            raise QueryError('TOKEN_STORE_CAPACITY')
        if kind in ('pending','browser') and db.execute('SELECT count(*) FROM tokens WHERE kind=?',(kind,)).fetchone()[0]>=1000:
            raise QueryError('RATE_LIMITED')
        value=secrets.token_urlsafe(32)
        db.execute('INSERT INTO tokens VALUES(?,?,?,?,0,?)',(token_hash(value),kind,canonical(body),expires,family))
        if kind in ('access','refresh','web'):
            validate_identity(body['identity'])
            db.execute('INSERT INTO identity_references VALUES(?,?,?)',(body['identity']['subject'],token_hash(value),expires))
        return value

    def load(self,db,value,kind,allow_used=False):
        if not isinstance(value,str) or not 20<=len(value)<=256:
            return None
        row=db.execute('SELECT * FROM tokens WHERE hash=? AND kind=?',(token_hash(value),kind)).fetchone()
        if not row or row['expires']<=self.clock() or (row['used'] and not allow_used):
            return None
        if row['family'] and db.execute('SELECT 1 FROM revoked WHERE family=?',(row['family'],)).fetchone():
            return None
        return {**dict(row),'data':json.loads(row['body'])}

    def principal(self,identity,scopes=None):
        validate_identity(identity)
        grant=self.grants.get(identity.get('subject'))
        if not grant:
            raise QueryError('UNAUTHORIZED')
        allowed=set(grant['scopes'])
        if grant['role']=='viewer':
            allowed &= {'analytics:read','settings:read','exports:create','subscriptions:manage'}
        requested=set(scopes) if scopes is not None else allowed
        if not requested<=allowed:
            raise QueryError('INSUFFICIENT_SCOPE')
        return Principal(identity['subject'],digest([identity['version'],grant]),grant['role'],frozenset(requested),frozenset(grant.get('accounts',ACCOUNTS)))

    async def checked(self,body):
        if 'client_id' in body:
            client=self.client(body['client_id'])
            params=body.get('params',{})
            if (client is None or params.get('resource')!=self.resource
                or params.get('redirect_uri') not in {str(uri) for uri in client.redirect_uris}
                or not set(body.get('scopes',[]))<=set((client.scope or '').split())):
                raise QueryError('CLIENT_AUTHORIZATION_CHANGED')
        await self.bridge.verify(body['identity'])
        actor=self.principal(body['identity'],body.get('scopes'))
        if actor.version!=body['version']:
            raise QueryError('IDENTITY_CHANGED')
        return actor

    def remember_background(self, token):
        """Persist only non-secret identity/grant evidence from an authenticated token."""
        with self.store.db() as db:
            row=self.load(db,token,'access')
            if row is None:
                raise QueryError('UNAUTHORIZED')
            body=row['data']
            actor=self.principal(body['identity'],body['scopes'])
            expiry=db.execute("SELECT max(expires) FROM tokens WHERE family=? AND used=0 AND kind IN ('access','refresh')",(row['family'],)).fetchone()[0]
            if expiry is None or expiry<=self.clock():
                raise QueryError('UNAUTHORIZED')
            evidence={key:body[key] for key in ('identity','version','scopes','client_id','params')}
            db.execute('INSERT OR REPLACE INTO background_identities VALUES(?,?,?,?)',
                       (actor.subject,canonical(evidence),row['family'],expiry))

    def background_evidence(self,subject):
        with self.store.db() as db:
            row=db.execute('SELECT * FROM background_identities WHERE subject=?',(subject,)).fetchone()
            if not row or row['expires']<=self.clock() or db.execute('SELECT 1 FROM revoked WHERE family=?',(row['family'],)).fetchone():
                raise QueryError('UNAUTHORIZED')
            active=db.execute("SELECT body FROM tokens WHERE family=? AND used=0 AND expires>? AND kind IN ('access','refresh')",(row['family'],self.clock())).fetchall()
            body=json.loads(row['body'])
            if not any(set(body['scopes'])<=set(json.loads(candidate['body'])['scopes']) for candidate in active):
                raise QueryError('UNAUTHORIZED')
            return body

    async def resolve_background(self,subject):
        evidence=self.background_evidence(subject)
        actor=await self.checked(evidence)
        latest=self.background_evidence(subject)
        if latest != evidence:
            raise QueryError('IDENTITY_CHANGED')
        return actor

    async def resolve_actor(self,subject):
        """Fresh identity for a server-side execution worker, without nested write locks."""
        def evidence():
            with closing(sqlite3.connect(self.store.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
                db.row_factory=sqlite3.Row
                rows=db.execute("""SELECT t.body,t.hash FROM identity_references r JOIN tokens t ON r.token_hash=t.hash
                    WHERE r.subject=? AND t.expires>? AND t.used=0
                    AND (t.family IS NULL OR NOT EXISTS(SELECT 1 FROM revoked v WHERE v.family=t.family))
                    ORDER BY t.expires DESC LIMIT 20""",(subject,self.clock())).fetchall()
                return [(r['hash'],json.loads(r['body'])) for r in rows]
        for identifier,body in evidence():
            try:
                actor=await self.checked(body)
            except QueryError:
                continue
            if any(key==identifier for key,_ in evidence()):
                return actor
        raise QueryError('UNAUTHORIZED')

    def client(self,client_id):
        return self.clients.get(client_id) or (self.registry.get(client_id) if self.registry else None)

    async def get_client(self,client_id):
        return self.client(client_id)

    async def register_client(self,client_info):
        from .client_registry import ClientRegistrationError
        if not self.registry or client_info.client_id in self.clients:
            raise ClientRegistrationError('invalid_client_metadata','Registration unavailable')
        self.registry.register(client_info)

    async def authorize(self,client,params):
        if self.client(client.client_id)!=client:
            raise AuthorizeError('unauthorized_client')
        if str(params.redirect_uri) not in {str(uri) for uri in client.redirect_uris}:
            raise AuthorizeError('invalid_request','Exact registered redirect required')
        if params.resource!=self.resource:
            raise AuthorizeError('invalid_target','Exact MCP resource required')
        allowed=set((client.scope or '').split())
        # OAuth scope is optional. Default only to registered read permissions,
        # never to all client capabilities; consent still checks the user grant.
        if params.scopes is None:
            params=params.model_copy(update={'scopes':[scope for scope in
                ('analytics:read','settings:read') if scope in allowed]})
        if not params.scopes or not set(params.scopes)<=allowed:
            raise AuthorizeError('invalid_scope','Registered permitted scopes required')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}',params.code_challenge):
            raise AuthorizeError('invalid_request','S256 PKCE required')
        body={'client_id':client.client_id,'params':params.model_dump(mode='json')}
        with self.store.db() as db:
            pending=self.put(db,'pending',body,self.clock()+300)
        return self.base+'/consent?request='+pending

    async def consent(self,pending,subject,password):
        identity=await self.bridge.authenticate(subject,password)
        with self.store.db() as db:
            row=self.load(db,pending,'pending')
            if not row:
                raise QueryError('AUTHORIZATION_EXPIRED')
            params=row['data']['params']
            actor=self.principal(identity,params['scopes'])
            body={'identity':identity,'version':actor.version,'client_id':row['data']['client_id'],
                  'scopes':params['scopes'],'params':params}
            code=self.put(db,'code',body,self.clock()+60)
            db.execute('UPDATE tokens SET used=1 WHERE hash=?',(row['hash'],))
        return construct_redirect_uri(params['redirect_uri'],code=code,state=params.get('state'))

    async def load_authorization_code(self,client,authorization_code):
        with self.store.db() as db:
            row=self.load(db,authorization_code,'code',allow_used=True)
            if row and row['used']:
                if row['family']:
                    db.execute('INSERT OR IGNORE INTO revoked VALUES(?)',(row['family'],))
                return None
        if not row or row['data']['client_id']!=client.client_id:
            return None
        data=row['data'];params=data['params']
        return AuthorizationCode(code=authorization_code,scopes=data['scopes'],expires_at=row['expires'],
            client_id=client.client_id,code_challenge=params['code_challenge'],redirect_uri=params['redirect_uri'],
            redirect_uri_provided_explicitly=params['redirect_uri_provided_explicitly'],resource=self.resource)

    def issue(self,db,body,family,refresh_expiry):
        access=self.put(db,'access',body,self.clock()+900,family)
        refresh=self.put(db,'refresh',body,refresh_expiry,family)
        if 'jobs:refresh' in body['scopes']:
            db.execute('INSERT OR REPLACE INTO background_identities VALUES(?,?,?,?)',
                       (body['identity']['subject'],canonical(body),family,refresh_expiry))
        return OAuthToken(access_token=access,refresh_token=refresh,token_type='Bearer',expires_in=900,scope=' '.join(body['scopes']))

    async def exchange_authorization_code(self,client,authorization_code):
        with self.store.db() as db:
            row=self.load(db,authorization_code.code,'code')
        if not row or row['data']['client_id']!=client.client_id:
            raise TokenError('invalid_grant')
        try:
            await self.checked(row['data'])
        except QueryError:
            raise TokenError('invalid_grant') from None
        with self.store.db() as db:
            current=self.load(db,authorization_code.code,'code')
            if not current:
                raise TokenError('invalid_grant')
            family=secrets.token_hex(16)
            db.execute('UPDATE tokens SET used=1,family=? WHERE hash=?',(family,current['hash']))
            return self.issue(db,current['data'],family,self.clock()+30*86400)

    async def load_refresh_token(self,client,refresh_token):
        with self.store.db() as db:
            row=self.load(db,refresh_token,'refresh',allow_used=True)
            if not row or row['data']['client_id']!=client.client_id:
                return None
            if row['used']:
                db.execute('INSERT OR IGNORE INTO revoked VALUES(?)',(row['family'],))
                return None
        return RefreshToken(token=refresh_token,client_id=client.client_id,scopes=row['data']['scopes'],expires_at=int(row['expires']))

    async def exchange_refresh_token(self,client,refresh_token,scopes):
        with self.store.db() as db:
            row=self.load(db,refresh_token.token,'refresh',allow_used=True)
            reused=bool(row and row['used'])
            if reused:
                db.execute('INSERT OR IGNORE INTO revoked VALUES(?)',(row['family'],))
        if reused:
            raise TokenError('invalid_grant')
        if not row or row['data']['client_id']!=client.client_id or not set(scopes)<=set(row['data']['scopes']):
            raise TokenError('invalid_grant')
        try:
            await self.checked(row['data'])
        except QueryError:
            raise TokenError('invalid_grant') from None
        with self.store.db() as db:
            current=self.load(db,refresh_token.token,'refresh',allow_used=True)
            if not current:
                raise TokenError('invalid_grant')
            if current['used']:
                db.execute('INSERT OR IGNORE INTO revoked VALUES(?)',(current['family'],))
                reused=True
            else:
                reused=False
                db.execute('UPDATE tokens SET used=1 WHERE family=? AND kind IN (\'access\',\'refresh\')',(current['family'],))
                body={**current['data'],'scopes':scopes}
                result=self.issue(db,body,current['family'],current['expires'])
        if reused:
            raise TokenError('invalid_grant')
        return result

    async def load_access_token(self,token):
        with self.store.db() as db:
            row=self.load(db,token,'access')
        if not row:
            return None
        try:
            actor=await self.checked(row['data'])
        except QueryError:
            return None
        with self.store.db() as db:
            if self.load(db,token,'access') is None:
                return None
        return UserAccessToken(token=token,client_id=row['data']['client_id'],scopes=list(actor.scopes),
            expires_at=int(row['expires']),resource=self.resource,subject=actor.subject,
            identity_version=actor.version,role=actor.role,accounts=list(actor.accounts))

    async def revoke_token(self,token):
        with self.store.db() as db:
            row=db.execute('SELECT family FROM tokens WHERE hash=?',(token_hash(token.token),)).fetchone()
            if row and row['family']:
                db.execute('INSERT OR IGNORE INTO revoked VALUES(?)',(row['family'],))

    async def login_web(self,subject,password):
        identity=await self.bridge.authenticate(subject,password)
        actor=self.principal(identity)
        body={'identity':identity,'version':actor.version,'scopes':list(actor.scopes),'csrf':secrets.token_urlsafe(32)}
        with self.store.db() as db:
            session=self.put(db,'web',body,self.clock()+3600)
        return session

    async def web_session(self,token):
        with self.store.db() as db:
            row=self.load(db,token,'web')
        if not row:
            raise QueryError('UNAUTHORIZED')
        actor=await self.checked(row['data'])
        with self.store.db() as db:
            if self.load(db,token,'web') is None:
                raise QueryError('UNAUTHORIZED')
        return actor,row['data']['csrf']
