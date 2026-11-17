"""Durable fail-closed execution queue, independent of transport and providers.

A narrow provider adapter is separately assembled by runtime.py. Both a verified
per-operation capability and an independent write switch are needed.
"""
from __future__ import annotations
from dataclasses import dataclass,asdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import json
import re
import secrets
from typing import Literal, Protocol

from .changes import Principal, canonical, digest
from .query import QueryError
from .capabilities import CapabilityRegistry
from .creation import object_identity


@dataclass(frozen=True)
class Receipt:
    request_id: str
    created_entity_id: str | None = None


@dataclass(frozen=True)
class Observation:
    outcome: Literal['applied','not_applied','unknown']
    actual: dict
    evidence_reference: str
    authoritative: bool = False


class RejectedBeforeSend(Exception):
    """Adapter guarantees that no mutation request was sent."""


class WriteAdapter(Protocol):
    adapter_id: str
    def read(self,account: str,operation: dict) -> dict:
        """Read current fields and verified parent/mode preconditions, <=40s."""
    def submit(self,account: str,operation: dict,intent_id: str) -> Receipt:
        """Reread, reauthorize and send once; <=80s with bounded runtime identity."""
    def reconcile(self,account: str,operation: dict,intent_id: str,
                  receipt: Receipt | None,before: dict,after: dict) -> Observation:
        """Read-only authoritative reconciliation; old values alone do not prove failure."""


def match_fields(actual,expected,fields):
    for field in fields:
        if field not in actual or field not in expected:return False
        left,right=actual[field],expected[field]
        if field in ('budget','maxPrice','price','campaignBid','campaignOcpcBid'):
            try:
                if Decimal(str(left))!=Decimal(str(right)):return False
            except InvalidOperation:return False
        elif field.endswith('Id') and str(left)!=str(right):return False
        elif not field.endswith('Id') and left!=right:return False
    return True


