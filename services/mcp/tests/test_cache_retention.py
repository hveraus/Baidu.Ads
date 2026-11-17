from pathlib import Path
import tempfile
import unittest
from baidu_ads_mcp.query import QueryError
from baidu_ads_mcp.sync import prune_cache, cache_writer


class CacheRetentionTests(unittest.TestCase):
    def test_active_release_preserved_and_old_releases_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            for number in range(6):
                root = cache/'releases'/f'{number:032x}'/'report'
                root.mkdir(parents=True)
                (root/'index.html').write_text('synthetic')
            prune_cache(cache, f'{0:032x}')
            self.assertTrue((cache/'releases'/f'{0:032x}').exists())
            self.assertEqual(len(list((cache/'releases').iterdir())), 3)

    def test_foreign_file_never_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            root = cache/'releases'/('a'*32)
            root.mkdir(parents=True)
            private = root/'unexpected.sqlite3'
            private.write_text('must remain')
            with self.assertRaisesRegex(QueryError, 'FOREIGN_CACHE_DIRECTORY'):
                prune_cache(cache)
            self.assertEqual(private.read_text(), 'must remain')

    def test_abandoned_stage_recovery_and_exclusive_import(self):
        with tempfile.TemporaryDirectory() as folder:
            cache=Path(folder)
            stage=cache/'.import-abandoned';stage.mkdir()
            (stage/'partial').write_text('synthetic')
            with cache_writer(cache):
                self.assertFalse(stage.exists())
                with self.assertRaisesRegex(QueryError,'CACHE_REFRESH_BUSY'):
                    with cache_writer(cache):
                        self.fail('second importer must not enter')
            with cache_writer(cache):
                pass
