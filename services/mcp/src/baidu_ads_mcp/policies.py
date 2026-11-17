"""Persisted observation-only rules. No timers, external messages, or ad mutations."""
import json
import secrets
from decimal import Decimal
from typing import Literal
from pydantic import Field
from .changes import Strict,Money,canonical
from .query import QueryError


class RuleInput(Strict):
    account: Literal['tinacms','ssw','yakshaver']
    name: str = Field(min_length=1,max_length=100)
    cost_threshold: Money
    minimum_clicks: int = Field(ge=0,le=100000000)
    mode: Literal['observe'] = 'observe'


class PolicyStore:
    def __init__(self,store):
        self.store=store
        with store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS policies(id TEXT PRIMARY KEY,author TEXT,account TEXT,body TEXT,paused INTEGER);
            CREATE TABLE IF NOT EXISTS evaluations(policy_id TEXT,release_id TEXT,start TEXT,end TEXT,result TEXT,
              PRIMARY KEY(policy_id,release_id,start,end));
            ''')

    def load(self,db,actor,policy_id):
        row=db.execute('SELECT * FROM policies WHERE id=?',(policy_id,)).fetchone()
        if not row or (row['author']!=actor.subject and actor.role!='owner'):
            raise QueryError('POLICY_NOT_FOUND')
        actor.require('policies:manage',row['account'])
        return dict(row)

    def create(self,actor,**values):
        try:rule=RuleInput(**values)
        except ValueError:raise QueryError('INVALID_POLICY') from None
        actor.require('policies:manage',rule.account)
        if actor.role not in ('operator','owner'):
            raise QueryError('INSUFFICIENT_ROLE')
        identifier=secrets.token_hex(16)
        with self.store.db() as db:
            if db.execute('SELECT count(*) FROM policies').fetchone()[0]>=1000:
                raise QueryError('POLICY_STORE_CAPACITY')
            db.execute('INSERT INTO policies VALUES(?,?,?,?,0)',(identifier,actor.subject,rule.account,canonical(rule.model_dump())))
        return self.get(actor,identifier)

    def get(self,actor,policy_id):
        with self.store.db() as db:row=self.load(db,actor,policy_id)
        return {'policy_id':policy_id,'author':row['author'],'paused':bool(row['paused']),
                'rule':json.loads(row['body']),'scheduled':False,'mutations_enabled':False}

    def pause(self,actor,policy_id):
        with self.store.db() as db:
            self.load(db,actor,policy_id)
            db.execute('UPDATE policies SET paused=1 WHERE id=?',(policy_id,))
        return self.get(actor,policy_id)

    def evaluate(self,actor,policy_id,start,end):
        query=self.store.query
        query.check()
        with self.store.db() as db:
            policy=self.load(db,actor,policy_id)
            if policy['paused']:raise QueryError('POLICY_PAUSED')
            existing=db.execute('SELECT result FROM evaluations WHERE policy_id=? AND release_id=? AND start=? AND end=?',
                                (policy_id,query.release_id,start,end)).fetchone()
            if existing:return json.loads(existing[0])
        rule=json.loads(policy['body'])
        summary=query.performance(policy['account'],start,end)
        if summary['is_partial']:raise QueryError('DATA_INCOMPLETE')
        totals=summary['observed_totals']
        matched=Decimal(totals['cost'])>=Decimal(rule['cost_threshold']) and Decimal(totals['click'])>=rule['minimum_clicks']
        result={'policy_id':policy_id,'release_id':query.release_id,'requested_range':{'start':start,'end':end},
                'matched':matched,'evidence':totals,'action':'observation_only',
                'warnings':['SPEND_THRESHOLD_IS_NOT_WASTE_OR_CONVERSION_EVIDENCE','NO_AD_CHANGES_OR_EXTERNAL_MESSAGES']}
        with self.store.db() as db:
            # Recheck pause and ownership after computation; deduplicate retries.
            current=self.load(db,actor,policy_id)
            if current['paused']:raise QueryError('POLICY_PAUSED')
            db.execute('INSERT OR IGNORE INTO evaluations VALUES(?,?,?,?,?)',
                       (policy_id,query.release_id,start,end,canonical(result)))
            saved=db.execute('SELECT result FROM evaluations WHERE policy_id=? AND release_id=? AND start=? AND end=?',
                            (policy_id,query.release_id,start,end)).fetchone()[0]
        return json.loads(saved)
