#!/usr/bin/env python3
"""Collect SSW contact-goal source aggregates; credentials remain on ECS."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from contact_attribution import FILENAME, PAID_SOURCE, fetch_rows, load_snapshot, validate_rows
from scripts.sync_website_conversions import BEIJING, SECRET, SSW_SITE_CONFIG, tongji, validate_site, SSW_HOSTS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--end', type=dt.date.fromisoformat)
    args = parser.parse_args()
    try:
        config = json.loads(os.environ[SECRET])
        site = json.loads(os.environ[SSW_SITE_CONFIG])
        config.update(site)
        validate_site(config, SSW_HOSTS, 'SSW China')
        end = args.end or dt.datetime.now(BEIJING).date() - dt.timedelta(days=1)
        if end >= dt.datetime.now(BEIJING).date():
            raise ValueError('Only complete Beijing dates may be published')
        start = max(dt.date.fromisoformat(config['start_date']), end - dt.timedelta(days=89))
        path = ROOT / 'accounts' / 'ssw' / 'data' / FILENAME
        old = load_snapshot(path)
        rows = fetch_rows(config, start, end, tongji)
        prior = [r for r in old['rows'] if r['date'] < start.isoformat()] if old else []
        # Keep this bounded rolling snapshot; no visitor identifiers or raw responses.
        rows = validate_rows(prior + rows)[-90:]
        snapshot = {'schema_version': 1, 'source': 'baidu_tongji.source/all/a', 'paid_source': PAID_SOURCE,
                    'timezone': 'Asia/Shanghai', 'goal_id': config['contact_click_target'],
                    'generated_at': dt.datetime.now(BEIJING).isoformat(), 'through': end.isoformat(), 'rows': rows}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        temporary.replace(path)
    except (ValueError, KeyError, TypeError, IndexError, OSError):
        sys.exit('SSW contact attribution was not updated; previous snapshot preserved. Check source coverage and goal access.')
    print(f'SSW contact attribution saved: {len(rows)} complete daily aggregates through {end}.')


if __name__ == '__main__':
    main()
