"""Password sign-in and authenticated Baidu.Ads reports behind the SSW HTTPS proxy."""
import argparse
import base64
from collections import deque
import datetime as dt
from functools import lru_cache
import hashlib
import hmac
import html
import json
import logging
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import Flask, Response, abort, g, redirect, request, session

from account_ui import account_template
from i18n import english_template
from publication import public_template
from scripts.accounts import PROFILES
from scripts.sync_posthog_cli import SNAPSHOT_HEADER as POSTHOG_HEADER
from wecom_snapshot import HEADER as WECOM_HEADER


ROOT = Path(__file__).resolve().parent
BASE_URL = 'https://ads.ssw.com.cn'
REPORT_REF = 'refs/remotes/origin/main'
USERNAME = re.compile(r'[A-Za-z0-9_.@-]{1,64}')
PASSWORD_HASH = re.compile(r'([0-9a-f]{32}):([0-9a-f]{64})')
MAX_PASSWORD_BYTES = 1024
LOGIN_ATTEMPT_LIMIT = 5
LOGIN_ATTEMPT_WINDOW = 60
GIT_REFRESH_LOCK = threading.Lock()
INLINE_HANDLER = re.compile(r'\son[a-z]+="([^"\n]+)"')
REPORT_DATA = re.compile(rb'<script id="report-data" type="application/json">(.*?)</script>', re.DOTALL)
REPORT_PAGES = {
    '/': ('tinacms', 'zh'), '/index.html': ('tinacms', 'zh'), '/en.html': ('tinacms', 'en'),
    '/yakshaver/': ('yakshaver', 'zh'), '/yakshaver/index.html': ('yakshaver', 'zh'),
    '/yakshaver/en.html': ('yakshaver', 'en'),
    '/ssw/': ('ssw', 'zh'), '/ssw/index.html': ('ssw', 'zh'), '/ssw/en.html': ('ssw', 'en'),
}
SNAPSHOTS = {
    'tinacms': ('data/posthog-cli.enc', POSTHOG_HEADER, 'private_posthog_cli', ('started', 'finished')),
    'ssw': ('accounts/ssw/data/wecom-consultations.enc', WECOM_HEADER, 'private_wecom_consultations', ('customers', 'messages')),
}


def key_bytes(value):
    if not re.fullmatch(r'[0-9a-fA-F]{64}', value or ''):
        raise ValueError('Expected a 32-byte hexadecimal key')
    return bytes.fromhex(value)


def password_digest(password, salt):
    return hashlib.scrypt(password, salt=salt, n=2**17, r=8, p=1, dklen=32, maxmem=256 * 1024 * 1024)


def hash_password(password):
    if len(password) > MAX_PASSWORD_BYTES or len(password.decode('utf-8')) < 15:
        raise ValueError('Use a password with at least 15 characters and at most 1024 UTF-8 bytes.')
    if b'\r' in password or b'\n' in password:
        raise ValueError('The password cannot contain a line break.')
    salt = secrets.token_bytes(16)
    return salt.hex() + ':' + password_digest(password, salt).hex()


@lru_cache(maxsize=2)
def report_revision(cache_bucket):
    # The Aliyun host can pull from GitHub even when the raw-content host is unreachable.
    try:
        subprocess.run(
            ['git', '-c', 'credential.helper=', 'fetch', '--quiet', '--no-tags',
             'origin', f'refs/heads/main:{REPORT_REF}'],
            cwd=ROOT, check=True, timeout=15, capture_output=True,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'},
        )
    except (OSError, subprocess.SubprocessError):
        logging.warning('Could not refresh report data. Serving the last downloaded Git revision.')
    return subprocess.check_output(['git', 'rev-parse', REPORT_REF], cwd=ROOT, timeout=5).strip().decode('ascii')


@lru_cache(maxsize=16)
def git_blob(path, revision, limit):
    content = subprocess.check_output(['git', 'show', f'{revision}:{path}'], cwd=ROOT, timeout=10)
    if len(content) > limit:
        raise ValueError('Report file exceeds size limit')
    return content


