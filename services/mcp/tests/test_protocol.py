"""Real SDK client -> stdio child process -> tools, with synthetic data."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from fixtures import make_release


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_tools_queries_and_errors(self):
        with tempfile.TemporaryDirectory() as root:
            release = make_release(root)
            source = Path(__file__).resolve().parents[1] / 'src'
            params = StdioServerParameters(command=sys.executable,
                args=['-m', 'baidu_ads_mcp.server', '--release-copy', str(release)],
                env={**os.environ, 'PYTHONPATH': str(source)})
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    tools = (await client.list_tools()).tools
                    self.assertEqual(len(tools), 46)
                    by_name={tool.name:tool for tool in tools}
                    for name in ('baidu_ads_execute_change_plan','baidu_ads_trigger_automatic_rule'):
                        self.assertTrue(by_name[name].annotations.destructiveHint)
                        self.assertTrue(by_name[name].annotations.openWorldHint)
                    self.assertTrue(by_name['baidu_ads_execute_change_plan'].annotations.idempotentHint)
                    self.assertFalse(by_name['baidu_ads_trigger_automatic_rule'].annotations.idempotentHint)
                    self.assertTrue(all(t.annotations.readOnlyHint for t in tools if t.name in ('baidu_ads_get_summary','baidu_ads_get_performance')))
                    self.assertNotIn('baidu_ads_approve_change_plan', [t.name for t in tools])
                    self.assertTrue(all(t.outputSchema for t in tools))
                    accounts = await client.call_tool('baidu_ads_list_accounts', {})
                    self.assertFalse(accounts.isError)
                    self.assertEqual(len(accounts.structuredContent['accounts']), 3)
                    summary = await client.call_tool('baidu_ads_get_summary', {'params': {
                        'account': 'tinacms', 'start': '2026-11-14', 'end': '2026-11-16'}})
                    self.assertFalse(summary.isError)
                    self.assertEqual(summary.structuredContent['observed_totals']['cost'], '0.300000')
                    self.assertEqual(summary.structuredContent['missing_dates'], ['2026-11-15'])
                    bad = await client.call_tool('baidu_ads_get_summary', {'params': {
                        'account': 'tinacms', 'start': 'bad', 'end': '2026-11-16'}})
                    self.assertTrue(bad.isError)
                    self.assertIn('INVALID_DATE', bad.content[0].text)
                    extra = await client.call_tool('baidu_ads_get_summary', {'params': {
                        'account': 'tinacms', 'start': '2026-11-14', 'end': '2026-11-16', 'confirmed': True}})
                    self.assertTrue(extra.isError)
                    perf = {'account': 'tinacms', 'start': '2026-11-14', 'end': '2026-11-16', 'dimension': 'campaign', 'limit': 1}
                    first = await client.call_tool('baidu_ads_get_performance', {'params': perf})
                    perf['cursor'] = first.structuredContent['next_cursor']
                    second = await client.call_tool('baidu_ads_get_performance', {'params': perf})
                    self.assertEqual(second.structuredContent['items'][0]['entity']['campaignId'], 1)
                    self.assertNotIn('SENTINEL', json.dumps(summary.structuredContent))
                    for name in ('baidu_ads_get_metric_catalog', 'baidu_ads_get_data_status'):
                        self.assertFalse((await client.call_tool(name, {})).isError)


if __name__ == '__main__':
    unittest.main()
