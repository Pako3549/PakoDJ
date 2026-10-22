#!/usr/bin/env bash
# Validation used by agents/humans before claiming work is done.
# SAFE against a real bot/.env: a placeholder is created only when the file
# is missing, and removed again only if we created it. Never touch it otherwise.
set -e
cd "$(dirname "$0")/.."

python3 -m py_compile bot/*.py && rm -rf bot/__pycache__

if [ ! -f bot/.env ]; then
  printf 'token=x\n' > bot/.env
  CREATED=1
fi

docker compose -f compose.yml config -q
docker compose -f deploy/compose.yml config -q
docker compose -f deploy/compose.build.yml config -q

if [ "${CREATED:-0}" = 1 ]; then
  rm bot/.env
fi

python3 -c "import yaml; yaml.safe_load(open('.github/workflows/docker-publish.yml'))"

echo "ALL_CHECKS_OK"
