# NetZero Hub – Part E microservices (WMA001, EA01, WMA002)

CSCI927 group project (NetZero Hub), Part E. Three provider services from Parts A–D run as Docker microservices
behind an API gateway, with the group's web front end:

| Service | Provider | Container | Gateway path | API docs |
|---|---|---|---|---|
| WMA001 Waste Collection Schedule | Waste Management Authority | wma001-schedule | `/wma001/` | `/wma001/docs` |
| EA01 Carbon Footprint Calculation | Environmental Agency | ea01-carbon | `/ea01/` | `/ea01/docs` |
| WMA002 Recycling and Waste Sorting | Waste Management Authority | wma002-sorting | `/wma002/` | `/wma002/docs` |
| NetZero Hub web front end | NetZero Hub | frontend | `/` (login.html) | – |
| NetZero Hub API gateway (nginx) | NetZero Hub | gateway | `/api` (service list) | – |

Every service has its own image, its own data volume (SQLite database + CSV event log) and its own OpenAPI
contract. The services never call each other. The front end composes them through the gateway and sends
one `X-Case-Id` per household visit, so a visit appears as one case in all three event logs. Those logs are
the input for process mining in Part F.

## Quick start (any machine with Docker)

```bash
docker compose up -d --build --wait
python3 tests/smoke_test.py http://localhost:8080 --load 50     # 27 checks
```

Open http://localhost:8080/ for the web app, or http://localhost:8080/ea01/docs for an API.
Stop with `docker compose down`; add `-v` to also delete the data volumes.

## As run for the assignment (new WSL2 virtual machine)

The services were run in a new WSL2 virtual machine, `NetZeroE` (Ubuntu 24.04), with Docker Engine from Docker's
apt repository. Neither Docker Desktop nor an existing distro was used. From PowerShell, in the repository folder:

```powershell
wsl --install Ubuntu-24.04 --name NetZeroE --no-launch
wsl -d NetZeroE -u root -- bash scripts/install_docker.sh
wsl -d NetZeroE -u root -- bash scripts/clean_run.sh
```

`clean_run.sh` removes old containers and volumes, then:
1. copies the project to `~/netzero-e` inside the VM;
2. builds and starts the stack;
3. runs the end-to-end test;
4. writes `evidence/`: the containers, the test output, fault isolation, sample responses and the three event logs.

If Windows cannot reach the VM on `localhost`, use the VM's address: `wsl -d NetZeroE hostname -I`.
The published ports can be changed with an env file, for example `docker compose --env-file desktop.env up -d`
(ports 9080 / 9001–9003) to run a second copy next to the first.

## Layout

```
docker-compose.yml          3 services + front end + gateway; one volume per service; health checks
gateway/nginx.conf          path-prefix routing, X-Case-Id passthrough, "/" -> frontend
frontend/site/              web app (login, homepage, WM001, EA01, WM002), from github.com/rbellazar/NetZero-Hub
services/<svc>/Dockerfile   python:3.12-slim, non-root user, HEALTHCHECK
services/<svc>/app/main.py  FastAPI service; data and event log in /data
tests/smoke_test.py         end-to-end + QoS test through the gateway (standard library only)
scripts/                    install_docker.sh, run_in_vm.sh, clean_run.sh, collect_evidence.sh
evidence/                   output of the last clean run (incl. 07_*_events.csv event logs)
```

## Event log (for Part F)

Each service appends one row per Part B activity to `/data/<svc>_events.csv`, readable at `GET /<svc>/events`:

| case_id | activity | timestamp | service | resource | outcome | detail |
|---|---|---|---|---|---|---|
| case-journey-… | Check if location is valid and supported | 2026-10-19T08:54:16.016+00:00 | WMA001 | WMA Schedule System | supported | 2500 |

The three logs share this structure, so they can be merged by `case_id` and loaded into Disco or ProM.
Several events of one request can share a millisecond; the row order is the event order.

All data (collection days, sorting rules, emission factors) is illustrative, not official council or NGA data.
The sign-in page is a prototype: there is no authentication.
