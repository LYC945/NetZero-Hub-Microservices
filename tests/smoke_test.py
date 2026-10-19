"""End-to-end test of the three NetZero Hub microservices through the API gateway.

    python3 smoke_test.py [base_url] [--load N]

Checks the success and failure paths of WMA001, EA01 and WMA002 (the XOR branches of the Part B
models), one composed household journey, and response time against the Part A QoS limits.
Only the standard library is used, so it runs in the VM and on Windows.
"""
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = next((a for a in sys.argv[1:] if a.startswith("http")), "http://localhost:8080").rstrip("/")
LOAD = int(sys.argv[sys.argv.index("--load") + 1]) if "--load" in sys.argv else 0
QOS = {"wma001": 3.0, "ea01": 5.0, "wma002": 3.0}  # seconds, Part A QoS upper bounds


def parse(r):
    raw = r.read() or b"null"
    return json.loads(raw) if "json" in (r.headers.get("Content-Type") or "") else raw.decode("utf-8", "replace")


def call(method, path, body=None, case=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if case:
        req.add_header("X-Case-Id", case)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            status, payload = r.status, parse(r)
    except urllib.error.HTTPError as e:
        status, payload = e.code, parse(e)
    return status, payload, time.perf_counter() - t0


FOOTPRINT = {"userId": "u-1001", "assessmentStartDate": "2026-09-01", "assessmentEndDate": "2026-09-30",
             "electricityConsumption": 320, "transportMode": "PRIVATE_VEHICLE", "travelDistance": 450,
             "wasteGenerated": 40, "recyclingAmount": 15, "consentGiven": True}
q = urllib.parse.quote

CASES = [
    # id, description, method, path, body, expected status, check(payload)
    ("G1", "Gateway lists the three services", "GET", "/api", None, 200, lambda p: len(p["services"]) == 3),
    ("F1", "Web front end served through the gateway", "GET", "/", None, 200, lambda p: "LOGIN" in p),
    ("F2", "Front-end service page with its API client", "GET", "/WM002.html", None, 200, lambda p: "/wma002/sort" in p),
    ("W1-1", "WMA001 health", "GET", "/wma001/health", None, 200, lambda p: p["status"] == "UP"),
    ("W1-2", "WMA001 address check: supported", "POST", "/wma001/address-check",
     {"location": "12 Main St, Blacktown NSW 2148"}, 200, lambda p: p["valid"] and p["supported"]),
    ("W1-3", "WMA001 schedule for a valid, supported postcode", "GET",
     "/wma001/schedule?location=2500&notifications=true&weeks=4&start=2026-12-21", None, 200,
     lambda p: len(p["collections"]) == 4 and p["notifications"]["reminders"]),
    ("W1-4", "WMA001 Christmas Day collection moves to the next day", "GET",
     "/wma001/schedule?location=Blacktown&weeks=2&start=2026-12-21", None, 200,
     lambda p: p["upcoming_changes"][0]["new_date"] == "2026-12-26"),
    ("W1-5", "WMA001 valid but unsupported postcode", "GET", "/wma001/schedule?location=3000", None, 404,
     lambda p: p["status"] == "UNSUPPORTED_LOCATION"),
    ("W1-6", "WMA001 invalid location", "GET", "/wma001/schedule?location=" + q("nowhere st"), None, 400,
     lambda p: p["status"] == "INVALID_LOCATION"),
    ("E1-1", "EA01 health", "GET", "/ea01/health", None, 200, lambda p: p["status"] == "UP"),
    ("E1-2", "EA01 complete footprint", "POST", "/ea01/footprints", FOOTPRINT, 201,
     lambda p: p["status"] == "COMPLETE" and abs(p["totalCarbonFootprint"] - 314.95) < 0.01),
    ("E1-3", "EA01 missing waste data -> INCOMPLETE, not estimated", "POST", "/ea01/footprints",
     {**FOOTPRINT, "wasteGenerated": None, "recyclingAmount": None}, 201,
     lambda p: p["status"] == "INCOMPLETE" and p["wasteEmissions"] is None),
    ("E1-4", "EA01 recycling > waste is rejected", "POST", "/ea01/footprints",
     {**FOOTPRINT, "recyclingAmount": 60}, 422, lambda p: p["status"] == "ERROR"),
    ("E1-5", "EA01 consent not current", "POST", "/ea01/footprints", {**FOOTPRINT, "consentGiven": False}, 403,
     lambda p: p["status"] == "CONSENT_REQUIRED"),
    ("E1-6", "EA01 history for the user", "GET", "/ea01/footprints/history/u-1001", None, 200,
     lambda p: p["count"] >= 2),
    ("W2-1", "WMA002 health", "GET", "/wma002/health", None, 200, lambda p: p["status"] == "UP"),
    ("W2-2", "WMA002 plastic bottle -> Return and Earn", "POST", "/wma002/sort",
     {"item": "plastic bottle", "location": "2500"}, 200, lambda p: p["category"] == "CONTAINER_DEPOSIT"),
    ("W2-3", "WMA002 food scraps in a FOGO area", "POST", "/wma002/sort",
     {"item": "banana peel", "location": "Wollongong 2500"}, 200, lambda p: p["category"] == "FOOD_ORGANICS"),
    ("W2-4", "WMA002 food scraps without FOGO -> local rule", "POST", "/wma002/sort",
     {"item": "food scraps", "location": "2148"}, 200,
     lambda p: p["category"] == "GENERAL_WASTE" and "red-lid" in p["instructions"][0]),
    ("W2-5", "WMA002 battery is hazardous", "GET", "/wma002/sort?item=" + q("old AA battery") + "&location=2519",
     None, 200, lambda p: p["category"] == "COMMUNITY_RECYCLING_CENTRE" and p["specialInstructions"]),
    ("W2-6", "WMA002 fuzzy match (typo)", "POST", "/wma002/sort", {"item": "cardbord box", "location": "2500"},
     200, lambda p: p["matchedItem"] == "cardboard box"),
    ("W2-7", "WMA002 unrecognised item", "POST", "/wma002/sort", {"item": "quantum flux", "location": "2500"},
     404, lambda p: p["status"] == "ITEM_NOT_RECOGNISED"),
    ("W2-8", "WMA002 location without postcode", "POST", "/wma002/sort", {"item": "newspaper", "location": "home"},
     400, lambda p: p["status"] == "INVALID_LOCATION"),
]


def main():
    print(f"NetZero Hub smoke test against {BASE}\n")
    results, failed = [], 0
    for cid, desc, m, path, body, exp, check in CASES:
        st, p, dt = call(m, path, body)
        try:
            ok = st == exp and bool(check(p))
        except Exception:
            ok = False
        svc = path.strip("/").split("/")[0].split("?")[0] or "gateway"
        qos_ok = dt <= QOS.get(svc, 3.0)
        failed += not (ok and qos_ok)
        results.append({"id": cid, "test": desc, "expected": exp, "status": st, "ms": round(dt * 1000, 1),
                        "pass": ok and qos_ok})
        print(f"{'PASS' if ok and qos_ok else 'FAIL'}  {cid:5} {st} (exp {exp}) {dt * 1000:7.1f} ms  {desc}")

    # Composed household journey: one case id across the three services (traceable in all three event logs)
    case = "case-journey-" + str(int(time.time()))
    print(f"\nHousehold journey {case}")
    s1, p1, _ = call("GET", "/wma001/schedule?location=2500&notifications=true", case=case)
    s2, p2, _ = call("POST", "/wma002/sort", {"item": "pizza box", "location": "2500"}, case=case)
    s3, p3, _ = call("POST", "/ea01/footprints", {**FOOTPRINT, "userId": "u-journey"}, case=case)
    ok = (s1, s2, s3) == (200, 200, 201) and p1["case_id"] == p2["case_id"] == p3["case_id"] == case
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  next collection {p1['collections'][0]['date']} {p1['collections'][0]['bins']}, "
          f"pizza box -> {p2.get('category')}, footprint {p3.get('totalCarbonFootprint')} kg CO2-e")
    results.append({"id": "J1", "test": "Composed journey with one case id", "pass": ok, "case_id": case})

    if LOAD:
        print(f"\nResponse time over {LOAD} requests per service (Part A QoS upper bound)")
        probes = {"wma001": ("GET", "/wma001/schedule?location=2148", None),
                  "ea01": ("POST", "/ea01/footprints", {**FOOTPRINT, "userId": "u-load"}),
                  "wma002": ("POST", "/wma002/sort", {"item": "glass jar", "location": "2150"})}
        for svc, (m, path, body) in probes.items():
            ts = sorted(call(m, path, body)[2] for _ in range(LOAD))
            p95 = ts[int(0.95 * (len(ts) - 1))]
            ok = p95 <= QOS[svc]
            failed += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {svc:7} mean {statistics.mean(ts) * 1000:6.1f} ms  "
                  f"p95 {p95 * 1000:6.1f} ms  max {ts[-1] * 1000:6.1f} ms  (QoS <= {QOS[svc]:.0f} s)")
            results.append({"id": "Q-" + svc, "test": f"p95 response time over {LOAD} requests",
                            "p95_ms": round(p95 * 1000, 1), "pass": ok})

    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    with open("smoke_results.json", "w") as f:
        json.dump(results, f, indent=2)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
