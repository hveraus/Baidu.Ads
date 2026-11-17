"""Bounded private exports, in-app subscriptions and durable read-only background jobs.

All state belongs to MCP. This module cannot call advertising mutation APIs or
external messaging services. A host supplies identity resolution and cache refresh.
"""
from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timedelta, timezone
import io
import json
import re
import secrets
from typing import Literal

from pydantic import Field

from .changes import Strict, canonical
from .query import QueryError, date_range, ACCOUNTS, DIMENSIONS

BEIJING = timezone(timedelta(hours=8))
MAX_EXPORT_BYTES = 10 * 1024 * 1024
MAX_STORED_EXPORT_BYTES = 50 * 1024 * 1024


class ExportInput(Strict):
    account: Literal['tinacms', 'ssw', 'yakshaver']
    start: str
    end: str
    dimension: Literal['account', 'campaign', 'adgroup', 'keyword', 'search_terms', 'keyword_device', 'keyword_region', 'keyword_match', 'creative', 'channels', 'keyword_hour', 'account_hour'] = 'account'
    format: Literal['json', 'csv'] = 'json'
    max_rows: int = Field(default=10000, ge=1, le=10000)


class SubscriptionInput(Strict):
    kind: Literal['daily_summary', 'policy_observation', 'decision_rule', 'cache_refresh']
    account: Literal['tinacms', 'ssw', 'yakshaver'] | None = None
    policy_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    rule_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    at_beijing: str = Field(default='09:15', pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
    lookback_days: int = Field(default=1, ge=1, le=90)
    expires_in_days: int = Field(default=30, ge=1, le=90)
    refresh_interval_seconds: int | None = Field(default=None, ge=300, le=86400)


def _safe_csv(value):
    # Spreadsheet-safe encoding. JSON exports preserve the exact source strings.
    text = '' if value is None else str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else text


def _next_daily(now, at):
    hour, minute = map(int, at.split(':'))
    current = datetime.fromtimestamp(now, BEIJING)
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target.timestamp() <= now:
        target += timedelta(days=1)
    return target.timestamp()


class Operations:
    def __init__(self, store, policies=None, decision_rules=None):
        self.store, self.policies = store, policies
        self.decision_rules = decision_rules
        self.clock = store.clock
        with store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS private_exports (
                id TEXT PRIMARY KEY, owner TEXT, identity_version TEXT, account TEXT,
                created REAL, expires REAL, mime TEXT, extension TEXT, size INTEGER,
                metadata TEXT, content BLOB);
            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY, owner TEXT, identity_version TEXT, body TEXT,
                paused INTEGER NOT NULL, next_due REAL, expires REAL);
            CREATE TABLE IF NOT EXISTS background_jobs (
                id TEXT PRIMARY KEY, owner TEXT, identity_version TEXT, subscription_id TEXT,
                kind TEXT, body TEXT, state TEXT, attempts INTEGER, due REAL,
                lease_until REAL, lease_token TEXT, result TEXT, dedupe TEXT UNIQUE,
                created REAL, updated REAL);
            CREATE INDEX IF NOT EXISTS background_job_due ON background_jobs(state,due);
            CREATE TABLE IF NOT EXISTS inbox (
                id TEXT PRIMARY KEY, owner TEXT, created REAL, expires REAL,
                title TEXT, body TEXT, read_at REAL, dedupe TEXT UNIQUE);
            ''')

    def _cleanup(self, db):
        now = self.clock()
        db.execute('DELETE FROM private_exports WHERE expires<=?', (now,))
        db.execute('DELETE FROM inbox WHERE expires<=?', (now,))
        db.execute("DELETE FROM background_jobs WHERE state IN ('completed','failed','cancelled') AND updated<?", (now-30*86400,))

    def create_export(self, actor, _export_id=None, **values):
        try:
            params = ExportInput(**values)
        except ValueError:
            raise QueryError('INVALID_EXPORT') from None
        actor.require('exports:create', params.account)
        actor.require('analytics:read')
        query = self.store.query  # Pin all pages to this exact in-memory release.
        query.check()
        date_range(params.start, params.end)
        items, cursor, first = [], None, None
        while True:
            page = query.performance(params.account, params.start, params.end,
                                     params.dimension, limit=200, cursor=cursor)
            if first is None:
                first = page
                if page['total_count'] > params.max_rows:
                    raise QueryError('EXPORT_ROW_LIMIT')
            items.extend(page['items'])
            if len(items) > params.max_rows:
                raise QueryError('EXPORT_ROW_LIMIT')
            cursor = page['next_cursor']
            if cursor is None:
                break
        metadata = {k: v for k, v in first.items() if k not in ('items', 'next_cursor')}
        metadata['export_rows'] = len(items)
        metadata['format'] = params.format
        if params.format == 'json':
            raw = canonical({**metadata, 'items': items}).encode('utf-8')
            mime = 'application/json; charset=utf-8'
        else:
            output = io.StringIO(newline='')
            fields = list(DIMENSIONS[params.dimension]) + ['cost', 'click', 'impression', 'cpc', 'ctr']
            writer = csv.writer(output, lineterminator='\r\n')
            writer.writerow(fields)
            for item in items:
                values = {**item['entity'], **item['metrics']}
                writer.writerow([_safe_csv(values.get(field)) for field in fields])
            raw = ('\ufeff' + output.getvalue()).encode('utf-8')
            mime = 'text/csv; charset=utf-8'
            metadata['csv_formula_protection'] = 'Potential formula cells prefixed with apostrophe; JSON keeps exact source text.'
        if len(raw) > MAX_EXPORT_BYTES:
            raise QueryError('EXPORT_SIZE_LIMIT')
        identifier, now = _export_id or secrets.token_hex(16), self.clock()
        with self.store.db() as db:
            self._cleanup(db)
            existing = db.execute('SELECT * FROM private_exports WHERE id=?', (identifier,)).fetchone()
            if existing:
                if existing['owner'] != actor.subject or existing['identity_version'] != actor.version:
                    raise QueryError('EXPORT_NOT_FOUND')
                return {'export_id': identifier, 'expires_at': existing['expires'], 'size_bytes': existing['size'],
                        'metadata': json.loads(existing['metadata']), 'download_path': '/exports/' + identifier}
            count, size = db.execute('SELECT count(*),coalesce(sum(size),0) FROM private_exports').fetchone()
            if count >= 100 or size + len(raw) > MAX_STORED_EXPORT_BYTES:
                raise QueryError('EXPORT_STORAGE_CAPACITY')
            db.execute('INSERT INTO private_exports VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (identifier, actor.subject, actor.version, params.account, now, now+3600,
                        mime, params.format, len(raw), canonical(metadata), raw))
        return {'export_id': identifier, 'expires_at': now+3600, 'size_bytes': len(raw),
                'metadata': metadata, 'download_path': '/exports/' + identifier}

    def download(self, actor, export_id):
        actor.require('exports:create')
        with self.store.db() as db:
            row = db.execute('SELECT * FROM private_exports WHERE id=?', (export_id,)).fetchone()
            if not row or row['owner'] != actor.subject or row['identity_version'] != actor.version:
                raise QueryError('EXPORT_NOT_FOUND')
            if row['expires'] <= self.clock():
                raise QueryError('EXPORT_EXPIRED')
            actor.require('exports:create', row['account'])
            return {'content': bytes(row['content']), 'mime': row['mime'],
                    'filename': 'baidu-ads-' + row['id'] + '.' + row['extension']}

    def create_subscription(self, actor, **values):
        try:
            params = SubscriptionInput(**values)
        except ValueError:
            raise QueryError('INVALID_SUBSCRIPTION') from None
        actor.require('subscriptions:manage')
        if params.kind == 'cache_refresh':
            actor.require('jobs:refresh')
            if params.account or params.policy_id or params.rule_id:
                raise QueryError('INVALID_SUBSCRIPTION')
        else:
            if params.refresh_interval_seconds is not None or params.account is None:
                raise QueryError('INVALID_SUBSCRIPTION')
            actor.require('analytics:read', params.account)
            if params.kind == 'decision_rule':
                if self.decision_rules is None or params.rule_id is None or params.policy_id:
                    raise QueryError('POLICY_NOT_FOUND')
                rule=self.decision_rules.get(actor,params.rule_id)
                if rule['definition']['account']!=params.account:
                    raise QueryError('INVALID_SUBSCRIPTION')
            elif params.rule_id:
                raise QueryError('INVALID_SUBSCRIPTION')
            elif params.kind == 'policy_observation':
                if self.policies is None or params.policy_id is None:
                    raise QueryError('POLICY_NOT_FOUND')
                policy = self.policies.get(actor, params.policy_id)
                if policy['rule']['account'] != params.account:
                    raise QueryError('INVALID_SUBSCRIPTION')
            elif params.policy_id:
                raise QueryError('INVALID_SUBSCRIPTION')
        identifier, now = secrets.token_hex(16), self.clock()
        next_due = now + params.refresh_interval_seconds if params.refresh_interval_seconds else _next_daily(now, params.at_beijing)
        with self.store.db() as db:
            if db.execute('SELECT count(*) FROM subscriptions WHERE owner=?', (actor.subject,)).fetchone()[0] >= 100:
                raise QueryError('SUBSCRIPTION_CAPACITY')
            db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,0,?,?)',
                       (identifier, actor.subject, actor.version, canonical(params.model_dump()), next_due, now+params.expires_in_days*86400))
        return self.get_subscription(actor, identifier)

    def get_subscription(self, actor, subscription_id):
        actor.require('subscriptions:manage')
        with self.store.db() as db:
            row = db.execute('SELECT * FROM subscriptions WHERE id=?', (subscription_id,)).fetchone()
        if not row or (row['owner'] != actor.subject and actor.role != 'owner'):
            raise QueryError('SUBSCRIPTION_NOT_FOUND')
        return {'subscription_id': subscription_id, 'recipient': row['owner'], 'channel': 'in_app',
                'timezone': 'Asia/Shanghai', 'paused': bool(row['paused']), 'next_due': row['next_due'],
                'expires_at': row['expires'], 'configuration': json.loads(row['body']),
                'advertising_mutations': False, 'external_messages': False}

    def pause_subscription(self, actor, subscription_id):
        self.get_subscription(actor, subscription_id)
        with self.store.db() as db:
            db.execute('UPDATE subscriptions SET paused=1 WHERE id=?', (subscription_id,))
            db.execute("UPDATE background_jobs SET state='cancelled',updated=? WHERE subscription_id=? AND state='queued'",
                       (self.clock(), subscription_id))
        return self.get_subscription(actor, subscription_id)

    def inbox(self, actor, limit=50):
        actor.require('subscriptions:manage')
        if type(limit) is not int or not 1 <= limit <= 200:
            raise QueryError('INVALID_LIMIT')
        with self.store.db() as db:
            rows = db.execute('SELECT * FROM inbox WHERE owner=? AND expires>? ORDER BY created DESC,id LIMIT ?',
                              (actor.subject, self.clock(), limit)).fetchall()
        return {'items': [{'notification_id': r['id'], 'created_at': r['created'],
                           'title': r['title'], 'body': json.loads(r['body']),
                           'read_at': r['read_at']} for r in rows]}

    def mark_read(self, actor, notification_id):
        actor.require('subscriptions:manage')
        with self.store.db() as db:
            result = db.execute('UPDATE inbox SET read_at=? WHERE id=? AND owner=? AND expires>?',
                                (self.clock(), notification_id, actor.subject, self.clock()))
            if result.rowcount != 1:
                raise QueryError('NOTIFICATION_NOT_FOUND')
        return {'notification_id': notification_id, 'read': True}

    def enqueue_refresh(self, actor, idempotency_key):
        actor.require('jobs:refresh')
        if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', idempotency_key):
            raise QueryError('INVALID_IDEMPOTENCY_KEY')
        with self.store.db() as db:
            job_id = self._enqueue(db, actor.subject, actor.version, None, 'cache_refresh', {},
                                   'refresh:' + actor.subject + ':' + idempotency_key)
        return self.job(actor, job_id)

    def enqueue_export(self, actor, idempotency_key, **values):
        try:
            params = ExportInput(**values)
        except ValueError:
            raise QueryError('INVALID_EXPORT') from None
        actor.require('exports:create', params.account)
        actor.require('analytics:read')
        if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', idempotency_key):
            raise QueryError('INVALID_IDEMPOTENCY_KEY')
        date_range(params.start, params.end)
        with self.store.db() as db:
            key = 'export:' + actor.subject + ':' + idempotency_key
            old = db.execute('SELECT id,body FROM background_jobs WHERE dedupe=?', (key,)).fetchone()
            payload = {**params.model_dump(), 'release_id':self.store.query.release_id}
            body = canonical(payload)
            if old and old['body'] != body:
                raise QueryError('IDEMPOTENCY_CONFLICT')
            identifier = self._enqueue(db, actor.subject, actor.version, None, 'export', payload, key)
        return self.job(actor, identifier)

    def _enqueue(self, db, owner, version, subscription, kind, body, dedupe):
        row = db.execute('SELECT id FROM background_jobs WHERE dedupe=?', (dedupe,)).fetchone()
        if row:
            return row['id']
        if db.execute("SELECT count(*) FROM background_jobs WHERE state IN ('queued','running')").fetchone()[0] >= 1000:
            raise QueryError('JOB_QUEUE_CAPACITY')
        identifier, now = secrets.token_hex(16), self.clock()
        db.execute('INSERT INTO background_jobs VALUES(?,?,?,?,?,?,?,0,?,NULL,NULL,?,?,?,?)',
                   (identifier, owner, version, subscription, kind, canonical(body), 'queued', now, '{}', dedupe, now, now))
        return identifier

    def due(self):
        """Coalesce missed schedule intervals; never replay an unbounded backlog."""
        now = self.clock()
        with self.store.db() as db:
            self._cleanup(db)
            db.execute("UPDATE background_jobs SET state=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END,lease_token=NULL,lease_until=NULL,result=?,updated=? WHERE state='running' AND lease_until<=?",
                       (canonical({'error': 'WORKER_LEASE_EXPIRED'}), now, now))
            rows = db.execute('SELECT * FROM subscriptions WHERE paused=0 AND next_due<=? AND expires>? ORDER BY next_due LIMIT 100', (now, now)).fetchall()
            for row in rows:
                config = json.loads(row['body'])
                scheduled = datetime.fromtimestamp(now, BEIJING)
                end = scheduled.date() - timedelta(days=1)
                start = end - timedelta(days=config['lookback_days']-1)
                payload = {**config, 'start': start.isoformat(), 'end': end.isoformat()}
                self._enqueue(db, row['owner'], row['identity_version'], row['id'], config['kind'],
                              payload, 'subscription:' + row['id'] + ':' + str(row['next_due']))
                interval = config['refresh_interval_seconds']
                next_due = now + interval if interval else _next_daily(now, config['at_beijing'])
                db.execute('UPDATE subscriptions SET next_due=? WHERE id=?', (next_due, row['id']))

    def claim(self):
        now = self.clock()
        with self.store.db() as db:
            row = db.execute("SELECT * FROM background_jobs WHERE state='queued' AND due<=? ORDER BY created,id LIMIT 1", (now,)).fetchone()
            if not row:
                return None
            token = secrets.token_hex(16)
            db.execute("UPDATE background_jobs SET state='running',attempts=attempts+1,lease_until=?,lease_token=?,updated=? WHERE id=?",
                       (now+60, token, now, row['id']))
            return {**dict(row), 'body': json.loads(row['body']), 'attempts': row['attempts']+1, 'lease_token': token}

    def job(self, actor, job_id):
        with self.store.db() as db:
            row = db.execute('SELECT * FROM background_jobs WHERE id=?', (job_id,)).fetchone()
        if not row or (row['owner'] != actor.subject and actor.role != 'owner'):
            raise QueryError('JOB_NOT_FOUND')
        actor.require({'cache_refresh':'jobs:refresh', 'export':'exports:create'}.get(row['kind'], 'subscriptions:manage'))
        return {'job_id': row['id'], 'kind': row['kind'], 'state': row['state'],
                'attempts': row['attempts'], 'result': json.loads(row['result'])}

    def complete(self, job, result, error=None):
        now = self.clock()
        # Error text is always an allowlisted business code, never exception text.
        retryable = error in {'UPSTREAM_UNAVAILABLE', 'IDENTITY_BRIDGE_UNAVAILABLE', 'SNAPSHOT_EXPIRED', 'DATA_INCOMPLETE', 'JOB_TIMEOUT'}
        retry = retryable and job['attempts'] < 3
        state = 'queued' if retry else ('failed' if error else 'completed')
        payload = {'error': error} if error else result
        with self.store.db() as db:
            row = db.execute("SELECT * FROM background_jobs WHERE id=? AND state='running' AND lease_token=? AND lease_until>?",
                             (job['id'], job['lease_token'], now)).fetchone()
            if not row:
                return False  # A recovered worker owns the job; late result must not win.
            if row['subscription_id']:
                subscription = db.execute('SELECT paused,expires FROM subscriptions WHERE id=?', (row['subscription_id'],)).fetchone()
                if not subscription or subscription['paused'] or subscription['expires'] <= now:
                    state, payload, error = 'cancelled', {}, 'SUBSCRIPTION_PAUSED'
            db.execute('UPDATE background_jobs SET state=?,result=?,due=?,lease_token=NULL,lease_until=NULL,updated=? WHERE id=?',
                       (state, canonical(payload), now + min(300, 5*2**job['attempts']), now, job['id']))
            if state == 'completed' and job['kind'] not in ('cache_refresh','export'):
                self._notify(db, job, payload)
            elif state == 'failed':
                self._notify(db, job, {'error': error})
        return True

    def _notify(self, db, job, payload):
        if db.execute('SELECT count(*) FROM inbox WHERE owner=?', (job['owner'],)).fetchone()[0] >= 1000:
            db.execute('DELETE FROM inbox WHERE id IN (SELECT id FROM inbox WHERE owner=? ORDER BY created LIMIT 100)', (job['owner'],))
        now = self.clock()
        title = 'MCP task failed' if 'error' in payload else ('Policy observation' if job['kind'] == 'policy_observation' else 'Daily advertising summary')
        db.execute('INSERT OR IGNORE INTO inbox VALUES(?,?,?,?,?,?,NULL,?)',
                   (secrets.token_hex(16), job['owner'], now, now+30*86400, title, canonical(payload), job['id']))


class BackgroundWorker:
    """Single bounded cooperative worker; injected refresh never starts core collection."""
    def __init__(self, operations, resolve_actor, refresh=None):
        self.operations, self.resolve_actor, self.refresh = operations, resolve_actor, refresh

    async def run_once(self):
        self.operations.due()
        job = self.operations.claim()
        if not job:
            return False
        try:
            result = await asyncio.wait_for(self._run(job), 25)
            self.operations.complete(job, result)
        except TimeoutError:
            self.operations.complete(job, None, 'JOB_TIMEOUT')
        except QueryError as exc:
            allowed = {'UPSTREAM_UNAVAILABLE', 'IDENTITY_BRIDGE_UNAVAILABLE', 'SNAPSHOT_EXPIRED', 'DATA_INCOMPLETE',
                       'IDENTITY_CHANGED', 'UNAUTHORIZED', 'INSUFFICIENT_SCOPE', 'INSUFFICIENT_ROLE', 'POLICY_PAUSED',
                       'POLICY_NOT_FOUND', 'SUBSCRIPTION_PAUSED', 'REFRESH_NOT_CONFIGURED', 'SNAPSHOT_CHANGED'}
            self.operations.complete(job, None, str(exc) if str(exc) in allowed else 'JOB_FAILED')
        except Exception:
            self.operations.complete(job, None, 'JOB_FAILED')
        return True

    async def _run(self, job):
        actor = await self.resolve_actor(job['owner'])
        if actor.subject != job['owner'] or actor.version != job['identity_version']:
            raise QueryError('IDENTITY_CHANGED')
        payload = job['body']
        if job['subscription_id']:
            subscription = self.operations.get_subscription(actor, job['subscription_id'])
            if subscription['paused'] or subscription['expires_at'] <= self.operations.clock():
                raise QueryError('SUBSCRIPTION_PAUSED')
        if job['kind'] == 'cache_refresh':
            actor.require('jobs:refresh')
            if self.refresh is None:
                raise QueryError('REFRESH_NOT_CONFIGURED')
            result = await self.refresh(actor)
        elif job['kind'] == 'export':
            actor.require('exports:create', payload['account'])
            # Run the bounded CPU work off the ASGI loop. No core process is involved.
            if self.operations.store.query.release_id != payload['release_id']:
                raise QueryError('SNAPSHOT_CHANGED')
            values = {k:v for k,v in payload.items() if k != 'release_id'}
            result = await asyncio.to_thread(self.operations.create_export, actor, _export_id=job['id'], **values)
        elif job['kind'] == 'daily_summary':
            actor.require('analytics:read', payload['account'])
            result = self.operations.store.query.performance(payload['account'], payload['start'], payload['end'])
        elif job['kind'] == 'decision_rule':
            actor.require('policies:manage',payload['account'])
            result=self.operations.decision_rules.evaluate(actor,payload['rule_id'],payload['start'],payload['end'])
        elif job['kind'] == 'policy_observation':
            actor.require('policies:manage', payload['account'])
            result = self.operations.policies.evaluate(actor, payload['policy_id'], payload['start'], payload['end'])
        else:
            raise QueryError('UNSUPPORTED_OPERATION')
        # Do not publish new notifications if the initiating identity was revoked during I/O.
        current = await self.resolve_actor(job['owner'])
        if current.version != actor.version:
            raise QueryError('IDENTITY_CHANGED')
        return result

    async def run(self, stop, poll_seconds=5):
        if not 1 <= poll_seconds <= 60:
            raise ValueError('Invalid poll interval')
        while not stop.is_set():
            try:
                active = await self.run_once()
            except Exception:
                active = False  # Only the optional worker stops/retries; core never waits.
            if not active:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
                except TimeoutError:
                    pass
