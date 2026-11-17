import hashlib
from pathlib import Path
import tempfile
import unittest
from baidu_ads_mcp.sync import import_release
from baidu_ads_mcp.query import QueryError
from fixtures import make_release


class SyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_import_and_last_good_on_failure(self):
        with tempfile.TemporaryDirectory() as root:
            source=make_release(Path(root)/'source');cache=Path(root)/'cache'
            class Bridge:
                fail=False
                async def verify(self,identity):pass
                async def call(self,action,**kwargs):
                    if action=='current':return {'release_id':source.name}
                    account=kwargs['account']
                    if self.fail and account=='ssw':raise QueryError('SOURCE_CLEANED_UP')
                    relative='report/index.html' if account=='tinacms' else f'report/{account}/index.html'
                    raw=(source/relative).read_bytes()
                    return {'release_id':source.name,'html':raw.decode(),'sha256':hashlib.sha256(raw).hexdigest()}
            bridge=Bridge()
            query=await import_release(bridge,{},cache)
            before=(cache/'current.json').read_bytes()
            self.assertEqual(query.release_id,source.name)
            bridge.fail=True
            with self.assertRaisesRegex(QueryError,'SOURCE_CLEANED_UP'):await import_release(bridge,{},cache)
            self.assertEqual(before,(cache/'current.json').read_bytes())
            self.assertEqual(query.performance('tinacms','2026-11-14','2026-11-14')['observed_totals']['cost'],'0.100000')
            self.assertFalse(list(cache.glob('.import-*')))

    async def test_foreign_cache_untouched(self):
        with tempfile.TemporaryDirectory() as root:
            marker=Path(root)/'current';marker.write_text('core-release')
            with self.assertRaisesRegex(QueryError,'FOREIGN_CACHE_DIRECTORY'):
                await import_release(None,{},Path(root))
            self.assertEqual(marker.read_text(),'core-release')
