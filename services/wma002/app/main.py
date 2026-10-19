"""WMA002 Recycling and Waste Sorting Service (Waste Management Authority).

Given an item name/description and the user's location, returns the proposed disposal method,
instructions and any special handling. Local rules (e.g. food organics in the green bin) override the
state-wide default. Sorting rules are illustrative, not official council data.
"""
import difflib
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .eventlog import EventLog

SERVICE = "WMA002"
DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "wma002.db")
log = EventLog(os.path.join(DATA_DIR, "wma002_events.csv"), SERVICE)

BINS = {
    "GENERAL_WASTE": ("Red-lid general waste bin", "Place in the red-lid bin."),
    "RECYCLING": ("Yellow-lid recycling bin", "Place loose (not bagged) in the yellow-lid bin."),
    "GARDEN_ORGANICS": ("Green-lid organics bin", "Place in the green-lid bin."),
    "FOOD_ORGANICS": ("Green-lid FOGO bin", "Place in the kitchen caddy, then the green-lid bin."),
    "CONTAINER_DEPOSIT": ("Return and Earn point", "Return to a Return and Earn point for a 10c refund, "
                                                  "or place in the yellow-lid bin."),
    "SOFT_PLASTICS": ("Soft plastics drop-off", "Do not place in the yellow bin; take to a soft plastics drop-off."),
    "COMMUNITY_RECYCLING_CENTRE": ("Community Recycling Centre", "Take to a Community Recycling Centre (free)."),
    "E_WASTE": ("E-waste drop-off", "Take to an e-waste drop-off or council e-waste event."),
    "BULKY_WASTE": ("Bulky waste collection", "Book a council bulky waste (kerbside) collection."),
}
# name, category, synonyms, item-specific instructions, hazardous
ITEMS = [
    ("plastic bottle", "CONTAINER_DEPOSIT", "water bottle,soft drink bottle,pet bottle", "Empty it and keep the lid on.", 0),
    ("aluminium can", "CONTAINER_DEPOSIT", "drink can,soda can,beer can,can", "Empty it; do not crush for Return and Earn.", 0),
    ("glass bottle", "CONTAINER_DEPOSIT", "wine bottle,beer bottle", "Wine bottles are not eligible for refund – use the yellow bin.", 0),
    ("glass jar", "RECYCLING", "jam jar,sauce jar", "Rinse; lids off and in the yellow bin.", 0),
    ("cardboard box", "RECYCLING", "carton,cardboard,delivery box", "Flatten it; remove plastic tape where possible.", 0),
    ("newspaper", "RECYCLING", "paper,magazine,junk mail,office paper", "Keep it dry and loose.", 0),
    ("pizza box", "RECYCLING", "pizza carton", "Only if clean; greasy parts go in the general waste bin.", 0),
    ("milk carton", "RECYCLING", "juice carton,liquid paperboard", "Rinse and flatten.", 0),
    ("plastic food container", "RECYCLING", "takeaway container,yoghurt tub,ice cream tub", "Rinse; rigid plastic only.", 0),
    ("steel can", "RECYCLING", "tin can,food tin,aerosol can", "Rinse food tins; aerosols must be empty.", 0),
    ("plastic bag", "SOFT_PLASTICS", "shopping bag,bread bag,cling wrap,chip packet,soft plastic", "Must be scrunchable and clean.", 0),
    ("food scraps", "FOOD_ORGANICS", "food waste,fruit peel,vegetable scraps,leftovers,banana peel", "No plastic bags; use compostable liners.", 0),
    ("coffee grounds", "FOOD_ORGANICS", "tea bags,coffee", "Paper tea bags only.", 0),
    ("grass clippings", "GARDEN_ORGANICS", "lawn clippings,grass", "No soil or rocks.", 0),
    ("tree branches", "GARDEN_ORGANICS", "prunings,small branches,leaves,garden waste", "Branches under 10 cm diameter and 1 m long.", 0),
    ("nappy", "GENERAL_WASTE", "diaper,disposable nappy", "Wrap and place in the red-lid bin.", 0),
    ("polystyrene", "GENERAL_WASTE", "styrofoam,foam box,foam packaging", "Break into pieces; not recyclable at kerbside.", 0),
    ("broken glass", "GENERAL_WASTE", "drinking glass,window glass,mirror,ceramic,crockery", "Wrap in newspaper for safety.", 0),
    ("household battery", "COMMUNITY_RECYCLING_CENTRE", "battery,aa battery,lithium battery,button battery",
     "Tape the terminals; never put batteries in any kerbside bin (fire risk).", 1),
    ("paint", "COMMUNITY_RECYCLING_CENTRE", "paint tin,leftover paint", "Keep the lid sealed; up to 20 L per visit.", 1),
    ("motor oil", "COMMUNITY_RECYCLING_CENTRE", "engine oil,used oil", "Keep in a sealed container.", 1),
    ("gas bottle", "COMMUNITY_RECYCLING_CENTRE", "lpg cylinder,gas cylinder", "Never put in a bin or bulky waste.", 1),
    ("fluorescent light", "COMMUNITY_RECYCLING_CENTRE", "light globe,fluoro tube,cfl", "Contains mercury; do not break.", 1),
    ("mobile phone", "E_WASTE", "smartphone,phone", "Wipe your data first; remove the battery if possible.", 0),
    ("laptop", "E_WASTE", "computer,tablet,monitor,printer,keyboard", "Wipe your data first.", 0),
    ("television", "E_WASTE", "tv", "Free drop-off under the national TV and computer recycling scheme.", 0),
    ("mattress", "BULKY_WASTE", "bed base", "Book a kerbside collection; do not leave out unbooked.", 0),
    ("sofa", "BULKY_WASTE", "couch,armchair,furniture", "Book a kerbside collection.", 0),
]
# postcode, council, food organics in green bin (FOGO), soft plastics drop-off available
AREAS = [
    ("2500", "Wollongong City Council", 1, 1), ("2519", "Wollongong City Council", 1, 1),
    ("2522", "Wollongong City Council", 1, 1), ("2529", "Shellharbour City Council", 1, 0),
    ("2148", "Blacktown City Council", 0, 1), ("2150", "City of Parramatta", 0, 1),
    ("2000", "City of Sydney", 0, 1),
]


