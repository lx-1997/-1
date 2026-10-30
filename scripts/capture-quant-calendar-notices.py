#!/usr/bin/env python3
"""Retain the eight official annual A-share closure notices; no calendar guessing."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
import httpx

SOURCES = {
    'SSE-2023': 'https://www.sse.com.cn/disclosure/announcement/general/c/c_20221227_5714458.shtml',
    'SSE-2024': 'https://www.sse.com.cn/disclosure/announcement/general/c/c_20231226_5733939.shtml',
    'SSE-2025': 'https://www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml',
    'SSE-2026': 'https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml',
    'SZSE-2023': 'https://www.szse.cn/disclosure/notice/general/t20221227_598022.html',
    'SZSE-2024': 'https://www.szse.cn/disclosure/notice/t20231226_605108.html',
    'SZSE-2025': 'https://www.szse.cn/disclosure/notice/general/t20241223_611283.html',
    'SZSE-2026': 'https://investor.szse.cn/disclosure/notice/general/t20251222_618087.html',
}


def capture(key, url, root):
    item = {'id': key, 'url': url, 'observed_at': datetime.now(timezone.utc).isoformat()}
    try:
        with httpx.Client(trust_env=False, timeout=25, follow_redirects=False,
                          headers={'User-Agent': 'Mozilla/5.0'}) as client:
            body = bytearray()
            with client.stream('GET', url) as response:
                item['http_status'] = response.status_code
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 2*1024*1024:
                        raise ValueError('Notice body exceeds 2MiB')
        raw = bytes(body)
        html = BeautifulSoup(raw, 'html.parser')
        plain = html.get_text('\n', strip=True)
        compact = re.sub(r'\s+', '', plain)
        year = key.split('-')[1]
        if year+'年' not in compact or '休市安排' not in compact or '照常开市' not in compact:
            raise ValueError('Notice identity/content not recognized; manual review required')
        name = key+'.html'
        with (root/name).open('xb') as stream:
            stream.write(raw)
        with (root/(key+'.txt')).open('x') as stream:
            stream.write(plain)
        item.update(file=name, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                    host=urlsplit(url).hostname, status='retained_unreviewed')
    except Exception as exc:
        item.update(status='failed', error=type(exc).__name__)
    return item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    if not args.workspace.is_absolute() or args.workspace.exists():
        parser.error('Use a new absolute workspace; preserve prior attempts')
    args.workspace.mkdir(parents=True, mode=0o700)
    source = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(capture, key, url, args.workspace) for key, url in SOURCES.items()]
        rows = [f.result() for f in futures]
    manifest = {'schema': 'official-closure-notice-captures-v1', 'captures': rows,
                'runner_sha256': source, 'manually_reviewed': False,
                'unexpected_closure_search_complete': False}
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != source:
        manifest['error'] = 'runner_changed'
    with (args.workspace/'manifest.json').open('x') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'retained': sum(r['status']=='retained_unreviewed' for r in rows),
                      'failed': [r['id'] for r in rows if r['status']=='failed']}))
    return int('error' in manifest or any(r['status']=='failed' for r in rows))


if __name__ == '__main__':
    raise SystemExit(main())
