"""Shared execution assembly for HTTP enqueue and the independent worker.

Only the MCP deployment's private configuration and independent provider
credentials are read. Importing or constructing this factory makes no API call.
"""
import asyncio
import json
import os
from pathlib import Path

from .baidu_write import BaiduWriteAdapter
from .capabilities import CapabilityRegistry
from .executor import ExecutionQueue
from .query import QueryError


def build_execution_queue(store, provider, config_path):
    """Return a fully wired queue; every mutation still defaults to disabled.

    Queue operations are synchronous and must run in ``asyncio.to_thread`` from
    HTTP routes. Both HTTP and worker use the exact OAuth revocation, original
    scope, client registration and current identity checks in resolve_actor.
    Capability records are independent, explicit, and never created here.
    """
    path=Path(config_path)
    config=json.loads(path.read_text())
    modes=config.get('verified_manual_bid_modes',{})
    if not isinstance(modes,dict) or any(not isinstance(values,list) or any(type(v) is not int for v in values) for values in modes.values()):
        raise ValueError('Invalid verified manual bid modes')

    def identity(subject):
        async def resolve():
            async with asyncio.timeout(5):
                return await provider.resolve_actor(subject)
        return asyncio.run(resolve())

    def enabled():
        # Fail closed on removal, corrupt config, or a non-boolean value. An
        # operator disabling the file switch takes effect before the next POST.
        try:return json.loads(path.read_text()).get('mutations_enabled') is True
        except (OSError,ValueError,AttributeError):return False

    def credentials(account):
        try:
            value=json.loads(os.environ['MCP_BAIDU_WRITE_CREDENTIALS_JSON'])[account]
            if not isinstance(value,dict):raise TypeError()
            return value
        except (KeyError,ValueError,TypeError):
            raise QueryError('PROVIDER_CREDENTIALS_UNAVAILABLE') from None

    adapter=BaiduWriteAdapter(credentials,enabled=enabled,manual_bid_modes=modes)
    queue=ExecutionQueue(store,CapabilityRegistry(store),adapter,identity,
                         mutations_enabled=enabled)
    from .automatic_rules import AutomaticRules
    def automatic_enabled():
        try:return json.loads(path.read_text()).get('automatic_mutations_enabled') is True
        except (OSError,ValueError,AttributeError):return False
    queue.automatic_rules=AutomaticRules(store,queue,enabled=automatic_enabled)
    queue.pre_execution_guard=queue.automatic_rules.guard
    return queue
