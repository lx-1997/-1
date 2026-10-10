import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_caddy_routes_every_declared_backend_public_page():
    from fastapi.routing import APIRoute
    from deepfocus_api.main import app

    config = (ROOT / 'deploy/Caddyfile').read_text()
    patterns = next(line.strip().split()[2:] for line in config.splitlines() if line.strip().startswith('@backend path '))
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = re.sub(r'\{[^}]+\}', 'sample', route.path)
        assert any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns), f'backend route sent to SPA: {route.path}'


def test_container_cloud_requirements_include_cold_start_modules():
    requirements = (ROOT / 'backend/requirements-cloud.txt').read_text().casefold()
    for dependency in ('sqlalchemy==', 'passlib==', 'python-jose[cryptography]==', 'pikepdf==', 'pymupdf=='):
        assert dependency in requirements
    dockerfile = (ROOT / 'deploy/Dockerfile.backend').read_text()
    assert 'DEEPFOCUS_DATA_DIR=/data' in dockerfile
    assert 'PYTHONPATH=/app/backend' in dockerfile
    assert 'COPY backend/ /app/backend/' in dockerfile
