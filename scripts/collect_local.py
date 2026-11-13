#!/usr/bin/env python3
"""Collect and publish a complete report revision to private ECS storage."""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from login_dashboard import REPORT_DATA, SNAPSHOTS, decrypt_snapshot, key_bytes
from scripts import vault

REPORTS = ('report/index.html', 'report/yakshaver/index.html', 'report/ssw/index.html')
SNAPSHOT_FILES = tuple(spec[0] for spec in SNAPSHOTS.values())
HEADER = b'BAIDU-ADS-ECS-BACKUP-v1\n'
BACKUP_FOLDERS = ('data', 'accounts', 'backups')
REQUIRED = (
    'DATA_BACKUP_KEY', 'BAIDU_TONGJI_CREDENTIALS_JSON', 'BAIDU_TONGJI_SSW_SITE_JSON',
    'POSTHOG_CLI_QUERY_KEY', 'POSTHOG_CLI_SNAPSHOT_KEY', 'WECOM_SNAPSHOT_KEY',
    'WECOM_CORP_ID', 'WECOM_OPEN_KFID', 'WECOM_APP_SECRET',
)
STEPS = (
    ('scripts/sync_website_conversions.py',),
    ('scripts/sync_website_conversions.py', '--site', 'tinacms-paid'),
    ('scripts/sync_website_traffic.py', '--site', 'tinacms'),
    ('scripts/sync_website_conversions.py', '--site', 'ssw'),
    ('scripts/sync_website_traffic.py', '--site', 'ssw'),
    ('scripts/sync_posthog_cli.py',),
    ('scripts/sync_wecom_consultations.py', '--output', SNAPSHOT_FILES[1]),
    ('tracker.py',),
    ('scripts/verify_data.py',),
    ('scripts/publish_data.py',),
    ('dashboard.py', '--public'),
    ('scripts/accounts.py', 'collect', '--account', 'yakshaver'),
    ('scripts/accounts.py', 'collect', '--account', 'ssw'),
    ('scripts/vault.py', 'backup'),
    ('scripts/vault.py', 'verify'),
    ('scripts/check_public.py',),
)


def collect():
    missing = [name for name in REQUIRED if not os.environ.get(name, '').strip()]
    if missing:
        raise ValueError('Missing collector settings: ' + ', '.join(missing))
    key = vault.key_bytes()
    if not (ROOT / 'baidu.sqlite3').exists():
        vault.restore(ROOT / 'backups/private-data.enc', ROOT, key)
    from scripts.accounts import PROFILES, prepare

    for slug, profile in PROFILES.items():
        _, runtime = prepare(slug)
        if not (runtime / '.credentials.json').exists():
            vault.restore(ROOT / 'accounts' / slug / 'backups/private-data.enc', runtime, key)
        os.environ[profile['secret']] = (runtime / '.credentials.json').read_text(encoding='utf-8')
    for step in STEPS:
        print('Running', *step, flush=True)
        subprocess.run([sys.executable, *step], cwd=ROOT, check=True)


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


def backup(release, output):
    plain = io.BytesIO()
    with zipfile.ZipFile(plain, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in REPORTS:
            archive.write(release / name, name)
        for folder in BACKUP_FOLDERS:
            for path in (ROOT / folder).rglob('*'):
                if path.is_file():
                    archive.write(path, path.relative_to(ROOT))
    nonce = os.urandom(12)
    cipher = AESGCM(vault.key_bytes())
    output.write_bytes(HEADER + nonce + cipher.encrypt(nonce, plain.getvalue(), HEADER))
    packed = output.read_bytes()
    offset = len(HEADER)
    restored = cipher.decrypt(packed[offset:offset + 12], packed[offset + 12:], HEADER)
    with zipfile.ZipFile(io.BytesIO(restored)) as archive:
        if archive.testzip() is not None:
            raise ValueError('ECS backup verification failed')


def publish(storage, *, oss=None, ossutil='ossutil'):
    revision = uuid.uuid4().hex
    releases = storage / 'releases'
    release = releases / revision
    release.mkdir(parents=True)
    pointer = storage / (revision + '.tmp')
    try:
        prepare_release(release)
        if oss:
            output = release / 'backup.enc'
            backup(release, output)
            target = oss.rstrip('/') + '/' + revision + '.enc'
            subprocess.run([ossutil, 'cp', str(output), target], check=True, timeout=300)
            output.unlink()
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('seed', 'collect', 'check'))
    parser.add_argument('--storage', type=Path, required=True)
    parser.add_argument('--oss')
    parser.add_argument('--ossutil', default='ossutil')
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
        if args.mode == 'collect':
            collect()
        publish(args.storage, oss=args.oss, ossutil=args.ossutil)


if __name__ == '__main__':
    main()
