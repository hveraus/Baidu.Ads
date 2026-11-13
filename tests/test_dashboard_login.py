import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import login_dashboard as login
from scripts.build_login_entry import build


class DashboardLoginTests(unittest.TestCase):
    password = 'test-password-with-length'

    @classmethod
    def setUpClass(cls):
        cls.password_hash = login.hash_password(cls.password.encode())

    def setUp(self):
        self.keys = {slug: secrets.token_bytes(32) for slug in login.SNAPSHOTS}
        self.client = login.create_app('ken', self.password_hash, secrets.token_bytes(32),
                                       self.keys).test_client()

    def get(self, path, **kwargs):
        return self.client.get(path, base_url=login.BASE_URL, **kwargs)

    def start_login(self, path='/ssw/en.html'):
        response = self.get(path)
        self.assertIn('Secure', response.headers['Set-Cookie'])
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertIn('SameSite=Lax', response.headers['Set-Cookie'])
        self.assertNotIn('Domain=', response.headers['Set-Cookie'])
        self.assertIn('__Host-baidu_ads_session=', response.headers['Set-Cookie'])
        return re.search(rb'name="csrf" value="([^"]+)"', response.data)[1].decode()

    def finish_login(self, csrf, path='/ssw/en.html', username='ken', password=None):
        return self.client.post('/login', base_url=login.BASE_URL, data={
            'page': path, 'csrf': csrf, 'username': username,
            'password': self.password if password is None else password,
        })

    def test_protected_routes_do_not_read_any_data_before_sign_in(self):
        with patch.object(login, 'source_bytes') as source:
            for path in login.REPORT_PAGES:
                response = self.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'action="/login"', response.data)
                self.assertNotIn(b'report-data', response.data)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
            source.assert_not_called()
        self.assertEqual(self.get('/ssw').location, '/ssw/')
        self.assertEqual(self.get('/data/posthog-cli.enc').status_code, 404)
        self.assertEqual(self.client.get('/', base_url='https://other.example').status_code, 400)

    def test_login_rejects_invalid_csrf_and_redirect_targets(self):
        with patch.object(login, 'password_digest') as digest:
            csrf = self.start_login()
            self.assertEqual(self.finish_login('invalid').status_code, 403)
            self.assertEqual(self.finish_login(csrf, path='//example.com').status_code, 400)
            digest.assert_not_called()

    def test_login_rejects_bad_credentials_and_throttles_repeated_attempts(self):
        csrf = self.start_login()
        with patch.object(login.time, 'monotonic', return_value=100):
            self.assertEqual(self.finish_login(csrf, username='someone').status_code, 401)
            for _ in range(login.LOGIN_ATTEMPT_LIMIT - 1):
                self.assertEqual(self.finish_login(csrf, password='wrong').status_code, 401)
            self.assertEqual(self.finish_login(csrf).status_code, 429)
        with patch.object(login.time, 'monotonic', return_value=100 + login.LOGIN_ATTEMPT_WINDOW):
            self.assertEqual(self.finish_login(csrf).status_code, 303)

    def test_owner_session_renders_reports_and_private_aggregates_until_logout(self):
        response = self.finish_login(self.start_login())
        self.assertEqual(response.location, '/ssw/en.html')
        self.assertEqual(response.status_code, 303)
        report = b'<script id="report-data" type="application/json">{"rows":[]}</script>'
        sources = {path: report for path in ['report/index.html', 'report/yakshaver/index.html', 'report/ssw/index.html']}
        snapshots = {}
        for slug, (path, header, field, fields) in login.SNAPSHOTS.items():
            day = '2026-11-11'
            snapshot = {'through': day, 'rows': [{'date': day, **dict.fromkeys(fields, 123)}]}
            if slug == 'ssw':
                snapshot['rows'].insert(0, {'date': '2026-11-10', 'customers': 1})
                snapshot['rows'][1]['messages'] = 0
            nonce = secrets.token_bytes(12)
            sources[path] = header + nonce + AESGCM(self.keys[slug]).encrypt(nonce, json.dumps(snapshot).encode(), header)
            snapshots[slug] = {field: snapshot}
        with patch.object(login, 'source_bytes', side_effect=lambda path, *_: sources[path]):
            for path, (slug, _) in login.REPORT_PAGES.items():
                response = self.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                header = response.data.split(b'</header>', 1)[0]
                refresh, signout = ('Refresh report', 'Sign out') if path.endswith('/en.html') else ('刷新报表', '退出登录')
                self.assertEqual(header.count(b'class="header-icon"'), 2)
                self.assertEqual(header.count(b'<svg '), 2)
                self.assertLess(header.index(f'aria-label="{refresh}"'.encode()), header.index(b'id="language"'))
                self.assertLess(header.index(b'id="language"'), header.index(f'aria-label="{signout}"'.encode()))
                nonce = re.search(rb'<script nonce="([^"]+)"', response.data)[1].decode()
                self.assertIn(f"'nonce-{nonce}'", response.headers['Content-Security-Policy'])
                match = re.search(rb'id="report-data" type="application/json">(.*?)</script>', response.data)
                self.assertEqual(json.loads(match[1]), {'rows': [], **snapshots.get(slug, {})})
            csrf = re.search(rb'name="csrf" value="([^"]+)"', response.data)[1].decode()
        self.assertEqual(self.client.post('/logout', base_url=login.BASE_URL).status_code, 403)
        self.assertEqual(self.client.post('/logout', data={'csrf': csrf}, base_url=login.BASE_URL).status_code, 303)
        self.assertIn(b'action="/login"', self.get('/ssw/').data)

    def test_local_source_pins_a_revision_without_calling_git(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {'BAIDU_ADS_LOCAL_STORAGE': directory}
        ), patch.object(login, 'source_bytes') as git_source:
            storage = Path(directory)
            for revision, content in [('a' * 32, b'first'), ('b' * 32, b'second')]:
                release = storage / 'releases' / revision
                release.mkdir(parents=True)
                (release / 'report.html').write_bytes(content)
            pointer = storage / 'current'
            pointer.write_text('a' * 32, encoding='ascii')
            read = login.report_source()
            pointer.write_text('b' * 32, encoding='ascii')
            self.assertEqual(read('report.html', 5), b'first')
            self.assertEqual(login.report_source()('report.html', 6), b'second')
            with self.assertRaises(ValueError):
                read('report.html', 4)
            git_source.assert_not_called()

    def test_kenshi_manages_viewers_and_reset_revokes_existing_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'users.sqlite3'
            session_key = secrets.token_bytes(32)
            app = login.create_app('kenshi', self.password_hash, session_key, self.keys, database)
            self.client = app.test_client()
            self.assertEqual(self.get('/users').status_code, 403)
            csrf = self.start_login()
            self.assertEqual(self.finish_login(csrf, username='kenshi').status_code, 303)
            page = self.get('/users?lang=en')
            self.assertIn(b'Manage users', page.data)
            csrf = re.search(rb'name="csrf" value="([^"]+)"', page.data)[1].decode()
            owner = self.client
            with patch.object(login, 'source_bytes', return_value=b'<script id="report-data" type="application/json">{}</script>'):
                self.assertIn(b'href="/users?lang=en"', self.get('/yakshaver/en.html').data)

            def save(action, name, password, token=csrf):
                return owner.post('/users?lang=en', base_url=login.BASE_URL, data={
                    'csrf': token, 'action': action, 'username': name, 'password': password,
                })

            initial = 'initial-viewer-password'
            updated = 'updated-viewer-password'
            self.assertEqual(save('create', 'viewer', initial, token='bad').status_code, 403)
            self.assertEqual(save('create', 'viewer', 'short').status_code, 400)
            self.assertEqual(save('create', 'KENSHI', initial).status_code, 400)
            self.assertEqual(save('create', 'Viewer', initial).status_code, 303)
            self.assertIn(b'<option>viewer</option>', self.get('/users').data)
            self.assertEqual(save('create', 'viewer', updated).status_code, 409)

            self.client = app.test_client()
            self.assertEqual(self.finish_login(self.start_login(), username='viewer', password=initial).status_code, 303)
            self.assertEqual(self.get('/users').status_code, 403)
            self.assertEqual(self.client.post('/users', base_url=login.BASE_URL, data={'csrf': csrf}).status_code, 403)
            report = b'<script id="report-data" type="application/json">{"private_posthog_cli":123,"private_wecom_consultations":456}</script>'
            with patch.object(login, 'source_bytes', return_value=report) as source:
                for path in ('/en.html', '/ssw/en.html'):
                    response = self.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertNotIn(b'href="/users', response.data)
                    match = re.search(rb'id="report-data" type="application/json">(.*?)</script>', response.data)
                    self.assertEqual(json.loads(match[1]), {})
                self.assertEqual(source.call_count, 2)

            self.assertEqual(save('reset', 'kenshi', updated).status_code, 400)
            self.assertEqual(save('reset', 'viewer', updated).status_code, 303)
            self.assertIn(b'action="/login"', self.get('/ssw/').data)
            self.client = login.create_app('kenshi', self.password_hash, session_key, self.keys, database).test_client()
            csrf = self.start_login()
            self.assertEqual(self.finish_login(csrf, username='viewer', password=initial).status_code, 401)
            self.assertEqual(self.finish_login(csrf, username='viewer', password=updated).status_code, 303)
            self.assertNotIn(initial.encode(), database.read_bytes())
            self.assertNotIn(updated.encode(), database.read_bytes())

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
