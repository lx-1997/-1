#!/usr/bin/env python3
"""Replay exactly the registered snapshot. Refuse to overwrite evidence."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from deepfocus_api.macro_insight_study import evaluate

protocol_path = ROOT / 'docs/macro-insight-study/protocol-20261110.json'
protocol_bytes = protocol_path.read_bytes()
protocol = json.loads(protocol_bytes)
data_path = ROOT / protocol['dataset']
raw = data_path.read_bytes()
data = json.loads(raw)
result = evaluate(data['series'])
result['provenance'] = {
    'dataset_sha256': hashlib.sha256(raw).hexdigest(), 'captured_at': data['captured_at'],
    'protocol_sha256': hashlib.sha256(protocol_bytes).hexdigest(),
    'source_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in (
        'backend/deepfocus_api/macro_insight_study.py', 'backend/deepfocus_api/market_regime.py')},
    'providers': data['sources'],
}
output = ROOT / 'backend/deepfocus_api/data/macro_insight_study_v1.json'
with output.open('x') as f:
    json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
print(json.dumps({k: result[k] for k in ('start','split_date','as_of','holdout_days')}, ensure_ascii=False))
for r in result['candidates']:
    print(r['name'], r['holdout'], 'passed:', r['screen_passed'])
