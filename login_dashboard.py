"""GitHub sign-in and authenticated Baidu.Ads reports behind the SSW HTTPS proxy."""
import argparse
import base64
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
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import Flask, Response, abort, g, redirect, request, session

from account_ui import account_template
from i18n import english_template
from publication import public_template
from scripts.accounts import PROFILES
from scripts.sync_posthog_cli import NoRedirect, SNAPSHOT_HEADER as POSTHOG_HEADER
from wecom_snapshot import HEADER as WECOM_HEADER


ROOT = Path(__file__).resolve().parent
BASE_URL = 'https://ads.ssw.com.cn'
REPORT_REF = 'refs/remotes/origin/main'
GITHUB_AUTHORIZE = 'https://github.com/login/oauth/authorize'
GITHUB_TOKEN = 'https://github.com/login/oauth/access_token'
GITHUB_USER = 'https://api.github.com/user'
OWNER_ID = 4745861
OPENER = build_opener(NoRedirect())
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
    'ssw': ('accounts/ssw/data/wecom-consultations.enc', WECOM_HEADER, 'private_wecom_consultations', ('customers',)),
}


def key_bytes(value):
    if not re.fullmatch(r'[0-9a-fA-F]{64}', value or ''):
        raise ValueError('Expected a 32-byte hexadecimal key')
    return bytes.fromhex(value)


def fetch(req, limit):
    with OPENER.open(req, timeout=15) as response:
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError('Upstream response exceeds size limit')
    return content


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


