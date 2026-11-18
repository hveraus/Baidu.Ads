import unittest
from baidu_ads_mcp.auth import Bridge


class BridgeTransportTests(unittest.TestCase):
    def test_explicit_same_host_transport(self):
        bridge = Bridge('http://127.0.0.1:8798', 'a' * 43, allow_loopback=True)
        self.assertEqual(bridge.url, 'http://127.0.0.1:8798')

    def test_cleartext_rejected_without_opt_in(self):
        with self.assertRaises(ValueError):
            Bridge('http://127.0.0.1:8798', 'a' * 43)

    def test_opt_in_never_allows_remote_cleartext(self):
        for host in ('localhost', '0.0.0.0', '106.14.246.176', '127.0.0.1.example.com'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                Bridge('http://' + host + ':8798', 'a' * 43, allow_loopback=True)
