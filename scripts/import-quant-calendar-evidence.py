#!/usr/bin/env python3
"""Operator-only import of reviewed official calendar notices; never deploy or fetch."""
import argparse
import json
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--db', type=Path, required=True)
parser.add_argument('--owner', required=True)
parser.add_argument('--label', required=True)
parser.add_argument('--manifest', type=Path, required=True)
parser.add_argument('--evidence-dir', type=Path, required=True)
args = parser.parse_args()
if not args.db.is_absolute():
    parser.error('须显式指定绝对SQLite路径')
os.environ['DEEPFOCUS_QUANT_RESEARCH_DB_PATH'] = str(args.db)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_calendar_store import put, MAX_SOURCE

manifest = json.loads(args.manifest.read_text())
root, sources = args.evidence_dir.resolve(), {}
for sha in {r['source_sha256'] for r in manifest['records']}:
    file = (root/manifest['source_files'][sha]).resolve()
    if file.parent != root or not file.is_file() or file.stat().st_size > MAX_SOURCE:
        parser.error('日历原文缺失、越界或过大')
    sources[sha] = file.read_bytes()
print(json.dumps(put(args.owner, args.label, manifest, sources), ensure_ascii=False))
