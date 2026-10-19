#!/usr/bin/env bash
# Runs inside the NetZeroE VM after run_in_vm.sh. Writes evidence files for the report to evidence/.
set -uo pipefail
OUT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/evidence}"
mkdir -p "$OUT"
cd "$HOME/netzero-e"
G=http://localhost:8080
{
  echo "\$ cat /etc/os-release | head -2"; head -2 /etc/os-release
  echo; echo "\$ uname -r"; uname -r
  echo; echo "\$ docker version --format '{{.Server.Version}}'"; docker version --format '{{.Server.Version}}'
  echo; echo "\$ docker compose version"; docker compose version
  echo; echo "\$ docker images netzero/*"; docker images 'netzero/*' --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}'
  echo; echo "\$ docker compose ps"; docker compose ps --format 'table {{.Name}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  echo; echo "\$ docker network inspect netzero-hub_netzero (containers)"
  docker network inspect netzero-hub_netzero --format '{{range .Containers}}{{.Name}}  {{.IPv4Address}}{{"\n"}}{{end}}'
} > "$OUT/01_environment.txt" 2>&1

{
  echo "== Fault isolation: stop WMA002, the other services keep working =="
  echo "\$ docker compose stop wma002-sorting"; docker compose stop wma002-sorting 2>&1
  for p in /wma001/health /ea01/health /wma002/health; do
    printf '%-16s -> HTTP %s\n' "$p" "$(curl -s -o /dev/null -w '%{http_code}' $G$p)"; done
  echo; echo "\$ docker compose start wma002-sorting"; docker compose start wma002-sorting 2>&1
  sleep 4
  for p in /wma001/health /ea01/health /wma002/health; do
    printf '%-16s -> HTTP %s\n' "$p" "$(curl -s -o /dev/null -w '%{http_code}' $G$p)"; done
  echo; echo "== Persistence: EA01 history survives a container restart =="
  before=$(curl -s $G/ea01/footprints/history/u-1001 | python3 -c 'import json,sys;print(json.load(sys.stdin)["count"])')
  docker compose restart ea01-carbon >/dev/null 2>&1; sleep 4
  after=$(curl -s $G/ea01/footprints/history/u-1001 | python3 -c 'import json,sys;print(json.load(sys.stdin)["count"])')
  echo "assessments for u-1001 before restart: $before, after restart: $after"
} > "$OUT/03_fault_isolation.txt" 2>&1

# sample responses
curl -s "$G/wma001/schedule?location=2500&notifications=true&weeks=4&start=2026-12-21" | python3 -m json.tool > "$OUT/04_wma001_schedule.json"
curl -s "$G/wma001/schedule?location=3000" | python3 -m json.tool > "$OUT/04_wma001_unsupported.json"
curl -s -X POST $G/ea01/footprints -H 'Content-Type: application/json' -d '{"userId":"u-2002","assessmentStartDate":"2026-09-01","assessmentEndDate":"2026-09-30","electricityConsumption":320,"transportMode":"PRIVATE_VEHICLE","travelDistance":450,"wasteGenerated":40,"recyclingAmount":15,"consentGiven":true}' | python3 -m json.tool > "$OUT/05_ea01_footprint.json"
curl -s -X POST $G/ea01/footprints -H 'Content-Type: application/json' -d '{"userId":"u-2002","assessmentStartDate":"2026-09-01","assessmentEndDate":"2026-09-30","electricityConsumption":320,"travelDistance":0,"consentGiven":true}' | python3 -m json.tool > "$OUT/05_ea01_incomplete.json"
curl -s -X POST $G/wma002/sort -H 'Content-Type: application/json' -d '{"item":"old AA battery","location":"2519"}' | python3 -m json.tool > "$OUT/06_wma002_battery.json"
curl -s -X POST $G/wma002/sort -H 'Content-Type: application/json' -d '{"item":"quantum flux","location":"2500"}' | python3 -m json.tool > "$OUT/06_wma002_unrecognised.json"

# event logs straight from each container's volume
for s in wma001-schedule:wma001 ea01-carbon:ea01 wma002-sorting:wma002; do
  docker compose cp "${s%%:*}:/data/${s##*:}_events.csv" "$OUT/07_${s##*:}_events.csv" >/dev/null 2>&1
done
cp tests/smoke_results.json "$OUT/02_smoke_results.json" 2>/dev/null
ls -la "$OUT"
