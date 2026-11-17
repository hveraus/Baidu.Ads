"""Explicit owner-preauthorized, bounded template automation. Default disabled.

This is deliberately separate from observation and recommendation rules. An
approval is bound to an entire immutable definition; execution always uses the
normal queue, capability verification, identity checks and reconciliation.
"""
from contextlib import closing
import json
import secrets
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field
from .changes import Strict, Money, canonical, digest
from .operations import BEIJING
from .query import QueryError, date_range, day
from .rule_engine import DecisionRules


class AutomaticRuleInput(Strict):
    account: Literal['tinacms','ssw','yakshaver']
    name: str = Field(min_length=1,max_length=100)
    kind: Literal['campaign','adgroup','keyword']
    entity_ids: list[str] = Field(min_length=1,max_length=20)
    action: Literal['pause','resume','set_campaign_budget','set_manual_bid'] = 'pause'
    target_amount: Money | None = None
    maximum_target_cny: Money = '0.00'
    maximum_action_change_cny: Money = '0.00'
    maximum_daily_change_cny: Money = '0.00'
    maximum_total_change_cny: Money = '0.00'
    maximum_budget_increase_cny: Money = '0.00'
    cost_threshold: Money
    minimum_clicks: int = Field(ge=0,le=100000000)
    maximum_observed_cost: Money
    lookback_days: int = Field(default=1,ge=1,le=30)
    interval_seconds: int = Field(default=86400,ge=300,le=7*86400,description='Scheduler polling cadence; at most one immutable action batch per rule version and Beijing date')
    cooldown_seconds: int = Field(default=86400,ge=300,le=30*86400)
    maximum_actions_per_run: int = Field(ge=1,le=20)
    maximum_actions_per_day: int = Field(ge=1,le=20)
    maximum_actions_total: int = Field(ge=1,le=1000)
    expires_in_days: int = Field(default=7,ge=1,le=30)
    stop_on_incomplete_data: Literal[True] = True
    stop_on_unknown_result: Literal[True] = True
    stop_on_identity_change: Literal[True] = True


