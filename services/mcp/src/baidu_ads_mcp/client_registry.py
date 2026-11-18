"""Bounded OAuth client metadata; registration never grants user access."""
import base64
import hashlib
import time
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from mcp.server.auth.provider import RegistrationError
from mcp.shared.auth import OAuthClientInformationFull


class ClientRegistrationError(RegistrationError):
    # SDK 1.26 freezes this exception; Python 3.14/contextlib must set traceback.
    __setattr__ = object.__setattr__


class ClientRegistry:
    def __init__(self, store, key, scopes, clock=time.time):
        if not isinstance(key, str) or len(key) < 43:
            raise ValueError('Registration encryption key required')
        self.store, self.scopes, self.clock = store, scopes, clock
        self.cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(
            b'baidu-mcp-client-registry-v1:' + key.encode()).digest()))
        with store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS oauth_clients(id TEXT PRIMARY KEY, body BLOB NOT NULL, created REAL NOT NULL)')

    def get(self, identifier):
        with self.store.db() as db:
            row = db.execute('SELECT body FROM oauth_clients WHERE id=?', (identifier,)).fetchone()
        if not row:
            return None
        try:
            return OAuthClientInformationFull.model_validate_json(self.cipher.decrypt(row['body']))
        except (InvalidToken, ValueError):
            return None

    def register(self, client):
        body = client.model_dump_json().encode()
        valid = (client.client_id and len(client.client_id) <= 128 and len(body) <= 8192
                 and client.redirect_uris and len(client.redirect_uris) <= 10
                 and set((client.scope or '').split()) <= self.scopes
                 and set(client.grant_types) == {'authorization_code', 'refresh_token'}
                 and client.response_types == ['code']
                 and client.token_endpoint_auth_method in ('none', 'client_secret_post', 'client_secret_basic'))
        if not valid:
            raise ClientRegistrationError('invalid_client_metadata', 'Unsupported or oversized client metadata')
        for uri in client.redirect_uris:
            parsed = urlsplit(str(uri))
            if (len(str(uri)) > 2048 or parsed.fragment or parsed.username or parsed.password or not parsed.hostname
                or not (parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', '::1', 'localhost')))):
                raise ClientRegistrationError('invalid_redirect_uri', 'HTTPS or loopback redirect required')
        with self.store.db() as db:
            count = db.execute('SELECT count(*) FROM oauth_clients').fetchone()[0]
            recent = db.execute('SELECT count(*) FROM oauth_clients WHERE created>?', (self.clock()-60,)).fetchone()[0]
            if count >= 1000 or recent >= 30:
                raise ClientRegistrationError('invalid_client_metadata', 'Client registration capacity reached')
            if db.execute('SELECT 1 FROM oauth_clients WHERE id=?', (client.client_id,)).fetchone():
                raise ClientRegistrationError('invalid_client_metadata', 'Client already registered')
            db.execute('INSERT INTO oauth_clients VALUES(?,?,?)', (client.client_id, self.cipher.encrypt(body), self.clock()))
