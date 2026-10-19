#!/usr/bin/env bash
# Runs inside the NetZeroE VM: copies the project into the VM's own file system, builds the three
# microservice images, starts them with Docker Compose and runs the end-to-end test.
set -euo pipefail
SRC="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"   # default: this repository
DST="$HOME/netzero-e"
mkdir -p "$DST"
rsync -a --delete --exclude '__pycache__' --exclude 'evidence' "$SRC/" "$DST/"
find "$DST" -name '*.sh' -exec sed -i 's/\r$//' {} +
cd "$DST"
docker compose build
docker compose up -d --wait
docker compose ps
cd tests && python3 smoke_test.py http://localhost:8080 --load 50