class AutomaticRules:
    def __init__(self,store,queue=None,enabled=lambda:False):
        self.store,self.queue,self.enabled=store,queue,enabled
        # Reuse only the pure date-coverage/metric calculations, not rule modes.
        self.facts=DecisionRules.facts.__get__(self)
        with store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS automatic_rules(id TEXT PRIMARY KEY,owner TEXT,identity_version TEXT,
                version INTEGER,body TEXT,hash TEXT,state TEXT,expires REAL,approved_version INTEGER,
                approved_hash TEXT,approved_identity TEXT,next_due REAL,stop_reason TEXT);
            CREATE TABLE IF NOT EXISTS automatic_runs(id TEXT PRIMARY KEY,rule_id TEXT,version INTEGER,
                hash TEXT,plan_id TEXT UNIQUE,job_id TEXT,day TEXT,at REAL,entity_ids TEXT,amount_cny TEXT,evidence TEXT,
                UNIQUE(rule_id,version,day));
            CREATE TABLE IF NOT EXISTS automatic_events(seq INTEGER PRIMARY KEY,rule_id TEXT,at REAL,
                actor TEXT,event TEXT,version INTEGER,hash TEXT);
            ''')

    def require(self,actor,account=None):
        if actor.subject!='kenshi' or actor.role!='owner':raise QueryError('AUTOMATIC_OWNER_REQUIRED')
        for scope in ('policies:automate','analytics:read','settings:read','changes:propose','changes:execute'):
            actor.require(scope,account)

    def definition(self,actor,values):
        try:rule=AutomaticRuleInput(**values).model_dump()
        except ValueError:raise QueryError('INVALID_AUTOMATIC_RULE') from None
        self.require(actor,rule['account'])
        identifiers=rule['entity_ids']
        if len(set(identifiers))!=len(identifiers) or any(not v.isascii() or not v.isdigit() or len(v)>24 for v in identifiers):
            raise QueryError('INVALID_ENTITY_ID')
        if not rule['maximum_actions_per_run']<=rule['maximum_actions_per_day']<=rule['maximum_actions_total']:
            raise QueryError('INVALID_AUTOMATIC_LIMITS')
        if Decimal(rule['maximum_observed_cost'])<Decimal(rule['cost_threshold']):
            raise QueryError('INVALID_AUTOMATIC_LIMITS')
        if rule['action'] in ('set_campaign_budget','set_manual_bid'):
            if rule['target_amount'] is None or not Decimal('0')<Decimal(rule['target_amount'])<=Decimal(rule['maximum_target_cny']):
                raise QueryError('INVALID_AUTOMATIC_LIMITS')
            if not Decimal('0')<Decimal(rule['maximum_action_change_cny'])<=Decimal(rule['maximum_daily_change_cny'])<=Decimal(rule['maximum_total_change_cny']):
                raise QueryError('INVALID_AUTOMATIC_LIMITS')
            if (rule['action']=='set_campaign_budget' and rule['kind']!='campaign') or (rule['action']=='set_manual_bid' and rule['kind'] not in ('adgroup','keyword')):
                raise QueryError('INVALID_AUTOMATIC_OPERATION')
        elif rule['target_amount'] is not None or any(Decimal(rule[k])!=0 for k in ('maximum_target_cny','maximum_action_change_cny','maximum_daily_change_cny','maximum_total_change_cny','maximum_budget_increase_cny')):
            raise QueryError('INVALID_AUTOMATIC_LIMITS')
        from .analytics import Analytics
        for identifier in identifiers:
            rows=Analytics(self.store.query).settings(rule['account'],rule['kind'],identifier)['items']
            if len([r for r in rows if r.get('getTemp',0)==0])!=1:raise QueryError('AMBIGUOUS_ENTITY')
        return rule

    def event(self,db,row,actor,event):
        db.execute('INSERT INTO automatic_events(rule_id,at,actor,event,version,hash) VALUES(?,?,?,?,?,?)',
                   (row['id'],self.store.clock(),actor.subject,event,row['version'],row['hash']))

    def load(self,db,actor,rule_id):
        row=db.execute('SELECT * FROM automatic_rules WHERE id=?',(rule_id,)).fetchone()
        if not row:raise QueryError('AUTOMATIC_RULE_NOT_FOUND')
        rule=json.loads(row['body']);self.require(actor,rule['account'])
        return dict(row),rule

    def create(self,actor,**values):
        rule=self.definition(actor,values);identifier=secrets.token_hex(16)
        row={'id':identifier,'owner':actor.subject,'identity_version':actor.version,'version':1,
             'body':canonical(rule),'hash':digest(rule),'state':'pending_approval',
             'expires':self.store.clock()+rule['expires_in_days']*86400,'approved_version':None,
             'approved_hash':None,'approved_identity':None,'next_due':None,'stop_reason':None}
        with self.store.db() as db:
            if db.execute('SELECT count(*) FROM automatic_rules').fetchone()[0]>=100:raise QueryError('POLICY_STORE_CAPACITY')
            db.execute('INSERT INTO automatic_rules VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',tuple(row.values()))
            self.event(db,row,actor,'created_pending_web_approval')
        return self.get(actor,identifier)

    def get(self,actor,rule_id):
        with self.store.db() as db:
            row,rule=self.load(db,actor,rule_id)
            runs=[dict(r) for r in db.execute('SELECT id,version,plan_id,job_id,day,at,entity_ids,amount_cny,evidence FROM automatic_runs WHERE rule_id=? ORDER BY at DESC LIMIT 100',(rule_id,))]
        return {'rule_id':rule_id,'version':row['version'],'hash':row['hash'],'definition':rule,
                'state':row['state'],'expires_at':row['expires'],'next_due':row['next_due'],
                'stop_reason':row['stop_reason'],'approved_version':row['approved_version'],
                'automatic_switch_enabled':bool(self.enabled()),
                'execution_switch_enabled':bool(self.queue and self.queue.enabled()),
                'maximum_batches_per_beijing_date':1,
                'approval_path':'/automatic-rules/'+rule_id,'runs':runs}

    def update(self,actor,rule_id,version,**values):
        rule=self.definition(actor,values)
        with self.store.db() as db:
            row,_=self.load(db,actor,rule_id)
            if row['version']!=version:raise QueryError('STALE_RULE_VERSION')
            row.update(version=version+1,body=canonical(rule),hash=digest(rule),identity_version=actor.version)
            db.execute('UPDATE automatic_rules SET version=?,body=?,hash=?,identity_version=?,state=?,expires=?,approved_version=NULL,approved_hash=NULL,approved_identity=NULL,next_due=NULL,stop_reason=NULL WHERE id=?',
                       (row['version'],row['body'],row['hash'],actor.version,'pending_approval',self.store.clock()+rule['expires_in_days']*86400,rule_id))
            self.event(db,row,actor,'updated_approval_invalidated')
        return self.get(actor,rule_id)

    def approve_from_web(self,actor,rule_id,version,rule_hash):
        # Called only from the authenticated Origin/CSRF checked owner page.
        with self.store.db() as db:
            row,rule=self.load(db,actor,rule_id)
            if row['version']!=version or row['hash']!=rule_hash or digest(rule)!=rule_hash:
                raise QueryError('STALE_RULE_VERSION')
            if row['identity_version']!=actor.version:raise QueryError('IDENTITY_CHANGED')
            if row['expires']<=self.store.clock():raise QueryError('RULE_EXPIRED')
            if row['state']!='pending_approval':raise QueryError('RULE_NOT_PENDING_APPROVAL')
            db.execute("UPDATE automatic_rules SET state='approved',approved_version=version,approved_hash=hash,approved_identity=?,next_due=? WHERE id=?",
                       (actor.version,self.store.clock(),rule_id))
            self.event(db,row,actor,'owner_web_approved')
        return self.get(actor,rule_id)

    def pause(self,actor,rule_id):
        with self.store.db() as db:
            row,_=self.load(db,actor,rule_id)
            db.execute("UPDATE automatic_rules SET state='paused',approved_version=NULL,approved_hash=NULL,next_due=NULL WHERE id=?",(rule_id,))
            self.event(db,row,actor,'paused_approval_invalidated')
        return self.get(actor,rule_id)

    def check(self,row,rule,actor=None):
        if row['state']!='approved' or row['approved_version']!=row['version'] or row['approved_hash']!=row['hash']:
            raise QueryError('AUTOMATIC_APPROVAL_REQUIRED')
        if digest(rule)!=row['hash']:raise QueryError('RULE_DIGEST_MISMATCH')
        if row['expires']<=self.store.clock():raise QueryError('RULE_EXPIRED')
        if row['approved_identity']!=row['identity_version'] or (actor and actor.version!=row['identity_version']):
            raise QueryError('IDENTITY_CHANGED')

    def totals(self,db,row,on_day):
        runs=db.execute('SELECT * FROM automatic_runs WHERE rule_id=? AND version=?',(row['id'],row['version'])).fetchall()
        total=sum(len(json.loads(r['entity_ids'])) for r in runs)
        daily=sum(len(json.loads(r['entity_ids'])) for r in runs if r['day']==on_day)
        last={}
        for run in runs:
            for entity_id in json.loads(run['entity_ids']):last[entity_id]=max(last.get(entity_id,0),run['at'])
        total_money=sum((Decimal(r['amount_cny']) for r in runs),Decimal('0'))
        daily_money=sum((Decimal(r['amount_cny']) for r in runs if r['day']==on_day),Decimal('0'))
        return total,daily,last,total_money,daily_money

    @staticmethod
    def operation(rule,entity_id):
        if rule['action'] in ('pause','resume'):
            return {'op':'set_pause','kind':rule['kind'],'entity_id':entity_id,'paused':rule['action']=='pause'}
        result={'op':rule['action'],'entity_id':entity_id,'amount':rule['target_amount']}
        if rule['action']=='set_manual_bid':result['kind']=rule['kind']
        return result

    @staticmethod
    def change_amount(rule,change):
        if rule['action'] in ('pause','resume'):
            field='pause';target=rule['action']=='pause'
            if change['after'].get(field) is not target or change['before'].get(field) is not (not target):raise QueryError('AUTOMATIC_OPERATION_OUT_OF_SCOPE')
            amount=Decimal('0')
        else:
            field='budget' if rule['action']=='set_campaign_budget' else ('price' if rule['kind']=='keyword' else 'maxPrice')
            try:
                before=Decimal(str(change['before'][field]));after=Decimal(str(change['after'][field]));amount=abs(after-before)
                if not before.is_finite() or not after.is_finite():raise ValueError()
            except (KeyError,ValueError,ArithmeticError):raise QueryError('AUTOMATIC_OPERATION_OUT_OF_SCOPE') from None
            if after!=Decimal(rule['target_amount']) or after>Decimal(rule['maximum_target_cny']) or amount>Decimal(rule['maximum_action_change_cny']):raise QueryError('AUTOMATIC_MONEY_LIMIT')
            if field=='budget' and after-before>Decimal(rule['maximum_budget_increase_cny']):raise QueryError('AUTOMATIC_MONEY_LIMIT')
        if set(change['changed_fields'])!={field} or {k:v for k,v in change['before'].items() if k!=field}!={k:v for k,v in change['after'].items() if k!=field}:
            raise QueryError('AUTOMATIC_OPERATION_OUT_OF_SCOPE')
        return amount

    def unknown(self,db,rule_id):
        # Include every previous rule version, and jobs whose enqueue succeeded
        # before the run's job_id was recorded. Unknown results never release a limit.
        return db.execute("SELECT 1 FROM automatic_runs r JOIN execution_jobs j ON j.plan_id=r.plan_id JOIN execution_items i ON i.job_id=j.id WHERE r.rule_id=? AND (j.state='unknown' OR i.state='unknown') LIMIT 1",(rule_id,)).fetchone()

    def stop(self,actor,rule_id,code):
        with self.store.db() as db:
            row,_=self.load(db,actor,rule_id)
            db.execute("UPDATE automatic_rules SET state='stopped',stop_reason=?,next_due=NULL WHERE id=?",(code,rule_id))
            self.event(db,row,actor,'stopped_'+code)

    def trigger(self,actor,rule_id):
        self.require(actor)
        if not self.enabled() or not self.queue or not self.queue.enabled():raise QueryError('AUTOMATIC_MUTATIONS_DISABLED')
        actor=self.queue.fresh(actor);self.require(actor)
        now=self.store.clock();today=datetime.fromtimestamp(now,BEIJING).date()
        source_query=self.store.query;release_id=source_query.release_id
        with self.store.db() as db:row,rule=self.load(db,actor,rule_id)
        self.check(row,rule,actor)
        end=(today-timedelta(days=1)).isoformat();start=(today-timedelta(days=rule['lookback_days'])).isoformat()
        facts=self.facts(rule,start,end)
        if self.store.query is not source_query:raise QueryError('SNAPSHOT_CHANGED')
        code='DATA_INCOMPLETE' if any(not r['complete'] for r in facts) else None
        if any(Decimal(r['metrics']['cost'] or '0')>Decimal(rule['maximum_observed_cost']) for r in facts):code='AUTOMATIC_COST_STOP'
        if code:self.stop(actor,rule_id,code);raise QueryError(code)
        from .analytics import Analytics
        eligible=[]
        for fact in facts:
            current=[r for r in Analytics(self.store.query).settings(rule['account'],rule['kind'],fact['entity_id'])['items'] if r.get('getTemp',0)==0]
            if len(current)!=1:raise QueryError('DATA_INCOMPLETE')
            if rule['action'] in ('pause','resume'):
                if type(current[0].get('pause')) is not bool:raise QueryError('DATA_INCOMPLETE')
                noop=current[0]['pause']==(rule['action']=='pause')
            else:
                field='budget' if rule['action']=='set_campaign_budget' else ('price' if rule['kind']=='keyword' else 'maxPrice')
                if current[0].get(field) is None:raise QueryError('DATA_INCOMPLETE')
                noop=Decimal(str(current[0][field]))==Decimal(rule['target_amount'])
            if fact['matched'] and not noop:eligible.append(fact['entity_id'])
        with self.store.db() as db:
            if self.store.query is not source_query:raise QueryError('SNAPSHOT_CHANGED')
            current,stored=self.load(db,actor,rule_id);self.check(current,stored,actor)
            if current['version']!=row['version'] or current['hash']!=row['hash']:raise QueryError('STALE_RULE_VERSION')
            if self.unknown(db,rule_id):raise QueryError('AUTOMATIC_UNKNOWN_STOP')
            old=db.execute('SELECT * FROM automatic_runs WHERE rule_id=? AND version=? AND day=?',(rule_id,row['version'],today.isoformat())).fetchone()
            if old:
                run=dict(old)
            else:
                if current['next_due'] is None or current['next_due']>now:raise QueryError('RULE_NOT_DUE')
                total,daily,last,total_money,daily_money=self.totals(db,row,today.isoformat())
                selected=[e for e in eligible if e not in last or now-last[e]>=rule['cooldown_seconds']]
                allowance=min(rule['maximum_actions_per_run'],rule['maximum_actions_per_day']-daily,rule['maximum_actions_total']-total)
                if selected and allowance<=0:raise QueryError('AUTOMATIC_ACTION_LIMIT')
                selected=selected[:max(0,allowance)]
                run={'id':secrets.token_hex(16),'rule_id':rule_id,'version':row['version'],'hash':row['hash'],
                     'plan_id':None,'job_id':None,'day':today.isoformat(),'at':now,'entity_ids':canonical(selected),'amount_cny':'0',
                     'evidence':canonical({'release_id':release_id,'start':start,'end':end,'entities':facts})}
                if selected:
                    payload=self.store.prepare(rule['account'],[self.operation(rule,e) for e in selected],
                                               'Owner-preauthorized fixed-action rule '+rule_id+' v'+str(row['version']))
                    if self.store.query is not source_query or payload['release_id']!=release_id:raise QueryError('SNAPSHOT_CHANGED')
                    amount=sum((self.change_amount(rule,c) for c in payload['changes']),Decimal('0'))
                    if total_money+amount>Decimal(rule['maximum_total_change_cny']) or daily_money+amount>Decimal(rule['maximum_daily_change_cny']):raise QueryError('AUTOMATIC_MONEY_LIMIT')
                    run['amount_cny']=str(amount)
                    payload['automatic_rule']={'id':rule_id,'version':row['version'],'hash':row['hash'],'run_id':run['id']}
                    plan={'id':secrets.token_hex(16),'author':actor.subject,'account':rule['account'],'version':1,'state':'approved',
                          'payload':canonical(payload),'hash':digest(payload),'author_version':actor.version,'approval_actor':actor.subject,
                          'approval_version':actor.version,'approval_expires':min(row['expires'],now+1800,datetime.combine(today+timedelta(days=1),datetime.min.time(),BEIJING).timestamp())}
                    db.execute('INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,?,?,?)',tuple(plan.values()))
                    self.store.event(db,actor,plan,'automatic_rule_approval_bound')
                    run['plan_id']=plan['id']
                db.execute('INSERT INTO automatic_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)',tuple(run.values()))
                self.event(db,row,actor,'evaluation_reserved')
            db.execute('UPDATE automatic_rules SET next_due=? WHERE id=?',(now+rule['interval_seconds'],rule_id))
        if run['plan_id']:
            job=self.queue.enqueue(actor,run['plan_id'],1,'automatic-rule-'+run['id'])
            with self.store.db() as db:db.execute('UPDATE automatic_runs SET job_id=? WHERE id=?',(job['job_id'],run['id']))
            run['job_id']=job['job_id']
        evidence=json.loads(run['evidence'])
        return {'rule_id':rule_id,'run':run,'existing_run':bool(old),'source_release_id':evidence['release_id'],
                'requested_range':{'start':evidence['start'],'end':evidence['end']},'entities':evidence['entities'],
                'warning':'QUEUED_IS_NOT_EXECUTED_CHECK_EXECUTION_JOB'}

    def guard(self,plan,payload):
        marker=payload.get('automatic_rule')
        if marker is None:return
        if not self.enabled():raise QueryError('AUTOMATIC_MUTATIONS_DISABLED')
        if not isinstance(marker,dict):raise QueryError('AUTOMATIC_RULE_BINDING_INVALID')
        # Guard is also invoked while the queue holds a write transaction.
        with closing(sqlite3.connect(self.store.path.resolve().as_uri()+'?mode=ro',uri=True)) as db, db:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT * FROM automatic_rules WHERE id=?',(marker.get('id'),)).fetchone()
            if row is None:raise QueryError('AUTOMATIC_RULE_NOT_FOUND')
            rule=json.loads(row['body']);self.check(row,rule)
            if self.queue is None:raise QueryError('AUTOMATIC_RULE_GUARD_REQUIRED')
            current=self.queue.identity_resolver(row['owner'])
            if current is None or current.version!=row['identity_version']:raise QueryError('IDENTITY_CHANGED')
            # Identity I/O is an unbounded concurrency boundary: reread approval
            # after it, so pausing/updating during verification cannot permit a send.
            row=db.execute('SELECT * FROM automatic_rules WHERE id=?',(marker.get('id'),)).fetchone()
            if row is None:raise QueryError('AUTOMATIC_RULE_NOT_FOUND')
            rule=json.loads(row['body']);self.check(row,rule,current)
            self.require(current,rule['account'])
            if not self.enabled():raise QueryError('AUTOMATIC_MUTATIONS_DISABLED')
            run=db.execute('SELECT * FROM automatic_runs WHERE id=?',(marker.get('run_id'),)).fetchone()
            if not run or marker!={'id':row['id'],'version':row['version'],'hash':row['hash'],'run_id':run['id']} or run['plan_id']!=plan['id'] or run['version']!=row['version'] or run['hash']!=row['hash'] or run['rule_id']!=row['id']:
                raise QueryError('AUTOMATIC_RULE_BINDING_INVALID')
            if run['day']!=datetime.fromtimestamp(self.store.clock(),BEIJING).date().isoformat():raise QueryError('AUTOMATIC_EXECUTION_DAY_EXPIRED')
            evidence=json.loads(run['evidence'])
            if payload.get('release_id')!=evidence['release_id'] or evidence['end']!=(day(run['day'])-timedelta(days=1)).isoformat() or evidence['start']!=(day(run['day'])-timedelta(days=rule['lookback_days'])).isoformat():
                raise QueryError('AUTOMATIC_RULE_BINDING_INVALID')
            if plan['account']!=rule['account'] or plan['author']!=row['owner'] or plan['approval_actor']!=row['owner'] or plan['approval_version']!=row['approved_identity']:
                raise QueryError('AUTOMATIC_RULE_BINDING_INVALID')
            changes=payload.get('changes',[]);ids=[];amount=Decimal('0')
            for change in changes:
                op=change['operation'];ids.append(op.get('entity_id'))
                if op!=self.operation(rule,op.get('entity_id')) or op['entity_id'] not in rule['entity_ids']:
                    raise QueryError('AUTOMATIC_OPERATION_OUT_OF_SCOPE')
                amount+=self.change_amount(rule,change)
            if ids!=json.loads(run['entity_ids']) or not 1<=len(ids)<=rule['maximum_actions_per_run']:
                raise QueryError('AUTOMATIC_ACTION_LIMIT')
            total,daily,_,total_money,daily_money=self.totals(db,row,run['day'])
            if total>rule['maximum_actions_total'] or daily>rule['maximum_actions_per_day']:raise QueryError('AUTOMATIC_ACTION_LIMIT')
            if amount!=Decimal(run['amount_cny']) or total_money>Decimal(rule['maximum_total_change_cny']) or daily_money>Decimal(rule['maximum_daily_change_cny']):raise QueryError('AUTOMATIC_MONEY_LIMIT')
            if self.unknown(db,row['id']):raise QueryError('AUTOMATIC_UNKNOWN_STOP')

    def simulate(self,actor,rule_id,start,end):
        with self.store.db() as db:row,rule=self.load(db,actor,rule_id)
        dates=date_range(start,end)
        if len(dates)>90:raise QueryError('SIMULATION_RANGE_LIMIT')
        windows=[];total=0;last={};money=Decimal('0');last_evaluation=None
        for index,end_day in enumerate(dates):
            first=(day(end_day)-timedelta(days=rule['lookback_days']-1)).isoformat()
            facts=self.facts(rule,first,end_day);stamp=datetime.combine(day(end_day)+timedelta(days=1),datetime.min.time(),BEIJING).timestamp()
            stopped=any(not r['complete'] or Decimal(r['metrics']['cost'] or '0')>Decimal(rule['maximum_observed_cost']) for r in facts) or index>=rule['expires_in_days']
            due=last_evaluation is None or stamp-last_evaluation>=rule['interval_seconds']
            candidates=[r['entity_id'] for r in facts if due and r['matched'] and (r['entity_id'] not in last or stamp-last[r['entity_id']]>=rule['cooldown_seconds'])]
            selected=[] if stopped else candidates[:min(rule['maximum_actions_per_run'],rule['maximum_actions_per_day'],max(0,rule['maximum_actions_total']-total))]
            amount=Decimal('0');operations=[]
            try:
                for identifier in selected:
                    operation=self.operation(rule,identifier)
                    try:payload=self.store.prepare(rule['account'],[operation],'Historical fixed-rule simulation')
                    except QueryError as exc:
                        if str(exc)=='NO_CHANGES':continue
                        raise
                    amount+=self.change_amount(rule,payload['changes'][0]);operations.append(operation)
                if amount>Decimal(rule['maximum_daily_change_cny']) or money+amount>Decimal(rule['maximum_total_change_cny']):raise QueryError('AUTOMATIC_MONEY_LIMIT')
            except QueryError as exc:
                stopped=True;operations=[];amount=Decimal('0');code=str(exc)
            else:code=None
            selected=[operation['entity_id'] for operation in operations]
            total+=len(selected)
            money+=amount
            for identifier in selected:last[identifier]=stamp
            if due:last_evaluation=stamp
            windows.append({'start':first,'end':end_day,'entities':facts,'would_apply':operations,'stopped':stopped,'stop_code':code,
                            'total_actions':total,'amount_change_cny':str(amount),'total_amount_change_cny':str(money)})
            if stopped:break
        return {'rule_id':rule_id,'version':row['version'],'hash':row['hash'],'windows':windows,'created_plans':[],
                'warnings':['SIMULATION_ONLY_NO_EXECUTION','CURRENT_SETTINGS_ASSUMPTION_NOT_HISTORICAL_EXECUTION_BACKTEST','COST_THRESHOLD_IS_NOT_PROOF_OF_WASTE']}

    def due(self):
        if not self.enabled() or not self.queue or not self.queue.enabled():return []
        with self.store.db() as db:
            return [dict(r) for r in db.execute("SELECT id,owner FROM automatic_rules WHERE state='approved' AND next_due<=? ORDER BY next_due LIMIT 10",(self.store.clock(),))]

    def run_due(self):
        results=[]
        for row in self.due():
            try:
                actor=self.queue.identity_resolver(row['owner'])
                if actor is None:raise QueryError('IDENTITY_CHANGED')
                results.append(self.trigger(actor,row['id']))
            except Exception as exc:
                # Bound failed scheduling, preserve diagnostics, never retry a send.
                code=str(exc) if isinstance(exc,QueryError) else 'AUTOMATIC_EVALUATION_FAILED'
                with self.store.db() as db:
                    db.execute("UPDATE automatic_rules SET state='stopped',stop_reason=?,next_due=NULL WHERE id=?",(code,row['id']))
                results.append({'rule_id':row['id'],'error':code})
        return results
