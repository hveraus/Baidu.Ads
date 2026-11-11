#!/usr/bin/env python3
"""Collect daily China Tina CLI counts for the Baidu.Ads dashboard."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
ENCRYPTED_SNAPSHOT = 'posthog-cli.enc'
SNAPSHOT_HEADER = b'BAIDU-ADS-POSTHOG-v1\n'
API = 'https://us.posthog.com/api/projects/237683/query/'
BEIJING = dt.timezone(dt.timedelta(hours=8))
HISTORY_DAYS = 90
QUERY = """SELECT
    toString(toDate(toTimeZone(timestamp, 'Asia/Shanghai'))) AS day,
    countIf(event = 'create-tina-app-started') AS started,
    countIf(event = 'create-tina-app-finished') AS finished
FROM events
WHERE properties.$geoip_country_code = 'CN'
    AND event IN ('create-tina-app-started', 'create-tina-app-finished')
    AND timestamp >= now() - INTERVAL 91 DAY
    AND toDate(toTimeZone(timestamp, 'Asia/Shanghai')) >= toDate(toTimeZone(now(), 'Asia/Shanghai')) - INTERVAL 90 DAY
    AND toDate(toTimeZone(timestamp, 'Asia/Shanghai')) < toDate(toTimeZone(now(), 'Asia/Shanghai'))
GROUP BY day
ORDER BY day DESC"""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def write_encrypted_snapshot(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(content)
            file.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def normalize_rows(results, through):
    if not isinstance(results, list):
        raise ValueError('PostHog results must be an array of daily counts')
    first = through - dt.timedelta(days=HISTORY_DAYS - 1)
    counts = {}
    for row in results:
        day, started, finished = row
        date = dt.date.fromisoformat(day)
        if day != date.isoformat() or not first <= date <= through or date in counts:
            raise ValueError('PostHog returned an unexpected or repeated date')
        if any(type(count) is not int or count < 0 for count in (started, finished)):
            raise ValueError('PostHog counts must be non-negative integers')
        counts[date] = started, finished
    rows = []
    for offset in range(HISTORY_DAYS):
        day = first + dt.timedelta(days=offset)
        started, finished = counts.get(day, (0, 0))
        rows.append({'date': day.isoformat(), 'started': started, 'finished': finished})
    return rows


def read_encrypted_snapshot(path):
    if not path.exists():
        return None
    payload = path.read_bytes()
    if not payload.startswith(SNAPSHOT_HEADER) or not len(SNAPSHOT_HEADER) + 28 < len(payload) <= 16_384:
        raise ValueError('Unexpected encrypted PostHog snapshot format')
    return payload


def publish_encrypted():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    query_key = os.environ.get('POSTHOG_CLI_QUERY_KEY', '').strip()
    snapshot_key = os.environ.get('POSTHOG_CLI_SNAPSHOT_KEY', '').strip()
    if not query_key and not snapshot_key:
        print('PostHog encrypted sync is not configured.')
        return
    if not query_key or len(snapshot_key) != 64:
        raise ValueError('Both PostHog cloud secrets are required')
    cipher = AESGCM(bytes.fromhex(snapshot_key))
    through = dt.datetime.now(BEIJING).date() - dt.timedelta(days=1)
    rows = normalize_rows(fetch_daily(query_key, through), through)
    plain = json.dumps({'through': through.isoformat(), 'rows': rows}, separators=(',', ':')).encode()
    nonce = os.urandom(12)
    encrypted = SNAPSHOT_HEADER + nonce + cipher.encrypt(nonce, plain, SNAPSHOT_HEADER)
    write_encrypted_snapshot(ROOT / 'data' / ENCRYPTED_SNAPSHOT, encrypted)
    print('Encrypted PostHog snapshot saved.')


def fetch_daily(key, through):
    request = urllib.request.Request(
        API,
        data=json.dumps({'query': {'kind': 'HogQLQuery', 'query': QUERY}}).encode(),
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
    )
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=25) as response:
        data = json.load(response)
    if data.get('columns') != ['day', 'started', 'finished'] or data.get('hasMore'):
        raise ValueError('PostHog returned unexpected columns or an incomplete result')
    if dt.datetime.now(BEIJING).date() - dt.timedelta(days=1) != through:
        raise ValueError('The Beijing date changed during the query; run the collector again')
    return data['results']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        publish_encrypted()
    except urllib.error.HTTPError as error:
        parser.exit(1, f'PostHog query failed with HTTP {error.code}. The previous encrypted snapshot was retained.\n')
    except urllib.error.URLError:
        parser.exit(1, 'Could not reach PostHog. The previous encrypted snapshot was retained.\n')
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(1, 'PostHog sync could not complete. Check the secrets and query response.\n')


if __name__ == '__main__':
    main()
