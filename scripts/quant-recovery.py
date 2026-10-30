#!/usr/bin/env python3
"""Prepare/verify a NEW quarantined QR copy. Never activate or overwrite a database."""
import argparse,json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_recovery import prepare,verify


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    create=sub.add_parser('prepare');create.add_argument('--source',type=Path,required=True);create.add_argument('--workspace',type=Path,required=True)
    create.add_argument('--max-bytes',type=int,default=1024*1024*1024);create.add_argument('--timeout',type=float,default=60)
    check=sub.add_parser('verify');check.add_argument('--workspace',type=Path,required=True)
    args=parser.parse_args()
    try:
        result=prepare(args.source,args.workspace,max_bytes=args.max_bytes,timeout=args.timeout) if args.command=='prepare' else verify(args.workspace)
        print(json.dumps(result,ensure_ascii=False));return 0
    except Exception as exc:
        print(json.dumps({'ok':False,'error_type':type(exc).__name__,'message':'Recovery not verified; retain the private failed workspace','activation_authorized':False}));return 1


if __name__=='__main__':raise SystemExit(main())
