"""Local synthetic fault injection; never opens production services or core data.

These tests prove file/process boundaries locally, not host-level production SLOs.
"""
from contextlib import closing
import ast
import asyncio
import hashlib
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx
from baidu_ads_mcp.auth import Bridge
from baidu_ads_mcp.changes import ChangeStore
from baidu_ads_mcp.query import QueryError,ReleaseQuery
from baidu_ads_mcp.sync import import_release
from fixtures import make_release

ROOT=Path(__file__).resolve().parents[3]
REAL_CLIENT=httpx.AsyncClient


def hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


class SourceBridge:
    def __init__(self,release):self.release=release;self.fail=None
    async def verify(self,identity):return identity
    async def call(self,action,**payload):
        if action=='current':return {'release_id':self.release.name}
        if self.fail:await self.fail(payload['account'])
        relative='report/index.html' if payload['account']=='tinacms' else f"report/{payload['account']}/index.html"
        try:raw=(self.release/relative).read_bytes()
        except FileNotFoundError:raise QueryError('SOURCE_RELEASE_REMOVED') from None
        return {'release_id':self.release.name,'html':raw.decode(),'sha256':hashlib.sha256(raw).hexdigest()}


class Fixtures:
    def setup(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.core=self.root/'synthetic-core'
        self.release=make_release(self.core/'releases')
        (self.core/'current').write_text(self.release.name)
        with closing(sqlite3.connect(self.core/'users.sqlite3')) as db, db:
            db.execute('CREATE TABLE synthetic_health(value TEXT)');db.execute("INSERT INTO synthetic_health VALUES('healthy')")
        self.cache=self.root/'mcp-cache'
        self.bridge=SourceBridge(self.release)
        self.query=ReleaseQuery(self.release)

    def core_probe(self):
        with closing(sqlite3.connect((self.core/'users.sqlite3').as_uri()+'?mode=ro',uri=True)) as db, db:
            self.assertEqual(db.execute('SELECT value FROM synthetic_health').fetchone()[0],'healthy')
        self.assertEqual((self.core/'current').read_text(),self.release.name)
        # This is a synthetic storage/read probe, not a dashboard/login/collector test.


class ProcessAndStorageFaults(Fixtures,unittest.TestCase):
    def setUp(self):self.setup()

    def test_kill_local_mcp_process_preserves_synthetic_core(self):
        before=hashes(self.core)
        with open(os.devnull,'w') as silent:
            process=subprocess.Popen([sys.executable,'-m','baidu_ads_mcp.server','--release-copy',str(self.release)],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=silent,text=True)
            try:
                process.stdin.write(json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{
                    'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'local-fault-test','version':'1'}}})+'\n')
                process.stdin.flush()
                responses=queue.Queue()
                reader=threading.Thread(target=lambda:responses.put(process.stdout.readline()),daemon=True)
                reader.start()
                response=json.loads(responses.get(timeout=10))
                self.assertEqual(response['id'],1);self.assertIn('result',response)
                process.kill();process.wait(timeout=5)
                self.assertNotEqual(process.returncode,0)
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=5)
                process.stdin.close();process.stdout.close()
        self.core_probe();self.assertEqual(before,hashes(self.core))

    def test_corrupt_mcp_state_does_not_touch_core(self):
        before=hashes(self.core)
        broken=self.root/'broken-control.sqlite3';broken.write_bytes(b'not a sqlite database')
        with self.assertRaises(sqlite3.DatabaseError):ChangeStore(broken,self.query)
        self.core_probe();self.assertEqual(before,hashes(self.core))
        self.assertEqual(broken.read_bytes(),b'not a sqlite database')

    def test_incompatible_mcp_schema_fails_locally(self):
        before=hashes(self.core)
        state=self.root/'bad-schema.sqlite3'
        with closing(sqlite3.connect(state)) as db, db:
            db.execute(f'PRAGMA application_id={ChangeStore.APP_ID}')
            db.execute('CREATE TABLE plans(incompatible_column INTEGER)')
        store=ChangeStore(state,self.query)
        from baidu_ads_mcp.changes import Principal
        actor=Principal('synthetic','v1','operator',frozenset({'changes:propose'}))
        with self.assertRaises(sqlite3.OperationalError):store.get(actor,'a'*32)
        self.core_probe();self.assertEqual(before,hashes(self.core))

    def test_core_code_dependencies_and_tasks_have_no_mcp_dependency(self):
        sources=[*ROOT.glob('*.py'),*(ROOT/'scripts').rglob('*.py')]
        for path in sources:
            tree=ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                modules=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
                self.assertFalse(any(m.split('.')[0] in ('mcp','baidu_ads_mcp') for m in modules),str(path.relative_to(ROOT)))
        dependency=(ROOT/'requirements.txt').read_text().lower()
        self.assertFalse(any(line.strip().startswith(('mcp','baidu-ads-mcp')) for line in dependency.splitlines()))
        # Only core-owned deployment/collection workflows and tasks are inspected.
        tasks=[p for p in (ROOT/'scripts').glob('*.ps1') if 'mcp' not in p.name.lower()]
        workflows=[ROOT/'.github/workflows'/name for name in ('collect.yml','pages.yml')]
        for path in [*tasks,*workflows]:
            if path.exists():
                value=path.read_text(encoding='utf-8').lower()
                self.assertNotIn('baidu_ads_mcp',value,str(path.relative_to(ROOT)))
                for forbidden in ('services/mcp','baidu-ads-mcp','run_mcp','core-reader'):
                    self.assertNotIn(forbidden,value,str(path.relative_to(ROOT)))


class ReaderAndImportFaults(Fixtures,unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.setup()

    async def test_reader_service_stopped_fails_without_core_change(self):
        before=hashes(self.core)
        async def handler(request):raise httpx.ConnectError('synthetic stopped adapter')
        transport=httpx.MockTransport(handler)
        def client(**kwargs):return REAL_CLIENT(**kwargs,transport=transport)
        with patch('baidu_ads_mcp.auth.httpx.AsyncClient',side_effect=client):
            bridge=Bridge('http://127.0.0.1:8798','s'*43,allow_loopback=True)
            with self.assertRaisesRegex(QueryError,'IDENTITY_BRIDGE_UNAVAILABLE'):
                await bridge.verify({'subject':'synthetic','version':'v1'})
        self.core_probe();self.assertEqual(before,hashes(self.core))

    async def test_reader_slow_response_has_absolute_timeout(self):
        before=hashes(self.core)
        cancelled=asyncio.Event()
        async def handler(request):
            try:await asyncio.sleep(60)
            except asyncio.CancelledError:cancelled.set();raise
        transport=httpx.MockTransport(handler)
        def client(**kwargs):return REAL_CLIENT(**kwargs,transport=transport)
        started=time.monotonic()
        with patch('baidu_ads_mcp.auth.httpx.AsyncClient',side_effect=client):
            bridge=Bridge('http://127.0.0.1:8798','s'*43,allow_loopback=True)
            with self.assertRaisesRegex(QueryError,'IDENTITY_BRIDGE_UNAVAILABLE'):
                await bridge.verify({'subject':'synthetic','version':'v1'})
        self.assertTrue(cancelled.is_set());self.assertLess(time.monotonic()-started,7)
        self.core_probe();self.assertEqual(before,hashes(self.core))

    async def test_corrupt_mcp_cache_preserves_source_and_loaded_last_good(self):
        current=await import_release(self.bridge,{},self.cache)
        before=hashes(self.core);pointer=(self.cache/'current.json').read_bytes()
        target=self.cache/'releases'/self.release.name/'report/index.html'
        target.write_text('corrupted MCP cache only')
        with self.assertRaisesRegex(QueryError,'INVALID_SNAPSHOT'):await import_release(self.bridge,{},self.cache)
        self.assertEqual(pointer,(self.cache/'current.json').read_bytes())
        self.assertEqual(current.summary('tinacms','2026-11-14','2026-11-14')['observed_totals']['cost'],'0.100000')
        self.core_probe();self.assertEqual(before,hashes(self.core))

    async def test_source_format_incompatible_preserves_last_good_no_core_rollback(self):
        current=await import_release(self.bridge,{},self.cache)
        pointer=(self.cache/'current.json').read_bytes()
        path=self.release/'report/index.html';path.write_text('<html>incompatible future source</html>')
        injected=hashes(self.core)
        with self.assertRaisesRegex(QueryError,'INVALID_SNAPSHOT'):await import_release(self.bridge,{},self.cache)
        self.assertEqual(pointer,(self.cache/'current.json').read_bytes())
        self.assertEqual(current.summary('tinacms','2026-11-14','2026-11-14')['observed_totals']['cost'],'0.100000')
        self.core_probe();self.assertEqual(injected,hashes(self.core))

    async def test_source_deleted_mid_import_leaves_no_partial_cache(self):
        current=await import_release(self.bridge,{},self.cache)
        pointer=(self.cache/'current.json').read_bytes()
        async def remove_last(account):
            if account=='yakshaver':(self.release/'report/ssw/index.html').unlink(missing_ok=True)
        self.bridge.fail=remove_last
        with self.assertRaisesRegex(QueryError,'SOURCE_RELEASE_REMOVED'):await import_release(self.bridge,{},self.cache)
        self.assertEqual(pointer,(self.cache/'current.json').read_bytes())
        self.assertFalse(list(self.cache.glob('.import-*')))
        self.assertFalse((self.release/'report/ssw/index.html').exists())
        self.assertEqual(current.summary('ssw','2026-11-14','2026-11-14')['observed_totals']['cost'],'0.100000')
        self.core_probe()
