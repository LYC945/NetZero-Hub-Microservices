"""WMA001 Waste Collection Schedule Service (Waste Management Authority).

Implements the AuthorityProviderInterface of Part C:
    checkAddressSupport(l: LocationData)  -> POST /wma001/address-check
    getCalendar(l: LocationData)          -> GET  /wma001/schedule
The XOR gateway of the Part B model (valid and supported / invalid or unsupported) is the
branch in get_schedule(). Collection data are illustrative, not real council data.
"""
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .eventlog import EventLog

SERVICE = "WMA001"
DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "wma001.db")
log = EventLog(os.path.join(DATA_DIR, "wma001_events.csv"), SERVICE)

# postcode, suburb, council, collection weekday (0 = Mon), recycling on even ISO weeks?, garden bin
AREAS = [
    ("2500", "Wollongong", "Wollongong City Council", 1, True, True),
    ("2519", "Fairy Meadow", "Wollongong City Council", 2, False, True),
    ("2522", "Keiraville", "Wollongong City Council", 3, True, True),
    ("2529", "Shellharbour", "Shellharbour City Council", 0, False, True),
    ("2148", "Blacktown", "Blacktown City Council", 4, True, False),
    ("2150", "Parramatta", "City of Parramatta", 2, True, True),
    ("2000", "Sydney", "City of Sydney", 0, False, False),
]
# NSW public holidays in the demo window; a collection on a holiday moves to the next day.
PUBLIC_HOLIDAYS = {
    date(2026, 12, 25): "Christmas Day",
    date(2026, 12, 28): "Boxing Day (additional day)",
    date(2027, 1, 1): "New Year's Day",
    date(2027, 1, 26): "Australia Day",
}
BIN_INSTRUCTIONS = {
    "General waste": "Red-lid bin. Bagged household rubbish that cannot be recycled or composted.",
    "Recycling": "Yellow-lid bin. Clean paper, cardboard, glass, rigid plastic and cans, loose (not bagged).",
    "Garden organics": "Green-lid bin. Grass, leaves and small branches; no plastic bags.",
}
BIN_RULES = "Put bins out after 6 pm the night before, lids closed, handles facing the street, 50 cm apart."

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS area(postcode TEXT PRIMARY KEY, suburb TEXT, council TEXT,
                weekday INTEGER, recycling_even INTEGER, garden INTEGER);
            CREATE TABLE IF NOT EXISTS schedule_change(id INTEGER PRIMARY KEY AUTOINCREMENT, postcode TEXT,
                original_date TEXT, new_date TEXT, reason TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS request(id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT,
                location TEXT, outcome TEXT, notifications INTEGER, created_at TEXT);
        """)
        if con.execute("SELECT COUNT(*) FROM area").fetchone()[0] == 0:
            con.executemany("INSERT INTO area VALUES (?,?,?,?,?,?)", AREAS)


@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="WMA001 Waste Collection Schedule Service", version="1.0.0", lifespan=lifespan,
              docs_url="/wma001/docs", openapi_url="/wma001/openapi.json", redoc_url=None,
              description="Waste Management Authority provider service for NetZero Hub (CSCI927 Part E).")


# ---------- message types (Part C LocationData / ScheduleData / WarningNotif) ----------
class LocationData(BaseModel):
    location: str = Field(..., description="Residential address or postcode", examples=["12 Main St, Blacktown NSW 2148"])


class AddressCheck(BaseModel):
    location: str
    valid: bool
    supported: bool
    postcode: Optional[str] = None
    suburb: Optional[str] = None
    council: Optional[str] = None
    reason: str


class ScheduleChangeIn(BaseModel):
    postcode: str = Field(..., pattern=r"^\d{4}$")
    original_date: date
    new_date: date
    reason: str = Field(..., min_length=3)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_address(location: str) -> AddressCheck:
    text = (location or "").strip()
    if len(text) < 3:
        return AddressCheck(location=text, valid=False, supported=False, reason="Address or postcode is empty or too short.")
    m = re.search(r"\b(\d{4})\b", text)
    with db() as con:
        row = None
        if m:
            row = con.execute("SELECT * FROM area WHERE postcode=?", (m.group(1),)).fetchone()
        else:  # suburb name only
            for r in con.execute("SELECT * FROM area"):
                if re.search(r"\b" + re.escape(r["suburb"].lower()) + r"\b", text.lower()):
                    row = r
                    break
    if not m and row is None:
        return AddressCheck(location=text, valid=False, supported=False,
                            reason="No Australian postcode or known suburb found in the address.")
    if row is None:
        return AddressCheck(location=text, valid=True, supported=False, postcode=m.group(1),
                            reason="Postcode is valid but is outside the authority's collection area.")
    return AddressCheck(location=text, valid=True, supported=True, postcode=row["postcode"], suburb=row["suburb"],
                        council=row["council"], reason="Location is covered by a collection schedule.")


def build_calendar(postcode: str, start: date, weeks: int):
    with db() as con:
        a = con.execute("SELECT * FROM area WHERE postcode=?", (postcode,)).fetchone()
        changes = {r["original_date"]: r for r in con.execute(
            "SELECT * FROM schedule_change WHERE postcode=?", (postcode,))}
    collections, upcoming = [], []
    first = start + timedelta(days=(a["weekday"] - start.weekday()) % 7)
    for w in range(weeks):
        day = first + timedelta(weeks=w)
        even = day.isocalendar()[1] % 2 == 0
        bins = ["General waste"]
        if even == bool(a["recycling_even"]):
            bins.append("Recycling")
        elif a["garden"]:
            bins.append("Garden organics")
        actual, reason = day, None
        if day.isoformat() in changes:
            c = changes[day.isoformat()]
            actual, reason = date.fromisoformat(c["new_date"]), c["reason"]
        elif day in PUBLIC_HOLIDAYS:
            actual, reason = day + timedelta(days=1), PUBLIC_HOLIDAYS[day] + " – collection one day later"
        if reason:
            upcoming.append({"original_date": day.isoformat(), "new_date": actual.isoformat(), "reason": reason})
        collections.append({"date": actual.isoformat(), "weekday": actual.strftime("%A"), "bins": bins,
                            "changed": reason is not None})
    return a, collections, upcoming


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    resp = await call_next(request)
    resp.headers["X-Response-Time-ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
    resp.headers["X-Service"] = SERVICE
    return resp


@app.get("/wma001/health")
def health():
    return {"service": SERVICE, "status": "UP", "time": now_iso()}


@app.post("/wma001/address-check", response_model=AddressCheck)
def address_check(body: LocationData, x_case_id: Optional[str] = Header(None)):
    """checkAddressSupport(l) – E5 'Check if location is valid and supported'."""
    case = x_case_id or log.new_case_id()
    res = check_address(body.location)
    log.log(case, "Check if location is valid and supported", "WMA Schedule System",
            "supported" if res.supported else ("unsupported" if res.valid else "invalid"), res.postcode or "")
    return res


@app.get("/wma001/schedule")
def get_schedule(location: str = Query(..., description="Residential address or postcode"),
                 notifications: bool = Query(False, description="Preferred notification setting (on/off)"),
                 weeks: int = Query(4, ge=1, le=12),
                 start: Optional[date] = Query(None, description="First day of the window (default today)"),
                 x_case_id: Optional[str] = Header(None)):
    """getCalendar(l) – returns ScheduleData, or a WarningNotif when the location is invalid/unsupported."""
    case = x_case_id or log.new_case_id()
    log.log(case, "Receive waste collection schedule request", "WMA Schedule System", "ok", location)
    chk = check_address(location)
    log.log(case, "Check if location is valid and supported", "WMA Schedule System",
            "supported" if chk.supported else ("unsupported" if chk.valid else "invalid"), chk.postcode or "")
    if not chk.supported:  # XOR gateway: invalid or unsupported branch (E12)
        log.log(case, "Send response: Location invalid or not supported", "WMA Schedule System", "rejected", chk.reason)
        with db() as con:
            con.execute("INSERT INTO request(case_id,location,outcome,notifications,created_at) VALUES (?,?,?,?,?)",
                        (case, location, "invalid" if not chk.valid else "unsupported", int(notifications), now_iso()))
        return JSONResponse(status_code=400 if not chk.valid else 404, headers={"X-Case-Id": case}, content={
            "case_id": case, "status": "INVALID_LOCATION" if not chk.valid else "UNSUPPORTED_LOCATION",
            "notification": {"type": "WarningNotif", "message": "Schedule unavailable for this location.",
                             "reason": chk.reason}})
    a, collections, upcoming = build_calendar(chk.postcode, start or date.today(), weeks)  # E6
    log.log(case, "Retrieve waste collection schedule for location", "WMA Schedule System", "ok",
            f"{len(collections)} collections")
    reminders = []
    if notifications:
        nxt = collections[0]
        eve = date.fromisoformat(nxt["date"]) - timedelta(days=1)
        reminders.append({"send_at": f"{eve.isoformat()}T18:00:00+11:00", "channel": "push",
                          "message": f"Put out your {', '.join(nxt['bins']).lower()} bin(s) tonight."})
    with db() as con:
        con.execute("INSERT INTO request(case_id,location,outcome,notifications,created_at) VALUES (?,?,?,?,?)",
                    (case, location, "schedule", int(notifications), now_iso()))
    log.log(case, "Send waste collection schedule to NetZero Hub", "WMA Schedule System", "ok", chk.postcode)
    return JSONResponse(headers={"X-Case-Id": case}, content={
        "case_id": case, "status": "OK", "type": "ScheduleData",
        "location": {"postcode": a["postcode"], "suburb": a["suburb"], "council": a["council"]},
        "collections": collections,
        "upcoming_changes": upcoming,
        "bin_instructions": [{"bin": b, "instruction": BIN_INSTRUCTIONS[b]}
                             for b in sorted({b for c in collections for b in c["bins"]})] + [
                                {"bin": "All bins", "instruction": BIN_RULES}],
        "notifications": {"enabled": notifications, "reminders": reminders},
        "data_note": "Illustrative schedule for the CSCI927 prototype; not official council data.",
        "generated_at": now_iso()})


@app.post("/wma001/schedule-changes", status_code=201)
def add_change(body: ScheduleChangeIn, x_case_id: Optional[str] = Header(None)):
    """Authority-side update (e.g. storm or holiday); visible to the next schedule query (update QoS ≤ 60 min)."""
    with db() as con:
        if not con.execute("SELECT 1 FROM area WHERE postcode=?", (body.postcode,)).fetchone():
            raise HTTPException(404, "Postcode is not in the collection area.")
        cur = con.execute("INSERT INTO schedule_change(postcode,original_date,new_date,reason,created_at) "
                          "VALUES (?,?,?,?,?)", (body.postcode, body.original_date.isoformat(),
                                                 body.new_date.isoformat(), body.reason, now_iso()))
    log.log(x_case_id or log.new_case_id(), "Update collection schedule", "WMA Schedule System", "ok", body.postcode)
    return {"id": cur.lastrowid, **body.model_dump(mode="json")}


@app.get("/wma001/schedule-changes")
def list_changes():
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM schedule_change ORDER BY id")]


@app.get("/wma001/areas")
def list_areas():
    with db() as con:
        return [dict(r) for r in con.execute("SELECT postcode, suburb, council FROM area ORDER BY postcode")]


@app.get("/wma001/events")
def events(limit: int = Query(100, ge=1, le=5000)) -> List[dict]:
    return log.tail(limit)
