"""Build the public Pages entry without report data once sign-in is deployed."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from login_dashboard import BASE_URL, REPORT_PAGES


def build(output):
    for route, (_, lang) in REPORT_PAGES.items():
        destination = output / route.lstrip('/')
        if route.endswith('/'):
            destination /= 'index.html'
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = BASE_URL + route
        label = 'Open Baidu.Ads' if lang == 'en' else '打开 Baidu.Ads 看板'
        destination.write_text(f'''<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<meta http-equiv="refresh" content="0;url={target}"><title>{label}</title></head>
<body><main><a href="{target}">{label}</a></main></body></html>''', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    build(parser.parse_args().output)
