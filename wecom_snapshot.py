"""Encrypted SSW WeChat Customer Service aggregates shared with the dashboard."""
from pathlib import Path


FILENAME = 'wecom-consultations.enc'
HEADER = b'BAIDU-ADS-WECOM-v1\n'
HISTORY_DAYS = 90


def read_encrypted_snapshot(path: Path):
    if not path.exists():
        return None
    payload = path.read_bytes()
    if not payload.startswith(HEADER) or not len(HEADER) + 28 < len(payload) <= 16_384:
        raise ValueError('Unexpected encrypted WeCom snapshot format')
    return payload
