#!/usr/bin/env python3
"""Collect and publish a complete report revision to private ECS storage."""
import argparse
import datetime as dt
import re
from cryptography.exceptions import InvalidTag
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from login_dashboard import REPORT_DATA, SNAPSHOTS, decrypt_snapshot, key_bytes
from scripts import vault

REPORTS = ('report/index.html', 'report/yakshaver/index.html', 'report/ssw/index.html')
SNAPSHOT_FILES = tuple(spec[0] for spec in SNAPSHOTS.values())
from scripts.accounts import prepare
from scripts.check_public import check_files, check_database
from website_conversions import FILENAME, FIELDS, SSW_FILENAME, SSW_FIELDS, TINA_PAID_FILENAME, TINA_PAID_FIELDS, load_rows
from website_traffic import TRAFFIC_FILENAME, load_traffic
from contact_attribution import FILENAME as CONTACT_FILENAME, load_snapshot

BEIJING = dt.timezone(dt.timedelta(hours=8))
# Source identifiers are also keys in the dashboard's bilingual coverage notice.
WEBSITE = {
    'tinacms': ('website_conversions', 'tina_paid_website_conversions', 'website_traffic'),
    'ssw': ('ssw_paid_website_conversions', 'ssw_contact_attribution', 'website_traffic'),
}
CONVERSIONS = {
    'website_conversions': ('tinacms', FILENAME, FIELDS),
    'tina_paid_website_conversions': ('tinacms-paid', TINA_PAID_FILENAME, TINA_PAID_FIELDS),
    'ssw_paid_website_conversions': ('ssw', SSW_FILENAME, SSW_FIELDS),
}


def run(*step, ads_only=False):
    print('Running', *step, flush=True)
    subprocess.run([sys.executable, *step], cwd=ROOT, check=True,
                   env=dict(os.environ, BAIDU_ADS_COLLECT_ADS_ONLY='1') if ads_only else None)


def read_report(path):
    content = path.read_bytes()
    match = REPORT_DATA.search(content)
    if not match:
        raise ValueError('Missing report data')
    return content, match, json.loads(match[1])


def require_settings(*names):
    if any(not os.environ.get(name, '').strip() for name in names):
        raise ValueError('Missing source configuration')


def collect_ads(slug, name, started, yesterday):
    runtime = ROOT if slug == 'tinacms' else prepare(slug)[1]
    account = ROOT if slug == 'tinacms' else ROOT / 'accounts' / slug
    if not (runtime / 'baidu.sqlite3').exists() or not (runtime / '.credentials.json').exists():
        vault.restore(account / 'backups/private-data.enc', runtime, vault.key_bytes())
    if slug == 'tinacms':
        run('tracker.py', ads_only=True)
        run('scripts/verify_data.py')
        run('scripts/publish_data.py')
        run('dashboard.py', '--public', ads_only=True)
    else:
        run('scripts/accounts.py', 'collect', '--account', slug, '--no-backup', ads_only=True)
    check_database(account / 'data/public.sqlite3')
    _, _, data = read_report(ROOT / name)
    status = data['status']
    required = set(json.loads((runtime / 'config.json').read_text())['reports'])
    if (not status.get('ok') or dt.datetime.fromisoformat(status['run_at']) < started
            or {r['report'] for r in data['coverage'] if r['end'] == yesterday} != required
            or status['end'] != yesterday or set(status['reports']) != required
            or any(not r.get('ok') or r.get('end') != yesterday or r.get('updated_at') != status['run_at']
                   for r in status['reports'].values())):
        raise ValueError('Incomplete or stale advertising reports')
    return {key: data[key] for key in ('reports', 'inventory', 'coverage', 'status', 'generated', 'record_counts', 'website')}


