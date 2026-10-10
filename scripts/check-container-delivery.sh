#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../deploy"
# This disposable Compose project uses only sample config and isolated volumes.
project="daocaijing-check-${GITHUB_RUN_ID:-$$}"
export DOMAIN=localhost PUBLIC_URL=https://localhost
cp .env.example .env.delivery-check
cleanup() {
  "${compose[@]}" down -v --remove-orphans
  rm -f .env.delivery-check
}
export DEPLOY_ENV_FILE=.env.delivery-check
compose=(docker compose --env-file .env.delivery-check -f docker-compose.yml -p "$project")
trap cleanup EXIT
"${compose[@]}" up -d --build --wait --wait-timeout 180
curl --fail --silent --insecure https://localhost/health > /dev/null
curl --fail --silent --insecure https://localhost/.well-known/oauth-authorization-server | python3 -c 'import json,sys; assert json.load(sys.stdin)["issuer"]'
curl --fail --silent --insecure https://localhost/robots.txt | python3 -c 'import sys; assert "User-agent:" in sys.stdin.read()'
status="$(curl --silent --insecure -o /dev/null -w '%{http_code}' https://localhost/research-workbench/api/status)"
test "$status" = 401
"${compose[@]}" exec -T backend python -c 'import json, urllib.request; assert isinstance(json.load(urllib.request.urlopen("http://research-workbench:3927/api/status", timeout=10)), dict)'
status="$(curl --silent --insecure -o /dev/null -w '%{http_code}' https://localhost/s/no-such-snapshot)"
test "$status" = 404
"${compose[@]}" exec -T backend python -c 'from deepfocus_api import db; c=db.connect(db.data_path("delivery-check.sqlite3")); c.execute("create table proof(value text)"); c.execute("insert into proof values (?)", ("persisted",)); c.commit(); c.close()'
"${compose[@]}" up -d --force-recreate --no-deps --wait backend
"${compose[@]}" exec -T backend python -c 'from deepfocus_api import db; c=db.connect(db.data_path("delivery-check.sqlite3")); assert c.execute("select value from proof").fetchone()[0]=="persisted"; c.close()'
printf '%s\n' 'Container delivery checks passed'
