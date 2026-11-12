import base64
import hashlib
import json
from pathlib import Path
import re
import secrets
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import login_dashboard as login
from scripts.build_login_entry import build


class DashboardLoginTests(unittest.TestCase):
    def setUp(self):
        self.keys = {slug: secrets.token_bytes(32) for slug in login.SNAPSHOTS}
        self.client = login.create_app('test-client', 'test-secret', secrets.token_bytes(32),
                                       self.keys).test_client()

    def get(self, path, **kwargs):
        return self.client.get(path, base_url=login.BASE_URL, **kwargs)

    def start_login(self, path='/ssw/en.html'):
        response = self.get('/login', query_string={'page': path})
        url = urlsplit(response.location)
        self.assertEqual(url.netloc, 'github.com')
        self.assertIn('Secure', response.headers['Set-Cookie'])
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertIn('SameSite=Lax', response.headers['Set-Cookie'])
        self.assertNotIn('Domain=', response.headers['Set-Cookie'])
        self.assertIn('__Host-baidu_ads_session=', response.headers['Set-Cookie'])
        query = parse_qs(url.query)
        self.assertEqual(query['redirect_uri'], [login.BASE_URL + '/callback'])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        with self.client.session_transaction(base_url=login.BASE_URL) as session:
            digest = hashlib.sha256(session['verifier'].encode()).digest()
        self.assertEqual(query['code_challenge'], [base64.urlsafe_b64encode(digest).rstrip(b'=').decode()])
        return query['state'][0]

    def finish_login(self, state, identity=login.OWNER_ID):
        with patch.object(login, 'github_identity', return_value={'id': identity}):
            return self.get('/callback', query_string={'code': 'example', 'state': state})

    def test_protected_routes_do_not_read_any_data_before_sign_in(self):
        with patch.object(login, 'source_bytes') as source:
            for path in login.REPORT_PAGES:
                response = self.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'/login?page=', response.data)
                self.assertNotIn(b'report-data', response.data)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
            source.assert_not_called()
        self.assertEqual(self.get('/ssw').location, '/ssw/')
        self.assertEqual(self.get('/login?page=//example.com').status_code, 400)
        self.assertEqual(self.get('/data/posthog-cli.enc').status_code, 404)
        self.assertEqual(self.client.get('/', base_url='https://other.example').status_code, 400)

    def test_callback_rejects_invalid_or_expired_state_before_token_exchange(self):
        with patch.object(login, 'github_identity') as exchange:
            state = self.start_login()
            self.assertEqual(self.get('/callback?code=example&state=wrong').status_code, 400)
            self.assertEqual(self.get('/callback', query_string={'code': 'example', 'state': state}).status_code, 400)
            state = self.start_login()
            with self.client.session_transaction(base_url=login.BASE_URL) as session:
                session['issued'] = int(time.time()) - 601
            self.assertEqual(self.get('/callback', query_string={'code': 'example', 'state': state}).status_code, 400)
            exchange.assert_not_called()

    def test_other_github_account_cannot_view_reports(self):
        response = self.finish_login(self.start_login(), login.OWNER_ID + 1)
        self.assertEqual(response.status_code, 403)
        self.assertIn(b'/login?page=', self.get('/ssw/').data)

    def test_owner_session_renders_reports_and_private_aggregates_until_logout(self):
        response = self.finish_login(self.start_login())
        self.assertEqual(response.location, '/ssw/en.html')
        self.assertEqual(response.status_code, 302)
        report = b'<script id="report-data" type="application/json">{"rows":[]}</script>'
        sources = {path: report for path in ['report/index.html', 'report/yakshaver/index.html', 'report/ssw/index.html']}
        snapshots = {}
        for slug, (path, header, field, fields) in login.SNAPSHOTS.items():
            day = '2026-11-11'
            snapshot = {'through': day, 'rows': [{'date': day, **dict.fromkeys(fields, 123)}]}
            nonce = secrets.token_bytes(12)
            sources[path] = header + nonce + AESGCM(self.keys[slug]).encrypt(nonce, json.dumps(snapshot).encode(), header)
            snapshots[slug] = {field: snapshot}
        with patch.object(login, 'source_bytes', side_effect=lambda path, *_: sources[path]):
            for path, (slug, _) in login.REPORT_PAGES.items():
                response = self.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                nonce = re.search(rb'<script nonce="([^"]+)"', response.data)[1].decode()
                self.assertIn(f"'nonce-{nonce}'", response.headers['Content-Security-Policy'])
                match = re.search(rb'id="report-data" type="application/json">(.*?)</script>', response.data)
                self.assertEqual(json.loads(match[1]), {'rows': [], **snapshots.get(slug, {})})
            csrf = re.search(rb'name="csrf" value="([^"]+)"', response.data)[1].decode()
        self.assertEqual(self.client.post('/logout', base_url=login.BASE_URL).status_code, 403)
        self.assertEqual(self.client.post('/logout', data={'csrf': csrf}, base_url=login.BASE_URL).status_code, 303)
        self.assertIn(b'/login?page=', self.get('/ssw/').data)

    def test_pages_entry_contains_only_links_to_authenticated_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            build(output)
            for route in login.REPORT_PAGES:
                path = route.lstrip('/') + ('index.html' if route.endswith('/') else '')
                body = (output / path).read_text(encoding='utf-8')
                self.assertIn(login.BASE_URL + route, body)
                self.assertNotIn('report-data', body)
                self.assertNotIn('snapshot', body)


if __name__ == '__main__':
    unittest.main()