def collect_website(slug, field):
    require_settings('BAIDU_TONGJI_CREDENTIALS_JSON')
    folder = ROOT / 'data'
    if slug == 'ssw':
        require_settings('BAIDU_TONGJI_SSW_SITE_JSON')
        folder = ROOT / 'accounts/ssw/data'
    if field in CONVERSIONS:
        site, filename, fields = CONVERSIONS[field]
        run('scripts/sync_website_conversions.py', '--site', site)
        return {field: load_rows(folder / filename, fields)}
    if field == 'website_traffic':
        run('scripts/sync_website_traffic.py', '--site', slug)
        return {field: load_traffic(folder / TRAFFIC_FILENAME)}
    run('scripts/sync_contact_attribution.py')
    contact = load_snapshot(folder / CONTACT_FILENAME)
    return {field: contact['rows'], field + '_status': {k: contact[k] for k in ('source', 'paid_source', 'generated_at', 'through')}}


def coverage(data, field):
    if field == 'ads':
        return data['status'].get('end')
    if field == 'website_traffic':
        return max((r['end'] for r in data.get(field, {}).get('ranges', [])), default=None)
    return max((r['date'] for r in data.get(field, [])), default=None)


def snapshot_date(slug, directory):
    name, header, _, fields = SNAPSHOTS[slug]
    key = 'POSTHOG_CLI_SNAPSHOT_KEY' if slug == 'tinacms' else 'WECOM_SNAPSHOT_KEY'
    return decrypt_snapshot((directory / name).read_bytes(), key_bytes(os.environ.get(key)), header, fields)['through']