class SortRequest(BaseModel):
    item: str = Field(..., description="Name or description of the waste item", examples=["plastic bottle"])
    location: str = Field(..., description="User's location or postcode", examples=["2500"])


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS item(name TEXT PRIMARY KEY, category TEXT, synonyms TEXT,
                instruction TEXT, hazardous INTEGER);
            CREATE TABLE IF NOT EXISTS area(postcode TEXT PRIMARY KEY, council TEXT, fogo INTEGER, soft_plastics INTEGER);
            CREATE TABLE IF NOT EXISTS query(id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, item TEXT,
                location TEXT, matched TEXT, category TEXT, created_at TEXT);
        """)
        if con.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 0:
            con.executemany("INSERT INTO item VALUES (?,?,?,?,?)", ITEMS)
            con.executemany("INSERT INTO area VALUES (?,?,?,?)", AREAS)


@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="WMA002 Recycling and Waste Sorting Service", version="1.0.0", lifespan=lifespan,
              docs_url="/wma002/docs", openapi_url="/wma002/openapi.json", redoc_url=None,
              description="Waste Management Authority provider service for NetZero Hub (CSCI927 Part E).")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


def match_item(text: str):
    """Exact name/synonym -> phrase contained in the description -> fuzzy match. Returns (row, confidence)."""
    q = " ".join(norm(text).split())
    with db() as con:
        items = con.execute("SELECT * FROM item").fetchall()
    names = {}
    for r in items:
        for n in [r["name"]] + [s for s in r["synonyms"].split(",") if s]:
            names[norm(n)] = r
    if q in names:
        return names[q], 1.0
    contained = [n for n in names if re.search(r"\b" + re.escape(n) + r"\b", q)]
    if contained:
        best = max(contained, key=len)
        return names[best], 0.9
    close = difflib.get_close_matches(q, names.keys(), n=1, cutoff=0.75)
    if close:
        return names[close[0]], round(difflib.SequenceMatcher(None, q, close[0]).ratio(), 2)
    sugg = difflib.get_close_matches(q, names.keys(), n=3, cutoff=0.4)
    return None, sugg


def find_area(location: str):
    m = re.search(r"\b(\d{4})\b", location or "")
    if not m:
        return None, None
    with db() as con:
        return m.group(1), con.execute("SELECT * FROM area WHERE postcode=?", (m.group(1),)).fetchone()


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    resp = await call_next(request)
    resp.headers["X-Response-Time-ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
    resp.headers["X-Service"] = SERVICE
    return resp


@app.get("/wma002/health")
def health():
    return {"service": SERVICE, "status": "UP", "time": now_iso()}


def sort_item(item: str, location: str, case: str):
    log.log(case, "Receive waste item query", "WMA Sorting System", "ok", item)
    if len(norm(item)) < 2:
        log.log(case, "Validate waste item", "WMA Sorting System", "invalid", "empty item")
        return JSONResponse(status_code=400, headers={"X-Case-Id": case}, content={
            "case_id": case, "status": "INVALID_ITEM", "message": "Please enter the name or a description of the item."})
    postcode, area = find_area(location)
    if postcode is None:
        log.log(case, "Validate location", "WMA Sorting System", "invalid", location)
        return JSONResponse(status_code=400, headers={"X-Case-Id": case}, content={
            "case_id": case, "status": "INVALID_LOCATION", "message": "Location must contain a 4-digit postcode."})
    log.log(case, "Validate location", "WMA Sorting System", "local" if area else "default", postcode)
    row, conf = match_item(item)
    if row is None:
        log.log(case, "Match item to waste category", "WMA Sorting System", "unrecognised", item)
        with db() as con:
            con.execute("INSERT INTO query(case_id,item,location,matched,category,created_at) VALUES (?,?,?,?,?,?)",
                        (case, item, postcode, None, None, now_iso()))
        return JSONResponse(status_code=404, headers={"X-Case-Id": case}, content={
            "case_id": case, "status": "ITEM_NOT_RECOGNISED",
            "message": "The item could not be matched to a waste category. Try a more general name.",
            "suggestions": conf})
    log.log(case, "Match item to waste category", "WMA Sorting System", "matched", row["name"])
    category, local_rule, special = row["category"], False, []
    instruction = row["instruction"]
    if category == "FOOD_ORGANICS" and not (area and area["fogo"]):
        category = "GENERAL_WASTE"  # no food organics service in this area
        instruction = "Wrap food scraps in newspaper before putting them in the red-lid bin."
        special.append("Your council does not collect food organics; use the red-lid bin or a home compost.")
        local_rule = area is not None
    elif category == "FOOD_ORGANICS":
        local_rule = True
    if category == "SOFT_PLASTICS" and area and not area["soft_plastics"]:
        category, local_rule = "GENERAL_WASTE", True
        instruction = "Put soft plastics in the red-lid bin, never in the yellow-lid recycling bin."
        special.append("No soft plastics drop-off is listed for your area; use the red-lid bin.")
    if row["hazardous"]:
        special.append("Hazardous item: never place it in a kerbside bin.")
    log.log(case, "Apply local disposal regulations", "WMA Sorting System", "local" if local_rule else "state-default",
            category)
    bin_name, method = BINS[category]
    with db() as con:
        con.execute("INSERT INTO query(case_id,item,location,matched,category,created_at) VALUES (?,?,?,?,?,?)",
                    (case, item, postcode, row["name"], category, now_iso()))
    log.log(case, "Return disposal method to NetZero Hub", "WMA Sorting System", "ok", category)
    return JSONResponse(headers={"X-Case-Id": case}, content={
        "case_id": case, "status": "OK", "item": item, "matchedItem": row["name"], "matchConfidence": conf,
        "category": category, "proposedDisposal": bin_name, "disposalMethod": method,
        "instructions": [instruction], "specialInstructions": special,
        "location": {"postcode": postcode, "council": area["council"] if area else None,
                     "rules": "local council rules" if area else "NSW default guidelines"},
        "data_note": "Illustrative sorting rules for the CSCI927 prototype; check your council for official rules.",
        "generated_at": now_iso()})


@app.post("/wma002/sort")
def sort_post(body: SortRequest, x_case_id: Optional[str] = Header(None)):
    return sort_item(body.item, body.location, x_case_id or log.new_case_id())


@app.get("/wma002/sort")
def sort_get(item: str = Query(...), location: str = Query(...), x_case_id: Optional[str] = Header(None)):
    return sort_item(item, location, x_case_id or log.new_case_id())


@app.get("/wma002/items")
def list_items(category: Optional[str] = None) -> List[dict]:
    with db() as con:
        sql, args = "SELECT name, category, synonyms FROM item", ()
        if category:
            sql, args = sql + " WHERE category=?", (category.upper(),)
        return [dict(r) for r in con.execute(sql + " ORDER BY category, name", args)]


@app.get("/wma002/events")
def events(limit: int = Query(100, ge=1, le=5000)):
    return log.tail(limit)
