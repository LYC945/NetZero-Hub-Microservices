#!/usr/bin/env bash
# Full clean run inside the NetZeroE VM: remove old containers and volumes, rebuild, test, collect evidence.
P="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # this repository
[ -d /root/netzero-e ] && (cd /root/netzero-e && docker compose down -v >/dev/null 2>&1)
rm -rf "$P/evidence"; mkdir -p "$P/evidence"
bash "$P/scripts/run_in_vm.sh" > "$P/evidence/00_build_and_run.log" 2>&1
echo "run exit=$?"
sed -n '/smoke test against/,$p' "$P/evidence/00_build_and_run.log" > "$P/evidence/02_smoke_test_output.txt"
sleep 3
bash "$P/scripts/collect_evidence.sh" > /dev/null
