"""Additional bounded queries; no network, credentials, or core imports."""
from __future__ import annotations

from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
import re
import json

from .query import ACCOUNTS, QueryError, date_range, day, number

TRAFFIC = ('pv_count', 'visitor_count', 'ip_count', 'avg_visit_time', 'trans_count')
PAID = ('paid_sessions', 'paid_email_click_sessions', 'paid_contact_click_sessions', 'paid_install_copy_sessions')
ENTITIES = {
    'creative': ('creativeId','campaignId','adgroupId','title','description1','description2','pcDestinationUrl','mobileDestinationUrl','pause','status','getTemp'),
    'campaign': ('campaignId', 'campaignName', 'budget', 'pause', 'status', 'negativeWords', 'exactNegativeWords', 'schedule', 'regionTarget', 'campaignBidType', 'campaignBid', 'campaignOcpcBidType', 'campaignOcpcBid', 'equipmentType'),
    'adgroup': ('adgroupId', 'campaignId', 'adgroupName', 'maxPrice', 'pause', 'status', 'negativeWords', 'exactNegativeWords', 'getTemp'),
    'keyword': ('keywordId', 'campaignId', 'adgroupId', 'keyword', 'price', 'pause', 'status', 'matchType', 'phraseType', 'quality', 'getTemp'),
}


def plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value


def daily(rows, fields):
    if not isinstance(rows, list) or len(rows) > 1000:
        raise QueryError('INVALID_SNAPSHOT')
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise QueryError('INVALID_SNAPSHOT')
        value = day(row.get('date')).isoformat()
        if value in seen:
            raise QueryError('INVALID_SNAPSHOT')
        seen.add(value)
        clean = {'date': value}
        for field in fields:
            count = number(row.get(field))
            if count is not None and count != count.to_integral_value():
                raise QueryError('INVALID_SNAPSHOT')
            clean[field] = int(count) if count is not None else None
        result.append(clean)
    return sorted(result, key=lambda r: r['date'])


def extract(data):
    """Allowlist aggregate and inventory fields; intentionally exclude private HTML payloads."""
    result = {'inventory': {}, 'website': None, 'paid': {}}
    inventory = data.get('inventory', {})
    if not isinstance(inventory, dict):
        raise QueryError('INVALID_SNAPSHOT')
    for kind, fields in ENTITIES.items():
        rows = inventory.get(kind + '_inventory', [])
        if not isinstance(rows, list) or len(rows) > 100000:
            raise QueryError('INVALID_SNAPSHOT')
        clean = []
        for row in rows:
            if not isinstance(row, dict):
                raise QueryError('INVALID_SNAPSHOT')
            record = {}
            for field in fields:
                if field not in row:
                    continue
                value = row[field]
                if field.endswith('Id'):
                    if type(value) not in (str, int) or not re.fullmatch(r'[0-9]{1,24}',str(value)):
                        raise QueryError('INVALID_SNAPSHOT')
                    value = str(value)
                elif field=='regionTarget':
                    if not isinstance(value,list) or len(value)>1000 or any(type(v) is not int or v<0 for v in value):
                        raise QueryError('INVALID_SNAPSHOT')
                elif field in ('negativeWords', 'exactNegativeWords'):
                    if not isinstance(value, list) or len(value) > 10000 or any(not isinstance(w, str) or len(w)>1000 for w in value):
                        raise QueryError('INVALID_SNAPSHOT')
                elif field == 'schedule':
                    if not isinstance(value, list) or len(value)>168:
                        raise QueryError('INVALID_SNAPSHOT')
                    if any(not isinstance(v,dict) or not all(type(v.get(k)) is int for k in ('weekDay','startHour','endHour')) for v in value):
                        raise QueryError('INVALID_SNAPSHOT')
                    if any(not (1<=v['weekDay']<=7 and 0<=v['startHour']<v['endHour']<=24) for v in value):
                        raise QueryError('INVALID_SNAPSHOT')
                    value = [{k:v[k] for k in ('weekDay','startHour','endHour')} for v in value]
                elif field in ('campaignName','adgroupName','keyword'):
                    if not isinstance(value,str):
                        raise QueryError('INVALID_SNAPSHOT')
                elif field=='pause':
                    if type(value) is not bool:
                        raise QueryError('INVALID_SNAPSHOT')
                elif value is not None:
                    if isinstance(value,bool) or not isinstance(value,(str,int,float,Decimal)):
                        raise QueryError('INVALID_SNAPSHOT')
                    try:
                        parsed=Decimal(str(value))
                        if not parsed.is_finite() or not -1<=parsed<=Decimal('1e15'):
                            raise InvalidOperation()
                        if field not in ('budget','maxPrice','price','campaignBid') and parsed!=parsed.to_integral_value():
                            raise InvalidOperation()
                    except InvalidOperation:
                        raise QueryError('INVALID_SNAPSHOT') from None
                if isinstance(value, str) and len(value)>2000:
                    raise QueryError('INVALID_SNAPSHOT')
                record[field] = plain(value)
            if fields[0] not in record:
                raise QueryError('INVALID_SNAPSHOT')
            clean.append(record)
        result['inventory'][kind] = clean
    traffic = data.get('website_traffic')
    if traffic is not None:
        if not isinstance(traffic, dict):
            raise QueryError('INVALID_SNAPSHOT')
        ranges = traffic.get('ranges', [])
        if not isinstance(ranges, list) or len(ranges)>100:
            raise QueryError('INVALID_SNAPSHOT')
        clean_ranges = []
        seen = set()
        for row in ranges:
            if not isinstance(row, dict):
                raise QueryError('INVALID_SNAPSHOT')
            date_range(row.get('start'), row.get('end'))
            key = (row['start'],row['end'])
            if key in seen:
                raise QueryError('INVALID_SNAPSHOT')
            seen.add(key)
            values = daily([{'date': row['start'], **{f:row.get(f) for f in TRAFFIC}}], TRAFFIC)[0]
            values.pop('date')
            clean_ranges.append({'start':row['start'],'end':row['end'],**values})
        result['website'] = {'days':daily(traffic.get('days',[]), TRAFFIC), 'ranges':clean_ranges}
    for account, key in [('tinacms','tina_paid_website_conversions'), ('ssw','ssw_paid_website_conversions')]:
        if key in data:
            result['paid'][account] = daily(data[key], PAID)
    return result