def collect_release(previous, release):
    started = dt.datetime.now(BEIJING)
    yesterday = (started.date() - dt.timedelta(days=1)).isoformat()
    failed = []
    for slug, name in zip(('tinacms', 'yakshaver', 'ssw'), REPORTS):
        content, match, data = read_report(previous / name)
        if slug == 'ssw' and 'ssw_promotion_trends' in data:
            data['ssw_paid_website_conversions'] = data['ssw_promotion_trends']
        issues = {}
        fields = ['ads', *WEBSITE.get(slug, ())]
        if slug in SNAPSHOTS:
            fields.append('posthog' if slug == 'tinacms' else 'wecom')
        for field in fields:
            try:
                if field in ('posthog', 'wecom'):
                    path = SNAPSHOTS[slug][0]
                    if field == 'posthog':
                        require_settings('POSTHOG_CLI_QUERY_KEY', 'POSTHOG_CLI_SNAPSHOT_KEY')
                        run('scripts/sync_posthog_cli.py')
                    else:
                        require_settings('WECOM_CORP_ID', 'WECOM_OPEN_KFID', 'WECOM_APP_SECRET', 'WECOM_SNAPSHOT_KEY')
                        run('scripts/sync_wecom_consultations.py', '--output', path)
                    expected = (started.date() - dt.timedelta(days=2)).isoformat() if field == 'wecom' and started.hour < 6 else yesterday
                    if snapshot_date(slug, ROOT) != expected:
                        raise ValueError('Stale private source')
                    (release / path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / path, release / path)
                else:
                    update = collect_ads(slug, name, started, yesterday) if field == 'ads' else collect_website(slug, field)
                    if coverage(update, field) != yesterday:
                        if field == 'ssw_paid_website_conversions':
                            print('SSW promotion trends: no numeric visits for', yesterday, '(log only); checking contact attribution.', flush=True)
                            continue
                        raise ValueError('Incomplete source coverage')
                    data.update(update)
            except (OSError, ValueError, TypeError, KeyError, subprocess.CalledProcessError, sqlite3.Error, AssertionError, InvalidTag) as exc:
                if field in ('posthog', 'wecom'):
                    path = SNAPSHOTS[slug][0]
                    (release / path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(previous / path, release / path)
                    try:
                        through = snapshot_date(slug, previous)
                    except (ValueError, TypeError, KeyError, InvalidTag):
                        through = data.get('private_source_through')
                else:
                    through = coverage(data, field)
                issues[field] = through
                failed.append(f'{slug}/{field}')
                print('Retaining previous result:', slug, field, type(exc).__name__, flush=True)
            if field in ('posthog', 'wecom'):
                data['private_source_through'] = issues[field] if field in issues else expected
        if slug == 'ssw':
            data['ssw_promotion_trends'] = data.get('ssw_paid_website_conversions', [])
        if slug == 'ssw' and 'ssw_contact_attribution' not in issues and data.get('ssw_contact_attribution'):
            data['ssw_paid_website_conversions'] = [
                {k: row[k] for k in ('date', 'paid_sessions', 'paid_contact_click_sessions')}
                for row in data['ssw_contact_attribution']]
        data['collection_issues'] = issues
        # Encrypted feeds are served from their own files after authentication.
        for field in ('encrypted_posthog_cli', 'encrypted_wecom_consultations'):
            data.pop(field, None)
        packed = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('<', r'\u003c').encode()
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        html = content[:match.start(1)] + packed + content[match.end(1):]
        if len(html) > 32 * 1024 * 1024:
            raise ValueError(f'Report exceeds dashboard read limit: {slug}')
        target.write_bytes(html)
    check_files(release)
    return failed


def prepare_release(release):
    for name in REPORTS + SNAPSHOT_FILES:
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    for name in REPORTS:
        match = REPORT_DATA.search((release / name).read_bytes())
        if not match or not isinstance(json.loads(match[1]).get('status'), dict):
            raise ValueError(f'Invalid generated report: {name}')
    for slug, (name, header, _, fields) in SNAPSHOTS.items():
        key_name = 'POSTHOG_CLI_SNAPSHOT_KEY' if slug == 'tinacms' else 'WECOM_SNAPSHOT_KEY'
        decrypt_snapshot((release / name).read_bytes(), key_bytes(os.environ.get(key_name)), header, fields)


def publish(storage, previous=None):
    revision = uuid.uuid4().hex
    releases = storage / 'releases'
    release = releases / revision
    release.mkdir(parents=True)
    pointer = storage / (revision + '.tmp')
    try:
        if previous is None:
            prepare_release(release)
            failed = []
        else:
            failed = collect_release(previous, release)
        pointer.write_text(revision + '\n', encoding='ascii')
        for attempt in range(5):
            try:
                pointer.replace(storage / 'current')
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1)
    except Exception:
        pointer.unlink(missing_ok=True)
        shutil.rmtree(release)
        raise
    for old in releases.iterdir():
        try:
            if old != release and old.is_dir() and time.time() - old.stat().st_mtime > 3 * 86400:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass
    print('Validated local report revision:', revision, flush=True)
    if failed:
        raise SystemExit('Partial collection: ' + ', '.join(failed))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('seed', 'collect', 'check'))
    parser.add_argument('--storage', type=Path, required=True)
    args = parser.parse_args()
    if args.mode == 'check':
        release = args.storage / 'releases' / (args.storage / 'current').read_text(encoding='ascii').strip()
        for slug, (name, header, _, fields) in SNAPSHOTS.items():
            key_name = 'POSTHOG_CLI_SNAPSHOT_KEY' if slug == 'tinacms' else 'WECOM_SNAPSHOT_KEY'
            snapshot = decrypt_snapshot((release / name).read_bytes(), key_bytes(os.environ.get(key_name)), header, fields)
            print(f"{slug} encrypted snapshot through {snapshot['through']}.")
        for name in REPORTS:
            match = REPORT_DATA.search((release / name).read_bytes())
            status = json.loads(match[1])['status']
            print(f"{name} through {status['end']}, {len(status['reports'])} reports.")
        return

    args.storage.mkdir(parents=True, exist_ok=True)
    with (args.storage / 'collect.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.mode == 'seed' and (args.storage / 'current').exists():
            raise SystemExit('This storage already has a report revision.')
        previous = None
        if args.mode == 'collect':
            revision = (args.storage / 'current').read_text(encoding='ascii').strip()
            if not re.fullmatch('[0-9a-f]{32}', revision):
                raise ValueError('Invalid active revision')
            previous = args.storage / 'releases' / revision
        publish(args.storage, previous)


if __name__ == '__main__':
    main()
