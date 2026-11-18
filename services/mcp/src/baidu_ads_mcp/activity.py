"""Private, bounded operation metadata. Never retain arguments or response bodies."""
import json
import re
import time

from .query import ACCOUNTS


class ActivityLog:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        with store.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS activity (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                subject TEXT NOT NULL, action TEXT NOT NULL, context TEXT NOT NULL,
                status TEXT NOT NULL, duration_ms INTEGER);
                CREATE INDEX IF NOT EXISTS activity_subject ON activity(subject,seq);
                CREATE INDEX IF NOT EXISTS activity_at ON activity(at);''')

    def begin(self, subject, action, arguments=None):
        context = {}
        params = arguments.get('params', {}) if isinstance(arguments, dict) else {}
        if isinstance(params, dict):
            if isinstance(params.get('account'), str) and params['account'] in ACCOUNTS:
                context['account'] = params['account']
            for key in ('start', 'end'):
                value = params.get(key)
                if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                    context[key] = value
            for key in ('plan_id', 'job_id'):
                value = params.get(key)
                if isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value):
                    context[key] = value
        with self.store.db() as db:
            db.execute('DELETE FROM activity WHERE at<?', (self.clock()-90*86400,))
            db.execute('DELETE FROM activity WHERE seq IN (SELECT seq FROM activity ORDER BY seq DESC LIMIT -1 OFFSET 99999)')
            return db.execute('INSERT INTO activity(at,subject,action,context,status) VALUES(?,?,?,?,?)',
                              (self.clock(), subject, action, json.dumps(context), 'started')).lastrowid

    def finish(self, seq, status, duration_ms):
        if status not in ('succeeded', 'failed'):
            raise ValueError('Invalid activity status')
        with self.store.db() as db:
            db.execute('UPDATE activity SET status=?,duration_ms=? WHERE seq=?',
                       (status, max(0, int(duration_ms)), seq))

    def list(self, actor, before=None):
        clauses, values = [], []
        if actor.role != 'owner':
            clauses.append('subject=?'); values.append(actor.subject)
        if before is not None:
            if not isinstance(before, int) or before < 1:
                raise ValueError('Invalid cursor')
            clauses.append('seq<?'); values.append(before)
        where = ' WHERE '+' AND '.join(clauses) if clauses else ''
        with self.store.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM activity'+where+' ORDER BY seq DESC LIMIT 50', values)]