class Analytics:
    def __init__(self, query):
        self.query = query

    def account(self, account):
        self.query.check()
        if account not in ACCOUNTS:
            raise QueryError('UNKNOWN_ACCOUNT')
        return self.query.data[account]['extra']

    def envelope(self, account, source, start, end, rows, fields):
        days = date_range(start,end)
        selected = [r for r in rows if start <= r['date'] <= end]
        indexed = {r['date']:r for r in selected}
        present = set(indexed)
        missing = {field: [d for d in days if indexed.get(d,{}).get(field) is None] for field in fields}
        totals = {field: sum(r[field] for r in selected) if not missing[field] else None for field in fields}
        return {'schema_version':'1','release_id':self.query.release_id,'source':source,
                'timezone':'Asia/Shanghai','requested_range':{'start':start,'end':end},
                'covered_range':{'start':min(present),'end':max(present)} if present else None,
                'missing_dates': sorted(set(days)-present), 'missing_dates_by_metric':missing,
                'generated_at':self.query.data[account]['generated'],
                'fetched_at':datetime.fromtimestamp(self.query.loaded_at,timezone.utc).isoformat(), 'is_partial':any(missing.values()),
                'applied_filters':{'account':account}, 'totals':totals,'days':selected,
                'warnings':['LOCAL_RELEASE_COPY_NOT_LIVE']}

    def website(self, account, start, end, traffic_scope='site_wide'):
        data = self.account(account)
        if traffic_scope == 'baidu_paid':
            if account not in data['paid']:
                raise QueryError('UNSUPPORTED_SOURCE')
            fields = ('paid_sessions','paid_email_click_sessions','paid_install_copy_sessions') if account=='tinacms' else ('paid_sessions','paid_contact_click_sessions')
            result = self.envelope(account,'baidu_tongji.paid',start,end,data['paid'][account],fields)
            # Return only fields that belong to this account's goals.
            result['days'] = [{k:v for k,v in r.items() if k=='date' or k in fields} for r in result['days']]
            result['warnings'] += ['ALL_CAMPAIGNS_ACCOUNT_SCOPE','GOAL_SESSIONS_NOT_LEADS_OR_INSTALLATIONS']
        elif traffic_scope == 'site_wide':
            if data['website'] is None:
                raise QueryError('UNSUPPORTED_SOURCE')
            source = data['website']
            result = self.envelope(account,'baidu_tongji.site_wide',start,end,source['days'],TRAFFIC)
            exact = next((r for r in source['ranges'] if r['start']==start and r['end']==end),None)
            if exact:
                result['daily_coverage'] = {key:result[key] for key in ('covered_range','missing_dates','missing_dates_by_metric','is_partial')}
                result['covered_range'] = {'start':start,'end':end}
                result['missing_dates'] = []
                result['missing_dates_by_metric'] = {f:[] if exact[f] is not None else date_range(start,end) for f in TRAFFIC}
                result['totals'] = {f:exact[f] for f in TRAFFIC}
                result['aggregation'] = 'exact_range'
                result['is_partial'] = any(exact[f] is None for f in TRAFFIC)
            else:
                result['aggregation'] = 'daily_single_date' if start==end else 'daily_additive_only'
                if start != end:
                    for field in ('visitor_count','ip_count','avg_visit_time'):
                        result['totals'][field] = None
                    result['warnings'].append('EXACT_RANGE_UV_IP_DURATION_UNAVAILABLE')
                    result['is_partial'] = True
        else:
            raise QueryError('UNSUPPORTED_SCOPE')
        result['traffic_scope'] = traffic_scope
        return result

    def entities(self, account, kind, entity_id=None, name=None, limit=50, cursor=None):
        data = self.account(account)
        if kind not in ENTITIES:
            raise QueryError('UNSUPPORTED_DIMENSION')
        if type(limit) is not int or not 1<=limit<=200:
            raise QueryError('INVALID_LIMIT')
        if entity_id is not None and (not isinstance(entity_id,str) or not re.fullmatch(r'[0-9]{1,24}',entity_id)):
            raise QueryError('INVALID_ENTITY_ID')
        if name is not None and (not isinstance(name,str) or not 1<=len(name)<=200):
            raise QueryError('INVALID_FILTER')
        fields = ENTITIES[kind]
        name_field = {'campaign':'campaignName','adgroup':'adgroupName','keyword':'keyword','creative':'title'}[kind]
        binding = [self.query.release_id, 'entities', account,kind,entity_id,name,limit]
        offset = self.query.decode_cursor(cursor,binding) if cursor else 0
        rows = [r for r in data['inventory'][kind] if (entity_id is None or r[fields[0]]==entity_id)
                and (name is None or name in (r.get(name_field) or ''))]
        rows = sorted(rows,key=lambda r:(int(r[fields[0]]),str(r.get('getTemp','')),json.dumps(r,sort_keys=True)))
        return {'schema_version':'1','release_id':self.query.release_id,'account':account,'kind':kind,
                'source':'inventory_snapshot','snapshot_at':None,
                'report_generated_at':self.query.data[account]['generated'],
                'items':rows[offset:offset+limit], 'total_count':len(rows),
                'next_cursor':self.query.encode_cursor(binding,offset+limit) if offset+limit<len(rows) else None,
                'warnings':['NOT_LIVE_SETTINGS','SNAPSHOT_TIME_UNKNOWN','GETTEMP_VARIANTS_NOT_MERGED','SCHEDULE_IS_PAUSED_INTERVALS']}

    def settings(self, account, kind, entity_id):
        result = self.entities(account,kind,entity_id=entity_id)
        if not result['items']:
            raise QueryError('ENTITY_NOT_FOUND')
        # Retain variants; do not silently choose a shadow record.
        result['ambiguous'] = result['total_count'] != 1
        return result

    def compare(self, account, start, end, previous_start, previous_end):
        first = self.query.performance(account,start,end)
        second = self.query.performance(account,previous_start,previous_end)
        equal = len(date_range(start,end)) == len(date_range(previous_start,previous_end))
        comparable = equal and not first['is_partial'] and not second['is_partial']
        changes = {}
        if comparable:
            for field, value in first['observed_totals'].items():
                old = second['observed_totals'][field]
                delta = Decimal(value)-Decimal(old) if value is not None and old is not None else None
                changes[field] = {'absolute':str(delta) if delta is not None else None,
                                  'relative':str(delta/Decimal(old)) if delta is not None and Decimal(old) else None}
        return {'current':first,'previous':second,'comparable':comparable,'changes':changes,
                'warnings':[] if comparable else ['UNEQUAL_OR_INCOMPLETE_PERIODS_NO_CHANGE_CLAIM']}

    def analyze(self, account, start, end):
        summary = self.query.performance(account,start,end)
        candidates = {}
        for dim in ('campaign','keyword','search_terms'):
            if dim in self.query.data[account]['reports']:
                result = self.query.performance(account,start,end,dim,limit=20)
                candidates[dim] = {'items':result['items'], 'is_partial':result['is_partial'],
                                   'missing_dates':result['missing_dates']}
        return {'summary':summary,'highest_cost_candidates':candidates,
                'warnings':['COST_RANKING_IS_NOT_PROOF_OF_WASTE',
                            'RELEVANCE_REQUIRES_BUSINESS_INTENT_REVIEW',
                            'NO_KEYWORD_GOAL_ATTRIBUTION_OR_AUTOMATIC_ACTION']}