def check_upstream():
    bucket = int(time.monotonic() // 180)
    for slug in ('tinacms', 'yakshaver', 'ssw'):
        path = 'report/index.html' if slug == 'tinacms' else f'report/{slug}/index.html'
        if not REPORT_DATA.search(source_bytes(path, bucket, 32 * 1024 * 1024)):
            raise ValueError(f'Missing report data for {slug}')
    for path, header, *_ in SNAPSHOTS.values():
        if not source_bytes(path, bucket, 16_384).startswith(header):
            raise ValueError('Unexpected encrypted snapshot format')
    print('Git report data and encrypted snapshots are readable.')
    for url in (GITHUB_TOKEN, GITHUB_USER):
        try:
            fetch(Request(url, method='HEAD', headers={'User-Agent': 'Baidu.Ads'}), 0)
        except HTTPError as error:
            if error.code not in (301, 302, 401, 403, 404, 405):
                raise
        print(f'HTTPS connection to {urlsplit(url).hostname} succeeded.')


def github_identity(code, verifier, client_id, client_secret):
    form = urlencode({
        'client_id': client_id, 'client_secret': client_secret, 'code': code,
        'redirect_uri': BASE_URL + '/callback', 'code_verifier': verifier,
    }).encode()
    token = json.loads(fetch(Request(GITHUB_TOKEN, data=form, headers={
        'Accept': 'application/json', 'User-Agent': 'Baidu.Ads',
        'Content-Type': 'application/x-www-form-urlencoded',
    }), 16_384))['access_token']
    return json.loads(fetch(Request(GITHUB_USER, headers={
        'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
        'User-Agent': 'Baidu.Ads',
    }), 64_000))


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
    for row in rows:
        if any(type(row[field]) is not int or not 0 <= row[field] < 2**53 for field in fields):
            raise ValueError('Unexpected snapshot counts')
    return {'through': through.isoformat(), 'rows': [
        {'date': day.isoformat(), **{field: row[field] for field in fields}}
        for day, row in zip(dates, rows, strict=True)
    ]}


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
    bucket = int(time.monotonic() // 180)
    path = 'report/index.html' if slug == 'tinacms' else f'report/{slug}/index.html'
    match = REPORT_DATA.search(source_bytes(path, bucket, 32 * 1024 * 1024))
    if not match:
        raise ValueError('Upstream report has no data block')
    data = json.loads(match[1])
    for field in ('encrypted_posthog_cli', 'encrypted_wecom_consultations',
                  'private_posthog_cli', 'private_wecom_consultations'):
        data.pop(field, None)
    if slug in SNAPSHOTS:
        snapshot_path, header, field, fields = SNAPSHOTS[slug]
        packed = source_bytes(snapshot_path, bucket, 16_384)
        data[field] = decrypt_snapshot(packed, keys[slug], header, fields)
    packed = json.dumps(data, ensure_ascii=False, separators=(',', ':')).translate({
        ord('<'): r'\u003c', ord('>'): r'\u003e', ord('&'): r'\u0026',
    })
    label = 'Sign out' if lang == 'en' else '退出登录'
    form = ('<form method="post" action="/logout">'
            f'<input type="hidden" name="csrf" value="{html.escape(csrf, quote=True)}">'
            f'<button type="submit">{label}</button></form>')
    template, handler_policy = report_template(slug, lang)
    nonce = secrets.token_urlsafe(18)
    template = template.replace('<script', f'<script nonce="{nonce}"').replace('<div class="header-right">',
        '<div class="header-right">' + form, 1)
    template = (template.replace('本页及所展示统计数据可公开访问', '已通过 GitHub 登录')
                .replace('This page and its advertising statistics are publicly accessible', 'Signed in with GitHub'))
    return template.replace('/*__DATA__*/', packed, 1), f"'nonce-{nonce}' 'unsafe-hashes' {handler_policy}"


def login_page(path, error=None):
    lang = 'en' if path.endswith('/en.html') else 'zh'
    title = 'Sign in to Baidu.Ads' if lang == 'en' else '登录 Baidu.Ads'
    button = 'Continue with GitHub' if lang == 'en' else '使用 GitHub 登录'
    notice = f'<p role="alert">{html.escape(error)}</p>' if error else ''
    target = '/login?' + urlencode({'page': path})
    return f'''<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f3f6f3;color:#152419;font:16px system-ui,sans-serif}}main{{background:white;border:1px solid #dce5de;border-radius:16px;padding:36px;max-width:30rem;margin:16px}}h1{{margin:0 0 22px;font-size:1.6rem}}a{{display:inline-block;background:#174d37;color:white;padding:11px 18px;border-radius:7px;text-decoration:none}}p{{line-height:1.5}}</style>
</head><body><main><h1>{title}</h1>{notice}<a href="{html.escape(target, quote=True)}">{button}</a></main></body></html>'''


def create_app(client_id, client_secret, session_key, keys):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=session_key, TRUSTED_HOSTS=[urlsplit(BASE_URL).hostname],
        SESSION_COOKIE_NAME='__Host-baidu_ads_session', SESSION_COOKIE_PATH='/',
        SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        SESSION_REFRESH_EACH_REQUEST=False, PERMANENT_SESSION_LIFETIME=dt.timedelta(days=7),
        MAX_CONTENT_LENGTH=4096,
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

    @app.get('/login')
    def login():
        page = request.args.get('page', '/')
        if page not in REPORT_PAGES:
            abort(400)
        if session.get('user_id') == OWNER_ID:
            return redirect(page)
        verifier = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        session.clear()
        session.update(oauth_state=state, verifier=verifier, issued=int(time.time()), page=page)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        return redirect(GITHUB_AUTHORIZE + '?' + urlencode({
            'client_id': client_id, 'redirect_uri': BASE_URL + '/callback', 'state': state,
            'code_challenge': challenge, 'code_challenge_method': 'S256', 'allow_signup': 'false',
        }))

    @app.get('/callback')
    def callback():
        state = session.pop('oauth_state', '')
        verifier = session.pop('verifier', '')
        issued = session.pop('issued', 0)
        page = session.pop('page', '/')
        received, code = request.args.get('state', ''), request.args.get('code', '')
        if (not state or not verifier or not code or page not in REPORT_PAGES or
                not received.isascii() or not hmac.compare_digest(state, received) or not 0 <= time.time() - issued < 600):
            return login_page('/', '登录会话已失效，请重试。'), 400
        try:
            identity = github_identity(code, verifier, client_id, client_secret)
        except (OSError, ValueError, KeyError, TypeError):
            return login_page(page, 'GitHub 登录暂不可用，请重试。'), 502
        if type(identity) is not dict or identity.get('id') != OWNER_ID:
            return login_page(page, '此 GitHub 账号没有看板访问权限。'), 403
        session.clear()
        session.update(user_id=OWNER_ID, csrf=secrets.token_urlsafe(32))
        session.permanent = True
        return redirect(page)

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
        if session.get('user_id') != OWNER_ID:
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
    parser.add_argument('--check-upstream', action='store_true', help='Check Git data and GitHub HTTPS connectivity')
    if parser.parse_args().check_upstream:
        check_upstream()
        raise SystemExit

    from waitress import serve

    application = create_app(
        os.environ['BAIDU_ADS_GITHUB_CLIENT_ID'], os.environ['BAIDU_ADS_GITHUB_CLIENT_SECRET'],
        key_bytes(os.environ['BAIDU_ADS_SESSION_KEY']),
        {'tinacms': key_bytes(os.environ['POSTHOG_CLI_SNAPSHOT_KEY']),
         'ssw': key_bytes(os.environ['WECOM_SNAPSHOT_KEY'])},
    )
    serve(application, host='127.0.0.1', port=8765, max_request_header_size=16_384, max_request_body_size=4096)