def source_bytes(path, cache_bucket, limit):
    with GIT_REFRESH_LOCK:
        revision = report_revision(cache_bucket)
    return git_blob(path, revision, limit)


def report_source():
    directory = os.environ.get('BAIDU_ADS_LOCAL_STORAGE')
    if not directory:
        bucket = int(time.monotonic() // 180)
        return lambda path, limit: source_bytes(path, bucket, limit)

    storage = Path(directory)
    revision = (storage / 'current').read_text(encoding='ascii').strip()
    if not re.fullmatch(r'[0-9a-f]{32}', revision):
        raise ValueError('Invalid local report revision')
    return lambda path, limit: local_blob(storage / 'releases' / revision, path, limit)


def local_blob(directory, path, limit):
    with (directory / path).open('rb') as file:
        content = file.read(limit + 1)
    if len(content) > limit:
        raise ValueError('Report file exceeds size limit')
    return content


def check_upstream():
    read = report_source()
    for slug in ('tinacms', 'yakshaver', 'ssw'):
        path = 'report/index.html' if slug == 'tinacms' else f'report/{slug}/index.html'
        if not REPORT_DATA.search(read(path, 32 * 1024 * 1024)):
            raise ValueError(f'Missing report data for {slug}')
    for path, header, *_ in SNAPSHOTS.values():
        if not read(path, 16_384).startswith(header):
            raise ValueError('Unexpected encrypted snapshot format')
    print('Report data and encrypted snapshots are readable.')


def decrypt_snapshot(packed, key, header, fields):
    offset = len(header)
    if not packed.startswith(header) or len(packed) <= offset + 28:
        raise ValueError('Unexpected snapshot format')
    plain = AESGCM(key).decrypt(packed[offset:offset + 12], packed[offset + 12:], header)
    snapshot = json.loads(plain)
    through = dt.date.fromisoformat(snapshot['through'])
    rows = snapshot['rows']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 90:
        raise ValueError('Unexpected snapshot dates')
    dates = [dt.date.fromisoformat(row['date']) for row in rows]
    if sorted(set(dates)) != dates or any(not 0 <= (through - day).days < 90 for day in dates):
        raise ValueError('Unexpected snapshot coverage')
    clean = []
    for day, row in zip(dates, rows, strict=True):
        counts = {field: row[field] for field in fields if field in row}
        if (not counts or (header != WECOM_HEADER and len(counts) != len(fields))
                or any(type(count) is not int or not 0 <= count < 2**53 for count in counts.values())):
            raise ValueError('Unexpected snapshot counts')
        clean.append({'date': day.isoformat(), **counts})
    return {'through': through.isoformat(), 'rows': clean}


@lru_cache(maxsize=6)
def report_template(slug, lang):
    profile = {'slug': 'tinacms', 'name': 'TinaCMS'} if slug == 'tinacms' else PROFILES[slug]
    source = public_template((ROOT / 'dashboard.template.html').read_text(encoding='utf-8'))
    if lang == 'en':
        source = english_template(source)
    template = account_template(source, profile, lang)
    hashes = sorted({
        base64.b64encode(hashlib.sha256(html.unescape(handler).encode()).digest()).decode()
        for handler in INLINE_HANDLER.findall(template)
    })
    handler_policy = ' '.join(f"'sha256-{digest}'" for digest in hashes)
    return template, handler_policy


def render_report(slug, lang, keys, csrf):
    read = report_source()
    path = 'report/index.html' if slug == 'tinacms' else f'report/{slug}/index.html'
    match = REPORT_DATA.search(read(path, 32 * 1024 * 1024))
    if not match:
        raise ValueError('Upstream report has no data block')
    data = json.loads(match[1])
    for field in ('encrypted_posthog_cli', 'encrypted_wecom_consultations',
                  'private_posthog_cli', 'private_wecom_consultations'):
        data.pop(field, None)
    if slug in SNAPSHOTS:
        snapshot_path, header, field, fields = SNAPSHOTS[slug]
        packed = read(snapshot_path, 16_384)
        data[field] = decrypt_snapshot(packed, keys[slug], header, fields)
    packed = json.dumps(data, ensure_ascii=False, separators=(',', ':')).translate({
        ord('<'): r'\u003c', ord('>'): r'\u003e', ord('&'): r'\u0026',
    })
    label = 'Sign out' if lang == 'en' else '退出登录'
    form = ('<form method="post" action="/logout">'
            f'<input type="hidden" name="csrf" value="{html.escape(csrf, quote=True)}">'
            f'<button class="header-icon" type="submit" aria-label="{label}" title="{label}">'
            '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>'
            '</svg></button></form>')
    template, handler_policy = report_template(slug, lang)
    nonce = secrets.token_urlsafe(18)
    template = template.replace('<script', f'<script nonce="{nonce}"').replace('<!-- dashboard-signout -->', form, 1)
    template = (template.replace('本页及所展示统计数据可公开访问', '已登录')
                .replace('This page and its advertising statistics are publicly accessible', 'Signed in'))
    return template.replace('/*__DATA__*/', packed, 1), f"'nonce-{nonce}' 'unsafe-hashes' {handler_policy}"


def login_page(path, error=None):
    lang = 'en' if path.endswith('/en.html') else 'zh'
    title, username, password, button = (
        ('Sign in to Baidu.Ads', 'Username', 'Password', 'Sign in') if lang == 'en'
        else ('登录 Baidu.Ads', '账号', '密码', '登录')
    )
    if 'login_csrf' not in session:
        session['login_csrf'] = secrets.token_urlsafe(32)
    csrf = html.escape(session['login_csrf'], quote=True)
    notice = f'<p role="alert">{html.escape(error)}</p>' if error else ''
    target = html.escape(path, quote=True)
    return f'''<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f3f6f3;color:#152419;font:16px system-ui,sans-serif}}main{{background:white;border:1px solid #dce5de;border-radius:16px;padding:32px;width:min(21rem,calc(100vw - 6rem));margin:16px}}h1{{margin:0 0 24px;font-size:1.6rem}}form{{display:grid;gap:16px}}label{{display:grid;gap:6px;font-weight:600}}input,button{{font:inherit;border-radius:7px;padding:10px 12px}}input{{min-width:0;border:1px solid #aebbb3;color:inherit}}button{{background:#174d37;color:white;border:0;cursor:pointer;font-weight:600}}[role=alert]{{color:#a32828}}</style>
</head><body><main><h1>{title}</h1>{notice}<form action="/login" method="post">
<input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="page" value="{target}">
<label>{username}<input name="username" autocomplete="username" autocapitalize="none" maxlength="64" required></label>
<label>{password}<input name="password" type="password" autocomplete="current-password" required></label>
<button type="submit">{button}</button></form></main></body></html>'''


def create_app(username, password_hash, session_key, keys):
    match = PASSWORD_HASH.fullmatch(password_hash)
    if not USERNAME.fullmatch(username) or not match:
        raise ValueError('Invalid dashboard login configuration')
    salt, expected_digest = map(bytes.fromhex, match.groups())
    username = username.encode()
    attempts = deque()
    attempts_lock = threading.Lock()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=session_key, TRUSTED_HOSTS=[urlsplit(BASE_URL).hostname],
        SESSION_COOKIE_NAME='__Host-baidu_ads_session', SESSION_COOKIE_PATH='/',
        SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        SESSION_REFRESH_EACH_REQUEST=False, PERMANENT_SESSION_LIFETIME=dt.timedelta(days=7),
        MAX_CONTENT_LENGTH=8192,
    )

    @app.after_request
    def protect_response(response):
        script_policy = g.get('script_policy', "'none'")
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Robots-Tag': 'noindex, nofollow',
            'Content-Security-Policy': f"default-src 'none'; script-src {script_policy}; style-src 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        return response

    @app.get('/health')
    def health():
        return 'OK\n'

    @app.post('/login')
    def login():
        page = request.form.get('page', '/')
        if page not in REPORT_PAGES:
            abort(400)
        csrf = request.form.get('csrf', '')
        expected = session.get('login_csrf', '')
        if not expected or not csrf.isascii() or not hmac.compare_digest(expected, csrf):
            abort(403)
        if session.get('authenticated') is True:
            return redirect(page, code=303)
        english = page.endswith('/en.html')
        password = request.form.get('password', '').encode()
        with attempts_lock:
            now = time.monotonic()
            while attempts and now - attempts[0] >= LOGIN_ATTEMPT_WINDOW:
                attempts.popleft()
            if len(attempts) >= LOGIN_ATTEMPT_LIMIT:
                message = 'Too many attempts. Try again in a minute.' if english else '尝试次数过多，请稍后再试。'
                return login_page(page, message), 429, {'Retry-After': str(LOGIN_ATTEMPT_WINDOW)}
            if (len(password) <= MAX_PASSWORD_BYTES and
                    hmac.compare_digest(password_digest(password, salt), expected_digest) and
                    hmac.compare_digest(request.form.get('username', '').encode(), username)):
                attempts.clear()
            else:
                attempts.append(now)
                message = 'Incorrect username or password.' if english else '账号或密码有误。'
                return login_page(page, message), 401
        session.clear()
        session.update(authenticated=True, csrf=secrets.token_urlsafe(32))
        session.permanent = True
        return redirect(page, code=303)

    @app.post('/logout')
    def logout():
        csrf = request.form.get('csrf', '')
        expected = session.get('csrf', '')
        if not expected or not csrf.isascii() or not hmac.compare_digest(expected, csrf):
            abort(403)
        session.clear()
        return redirect('/', code=303)

    @app.get('/ssw')
    @app.get('/yakshaver')
    def canonical_dashboard():
        return redirect(request.path + '/', code=308)

    @app.get('/')
    @app.get('/<path:page>')
    def dashboard(page=''):
        path = '/' + page
        if path not in REPORT_PAGES:
            abort(404)
        if session.get('authenticated') is not True:
            return login_page(path)
        slug, lang = REPORT_PAGES[path]
        try:
            body, g.script_policy = render_report(slug, lang, keys, session['csrf'])
        except (InvalidTag, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            return Response('看板数据暂不可用，请稍后刷新。', status=502, mimetype='text/plain')
        return Response(body, mimetype='text/html')

    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check-upstream', action='store_true', help='Check report data and encrypted snapshots')
    modes.add_argument('--check-posthog-key', action='store_true', help='Validate the Tina CLI key from standard input')
    modes.add_argument('--hash-password', action='store_true', help='Hash a UTF-8 password from standard input')
    args = parser.parse_args()
    if args.hash_password:
        try:
            print(hash_password(sys.stdin.buffer.readline(MAX_PASSWORD_BYTES + 3).rstrip(b'\r\n')))
        except ValueError as error:
            raise SystemExit(str(error))
        raise SystemExit
    if args.check_posthog_key:
        try:
            key = key_bytes(sys.stdin.buffer.readline(67).strip().decode('ascii'))
            path, header, _, fields = SNAPSHOTS['tinacms']
            snapshot = report_source()(path, 16_384)
            decrypt_snapshot(snapshot, key, header, fields)
        except InvalidTag:
            raise SystemExit('This key does not match the Tina CLI snapshot.')
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            raise SystemExit('Could not validate the Tina CLI key and snapshot.')
        print('Tina CLI key validated.')
        raise SystemExit
    if args.check_upstream:
        check_upstream()
        raise SystemExit

    from waitress import serve

    application = create_app(
        os.environ['BAIDU_ADS_LOGIN_USERNAME'], os.environ['BAIDU_ADS_PASSWORD_HASH'],
        key_bytes(os.environ['BAIDU_ADS_SESSION_KEY']),
        {'tinacms': key_bytes(os.environ['POSTHOG_CLI_SNAPSHOT_KEY']),
         'ssw': key_bytes(os.environ['WECOM_SNAPSHOT_KEY'])},
    )
    serve(application, host='127.0.0.1', port=8765, max_request_header_size=16_384, max_request_body_size=8192)
