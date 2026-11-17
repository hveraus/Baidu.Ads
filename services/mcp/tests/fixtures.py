"""Synthetic fixtures only; no production data or identity material."""
import json
from pathlib import Path

REVISION = 'a' * 32


def make_release(root):
    release = Path(root) / REVISION
    data = {'generated': '2026-11-17T09:00:00+08:00', 'reports': {
        'account': [
            {'date': '2026-11-14', 'cost': '0.10', 'click': 1, 'impression': 10},
            {'date': '2026-11-16', 'cost': '0.20', 'click': 3, 'impression': 30}],
        'campaign': [
            {'date': '2026-11-14', 'campaignId': 1, 'campaignName': '同名', 'cost': '0.10', 'click': 1, 'impression': 10},
            {'date': '2026-11-14', 'campaignId': 2, 'campaignName': '同名', 'cost': '0.20', 'click': 2, 'impression': 20}],
        'keyword': [
            {'date': '2026-11-14', 'campaignId': 1, 'adGroupId': 2, 'wInfoId': 3,
             'wInfoNameStatus': '原始关键词', 'cost': '0', 'click': 0, 'impression': 0}],
        'search_terms': [
            {'date': '2026-11-14', 'campaignId': 1, 'adGroupId': 2, 'wInfoId': 3,
             'queryWord': '原始搜索词', 'cost': '0', 'click': 0, 'impression': 0}],
    }, 'encrypted_posthog_cli': 'PRIVATE_SENTINEL', 'unknown_secret': 'SECRET_SENTINEL'}
    for relative in ('report/index.html', 'report/yakshaver/index.html', 'report/ssw/index.html'):
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('<script id="report-data" type="application/json">' + json.dumps(data) + '</script>')
    return release
