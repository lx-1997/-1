#!/usr/bin/env python3
"""Seal a read-only complete-line journal prefix into a NEW data-only workspace.

The producer may keep appending. This checkpoint never claims full coverage,
never executes archived code and never edits/cancels the original capture.
"""
import argparse,hashlib,json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api import quant_historical_universe as u

LOADED_TOOL_SHA=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def seal(source,workspace):
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=LOADED_TOOL_SHA:raise ValueError('Prefix tool source changed')
    source,workspace=Path(source),Path(workspace)
    if not source.is_absolute() or not source.is_dir() or not workspace.is_absolute() or workspace.exists():
        raise ValueError('Existing absolute source and new absolute workspace required')
    plan_raw=(source/'capture-plan.json').read_bytes();plan=json.loads(plan_raw);scope=plan['scope']
    journal=source/'completed-queries.jsonl'
    if journal.stat().st_size>16*1024*1024:raise ValueError('Prefix journal exceeds 16MiB budget')
    raw=journal.read_bytes();end=raw.rfind(b'\n')+1
    if end==0:raise ValueError('No complete query entry available')
    entries=[json.loads(line) for line in raw[:end].splitlines()]
    expected=[{'method':'query_stock_basic','code':''}]+[{'method':'query_all_stock','day':d} for d in scope['days']]+[{'method':'query_stock_basic','code':''}]
    if not entries or len(entries)>len(expected) or [e['query'] for e in entries]!=expected[:len(entries)]:
        raise ValueError('Journal is not a complete ordered query prefix')
    u.require_current_source();workspace.mkdir(mode=0o700)
    try:
        files=set()
        for entry in entries:
            u.verify_entry(source,entry)
            for frame in entry['frames']:
                name=frame['file']
                if name in files or Path(name).name!=name:raise ValueError('Reused or out-of-scope prefix source')
                files.add(name);packed=(source/name).read_bytes()
                if hashlib.sha256(packed).hexdigest()!=frame['gzip_sha256']:raise ValueError('Prefix source changed during copy')
                with (workspace/name).open('xb') as f:f.write(packed)
            u.verify_entry(workspace,entry)
        (workspace/'source-capture-plan.json').write_bytes(plan_raw)
        (workspace/'source-journal-snapshot.jsonl').write_bytes(raw)
        u.require_current_source()
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=LOADED_TOOL_SHA:raise ValueError('Prefix tool source changed')
        out={'schema':u.VERSION,'scope':scope,'results':entries,'collector_sha256':plan['collector_sha256'],
             'source_pins':plan['source_pins'],'errors':[{'type':'PrefixCheckpointNotFullCapture'}],
             'checkpoint':{'source_plan_sha256':hashlib.sha256(plan_raw).hexdigest(),
                           'journal_snapshot_sha256':hashlib.sha256(raw).hexdigest(),
                           'complete_entry_count':len(entries),'trailing_partial_bytes_preserved':len(raw)-end,
                           'source_may_continue_appending':True,'source_writes':False,
                           'source_pins':dict(u.SOURCE_PINS),'tool_sha256':LOADED_TOOL_SHA},
             'full_capture_complete':False,'historical_pit_universe_certified':False,'promotion_allowed':False}
        with (workspace/'manifest.json').open('x') as f:json.dump(out,f,ensure_ascii=False,indent=2)
        return {'complete_entries':len(entries),'daily_entries':sum(e['query']['method']=='query_all_stock' for e in entries),
                'trailing_partial_bytes_preserved':len(raw)-end,'full_capture_complete':False,'source_writes':False}
    except BaseException as exc:
        with (workspace/'FAILED.json').open('x') as f:json.dump({'error_type':type(exc).__name__,'full_capture_complete':False},f)
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--capture-dir',type=Path,required=True);p.add_argument('--workspace',type=Path,required=True);a=p.parse_args()
    try:print(json.dumps(seal(a.capture_dir,a.workspace)));return 0
    except (ValueError,KeyError,TypeError,OSError) as exc:print(json.dumps({'ok':False,'error':str(exc)}));return 1


if __name__=='__main__':raise SystemExit(main())
