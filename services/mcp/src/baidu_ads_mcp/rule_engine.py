"""Typed historical simulation and recommendation-only policies; never executes ads."""
import json
import secrets
from datetime import datetime, time as daytime, timedelta
from decimal import Decimal
from typing import Literal
from pydantic import Field

from .changes import Strict, Money, canonical, digest
from .operations import BEIJING
from .query import QueryError, date_range, day, metrics


class DecisionRuleInput(Strict):
    account: Literal['tinacms','ssw','yakshaver']
    name: str = Field(min_length=1,max_length=100)
    kind: Literal['campaign','adgroup','keyword']
    entity_ids: list[str] = Field(min_length=1,max_length=20)
    cost_threshold: Money
    minimum_clicks: int = Field(ge=0,le=100000000)
    mode: Literal['observe','propose_pause'] = 'observe'
    cooldown_seconds: int = Field(default=86400,ge=300,le=30*86400)
    expires_in_days: int = Field(default=30,ge=1,le=90)


class DecisionRules:
    def __init__(self,store):
        self.store=store
        with store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS decision_rules(id TEXT PRIMARY KEY,owner TEXT,identity_version TEXT,
                body TEXT,paused INTEGER,expires REAL);
            CREATE TABLE IF NOT EXISTS decision_evaluations(rule_id TEXT,release_id TEXT,start TEXT,end TEXT,result TEXT,
                PRIMARY KEY(rule_id,release_id,start,end));
            CREATE TABLE IF NOT EXISTS decision_cooldowns(rule_id TEXT,entity_id TEXT,at REAL,
                PRIMARY KEY(rule_id,entity_id));
            ''')

    def create(self,actor,**values):
        try:rule=DecisionRuleInput(**values)
        except ValueError:raise QueryError('INVALID_RULE') from None
        if len(set(rule.entity_ids))!=len(rule.entity_ids) or any(not v.isascii() or not v.isdigit() or len(v)>24 for v in rule.entity_ids):
            raise QueryError('INVALID_ENTITY_ID')
        actor.require('policies:manage',rule.account)
        if rule.mode=='propose_pause':actor.require('changes:propose',rule.account)
        # Check identifiers and preserve ambiguity: no choosing a same-name object.
        from .analytics import Analytics
        analytics=Analytics(self.store.query)
        for identifier in rule.entity_ids:
            value=analytics.settings(rule.account,rule.kind,identifier)
            if len([r for r in value['items'] if r.get('getTemp',0)==0])!=1:
                raise QueryError('AMBIGUOUS_ENTITY')
        identifier=secrets.token_hex(16)
        with self.store.db() as db:
            if db.execute('SELECT count(*) FROM decision_rules').fetchone()[0]>=1000:
                raise QueryError('POLICY_STORE_CAPACITY')
            db.execute('INSERT INTO decision_rules VALUES(?,?,?,?,0,?)',
                       (identifier,actor.subject,actor.version,canonical(rule.model_dump()),self.store.clock()+rule.expires_in_days*86400))
        return self.get(actor,identifier)

    def load(self,db,actor,rule_id):
        row=db.execute('SELECT * FROM decision_rules WHERE id=?',(rule_id,)).fetchone()
        if not row or (row['owner']!=actor.subject and actor.role!='owner'):
            raise QueryError('POLICY_NOT_FOUND')
        rule=json.loads(row['body'])
        actor.require('policies:manage',rule['account'])
        return dict(row),rule

    def get(self,actor,rule_id):
        with self.store.db() as db:row,rule=self.load(db,actor,rule_id)
        return {'rule_id':rule_id,'owner':row['owner'],'definition':rule,'paused':bool(row['paused']),
                'expires_at':row['expires'],'execution_enabled':False}

    def pause(self,actor,rule_id):
        with self.store.db() as db:
            self.load(db,actor,rule_id)
            db.execute('UPDATE decision_rules SET paused=1 WHERE id=?',(rule_id,))
        return self.get(actor,rule_id)

    def facts(self,rule,start,end):
        query=self.store.query;query.check()
        dates=date_range(start,end)
        dimension=rule['kind']
        rows=query.data[rule['account']]['reports'].get(dimension)
        if rows is None:raise QueryError('UNSUPPORTED_DIMENSION')
        field={'campaign':'campaignId','adgroup':'adGroupId','keyword':'wInfoId'}[dimension]
        result=[]
        for identifier in rule['entity_ids']:
            selected=[r for r in rows if str(r.get(field))==identifier and start<=r['date']<=end]
            missing=sorted(set(dates)-{r['date'] for r in selected})
            totals=metrics(selected)
            complete=not missing and all(totals[k] is not None for k in ('cost','click','impression'))
            matched=complete and Decimal(totals['cost'])>=Decimal(rule['cost_threshold']) and Decimal(totals['click'])>=rule['minimum_clicks']
            result.append({'entity_id':identifier,'metrics':totals,'missing_dates':missing,'complete':complete,'matched':bool(matched)})
        return result

    def simulate(self,actor,rule_id,start,end,window_days=1):
        with self.store.db() as db:_,rule=self.load(db,actor,rule_id)
        actor.require('analytics:read',rule['account'])
        dates=date_range(start,end)
        if len(dates)>90 or type(window_days) is not int or not 1<=window_days<=30:
            raise QueryError('SIMULATION_RANGE_LIMIT')
        windows=[];last={}
        for offset in range(0,len(dates),window_days):
            chunk=dates[offset:offset+window_days]
            if len(chunk)!=window_days:break
            facts=self.facts(rule,chunk[0],chunk[-1])
            stamp=datetime.combine(day(chunk[-1])+timedelta(days=1),daytime(),BEIJING).timestamp()
            for item in facts:
                previous=last.get(item['entity_id'])
                item['cooldown_suppressed']=bool(item['matched'] and previous is not None and stamp-previous<rule['cooldown_seconds'])
                item['would_recommend']=item['matched'] and not item['cooldown_suppressed']
                if item['would_recommend']:last[item['entity_id']]=stamp
            windows.append({'start':chunk[0],'end':chunk[-1],'entities':facts})
        return {'rule_id':rule_id,'release_id':self.store.query.release_id,'windows':windows,'created_plans':[],
                'warnings':['SIMULATION_ONLY_NO_CHANGES','COST_THRESHOLD_IS_NOT_PROOF_OF_WASTE_OR_CONVERSION_LOSS']}

    def evaluate(self,actor,rule_id,start,end):
        query=self.store.query;query.check()
        # All preparation is pure; final state/identity/version checks and plan creation share one transaction.
        with self.store.db() as db:row,rule=self.load(db,actor,rule_id)
        actor.require('analytics:read',rule['account'])
        if row['owner']!=actor.subject or row['identity_version']!=actor.version:
            raise QueryError('IDENTITY_CHANGED')
        if rule['mode']=='propose_pause':actor.require('changes:propose',rule['account'])
        facts=self.facts(rule,start,end)
        if any(not r['complete'] for r in facts):raise QueryError('DATA_INCOMPLETE')
        now=self.store.clock()
        with self.store.db() as db:
            current,stored=self.load(db,actor,rule_id)
            if current['paused']:raise QueryError('POLICY_PAUSED')
            if current['expires']<=now:raise QueryError('POLICY_EXPIRED')
            if stored!=rule or current['identity_version']!=actor.version:raise QueryError('IDENTITY_CHANGED')
            old=db.execute('SELECT result FROM decision_evaluations WHERE rule_id=? AND release_id=? AND start=? AND end=?',
                           (rule_id,query.release_id,start,end)).fetchone()
            if old:return json.loads(old[0])
            candidates=[]
            for item in facts:
                previous=db.execute('SELECT at FROM decision_cooldowns WHERE rule_id=? AND entity_id=?',(rule_id,item['entity_id'])).fetchone()
                item['cooldown_suppressed']=bool(item['matched'] and previous and now-previous[0]<rule['cooldown_seconds'])
                if item['matched'] and not item['cooldown_suppressed']:candidates.append(item['entity_id'])
            result={'rule_id':rule_id,'release_id':query.release_id,'requested_range':{'start':start,'end':end},
                    'entities':facts,'created_plan_id':None,'action':'observation_only','execution_enabled':False,
                    'warnings':['COST_THRESHOLD_IS_NOT_PROOF_OF_WASTE','HUMAN_REVIEW_REQUIRED_NO_AUTOMATIC_EXECUTION']}
            if candidates and rule['mode']=='propose_pause':
                # Preparing requires current inventory; it does not call an upstream service.
                operations=[{'op':'set_pause','kind':rule['kind'],'entity_id':identifier,'paused':True} for identifier in candidates]
                reason='Rule '+rule_id+' ('+rule['name']+'): cost/click threshold met for '+start+' to '+end+'. Review intent and evidence before approval.'
                payload=self.store.prepare(rule['account'],operations,reason)
                if payload['release_id']!=query.release_id:raise QueryError('SNAPSHOT_CHANGED')
                plan={'id':secrets.token_hex(16),'author':actor.subject,'account':rule['account'],'version':1,
                      'state':'draft','payload':canonical(payload),'hash':digest(payload),'author_version':actor.version,
                      'approval_actor':None,'approval_version':None,'approval_expires':None}
                db.execute('INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,?,?,?)',tuple(plan.values()))
                self.store.event(db,actor,plan,'rule_created_review_draft')
                result.update(created_plan_id=plan['id'],action='draft_created_requires_approval')
                for identifier in candidates:
                    db.execute('INSERT OR REPLACE INTO decision_cooldowns VALUES(?,?,?)',(rule_id,identifier,now))
            db.execute('INSERT INTO decision_evaluations VALUES(?,?,?,?,?)',(rule_id,query.release_id,start,end,canonical(result)))
        return result