class ExecutionQueue:
    def __init__(self,store,registry: CapabilityRegistry,adapter: WriteAdapter | None,
                 identity_resolver,*,mutations_enabled=lambda:False,lease_seconds=90,pre_execution_guard=None):
        self.store,self.registry,self.adapter=store,registry,adapter
        self.identity_resolver,self.enabled=identity_resolver,mutations_enabled
        self.lease_seconds=lease_seconds
        self.pre_execution_guard=pre_execution_guard
        with store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS execution_jobs(id TEXT PRIMARY KEY,plan_id TEXT,version INTEGER,
              plan_hash TEXT,requester TEXT,requester_version TEXT,account TEXT,state TEXT,
              frozen TEXT,claim TEXT,lease_until REAL,created_at REAL,
              UNIQUE(plan_id,version));
            CREATE TABLE IF NOT EXISTS execution_keys(key TEXT PRIMARY KEY,job_id TEXT,requester TEXT,plan_id TEXT,version INTEGER);
            CREATE TABLE IF NOT EXISTS execution_items(job_id TEXT,ordinal INTEGER,body TEXT,state TEXT,
              intent_id TEXT,receipt TEXT,result TEXT,PRIMARY KEY(job_id,ordinal));
            CREATE TABLE IF NOT EXISTS execution_locks(account TEXT,kind TEXT,entity_id TEXT,job_id TEXT,
              PRIMARY KEY(account,kind,entity_id));
            CREATE TABLE IF NOT EXISTS execution_spend(account TEXT,day TEXT,op TEXT,intent_id TEXT PRIMARY KEY,amount TEXT);
            CREATE TABLE IF NOT EXISTS execution_events(seq INTEGER PRIMARY KEY,job_id TEXT,at REAL,event TEXT,ordinal INTEGER,code TEXT);
            CREATE TABLE IF NOT EXISTS execution_late_receipts(job_id TEXT,ordinal INTEGER,intent_id TEXT,claim TEXT,receipt TEXT,at REAL);
            ''')

    def event(self,db,job,event,ordinal=None,code=None):
        db.execute('INSERT INTO execution_events(job_id,at,event,ordinal,code) VALUES(?,?,?,?,?)',
                   (job,self.store.clock(),event,ordinal,code))

    def fresh(self,actor):
        current=self.identity_resolver(actor.subject)
        if not isinstance(current,Principal) or current.subject!=actor.subject or current.version!=actor.version:
            raise QueryError('IDENTITY_CHANGED')
        return current

    def guard(self,plan,payload):
        if self.pre_execution_guard is not None:self.pre_execution_guard(plan,payload)
        elif 'automatic_rule' in payload:raise QueryError('AUTOMATIC_RULE_GUARD_REQUIRED')

    def approval(self,plan,actor):
        actor=self.fresh(actor);actor.require('changes:execute',plan['account'])
        if plan['state']!='approved':raise QueryError('APPROVAL_REQUIRED')
        if not plan['approval_expires'] or plan['approval_expires']<=self.store.clock():raise QueryError('APPROVAL_EXPIRED')
        payload=json.loads(plan['payload'])
        if digest(payload)!=plan['hash']:raise QueryError('PLAN_DIGEST_MISMATCH')
        owner=self.identity_resolver(plan['approval_actor'])
        if not isinstance(owner,Principal) or owner.subject!='kenshi' or owner.role!='owner' or owner.version!=plan['approval_version']:
            raise QueryError('APPROVER_IDENTITY_CHANGED')
        owner.require('changes:execute',plan['account'])
        if any(c['operation']['op']=='delete_entity' for c in payload['changes']):
            owner.require('changes:delete',plan['account'])
        author=self.identity_resolver(plan['author'])
        if not isinstance(author,Principal) or author.subject!=plan['author'] or author.version!=plan['author_version']:
            raise QueryError('AUTHOR_IDENTITY_CHANGED')
        author.require('changes:propose',plan['account'])
        self.guard(plan,payload)
        return payload

    def enqueue(self,actor,plan_id,version,idempotency_key):
        actor=self.fresh(actor)
        if not isinstance(idempotency_key,str) or not 16<=len(idempotency_key)<=128:
            raise QueryError('INVALID_IDEMPOTENCY_KEY')
        # Bridge requests must not run while holding the write transaction. The
        # exact approved record is checked again below after identity resolution.
        with self.store.db() as db:
            checked_plan=self.store.load(db,actor,plan_id)
            already=db.execute('SELECT id FROM execution_jobs WHERE plan_id=? AND version=?',(plan_id,version)).fetchone()
        payload=None if already else self.approval(checked_plan,actor)
        with self.store.db() as db:
            plan=self.store.load(db,actor,plan_id);actor.require('changes:execute',plan['account'])
            existing_key=db.execute('SELECT * FROM execution_keys WHERE key=?',(idempotency_key,)).fetchone()
            if existing_key and (existing_key['requester'],existing_key['plan_id'],existing_key['version'])!=(actor.subject,plan_id,version):
                raise QueryError('IDEMPOTENCY_CONFLICT')
            existing=db.execute('SELECT id FROM execution_jobs WHERE plan_id=? AND version=?',(plan_id,version)).fetchone()
            if existing:
                job_id=existing['id']
                db.execute('INSERT OR IGNORE INTO execution_keys VALUES(?,?,?,?,?)',(idempotency_key,job_id,actor.subject,plan_id,version))
            else:
                if plan['version']!=version:raise QueryError('STALE_PLAN_VERSION')
                if plan!=checked_plan or payload is None:raise QueryError('STALE_PLAN_VERSION')
                if plan['approval_expires']<=self.store.clock():raise QueryError('APPROVAL_EXPIRED')
                if not self.enabled() or self.adapter is None:raise QueryError('MUTATIONS_DISABLED')
                if not 1<=len(payload['changes'])<=20:raise QueryError('INVALID_OPERATION_COUNT')
                for change in payload['changes']:
                    if not change['changed_fields']:raise QueryError('NO_CHANGES')
                    operation=change['operation'];kind=operation.get('kind','campaign')
                    if operation['op']=='delete_entity':
                        if actor.role!='owner':raise QueryError('IRREVERSIBLE_OWNER_REQUIRED')
                        actor.require('changes:delete',plan['account'])
                    capability=self.registry.require(db,plan['account'],operation['op'],kind,self.adapter.adapter_id)
                    required=set(capability.precondition_fields)|(set() if change.get('creation') else set(change['changed_fields']))
                    if operation['op']=='delete_entity':required|=set(change['before'])-{'getTemp','quality'}
                    if any(field not in change['before'] for field in required):raise QueryError('DATA_INCOMPLETE')
                job_id=secrets.token_hex(16)
                frozen={'plan':plan,'payload':payload}
                db.execute('INSERT INTO execution_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (job_id,plan_id,version,plan['hash'],actor.subject,actor.version,plan['account'],'queued',canonical(frozen),None,None,self.store.clock()))
                for index,change in enumerate(payload['changes']):
                    db.execute('INSERT INTO execution_items VALUES(?,?,?,?,?,?,?)',(job_id,index,canonical(change),'pending',secrets.token_hex(16),None,None))
                db.execute('INSERT INTO execution_keys VALUES(?,?,?,?,?)',(idempotency_key,job_id,actor.subject,plan_id,version))
                self.event(db,job_id,'queued')
        return self.get(actor,job_id)

    def get(self,actor,job_id):
        actor=self.fresh(actor)
        with self.store.db() as db:
            row=db.execute('SELECT * FROM execution_jobs WHERE id=?',(job_id,)).fetchone()
            if not row or (row['requester']!=actor.subject and actor.role!='owner'):raise QueryError('JOB_NOT_FOUND')
            actor.require('changes:execute',row['account'])
            items=db.execute('SELECT ordinal,state,intent_id,receipt,result FROM execution_items WHERE job_id=? ORDER BY ordinal',(job_id,)).fetchall()
            events=db.execute('SELECT at,event,ordinal,code FROM execution_events WHERE job_id=? ORDER BY seq',(job_id,)).fetchall()
            late=db.execute('SELECT ordinal,intent_id,receipt,at FROM execution_late_receipts WHERE job_id=? ORDER BY at',(job_id,)).fetchall()
        return {'job_id':job_id,'plan_id':row['plan_id'],'version':row['version'],'state':row['state'],
                'items':[{**dict(r),'result':json.loads(r['result']) if r['result'] else None} for r in items],
                'events':[dict(r) for r in events],
                'late_receipts':[{**dict(r),'receipt':json.loads(r['receipt'])} for r in late]}

    def recover_expired(self):
        """Only after the worker lease expires; never resubmit an uncertain intent."""
        with self.store.db() as db:
            jobs=db.execute("SELECT id FROM execution_jobs WHERE state='running' AND lease_until<=?",(self.store.clock(),)).fetchall()
            for job in jobs:
                db.execute("UPDATE execution_items SET state='unknown' WHERE job_id=? AND state IN ('sending','verifying')",(job['id'],))
                db.execute("UPDATE execution_jobs SET state='recovering',claim=NULL,lease_until=NULL WHERE id=?",(job['id'],))
                self.event(db,job['id'],'lease_expired_reconcile_only')
        return len(jobs)

    def claim(self,job_id):
        with self.store.db() as db:
            row=db.execute('SELECT * FROM execution_jobs WHERE id=?',(job_id,)).fetchone()
            if not row or row['state'] not in ('queued','recovering','unknown'):return None
            items=db.execute('SELECT * FROM execution_items WHERE job_id=? ORDER BY ordinal',(job_id,)).fetchall()
            if row['state']=='queued' and (not self.enabled() or self.adapter is None):return None
            # Conservative hierarchy protection: an uncertain child creation or
            # update blocks every subsequent mutation on that account, including
            # parent pause/deletion. Unknown jobs retain this lock until resolved.
            held=db.execute("SELECT job_id FROM execution_locks WHERE account=? AND kind='account' AND entity_id='*'",(row['account'],)).fetchone()
            if held and held['job_id']!=job_id:return None
            for item in items:
                if item['state'] in ('succeeded','failed','stopped'):continue
                operation=json.loads(item['body'])['operation'];kind,object_id=object_identity(operation)
                held=db.execute('SELECT job_id FROM execution_locks WHERE account=? AND kind=? AND entity_id=?',(row['account'],kind,object_id)).fetchone()
                if held and held['job_id']!=job_id:return None
            for item in items:
                if item['state'] in ('succeeded','failed','stopped'):continue
                operation=json.loads(item['body'])['operation'];kind,object_id=object_identity(operation)
                db.execute('INSERT OR IGNORE INTO execution_locks VALUES(?,?,?,?)',(row['account'],kind,object_id,job_id))
            db.execute('INSERT OR IGNORE INTO execution_locks VALUES(?,?,?,?)',(row['account'],'account','*',job_id))
            claim=secrets.token_hex(16)
            db.execute("UPDATE execution_jobs SET state='running',claim=?,lease_until=? WHERE id=?",(claim,self.store.clock()+self.lease_seconds,job_id))
            return {**dict(row),'claim':claim},[dict(i) for i in items]

    def result(self,job,item,state,code,observation=None):
        body={'code':code}
        if observation:
            # Store only requested fields; no arbitrary provider response or errors.
            change=json.loads(item['body'])
            body['readback']={k:observation.actual[k] for k in change['changed_fields'] if k in observation.actual}
            body['evidence_reference']=observation.evidence_reference
            if change.get('creation'):
                kind=change['operation']['kind'];field={'campaign':'campaignId','adgroup':'adgroupId','keyword':'keywordId','creative':'creativeId'}[kind]
                body['created_entity_id']=str(observation.actual[field]) if field in observation.actual else None
        with self.store.db() as db:
            owned=db.execute('SELECT claim FROM execution_jobs WHERE id=?',(job['id'],)).fetchone()
            if not owned or owned['claim']!=job['claim']:return False
            db.execute('UPDATE execution_items SET state=?,result=? WHERE job_id=? AND ordinal=?',
                       (state,canonical(body),job['id'],item['ordinal']))
            self.event(db,job['id'],state,item['ordinal'],code)
            db.execute('UPDATE execution_jobs SET lease_until=? WHERE id=?',(self.store.clock()+self.lease_seconds,job['id']))
        return True

    def verify_item(self,job,item):
        if self.adapter is None:return self.result(job,item,'unknown','PROVIDER_UNAVAILABLE')
        change=json.loads(item['body'])
        if not item['receipt']:
            with self.store.db() as db:
                receipts=[row[0] for row in db.execute('SELECT DISTINCT receipt FROM execution_late_receipts WHERE job_id=? AND ordinal=? AND intent_id=?',(job['id'],item['ordinal'],item['intent_id']))]
            if len(receipts)==1:item={**item,'receipt':receipts[0]}
        if change.get('creation') and not item['receipt']:
            return self.result(job,item,'unknown','MANUAL_CREATED_ID_RECONCILIATION_REQUIRED')
        receipt=(Receipt(**json.loads(item['receipt'])) if item['receipt'].startswith('{') else Receipt(item['receipt'])) if item['receipt'] else None
        try:
            observation=self.adapter.reconcile(job['account'],change['operation'],item['intent_id'],receipt,change['before'],change['after'])
            if not isinstance(observation,Observation) or not observation.authoritative or not observation.evidence_reference:
                return self.result(job,item,'unknown','OUTCOME_UNKNOWN')
            if observation.outcome=='applied' and match_fields(observation.actual,change['after'],change['changed_fields']):
                return self.result(job,item,'succeeded','READBACK_CONFIRMED',observation)
            if observation.outcome=='not_applied':return self.result(job,item,'failed','AUTHORITATIVE_NOT_APPLIED',observation)
            return self.result(job,item,'unknown','OUTCOME_UNKNOWN')
        except Exception:
            return self.result(job,item,'unknown','RECONCILIATION_UNAVAILABLE')

    def intent(self,job,item,current):
        change=json.loads(item['body']);operation=change['operation'];kind,object_id=object_identity(operation)
        actor=self.identity_resolver(job['requester'])
        if not isinstance(actor,Principal) or actor.subject!=job['requester'] or actor.version!=job['requester_version']:raise QueryError('IDENTITY_CHANGED')
        # Identity bridge runs before the SQLite transaction; core never waits on MCP.
        with self.store.db() as db:
            plan=self.store.load(db,actor,job['plan_id'])
        self.approval(plan,actor)
        if operation['op']=='delete_entity':
            if actor.role!='owner':raise QueryError('IRREVERSIBLE_OWNER_REQUIRED')
            actor.require('changes:delete',plan['account'])
        if plan['version']!=job['version'] or plan['hash']!=job['plan_hash']:raise QueryError('STALE_PLAN_VERSION')
        with self.store.db() as db:
            owned=db.execute('SELECT claim FROM execution_jobs WHERE id=?',(job['id'],)).fetchone()
            fresh_plan=db.execute('SELECT version,hash,state,approval_expires FROM plans WHERE id=?',(job['plan_id'],)).fetchone()
            if not owned or owned['claim']!=job['claim']:raise QueryError('LEASE_LOST')
            if not fresh_plan or fresh_plan['version']!=job['version'] or fresh_plan['hash']!=job['plan_hash'] or fresh_plan['state']!='approved' or fresh_plan['approval_expires']<=self.store.clock():raise QueryError('STALE_PLAN_VERSION')
            if not self.enabled():raise QueryError('MUTATIONS_DISABLED')
            capability=self.registry.require(db,job['account'],operation['op'],kind,self.adapter.adapter_id)
            enum_fields={'set_campaign_regions':('regionTarget',),'set_campaign_devices':('equipmentType',),'set_keyword_match':('matchType','phraseType'),'create_keyword':('matchType','phraseType'),'set_campaign_bid_strategy':('campaignBidType','campaignOcpcBidType')}
            for field in enum_fields.get(operation['op'],()):
                if field not in change['changed_fields']:continue
                value=change['after'][field];values=value if isinstance(value,list) else [value]
                allowed=(capability.allowed_values or {}).get(field,())
                if not allowed or any(v not in allowed for v in values):raise QueryError('UNVERIFIED_ENUM_VALUE')
            fields=set(capability.precondition_fields)|(set() if change.get('creation') else set(change['changed_fields']))
            if operation['op']=='delete_entity':fields|=set(change['before'])-{'getTemp','quality'}
            if not match_fields(current,change['before'],fields):raise QueryError('STALE_PRECONDITION')
            if capability.money_field:
                delta=Decimal(0)
                for field in capability.money_fields:
                    value=Decimal(str(change['after'][field]));old=Decimal(0) if change.get('creation') else Decimal(str(current[field]))
                    if not value.is_finite() or not old.is_finite() or value<=0 or (old<=0 and not change.get('creation')):raise QueryError('UNSUPPORTED_MONEY_SEMANTICS')
                    adjustment=abs(value-old)
                    if value>Decimal(capability.max_target_cny) or adjustment>Decimal(capability.max_change_cny):raise QueryError('MONEY_LIMIT_EXCEEDED')
                    delta+=adjustment
                today=datetime.fromtimestamp(self.store.clock(),timezone(timedelta(hours=8))).date().isoformat()
                rows=db.execute('SELECT amount FROM execution_spend WHERE account=? AND day=? AND op=?',(job['account'],today,operation['op'])).fetchall()
                total=sum((Decimal(r['amount']) for r in rows),Decimal(0))
                if total+delta>Decimal(capability.daily_change_cny):raise QueryError('DAILY_LIMIT_EXCEEDED')
                db.execute('INSERT INTO execution_spend VALUES(?,?,?,?,?)',(job['account'],today,operation['op'],item['intent_id'],str(delta)))
            db.execute("UPDATE execution_items SET state='sending' WHERE job_id=? AND ordinal=?",(job['id'],item['ordinal']))
            db.execute('UPDATE execution_jobs SET lease_until=? WHERE id=?',(self.store.clock()+self.lease_seconds,job['id']))
            self.event(db,job['id'],'intent_persisted',item['ordinal'])
        return digest(asdict(capability))

    def run(self,job_id):
        claimed=self.claim(job_id)
        if claimed is None:return False
        job,items=claimed
        for item in items:
            if item['state'] in ('succeeded','failed','stopped'):continue
            if item['state'] in ('unknown','sending','verifying'):
                self.verify_item(job,item)
            else:
                change=json.loads(item['body'])
                try:
                    if self.adapter is None:raise QueryError('MUTATIONS_DISABLED')
                    current=self.adapter.read(job['account'],change['operation'])
                    capability_hash=self.intent(job,item,current)
                except QueryError as exc:
                    self.result(job,item,'stopped',str(exc));break
                except Exception:
                    self.result(job,item,'stopped','PRECONDITION_READ_UNAVAILABLE');break
                try:
                    frozen=json.loads(job['frozen'])
                    self.guard(frozen['plan'],frozen['payload'])
                except QueryError as exc:
                    self.result(job,item,'stopped',str(exc));break
                except Exception:
                    self.result(job,item,'stopped','EXECUTION_GUARD_UNAVAILABLE');break
                try:
                    with self.store.db() as db:
                        owned=db.execute('SELECT claim,lease_until FROM execution_jobs WHERE id=?',(job['id'],)).fetchone()
                        if not owned or owned['claim']!=job['claim'] or owned['lease_until']<=self.store.clock():raise QueryError('LEASE_LOST')
                    submit_authorized=getattr(self.adapter,'submit_authorized',None)
                    if submit_authorized:
                        receipt=submit_authorized(job['account'],change['operation'],item['intent_id'],lambda:self.authorize_submission(job,item,capability_hash))
                    else:
                        receipt=self.adapter.submit(job['account'],change['operation'],item['intent_id'])
                    if isinstance(receipt,Receipt) and receipt.created_entity_id is not None and not re.fullmatch(r'[0-9]{1,24}',receipt.created_entity_id):raise ValueError('INVALID_CREATED_ID')
                    if not isinstance(receipt,Receipt) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',receipt.request_id):
                        raise ValueError('INVALID_RECEIPT')
                    with self.store.db() as db:
                        owned=db.execute('SELECT claim FROM execution_jobs WHERE id=?',(job['id'],)).fetchone()
                        if not owned or owned['claim']!=job['claim']:
                            db.execute('INSERT INTO execution_late_receipts VALUES(?,?,?,?,?,?)',(job['id'],item['ordinal'],item['intent_id'],job['claim'],canonical(asdict(receipt)),self.store.clock()))
                            self.event(db,job['id'],'late_receipt_requires_reconciliation',item['ordinal'])
                            return False
                        db.execute("UPDATE execution_items SET state='verifying',receipt=? WHERE job_id=? AND ordinal=?",(canonical(asdict(receipt)),job['id'],item['ordinal']))
                    item['receipt']=canonical(asdict(receipt))
                    self.verify_item(job,item)
                except RejectedBeforeSend:
                    self.result(job,item,'failed','REJECTED_BEFORE_SEND')
                except Exception:
                    self.result(job,item,'unknown','OUTCOME_UNKNOWN')
            with self.store.db() as db:
                state=db.execute('SELECT state FROM execution_items WHERE job_id=? AND ordinal=?',(job_id,item['ordinal'])).fetchone()['state']
            if state!='succeeded':break
        with self.store.db() as db:
            owned=db.execute('SELECT claim FROM execution_jobs WHERE id=?',(job_id,)).fetchone()
            if not owned or owned['claim']!=job['claim']:return False
            states=[r[0] for r in db.execute('SELECT state FROM execution_items WHERE job_id=?',(job_id,))]
            if any(s in ('unknown','sending','verifying') for s in states):state='unknown'
            elif all(s=='succeeded' for s in states):state='succeeded'
            else:
                state='partial' if 'succeeded' in states else 'stopped'
                db.execute("UPDATE execution_items SET state='stopped',result=? WHERE job_id=? AND state='pending'",(canonical({'code':'PRIOR_ITEM_NOT_SUCCEEDED'}),job_id))
            db.execute('UPDATE execution_jobs SET state=?,claim=NULL,lease_until=NULL WHERE id=?',(state,job_id))
            if state!='unknown':db.execute('DELETE FROM execution_locks WHERE job_id=?',(job_id,))
            self.event(db,job_id,'job_'+state)
        return True

    def authorize_submission(self,job,item,capability_hash):
        """Recheck after the adapter's potentially slow final live reread."""
        actor=self.identity_resolver(job['requester'])
        if not isinstance(actor,Principal) or actor.subject!=job['requester'] or actor.version!=job['requester_version']:raise QueryError('IDENTITY_CHANGED')
        with self.store.db() as db:
            plan=self.store.load(db,actor,job['plan_id'])
        self.approval(plan,actor)
        change=json.loads(item['body']);operation=change['operation']
        if operation['op']=='delete_entity':
            if actor.role!='owner':raise QueryError('IRREVERSIBLE_OWNER_REQUIRED')
            actor.require('changes:delete',job['account'])
        with self.store.db() as db:
            latest=self.store.load(db,actor,job['plan_id'])
            claim=db.execute('SELECT claim,lease_until FROM execution_jobs WHERE id=?',(job['id'],)).fetchone()
            if latest!=plan or latest['version']!=job['version'] or latest['hash']!=job['plan_hash']:raise QueryError('STALE_PLAN_VERSION')
            if latest['approval_expires']<=self.store.clock():raise QueryError('APPROVAL_EXPIRED')
            if not claim or claim['claim']!=job['claim'] or claim['lease_until']<=self.store.clock():raise QueryError('LEASE_LOST')
            if not self.enabled():raise QueryError('MUTATIONS_DISABLED')
            capability=self.registry.require(db,job['account'],operation['op'],operation.get('kind','campaign'),self.adapter.adapter_id)
            if digest(asdict(capability))!=capability_hash:raise QueryError('CAPABILITY_CHANGED')
            if capability.money_field:
                for field in capability.money_fields:
                    amount=Decimal(str(change['after'][field]));before=Decimal(0) if change.get('creation') else Decimal(str(change['before'][field]))
                    if amount>Decimal(capability.max_target_cny) or abs(amount-before)>Decimal(capability.max_change_cny):raise QueryError('MONEY_LIMIT_EXCEEDED')
                day=datetime.fromtimestamp(self.store.clock(),timezone(timedelta(hours=8))).date().isoformat()
                spent=sum((Decimal(row[0]) for row in db.execute('SELECT amount FROM execution_spend WHERE account=? AND day=? AND op=?',(job['account'],day,operation['op']))),Decimal(0))
                if spent>Decimal(capability.daily_change_cny):raise QueryError('DAILY_LIMIT_EXCEEDED')

    def bind_created_entity_from_web(self,owner,job_id,ordinal,entity_id,evidence_reference):
        """Human-reviewed binding for uncertain creation; never infer by name.

        Only call from an owner-authenticated CSRF-protected page. This method is
        deliberately not an MCP tool. It performs reads and saves audit evidence;
        it never issues another add request.
        """
        owner=self.fresh(owner)
        if owner.subject!='kenshi' or owner.role!='owner':raise QueryError('INSUFFICIENT_ROLE')
        if not isinstance(entity_id,str) or not re.fullmatch(r'[0-9]{1,24}',entity_id):raise QueryError('INVALID_ENTITY_ID')
        if not isinstance(evidence_reference,str) or not 1<=len(evidence_reference)<=500:raise QueryError('EVIDENCE_REQUIRED')
        with self.store.db() as db:
            job=db.execute('SELECT * FROM execution_jobs WHERE id=?',(job_id,)).fetchone()
            item=db.execute('SELECT * FROM execution_items WHERE job_id=? AND ordinal=?',(job_id,ordinal)).fetchone()
        if not job or not item or job['state']!='unknown' or item['state']!='unknown':raise QueryError('RECONCILIATION_NOT_READY')
        owner.require('changes:execute',job['account'])
        change=json.loads(item['body'])
        if not change.get('creation') or self.adapter is None:raise QueryError('UNSUPPORTED_RECONCILIATION')
        # An owner's candidate ID and matching fields do not prove which API
        # request created it. Only a separately verified provider provenance
        # contract can turn this candidate into a receipt; Baidu's adapter does
        # not currently expose such a contract, so preserve unknown and locks.
        verifier=getattr(self.adapter,'verify_creation_provenance',None)
        receipt=verifier(job['account'],change['operation'],item['intent_id'],entity_id,evidence_reference) if verifier else None
        if not isinstance(receipt,Receipt) or receipt.created_entity_id!=entity_id:
            with self.store.db() as db:
                self.event(db,job_id,'owner_candidate_without_creation_provenance',ordinal,digest({'owner':owner.subject,'evidence':evidence_reference,'entity_id':entity_id}))
            return {'job_id':job_id,'ordinal':ordinal,'candidate_entity_id':entity_id,'state':'unknown','requires_upstream_creation_provenance':True,'resubmitted':False}
        observed=self.adapter.reconcile(job['account'],change['operation'],item['intent_id'],receipt,change['before'],change['after'])
        if not observed.authoritative or observed.outcome!='applied' or not match_fields(observed.actual,change['after'],change['changed_fields']):raise QueryError('CANDIDATE_DOES_NOT_MATCH')
        with self.store.db() as db:
            updated=db.execute("UPDATE execution_items SET receipt=? WHERE job_id=? AND ordinal=? AND state='unknown'",(canonical(asdict(receipt)),job_id,ordinal))
            if updated.rowcount!=1:raise QueryError('STALE_RECONCILIATION')
            self.event(db,job_id,'provider_proven_created_entity',ordinal,digest({'owner':owner.subject,'evidence':evidence_reference,'entity_id':entity_id}))
        return {'job_id':job_id,'ordinal':ordinal,'created_entity_id':entity_id,'state':'awaiting_readback_reconciliation','resubmitted':False}

    def compensation(self,actor,job_id,reason):
        actor=self.fresh(actor)
        with self.store.db() as db:
            job=db.execute('SELECT * FROM execution_jobs WHERE id=?',(job_id,)).fetchone()
            if not job or (job['requester']!=actor.subject and actor.role!='owner'):raise QueryError('JOB_NOT_FOUND')
            actor.require('changes:propose',job['account'])
            if job['state'] not in ('succeeded','partial'):raise QueryError('COMPENSATION_NOT_READY')
            items=db.execute("SELECT body,result FROM execution_items WHERE job_id=? AND state='succeeded' ORDER BY ordinal",(job_id,)).fetchall()
        from .analytics import Analytics
        operations=[]
        for item in items:
            change=json.loads(item['body']);original=change['operation'];before=change['before'];after=change['after']
            if original['op']=='delete_entity':raise QueryError('IRREVERSIBLE_NO_COMPENSATION')
            if change.get('creation'):
                outcome=json.loads(item['result']);new_id=outcome.get('created_entity_id')
                if not new_id:raise QueryError('CREATED_ENTITY_ID_UNAVAILABLE')
                snapshot=Analytics(self.store.query).settings(job['account'],original['kind'],new_id)
                records=[r for r in snapshot['items'] if r.get('getTemp',0)==0]
                if len(records)!=1 or not match_fields(records[0],after,change['changed_fields']):raise QueryError('POST_CREATION_OBJECT_CHANGED')
                if records[0].get('pause') is not True:
                    operations.append({'op':'set_pause','kind':original['kind'],'entity_id':new_id,'paused':True})
                continue
            settings=Analytics(self.store.query).settings(job['account'],original.get('kind','campaign'),original['entity_id'])
            current=[r for r in settings['items'] if r.get('getTemp',0)==0]
            if len(current)!=1 or not match_fields(current[0],after,change['changed_fields']):raise QueryError('WAITING_POST_CHANGE_SNAPSHOT')
            inverse=dict(original);op=original['op']
            if op=='set_campaign_budget':inverse['amount']=format(Decimal(str(before['budget'])),'.2f')
            elif op=='set_manual_bid':inverse['amount']=format(Decimal(str(before['price' if original['kind']=='keyword' else 'maxPrice'])),'.2f')
            elif op=='set_pause':inverse['paused']=before['pause']
            elif op=='set_campaign_regions':inverse['region_ids']=before['regionTarget']
            elif op=='set_campaign_schedule':inverse['paused_intervals']=before['schedule']
            elif op=='set_campaign_devices':inverse['equipment_type']=before['equipmentType']
            elif op=='update_creative':
                for key,field in (('title','title'),('description1','description1'),('description2','description2'),('pc_destination_url','pcDestinationUrl'),('mobile_destination_url','mobileDestinationUrl')):
                    if field not in before:raise QueryError('DATA_INCOMPLETE')
                    inverse[key]=before[field]
            elif op=='set_campaign_bid_strategy':
                inverse.update(campaign_bid_type=before['campaignBidType'],campaign_ocpc_bid_type=before['campaignOcpcBidType'],campaign_bid=format(Decimal(str(before['campaignBid'])),'.2f'),campaign_ocpc_bid=format(Decimal(str(before['campaignOcpcBid'])),'.2f'))
            elif op=='set_keyword_match':
                inverse['match_type']=before['matchType']
                inverse['phrase_type']=before.get('phraseType')
            else:
                field='exactNegativeWords' if original['match']=='exact' else 'negativeWords'
                inverse['op']='remove_negative_keywords' if op.startswith('add_') else 'add_negative_keywords'
                inverse['words']=[v for v in after[field] if v not in before[field]] if op.startswith('add_') else [v for v in before[field] if v not in after[field]]
                if not inverse['words']:continue
            operations.append(inverse)
        if not operations:raise QueryError('NO_COMPENSATION_NEEDED')
        # A fresh draft uses the current immutable snapshot and requires fresh web approval.
        result=self.store.create(actor,job['account'],operations,reason)
        result['compensates_job_id']=job_id
        result['warnings']=['NEW_APPROVAL_AND_LIVE_PRECONDITIONS_REQUIRED','CANNOT_REVERSE_SPEND_OR_EXPOSURES']
        return result
