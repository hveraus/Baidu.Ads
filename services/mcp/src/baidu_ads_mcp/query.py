"""Bounded, read-only queries over one explicitly selected local release copy.

This development reader is not the production network/identity adapter. It never
opens raw databases, credentials, encrypted private panels or storage/current.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ACCOUNTS = {"tinacms": "TinaCMS", "yakshaver": "YakShaver", "ssw": "SSW"}
DIMENSIONS = {
    "account": (),
    "campaign": ("campaignId", "campaignName"),
    "adgroup": ("campaignId", "campaignName", "adGroupId", "adGroupName"),
    "keyword": ("campaignId", "campaignName", "adGroupId", "adGroupName", "wInfoId", "wInfoNameStatus"),
    "search_terms": ("campaignId", "campaignName", "adGroupId", "adGroupName", "wInfoId", "wInfoNameStatus", "queryWord"),
}
IDENTITIES = {
    "account": (), "campaign": ("campaignId",),
    "adgroup": ("campaignId", "adGroupId"),
    "keyword": ("campaignId", "adGroupId", "wInfoId"),
    "search_terms": ("campaignId", "adGroupId", "wInfoId", "queryWord"),
}
# Additional report families verified against the published schema. Each remains
# a separate slice; never add its totals to account or keyword totals.
DIMENSIONS.update({
    'keyword_device': ('campaignId','adGroupId','wInfoId','wInfoNameStatus','device'),
    'keyword_region': ('campaignId','adGroupId','wInfoId','wInfoNameStatus','provinceName','provinceCityName'),
    'keyword_match': ('campaignId','adGroupId','wInfoId','wInfoNameStatus','wMatchId'),
    'creative': ('campaignId','campaignName','adGroupId','adGroupName','ideaId','ideaInfo'),
    'channels': ('product',),
    'keyword_hour': ('campaignId','adGroupId','wInfoId','wInfoNameStatus','hour'),
    'account_hour': ('hour',),
})
IDENTITIES.update({
    'keyword_device': ('campaignId','adGroupId','wInfoId','device'),
    'keyword_region': ('campaignId','adGroupId','wInfoId','provinceName','provinceCityName'),
    'keyword_match': ('campaignId','adGroupId','wInfoId','wMatchId'),
    'creative': ('campaignId','adGroupId','ideaId'),
    'channels': ('product',),
    'keyword_hour': ('campaignId','adGroupId','wInfoId','hour'),
    'account_hour': ('hour',),
})
MAX_BYTES = 32 * 1024 * 1024


class QueryError(ValueError):
    """Stable business code; never include paths, raw data or credentials."""


def strict_json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:
                raise QueryError('INVALID_SNAPSHOT')
            result[key]=value
        return result
    def constant(_):
        raise QueryError('INVALID_SNAPSHOT')
    return json.loads(raw,parse_float=Decimal,parse_constant=constant,object_pairs_hook=pairs)


def day(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        raise QueryError("INVALID_DATE") from None


def date_range(start: str, end: str) -> list[str]:
    first, last = day(start), day(end)
    count = (last - first).days + 1
    if not 1 <= count <= 366:
        raise QueryError("INVALID_RANGE")
    return [(first + timedelta(days=i)).isoformat() for i in range(count)]


def number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise QueryError("INVALID_SNAPSHOT")
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result > Decimal('1e15'):
            raise InvalidOperation()
        return result
    except InvalidOperation:
        raise QueryError("INVALID_SNAPSHOT") from None


def metrics(rows):
    totals = {}
    for field in ("cost", "click", "impression"):
        values = [number(row.get(field)) for row in rows]
        totals[field] = sum(values, Decimal(0)) if values and None not in values else None
    cost, clicks, impressions = (totals[k] for k in ("cost", "click", "impression"))
    totals["cpc"] = cost / clicks if clicks and cost is not None else None
    totals["ctr"] = clicks / impressions if impressions and clicks is not None else None
    # Decimal strings avoid loss of precision in JSON clients. CTR is a fraction.
    return {k: format(v.quantize(Decimal('.000001')), 'f') if v is not None else None
            for k, v in totals.items()}


class ReleaseQuery:
    def __init__(self, release: Path, *, ttl=3600, clock=time.time):
        self.clock, self.loaded_at, self.ttl = clock, clock(), ttl
        self.secret = secrets.token_bytes(32)
        release = Path(release).resolve(strict=True)
        if not re.fullmatch(r"[0-9a-f]{32}", release.name):
            raise QueryError("INVALID_RELEASE")
        self.release_id = release.name
        self.data, self.hashes = {}, {}
        # All three files must validate before any query can be served.
        for account in ACCOUNTS:
            relative = "report/index.html" if account == "tinacms" else f"report/{account}/index.html"
            try:
                path = (release / relative).resolve(strict=True)
                if not path.is_relative_to(release):
                    raise QueryError("INVALID_RELEASE")
                with path.open('rb') as source:
                    raw = source.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise QueryError("SNAPSHOT_TOO_LARGE")
                matches = re.findall(rb'<script id="report-data" type="application/json">(.*?)</script>', raw, re.S)
                if len(matches) != 1:
                    raise QueryError("INVALID_SNAPSHOT")
                data = strict_json(matches[0])
                if not isinstance(data, dict) or not isinstance(data.get('reports'), dict):
                    raise QueryError("INVALID_SNAPSHOT")
                reports = {}
                for dimension, fields in DIMENSIONS.items():
                    if dimension not in data['reports']:
                        continue
                    rows = data['reports'][dimension]
                    if not isinstance(rows, list) or len(rows) > 200000:
                        raise QueryError("INVALID_SNAPSHOT")
                    clean = []
                    for row in rows:
                        if not isinstance(row, dict):
                            raise QueryError("INVALID_SNAPSHOT")
                        source_date = row.get('date')
                        if dimension in ('keyword_hour','account_hour'):
                            if not isinstance(source_date,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2} (?:[01]\d|2[0-3]):00',source_date):
                                raise QueryError('INVALID_SNAPSHOT')
                            if type(row.get('hour')) is not int or row['hour']!=int(source_date[11:13]):
                                raise QueryError('INVALID_SNAPSHOT')
                            source_date = source_date[:10]
                        day(source_date)
                        item = {'date': source_date}
                        for field in ('cost', 'click', 'impression'):
                            item[field] = number(row.get(field))
                            if field != 'cost' and item[field] is not None and item[field] != item[field].to_integral_value():
                                raise QueryError('INVALID_SNAPSHOT')
                        for field in fields:
                            value = row.get(field)
                            if field == 'ideaInfo' and isinstance(value,dict):
                                allowed=('ideaDesc','ideaDesc2','ideaId','ideaMShowURL','ideaShowURL','ideaTitle','ideaTitleStatus')
                                value={k:v for k,v in value.items() if k in allowed}
                                if any(type(v) not in (str,int) or (isinstance(v,str) and len(v)>2000) for v in value.values()):
                                    raise QueryError('INVALID_SNAPSHOT')
                                item[field]=value
                                continue
                            if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int))):
                                raise QueryError("INVALID_SNAPSHOT")
                            if isinstance(value, str) and len(value) > 2000:
                                raise QueryError("INVALID_SNAPSHOT")
                            item[field] = value
                        if any(item.get(key) is None for key in IDENTITIES[dimension]):
                            raise QueryError('INVALID_SNAPSHOT')
                        clean.append(item)
                    reports[dimension] = clean
                # Explicit allowlist: never retain private panels or unknown fields.
                self.data[account] = {'reports': reports,
                                      'generated': data.get('generated') if isinstance(data.get('generated'), str) and len(data['generated'])<=200 else None}
                from .analytics import extract
                self.data[account]['extra'] = extract(data)
                self.hashes[account] = hashlib.sha256(raw).hexdigest()
            except (OSError, json.JSONDecodeError, UnicodeError, TypeError, RecursionError):
                raise QueryError("INVALID_SNAPSHOT") from None

    def check(self):
        if self.clock() - self.loaded_at >= self.ttl:
            raise QueryError("SNAPSHOT_EXPIRED")

    def accounts(self):
        self.check()
        return {'release_id': self.release_id, 'mode': 'local_development',
                'accounts': [{'id': k, 'name': v, 'read_dimensions': list(self.data[k]['reports']),
                              'write_enabled': False} for k, v in ACCOUNTS.items()]}

    def status(self):
        self.check()
        return {'schema_version': '1', 'release_id': self.release_id,
                'fetched_at': datetime.fromtimestamp(self.loaded_at, timezone.utc).isoformat(),
                'expires_at': datetime.fromtimestamp(self.loaded_at + self.ttl, timezone.utc).isoformat(),
                'mode': 'local_development', 'live_collection_status': None,
                'sources': {a: {'sha256': self.hashes[a], 'generated_at': d['generated'],
                    'observed_dates': {dim: sorted({r['date'] for r in rows}) for dim, rows in d['reports'].items()}}
                    for a, d in self.data.items()},
                'warnings': ['LOCAL_RELEASE_COPY_NOT_LIVE', 'PRIVATE_SOURCES_NOT_CONNECTED']}

    def summary(self, account, start, end):
        """Account report totals, with an explicit reconciliation to overview's keyword report.

        The production overview currently sums keyword performance. These are two
        report dimensions, not interchangeable totals or components to add together.
        """
        result = self.performance(account, start, end)
        reconciliation = {
            'primary_source': 'baidu_ads.account',
            'dashboard_overview_source': 'baidu_ads.keyword',
            'comparison_available': False,
            'meaning': 'Account summary uses account-report rows; dashboard overview uses keyword-report rows. Compare the same dimension and dates; never add dimensions together.',
        }
        if 'keyword' in self.data[account]['reports']:
            alternate = self.performance(account, start, end, dimension='keyword', limit=1)
            differences = {}
            for field in ('cost', 'click', 'impression'):
                primary_value = result['observed_totals'][field]
                alternate_value = alternate['observed_totals'][field]
                differences[field] = (
                    format(Decimal(primary_value) - Decimal(alternate_value), 'f')
                    if primary_value is not None and alternate_value is not None else None
                )
            known = [Decimal(value) for value in differences.values() if value is not None]
            different = any(value != 0 for value in known)
            reconciliation.update(
                comparison_available=True,
                primary_observed_totals=dict(result['observed_totals']),
                dashboard_observed_totals=dict(alternate['observed_totals']),
                observed_difference_primary_minus_dashboard=differences,
                totals_differ=different,
                coverage_comparable=(result['missing_dates'] == alternate['missing_dates']
                                     and result['is_partial'] == alternate['is_partial']),
                dashboard_missing_dates=alternate['missing_dates'],
                dashboard_is_partial=alternate['is_partial'],
                primary_is_partial=result['is_partial'],
                complete_comparison=(not result['is_partial'] and not alternate['is_partial']),
            )
            if different:
                result['warnings'].append('ACCOUNT_AND_DASHBOARD_KEYWORD_TOTALS_DIFFER')
        else:
            result['warnings'].append('DASHBOARD_KEYWORD_COMPARISON_UNAVAILABLE')
        result['reconciliation'] = reconciliation
        return result

    def performance(self, account, start, end, dimension='account', limit=50, cursor=None,
                    filters=None, sort_by='cost', descending=True):
        self.check()
        days = date_range(start, end)
        if account not in ACCOUNTS:
            raise QueryError('UNKNOWN_ACCOUNT')
        if dimension not in DIMENSIONS or dimension not in self.data[account]['reports']:
            raise QueryError('UNSUPPORTED_DIMENSION')
        if type(limit) is not int or not 1 <= limit <= 200:
            raise QueryError('INVALID_LIMIT')
        if sort_by not in ('cost','click','impression','cpc','ctr') or type(descending) is not bool:
            raise QueryError('INVALID_SORT')
        if filters is None:
            filters = {}
        if not isinstance(filters, dict) or len(filters) > 8 or any(
            key not in DIMENSIONS[dimension] or key == 'ideaInfo' or not isinstance(value, str) or not 1 <= len(value) <= 2000
            for key, value in filters.items()
        ):
            raise QueryError('INVALID_FILTER')
        binding = [self.release_id, account, start, end, dimension, limit,
                   [[key, filters[key]] for key in sorted(filters)], sort_by, descending]
        offset = self.decode_cursor(cursor, binding) if cursor else 0
        rows = [r for r in self.data[account]['reports'][dimension] if start <= r['date'] <= end
                and all(r.get(key) is not None and str(r[key]) == value for key, value in filters.items())]
        present = sorted({r['date'] for r in rows})
        missing = sorted(set(days) - set(present))
        fields = DIMENSIONS[dimension]
        groups = {}
        for row in rows:
            key = tuple(row.get(f) for f in IDENTITIES[dimension])
            groups.setdefault(key, []).append(row)
        ranked = []
        for group in groups.values():
            # IDs define identity. Use the latest observed label if renamed.
            latest = max(group, key=lambda row: row['date'])
            ranked.append({'entity': {field: latest.get(field) for field in fields},
                           'metrics': metrics(group)})
        ranked.sort(key=lambda r: (r['metrics'][sort_by] is None,
                                  Decimal(r['metrics'][sort_by] or '0') * (-1 if descending else 1),
                                  json.dumps(r['entity'], ensure_ascii=False, sort_keys=True)))
        following = offset + limit
        return {'schema_version': '1', 'release_id': self.release_id,
                'source': f'baidu_ads.{dimension}', 'timezone': 'Asia/Shanghai', 'currency': 'CNY',
                'requested_range': {'start': start, 'end': end},
                'covered_range': {'start': present[0], 'end': present[-1]} if present else None,
                'missing_dates': missing, 'generated_at': self.data[account]['generated'],
                'fetched_at': datetime.fromtimestamp(self.loaded_at, timezone.utc).isoformat(),
                'is_partial': bool(missing) or any(r[k] is None for r in rows for k in ('cost','click','impression')),
                'warnings': ['LOCAL_RELEASE_COPY_NOT_LIVE', 'ABSENT_ROWS_ARE_NOT_ZERO',
                             'TOTALS_INCLUDE_OBSERVED_ROWS_ONLY', 'NO_KEYWORD_CONVERSION_ATTRIBUTION'],
                'applied_filters': {'account': account, 'dimension': dimension, **filters},
                'sort': {'field':sort_by, 'descending':descending, 'nulls':'last'},
                'observed_totals': metrics(rows), 'items': ranked[offset:following],
                'total_count': len(ranked),
                'next_cursor': self.encode_cursor(binding, following) if following < len(ranked) else None}

    def encode_cursor(self, binding, offset):
        raw = json.dumps({'binding': binding, 'offset': offset, 'expires': self.loaded_at + self.ttl}, separators=(',', ':')).encode()
        signed = hmac.new(self.secret, raw, hashlib.sha256).digest() + raw
        return base64.urlsafe_b64encode(signed).decode()

    def decode_cursor(self, cursor, binding):
        try:
            if not isinstance(cursor, str) or len(cursor) > 2048:
                raise ValueError()
            value = base64.b64decode(cursor, altchars=b'-_', validate=True)
            signature, raw = value[:32], value[32:]
            if not hmac.compare_digest(signature, hmac.new(self.secret, raw, hashlib.sha256).digest()):
                raise ValueError()
            data = json.loads(raw)
            if data['binding'] != binding or data['expires'] <= self.clock() or type(data['offset']) is not int or data['offset'] < 0:
                raise ValueError()
            return data['offset']
        except (ValueError, KeyError, TypeError):
            raise QueryError('INVALID_CURSOR') from None
