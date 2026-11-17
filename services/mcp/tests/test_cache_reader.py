import json
from datetime import datetime,timezone
from pathlib import Path
import tempfile
import unittest

from baidu_ads_mcp.cache_reader import load_runtime_query
from baidu_ads_mcp.query import QueryError,ReleaseQuery
from fixtures import make_release


class CacheReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.cache=root/'cache';self.now=10000
        self.fallback=make_release(root/'old')
        self.release=make_release(self.cache/'releases');self.release=self.release.rename(self.release.with_name('b'*32))
        self.query=ReleaseQuery(self.release,clock=lambda:self.now)
        (self.cache/'.mcp-cache').write_text('baidu-ads-mcp-cache-v1')
        self.manifest={'release_id':self.release.name,'hashes':self.query.hashes,'fetched_at':datetime.fromtimestamp(self.now-60,timezone.utc).isoformat()}
        self.pointer=self.cache/'current.json';self.write()
        self.config={'cache_directory':str(self.cache),'release_copy':str(self.fallback)}
    def write(self):self.pointer.write_text(json.dumps(self.manifest))
    def load(self):return load_runtime_query(self.config,clock=lambda:self.now)
    def test_current_cache_takes_priority_and_restart_preserves_fetch_age(self):
        result=self.load()
        self.assertEqual(result.release_id,'b'*32)
        self.assertEqual(result.loaded_at,self.now-60)
    def test_missing_or_empty_cache_uses_explicit_copy(self):
        for path in (Path(self.tmp.name)/'absent',Path(self.tmp.name)/'empty'):
            if path.name=='empty':path.mkdir()
            result=load_runtime_query({**self.config,'cache_directory':str(path)})
            self.assertEqual(result.release_id,self.fallback.name)
    def test_invalid_pointer_never_falls_back(self):
        for body in ('{','{}',json.dumps({**self.manifest,'release_id':'../core'})):
            self.pointer.write_text(body)
            with self.assertRaisesRegex(QueryError,'INVALID_CACHE_POINTER'):self.load()
    def test_hash_corruption_never_falls_back(self):
        self.manifest['hashes']['ssw']='0'*64;self.write()
        with self.assertRaisesRegex(QueryError,'CACHE_HASH_MISMATCH'):self.load()
    def test_stale_manifest_cannot_be_refreshed_by_restart(self):
        self.now+=3600
        with self.assertRaisesRegex(QueryError,'SNAPSHOT_EXPIRED'):self.load()
    def test_http_initialization_preserves_expired_data_rejection(self):
        self.now+=3600
        query=load_runtime_query(self.config,clock=lambda:self.now,allow_expired=True)
        self.assertEqual(query.loaded_at,9940)
        for operation in (query.accounts,query.status,lambda:query.summary('tinacms','2026-11-14','2026-11-16')):
            with self.assertRaisesRegex(QueryError,'SNAPSHOT_EXPIRED'):operation()
        self.manifest['hashes']['ssw']='0'*64;self.write()
        with self.assertRaisesRegex(QueryError,'CACHE_HASH_MISMATCH'):
            load_runtime_query(self.config,clock=lambda:self.now,allow_expired=True)
    def test_foreign_or_linked_cache_rejected(self):
        (self.cache/'.mcp-cache').write_text('not-mcp')
        with self.assertRaisesRegex(QueryError,'FOREIGN_CACHE_DIRECTORY'):self.load()
        (self.cache/'.mcp-cache').write_text('baidu-ads-mcp-cache-v1')
        self.pointer.unlink();self.pointer.symlink_to(Path(self.tmp.name)/'other')
        with self.assertRaisesRegex(QueryError,'INVALID_CACHE_POINTER'):self.load()
