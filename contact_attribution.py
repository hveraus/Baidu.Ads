"""SSW contact-goal sessions, partitioned by Tongji's exhaustive source report."""
import datetime as dt
import json
from pathlib import Path

FILENAME = 'website-contact-attribution.json'
FIELDS = ('sessions', 'contact_click_sessions', 'paid_sessions',
          'paid_contact_click_sessions', 'natural_contact_click_sessions')
PAID_SOURCE = 'searchBaiduProFc'


def count(value):
    if value is None or value in ('--', '—', ''):
        return None
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    raise ValueError('Invalid aggregate count')


def parse_sources(report):
    if report.get('fields') != ['source_type_title', 'visit_count', 'trans_count']:
        raise ValueError('Unexpected source report fields')
    dimensions, values = report['items'][:2]
    if type(report.get('total')) is not int or report['total'] != len(dimensions) or len(dimensions) != len(values):
        raise ValueError('Incomplete source partition')
    raw_totals = report['sum'][0]
    if len(raw_totals) != 2:
        raise ValueError('Invalid source totals')
    totals = [count(v) for v in raw_totals]
    rows = {}
    for dimension, raw in zip(dimensions, values):
        source = dimension[0]['source']
        if not isinstance(source, str) or not source.strip() or source in rows or len(raw) != 2:
            raise ValueError('Invalid or duplicate source classification')
        rows[source] = [count(v) for v in raw]
    # A dash alone is unknown. A numeric total and a complete non-negative
    # partition can prove that all remaining cells are zero.
    for i, total in enumerate(totals):
        known = sum(v[i] for v in rows.values() if v[i] is not None)
        missing = any(v[i] is None for v in rows.values())
        if total is not None:
            if known > total or (not missing and known != total):
                raise ValueError('Source totals do not reconcile')
            if known == total:
                for v in rows.values():
                    if v[i] is None:
                        v[i] = 0
    visits, goal = totals
    visits_complete = visits is not None and all(v[0] is not None for v in rows.values()) and sum(v[0] for v in rows.values()) == visits
    if PAID_SOURCE in rows:
        paid_visits, paid_goal = rows[PAID_SOURCE]
    elif visits_complete:
        paid_visits, paid_goal = 0, 0
    else:
        paid_visits, paid_goal = None, None
    if paid_visits == 0:
        paid_goal = 0
    result = {'sessions': visits, 'contact_click_sessions': goal,
              'paid_sessions': paid_visits, 'paid_contact_click_sessions': paid_goal,
              'natural_contact_click_sessions': None}
    derive_natural(result)
    return result


def derive_natural(row):
    total, paid = row['contact_click_sessions'], row['paid_contact_click_sessions']
    if total is not None and paid is not None:
        if paid > total:
            raise ValueError('Paid goals exceed all-source goals')
        row['natural_contact_click_sessions'] = total - paid


def validate_rows(rows):
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError('Invalid contact rows')
    seen = set()
    for row in rows:
        if set(row) != {'date', *FIELDS} or dt.date.fromisoformat(row['date']).isoformat() != row['date'] or row['date'] in seen:
            raise ValueError('Invalid contact date or fields')
        seen.add(row['date'])
        for field in FIELDS:
            v = row[field]
            if v is not None and (type(v) is not int or not 0 <= v <= 1_000_000_000):
                raise ValueError('Invalid contact count')
        for total, part in [('sessions', 'contact_click_sessions'), ('sessions', 'paid_sessions'), ('paid_sessions', 'paid_contact_click_sessions')]:
            if row[total] is not None and row[part] is not None and row[part] > row[total]:
                raise ValueError('Contact count exceeds session count')
        a, p, n = (row[k] for k in ('contact_click_sessions', 'paid_contact_click_sessions', 'natural_contact_click_sessions'))
        if all(v is not None for v in (a, p, n)) and p + n != a:
            raise ValueError('Contact groups do not reconcile')
    return sorted(rows, key=lambda r: r['date'])


def fetch_rows(config, start, end, request):
    if end < start or (end - start).days >= 90:
        raise ValueError('Contact query must cover 1-90 dates')
    def get(first, last):
        report = request(config, 'getData', {
            'site_id': config['site_id'], 'method': 'source/all/a', 'viewType': 'type',
            'start_date': first.strftime('%Y%m%d'), 'end_date': last.strftime('%Y%m%d'),
            'metrics': 'visit_count,trans_count', 'target': config['contact_click_target'], 'max_results': 0,
        })['result']
        return parse_sources(report)
    total = get(start, end)
    rows = [{'date': (start + dt.timedelta(days=i)).isoformat(), **get(start + dt.timedelta(days=i), start + dt.timedelta(days=i))}
            for i in range((end - start).days + 1)]
    for field in FIELDS[:-1]:
        known = sum(r[field] for r in rows if r[field] is not None)
        missing = any(r[field] is None for r in rows)
        value = total[field]
        if value is not None:
            if known > value or (not missing and known != value):
                raise ValueError('Daily contact totals changed or do not reconcile; retry later')
            if known == value:
                for r in rows:
                    if r[field] is None:
                        r[field] = 0
    for row in rows:
        derive_natural(row)
    return validate_rows(rows)


def load_snapshot(path):
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('schema_version') != 1 or value.get('source') != 'baidu_tongji.source/all/a' or value.get('paid_source') != PAID_SOURCE:
        raise ValueError('Invalid contact snapshot')
    validate_rows(value['rows'])
    return value
