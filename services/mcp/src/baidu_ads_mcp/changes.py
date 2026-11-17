"""MCP-owned proposal store. No provider writes; web approval is a separate path."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal,InvalidOperation
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from .analytics import Analytics
from .query import ACCOUNTS, QueryError


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


EntityID = Annotated[str, Field(pattern=r'^[0-9]{1,24}$')]
Money = Annotated[str, Field(pattern=r'^(0|[1-9][0-9]{0,9})(\.[0-9]{1,2})?$')]


class Budget(Strict):
    op: Literal['set_campaign_budget']
    entity_id: EntityID
    amount: Money


class Pause(Strict):
    op: Literal['set_pause']
    kind: Literal['campaign','adgroup','keyword','creative']
    entity_id: EntityID
    paused: bool


class Bid(Strict):
    op: Literal['set_manual_bid']
    kind: Literal['adgroup','keyword']
    entity_id: EntityID
    amount: Money


class Negatives(Strict):
    op: Literal['add_negative_keywords','remove_negative_keywords']
    kind: Literal['campaign','adgroup']
    entity_id: EntityID
    match: Literal['exact','phrase']
    words: list[Annotated[str, Field(min_length=1,max_length=100)]] = Field(min_length=1,max_length=100)


class Regions(Strict):
    op: Literal['set_campaign_regions']
    entity_id: EntityID
    region_ids: list[Annotated[int, Field(ge=1,le=2147483647)]] = Field(min_length=1,max_length=1000)


class Interval(Strict):
    weekDay: int = Field(ge=1,le=7)
    startHour: int = Field(ge=0,le=23)
    endHour: int = Field(ge=1,le=24)
    @model_validator(mode='after')
    def ordered(self):
        if self.endHour<=self.startHour:raise ValueError('Paused interval must have a positive duration')
        return self


class Schedule(Strict):
    op: Literal['set_campaign_schedule']
    entity_id: EntityID
    paused_intervals: list[Interval] = Field(max_length=168)
    @model_validator(mode='after')
    def no_overlap(self):
        intervals=sorted(self.paused_intervals,key=lambda x:(x.weekDay,x.startHour))
        for before,after in zip(intervals,intervals[1:]):
            if before.weekDay==after.weekDay and after.startHour<before.endHour:
                raise ValueError('Paused intervals overlap')
        return self


class Devices(Strict):
    op: Literal['set_campaign_devices']
    entity_id: EntityID
    equipment_type: int = Field(ge=0,le=2147483647,description='Provider equipmentType code; only separately verified capability values may execute')


class KeywordMatch(Strict):
    op: Literal['set_keyword_match']
    kind: Literal['keyword'] = 'keyword'
    entity_id: EntityID
    match_type: int = Field(ge=0,le=2147483647)
    phrase_type: int | None = Field(default=None,ge=0,le=2147483647)


class BidStrategy(Strict):
    op: Literal['set_campaign_bid_strategy']
    entity_id: EntityID
    campaign_bid_type: int = Field(ge=0,le=2147483647)
    campaign_ocpc_bid_type: int = Field(ge=0,le=2147483647)
    campaign_bid: Money
    campaign_ocpc_bid: Money


from .creation import CreateCampaign,CreateAdgroup,CreateKeyword,CreateCreative,UpdateCreative,DeleteEntity,creation_target,object_identity,parent_preconditions

Operation = Annotated[Budget | Pause | Bid | Negatives | Regions | Schedule | Devices | KeywordMatch | BidStrategy | CreateCampaign | CreateAdgroup | CreateKeyword | CreateCreative | UpdateCreative | DeleteEntity, Field(discriminator='op')]
OPERATIONS = TypeAdapter(list[Operation])


@dataclass(frozen=True)
class Principal:
    subject: str
    version: str
    role: str
    scopes: frozenset[str]
    accounts: frozenset[str] = frozenset(ACCOUNTS)

    def require(self, scope, account=None):
        if scope not in self.scopes or (account is not None and account not in self.accounts):
            raise QueryError('INSUFFICIENT_SCOPE')
        if (scope.startswith('changes:') or scope=='policies:manage') and self.role not in ('owner','operator'):
            raise QueryError('INSUFFICIENT_ROLE')


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class ChangeStore:
    APP_ID = 0x42414D43

    def __init__(self, path, query, clock=time.time):
        self.path, self.query, self.clock = Path(path), query, clock
        if self.path.exists():
            # Reject foreign/core databases before making any write or PRAGMA change.
            with sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True) as db:
                if db.execute('PRAGMA application_id').fetchone()[0] != self.APP_ID:
                    raise QueryError('FOREIGN_DATABASE')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute(f'PRAGMA application_id={self.APP_ID}')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, author TEXT, account TEXT,
                version INTEGER, state TEXT, payload TEXT, hash TEXT, author_version TEXT,
                approval_actor TEXT, approval_version TEXT, approval_expires REAL);
            CREATE TABLE IF NOT EXISTS import_jobs(id TEXT PRIMARY KEY,author TEXT,state TEXT,result TEXT);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, at REAL, actor TEXT,
                plan_id TEXT, action TEXT, version INTEGER, hash TEXT);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path,timeout=3,isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def event(self,db,actor,plan,action):
        db.execute('INSERT INTO events(at,actor,plan_id,action,version,hash) VALUES(?,?,?,?,?,?)',
                   (self.clock(),actor.subject,plan['id'],action,plan['version'],plan['hash']))

    def load(self,db,actor,plan_id):
        row = db.execute('SELECT * FROM plans WHERE id=?',(plan_id,)).fetchone()
        if row is None or (row['author'] != actor.subject and actor.role != 'owner'):
            raise QueryError('PLAN_NOT_FOUND')
        return dict(row)

    def public(self,plan):
        result = {k:plan[k] for k in ('id','author','account','version','state','hash','approval_actor','approval_expires')}
        result['proposal'] = json.loads(plan['payload'])
        result['execution_enabled'] = False
        result['execution_blockers'] = ['LIVE_CAPABILITY_NOT_VERIFIED','LIVE_PRECONDITIONS_REQUIRED','MUTATIONS_DISABLED']
        return result

    def prepare(self,account,operations,reason):
        self.query.check()
        if account not in ACCOUNTS:
            raise QueryError('UNKNOWN_ACCOUNT')
        if not isinstance(operations,list) or not 1<=len(operations)<=20:
            raise QueryError('INVALID_OPERATION_COUNT')
        try:
            values = [o.model_dump() for o in OPERATIONS.validate_python(operations)]
        except ValueError:
            raise QueryError('INVALID_OPERATION') from None
        if not isinstance(reason,str) or not 1<=len(reason.strip())<=2000:
            raise QueryError('INVALID_REASON')
        seen, changes = set(), []
        analytics = Analytics(self.query)
        for operation in values:
            kind,key_id = object_identity(operation)
            key = (kind,key_id)
            if key in seen:
                raise QueryError('DUPLICATE_ENTITY_SPLIT_PLAN')
            seen.add(key)
            if operation['op'].startswith('create_'):
                if operation['op']=='create_campaign':before=parent_preconditions(account,operation)
                else:
                    parent_kind='campaign' if operation['op']=='create_adgroup' else 'adgroup'
                    parent_id=operation['campaign_id'] if parent_kind=='campaign' else operation['adgroup_id']
                    parent=analytics.settings(account,parent_kind,parent_id)
                    base=[r for r in parent['items'] if r.get('getTemp',0)==0]
                    if len(base)!=1:raise QueryError('AMBIGUOUS_PARENT')
                    before=parent_preconditions(account,operation,base[0])
                after=creation_target(operation)
                changes.append({'operation':operation,'before':before,'after':after,'irreversible':False,'creation':True,
                    'changed_fields':{k:{'before':None,'after':v} for k,v in after.items()},
                    'requires_manual_reconciliation_on_unknown':True})
                continue
            snapshot = analytics.settings(account,kind,operation['entity_id'])
            # A getTemp=0 row is explicitly the base entity; unknown/duplicate base fails closed.
            rows = [r for r in snapshot['items'] if r.get('getTemp',0)==0]
            if len(rows)!=1:
                raise QueryError('AMBIGUOUS_ENTITY')
            before = rows[0]
            after = dict(before)
            op = operation['op']
            if op=='delete_entity':
                before={**before,'deleted':False};after={**before,'deleted':True}
            elif op=='update_creative':
                after.update(creation_target(operation))
            elif op=='set_campaign_budget':
                after['budget'] = operation['amount']
            elif op=='set_campaign_bid_strategy':
                for field,value in (('campaignBidType',operation['campaign_bid_type']),('campaignOcpcBidType',operation['campaign_ocpc_bid_type']),('campaignBid',operation['campaign_bid']),('campaignOcpcBid',operation['campaign_ocpc_bid'])):
                    if field not in before:raise QueryError('DATA_INCOMPLETE')
                    after[field]=value
            elif op=='set_campaign_regions':
                if 'regionTarget' not in before:raise QueryError('DATA_INCOMPLETE')
                after['regionTarget'] = list(dict.fromkeys(operation['region_ids']))
            elif op=='set_campaign_schedule':
                if 'schedule' not in before:raise QueryError('DATA_INCOMPLETE')
                after['schedule'] = operation['paused_intervals']
            elif op=='set_campaign_devices':
                if 'equipmentType' not in before:raise QueryError('DATA_INCOMPLETE')
                after['equipmentType'] = operation['equipment_type']
            elif op=='set_keyword_match':
                if 'matchType' not in before:raise QueryError('DATA_INCOMPLETE')
                after['matchType'] = operation['match_type']
                if operation['phrase_type'] is not None:
                    if 'phraseType' not in before:raise QueryError('DATA_INCOMPLETE')
                    after['phraseType'] = operation['phrase_type']
            elif op=='set_pause':
                after['pause'] = operation['paused']
            elif op=='set_manual_bid':
                after['price' if kind=='keyword' else 'maxPrice'] = operation['amount']
            else:
                field = 'exactNegativeWords' if operation['match']=='exact' else 'negativeWords'
                if field not in before:
                    raise QueryError('DATA_INCOMPLETE')
                words = list(dict.fromkeys(operation['words']))
                if any(not w.strip() for w in words):
                    raise QueryError('INVALID_OPERATION')
                after[field] = list(dict.fromkeys(before[field]+words)) if op.startswith('add_') else [w for w in before[field] if w not in words]
            changed={}
            for field,value in after.items():
                equal=before.get(field)==value
                if field in ('budget','maxPrice','price','campaignBid','campaignOcpcBid') and field in before:
                    try:equal=Decimal(str(before[field]))==Decimal(str(value))
                    except InvalidOperation:pass
                if not equal:changed[field]={'before':before.get(field),'after':value}
            if not changed:raise QueryError('NO_CHANGES')
            changes.append({'operation':operation,'before':before,'after':after,'irreversible':op=='delete_entity',
                            'changed_fields':changed})
        return {'release_id':self.query.release_id,'reason':reason,'changes':changes,
                'precondition_source':'historical_inventory_snapshot','snapshot_at':None}

    def create(self,actor,account,operations,reason):
        actor.require('changes:propose',account)
        payload = self.prepare(account,operations,reason)
        plan = {'id':secrets.token_hex(16),'author':actor.subject,'account':account,'version':1,
                'state':'draft','payload':canonical(payload),'hash':digest(payload),'author_version':actor.version,
                'approval_actor':None,'approval_version':None,'approval_expires':None}
        with self.db() as db:
            db.execute('INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,?,?,?)',tuple(plan.values()))
            self.event(db,actor,plan,'created')
        return self.public(plan)

    def get(self,actor,plan_id):
        with self.db() as db:
            plan = self.load(db,actor,plan_id)
        return self.public(plan)

    def revise(self,actor,plan_id,version,operations,reason):
        with self.db() as db:
            plan = self.load(db,actor,plan_id)
            # Owner review/cancellation does not transfer authorship. Keeping the
            # original subject with an owner's version would break execution-time
            # author validation and obscure who prepared the changed proposal.
            if plan['author'] != actor.subject:
                raise QueryError('PLAN_AUTHOR_REQUIRED')
            actor.require('changes:propose',plan['account'])
            if plan['version']!=version or plan['state']=='cancelled':
                raise QueryError('STALE_PLAN_VERSION')
            payload = self.prepare(plan['account'],operations,reason)
            plan.update(version=version+1,state='draft',payload=canonical(payload),hash=digest(payload),
                        author_version=actor.version,approval_actor=None,approval_version=None,approval_expires=None)
            db.execute('UPDATE plans SET version=?,state=?,payload=?,hash=?,author_version=?,approval_actor=NULL,approval_version=NULL,approval_expires=NULL WHERE id=?',
                       (plan['version'],plan['state'],plan['payload'],plan['hash'],actor.version,plan_id))
            self.event(db,actor,plan,'revised_approval_invalidated')
        return self.public(plan)

    def request_approval(self,actor,plan_id,version):
        with self.db() as db:
            plan = self.load(db,actor,plan_id)
            actor.require('changes:propose',plan['account'])
            if plan['version']!=version or plan['state'] not in ('draft','awaiting_approval'):
                raise QueryError('STALE_PLAN_VERSION')
            plan['state']='awaiting_approval'
            db.execute('UPDATE plans SET state=? WHERE id=?',(plan['state'],plan_id))
            self.event(db,actor,plan,'approval_requested')
        return self.public(plan)

    def approve_from_web(self,actor,plan_id,version,expected_hash):
        # Only the authenticated, CSRF-protected web route calls this method.
        if actor.role!='owner' or actor.subject!='kenshi':
            raise QueryError('INSUFFICIENT_ROLE')
        with self.db() as db:
            plan = self.load(db,actor,plan_id)
            actor.require('changes:execute',plan['account'])
            if any(c['operation']['op']=='delete_entity' for c in json.loads(plan['payload'])['changes']):
                actor.require('changes:delete',plan['account'])
            if plan['state']!='awaiting_approval' or plan['version']!=version or plan['hash']!=expected_hash:
                raise QueryError('STALE_PLAN_VERSION')
            plan.update(state='approved',approval_actor=actor.subject,approval_version=actor.version,approval_expires=self.clock()+1800)
            db.execute('UPDATE plans SET state=?,approval_actor=?,approval_version=?,approval_expires=? WHERE id=?',
                       (plan['state'],actor.subject,actor.version,plan['approval_expires'],plan_id))
            self.event(db,actor,plan,'proposal_approved_no_live_execution')
        return self.public(plan)

    def cancel(self,actor,plan_id,version):
        with self.db() as db:
            plan = self.load(db,actor,plan_id)
            actor.require('changes:propose',plan['account'])
            if plan['version']!=version:
                raise QueryError('STALE_PLAN_VERSION')
            plan['state']='cancelled'
            db.execute('UPDATE plans SET state=?,approval_actor=NULL,approval_version=NULL,approval_expires=NULL WHERE id=?',(plan['state'],plan_id))
            self.event(db,actor,plan,'cancelled')
        return self.get(actor,plan_id)

    def execute(self,actor,plan_id,version,idempotency_key):
        with self.db() as db:
            plan = self.load(db,actor,plan_id)
            actor.require('changes:execute',plan['account'])
            if plan['version']!=version:
                raise QueryError('STALE_PLAN_VERSION')
            if plan['state']!='approved':
                raise QueryError('APPROVAL_REQUIRED')
            if plan['approval_expires']<=self.clock():
                raise QueryError('APPROVAL_EXPIRED')
            if plan['author_version']!=actor.version and plan['author']==actor.subject:
                raise QueryError('IDENTITY_CHANGED')
            # No provider adapter is enabled. Never pretend a job was queued.
            raise QueryError('MUTATIONS_DISABLED')

    def history(self,actor,plan_id):
        with self.db() as db:
            self.load(db,actor,plan_id)
            rows = db.execute('SELECT at,actor,action,version,hash FROM events WHERE plan_id=? ORDER BY seq DESC LIMIT 200',(plan_id,)).fetchall()
        return {'plan_id':plan_id,'events':[dict(r) for r in rows],'limit':200}
