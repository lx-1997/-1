#!/usr/bin/env python3
"""Validate generated API refs, route uniqueness and critical product contracts."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


def validate_contract(app):
    from fastapi.routing import APIRoute
    seen = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            key = (method, route.path)
            if key in seen:
                raise AssertionError(f'duplicate route: {method} {route.path}')
            seen.add(key)
    spec = app.openapi()
    operation_ids = set()
    for path, methods in spec['paths'].items():
        for method, operation in methods.items():
            if method not in {'get', 'post', 'put', 'delete', 'patch', 'options', 'head', 'trace'}:
                continue
            op_id = operation.get('operationId')
            if not op_id or op_id in operation_ids:
                raise AssertionError(f'non-unique operationId: {method} {path}: {op_id}')
            operation_ids.add(op_id)
    def walk(value):
        if isinstance(value, dict):
            ref = value.get('$ref')
            if ref:
                if not ref.startswith('#/'):
                    raise AssertionError(f'non-local schema ref: {ref}')
                resolved = spec
                for part in ref[2:].split('/'):
                    resolved = resolved[part.replace('~1', '/').replace('~0', '~')]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(spec)
    required = {('/api/auth/login', 'post'), ('/api/quant/lab/jobs', 'post'),
                ('/api/quant/lab/jobs/{job_id}', 'get'), ('/api/backtest/{backtest_id}/run', 'post'),
                ('/api/share/snapshots', 'post')}
    missing = {(path, method) for path, method in required if method not in spec['paths'].get(path, {})}
    if missing:
        raise AssertionError(f'missing required API contracts: {sorted(missing)}')
    return spec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / 'backend'))
    # Import never touches an existing deployment's runtime stores.
    with tempfile.TemporaryDirectory(prefix='daocaijing-contract-') as directory:
        os.environ['DEEPFOCUS_DATA_DIR'] = directory
        os.environ['DEEPFOCUS_MODEL_CONFIG_PATH'] = str(Path(directory) / 'model.json')
        os.environ['DEEPFOCUS_LLM_PROVIDER'] = 'mock'
        os.environ['DEEPFOCUS_RESEARCH_WORKBENCH_AUTOSTART'] = '0'
        os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
        from deepfocus_api.main import app
        spec = validate_contract(app)
    if args.output:
        args.output.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + '\n')
    print(f'API contract passed: {len(spec["paths"])} paths, {len(spec.get("components", {}).get("schemas", {}))} schemas')


if __name__ == '__main__':
    main()
