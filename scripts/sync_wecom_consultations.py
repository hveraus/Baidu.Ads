#!/usr/bin/env python3
"""Collect encrypted daily consultation and customer message counts from the WeCom trusted IP."""
import argparse
import base64
import datetime as dt
import http.client
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sync_posthog_cli import NoRedirect, write_encrypted_snapshot


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wecom_snapshot import FILENAME, HEADER, HISTORY_DAYS


BEIJING = dt.timezone(dt.timedelta(hours=8))
WECOM_API = 'https://qyapi.weixin.qq.com/cgi-bin'
REPOSITORY = 'hveraus/Baidu.Ads'
SNAPSHOT_PATH = f'accounts/ssw/data/{FILENAME}'
GITHUB_FILE = f'https://api.github.com/repos/{REPOSITORY}/contents/{SNAPSHOT_PATH}'
HTTP = urllib.request.build_opener(NoRedirect())


def request_json(url, *, method='GET', query=None, payload=None, headers=None):
    if query:
        url += '?' + urllib.parse.urlencode(query)
    request = urllib.request.Request(
        url, method=method, headers=headers or {},
        data=json.dumps(payload).encode() if payload is not None else None,
    )
    if payload is not None:
        request.add_header('Content-Type', 'application/json')
    with HTTP.open(request, timeout=25) as response:
        return json.load(response)


def wecom(endpoint, **kwargs):
    try:
        response = request_json(f'{WECOM_API}/{endpoint}', **kwargs)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'WeCom {endpoint}: HTTP {error.code}') from None
    except urllib.error.URLError:
        raise RuntimeError(f'Could not reach WeCom {endpoint}') from None
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as error:
        raise RuntimeError(f'WeCom {endpoint}: {type(error).__name__}') from None
    if not isinstance(response, dict):
        raise RuntimeError(f'WeCom {endpoint}: expected a response object')
    if response.get('errcode') != 0:
        code = response.get('errcode')
        raise RuntimeError(f'WeCom {endpoint}: errcode={code if type(code) is int else "unknown"}')
    return response


def timestamp(day):
    return int(dt.datetime.combine(day, dt.time.min, tzinfo=BEIJING).timestamp())


def normalize_rows(results, start, end):
    if not isinstance(results, list):
        raise ValueError('WeCom statistic_list must be an array')
    counts = {}
    for row in results:
        stat_time = row['stat_time']
        if type(stat_time) is not int:
            raise ValueError('Invalid WeCom stat_time')
        day = dt.datetime.fromtimestamp(stat_time, BEIJING).date()
        if not start <= day <= end or stat_time != timestamp(day) or day in counts:
            raise ValueError('WeCom returned an unexpected or repeated date')
        statistic = row.get('statistic')
        if statistic is not None and not isinstance(statistic, dict):
            raise ValueError('WeCom statistic must be an object')
        daily = {}
        for source, field in (('customer_cnt', 'customers'), ('customer_msg_cnt', 'messages')):
            count = (statistic or {}).get(source)
            if count is not None:
                if type(count) is not int or not 0 <= count < 2**53:
                    raise ValueError(f'WeCom returned an invalid {source}')
                daily[field] = count
        counts[day] = daily
    return [{'date': day.isoformat(), **daily} for day, daily in sorted(counts.items()) if daily]


def fetch_rows(token, account, through):
    first = through - dt.timedelta(days=HISTORY_DAYS - 1)
    rows = []
    for offset in range(0, HISTORY_DAYS, 31):
        start = first + dt.timedelta(days=offset)
        end = min(start + dt.timedelta(days=30), through)
        response = wecom('kf/get_corp_statistic', method='POST', query={'access_token': token},
                         payload={'open_kfid': account, 'start_time': timestamp(start), 'end_time': timestamp(end)})
        results = response.get('statistic_list')
        try:
            rows.extend(normalize_rows([] if results is None else results, start, end))
        except (AttributeError, KeyError, OSError, OverflowError, TypeError, ValueError) as error:
            raise RuntimeError(f'WeCom statistics format for {start} to {end}: {type(error).__name__}') from None
    return rows


def encrypt_snapshot(rows, through, key):
    if not re.fullmatch(r'[0-9a-fA-F]{64}', key):
        raise ValueError('WECOM_SNAPSHOT_KEY must be 64 hexadecimal characters')
    nonce = os.urandom(12)
    payload = json.dumps({'through': through.isoformat(), 'rows': rows}, separators=(',', ':')).encode()
    return HEADER + nonce + AESGCM(bytes.fromhex(key)).encrypt(nonce, payload, HEADER)


def publish_snapshot(encrypted, token):
    headers = {'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {token}',
               'X-GitHub-Api-Version': '2022-11-28'}
    payload = {'message': 'Update encrypted SSW WeCom customer service totals', 'branch': 'main',
               'content': base64.b64encode(encrypted).decode('ascii')}
    try:
        current = request_json(GITHUB_FILE, query={'ref': 'main'}, headers=headers)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    else:
        payload['sha'] = current['sha']
    request_json(GITHUB_FILE, method='PUT', payload=payload, headers=headers)


def config(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise ValueError(f'Missing {name}')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='check read access without saving or publishing')
    mode.add_argument('--publish', action='store_true', help='publish encrypted totals to the dashboard repository')
    mode.add_argument('--output', type=Path, help='write only the encrypted snapshot to this path')
    args = parser.parse_args()
    try:
        account = config('WECOM_OPEN_KFID')
        corp = config('WECOM_CORP_ID')
        secret = config('WECOM_APP_SECRET')
        key = config('WECOM_SNAPSHOT_KEY') if not args.check else None
        github_token = config('WECOM_GITHUB_TOKEN') if args.publish else None
        now = dt.datetime.now(BEIJING)
        through = now.date() - dt.timedelta(days=1 if now.hour >= 6 else 2)
        access = wecom('gettoken', query={'corpid': corp, 'corpsecret': secret}).get('access_token')
        if not isinstance(access, str) or not access:
            raise RuntimeError('WeCom gettoken did not return an access token')
        rows = fetch_rows(access, account, through)
        if args.check:
            latest = rows[-1]['date'] if rows else 'none'
            print(f'WeCom statistics API OK. Latest date with a count: {latest}.')
            return
        if not rows:
            parser.exit(1, 'WeCom returned no daily counts. The previous snapshot was retained.\n')
        encrypted = encrypt_snapshot(rows, through, key)
        if args.publish:
            publish_snapshot(encrypted, github_token)
            print('Encrypted WeCom snapshot published.')
        else:
            write_encrypted_snapshot(args.output, encrypted)
            print('Encrypted WeCom snapshot saved.')
    except urllib.error.HTTPError as error:
        parser.exit(1, f'GitHub request failed with HTTP {error.code}.\n')
    except urllib.error.URLError:
        parser.exit(1, 'Could not reach GitHub.\n')
    except RuntimeError as error:
        parser.exit(1, f'{error}\n')
    except ValueError as error:
        if str(error).startswith('Missing WECOM_'):
            parser.exit(1, f'{error}\n')
        parser.exit(1, 'Invalid WeCom response or snapshot key.\n')
    except (AttributeError, KeyError, OSError, OverflowError, TypeError) as error:
        parser.exit(1, f'WeCom sync could not complete ({type(error).__name__}).\n')


if __name__ == '__main__':
    main()
