#!/usr/bin/env python3
"""Operator-only immutable PDF evidence import; no public upload or network fetch."""
import argparse
import json
import os
from pathlib import Path
import sys

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--db',type=Path,required=True)
p.add_argument('--owner',required=True)
p.add_argument('--manifest',type=Path,required=True)
p.add_argument('--evidence-dir',type=Path,required=True)
p.add_argument('--label',required=True)
a=p.parse_args()
if not a.db.is_absolute():p.error('须显式指定绝对 SQLite 路径')
os.environ['DEEPFOCUS_QUANT_RESEARCH_DB_PATH']=str(a.db)
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_corporate_actions import CashDividend
from deepfocus_api.quant_evidence_store import put

m=json.loads(a.manifest.read_text());events=[CashDividend(**e) for e in m['events']]
root=a.evidence_dir.resolve();sources={}
for sha in {e.source_sha256 for e in events}:
    file=(root/m['source_files'][sha]).resolve()
    if root not in file.parents or not file.is_file() or file.stat().st_size>10*1024*1024:p.error('原文路径越界、缺失或过大')
    sources[sha]=file.read_bytes()
result=put(a.owner,a.label,events,sources,completeness_attested=m.get('completeness_attested',False))
print(json.dumps(result,ensure_ascii=False))
