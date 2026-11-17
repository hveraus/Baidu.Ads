from pathlib import Path
from unittest.mock import patch

from baidu_ads_mcp.runtime import build_execution_queue
from baidu_ads_mcp.query import QueryError
from test_analytics_changes import Base,OWNER


class RuntimeTests(Base):
    def test_default_off_dynamic_switch_and_shared_identity_resolver(self):
        class Provider:
            async def resolve_actor(self,subject):
                self.subject=subject
                return OWNER
        provider=Provider()
        config=Path(self.tmp.name)/'execution-config.json';config.write_text('{}')
        queue=build_execution_queue(self.store,provider,config)
        self.assertFalse(queue.enabled())
        self.assertEqual(queue.identity_resolver('kenshi'),OWNER)
        self.assertEqual(provider.subject,'kenshi')
        self.assertEqual(queue.registry.describe('tinacms'),[])
        config.write_text('{"mutations_enabled":true}')
        self.assertTrue(queue.enabled())
        config.write_text('{"mutations_enabled":"true"}')
        self.assertFalse(queue.enabled())
        config.unlink()
        self.assertFalse(queue.enabled())

    def test_provider_revocation_is_not_bypassed(self):
        class Provider:
            async def resolve_actor(self,subject):raise QueryError('UNAUTHORIZED')
        config=Path(self.tmp.name)/'execution-config.json';config.write_text('{}')
        queue=build_execution_queue(self.store,Provider(),config)
        with self.assertRaisesRegex(QueryError,'UNAUTHORIZED'):queue.identity_resolver('kenshi')
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaisesRegex(QueryError,'PROVIDER_CREDENTIALS_UNAVAILABLE'):queue.adapter.credentials('tinacms')
