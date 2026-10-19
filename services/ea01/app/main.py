"""EA01 Carbon Footprint Calculation Service (Environmental Agency).

Implements ICarbonFootprintService of Part C:
    validateActivityData(request)      -> POST /ea01/validate
    calculateCarbonFootprint(request)  -> POST /ea01/footprints
    getCarbonFootprintHistory(userId)  -> GET  /ea01/footprints/history/{userId}
Emission factors are illustrative values for the prototype (versioned so every result can be audited).
"""
import json
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .eventlog import EventLog

SERVICE = "EA01"
DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "ea01.db")
RETENTION_DAYS = 730  # QoS: history retained 24 months
log = EventLog(os.path.join(DATA_DIR, "ea01_events.csv"), SERVICE)

FACTOR_VERSION = "EF-2026.10-demo"
FACTORS = {  # kg CO2-e per unit; illustrative values for the prototype
    "electricity_kwh": 0.66,
    "transport_km": {"PRIVATE_VEHICLE": 0.19, "EV": 0.11, "BUS": 0.10, "TRAIN": 0.05},
    "landfill_kg": 0.70,
    "recycling_kg": 0.05,
}
LIMITS = {"electricityConsumption": 100_000, "travelDistance": 200_000, "wasteGenerated": 20_000,
          "recyclingAmount": 20_000}


class TransportMode(str, Enum):
    PRIVATE_VEHICLE = "PRIVATE_VEHICLE"
    EV = "EV"
    BUS = "BUS"
    TRAIN = "TRAIN"


class CarbonFootprintRequest(BaseModel):
    userId: str = Field(..., examples=["u-1001"])
    assessmentStartDate: date
    assessmentEndDate: date
    electricityConsumption: Optional[float] = Field(None, description="kWh in the period")
    transportMode: Optional[TransportMode] = None
    travelDistance: Optional[float] = Field(None, description="km in the period")
    wasteGenerated: Optional[float] = Field(None, description="kg in the period")
    recyclingAmount: Optional[float] = Field(None, description="kg of the waste that was recycled")
    consentGiven: bool = Field(..., description="User consent for accessing activity data is current")


class ValidationResult(BaseModel):
    valid: bool
    errors: List[str]
    missingCategories: List[str]


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS assessment(assessment_id TEXT PRIMARY KEY, case_id TEXT, user_id TEXT,
                period_from TEXT, period_to TEXT, total REAL, energy REAL, transport REAL, waste REAL,
                status TEXT, factor_version TEXT, request_json TEXT, response_json TEXT, calculated_at TEXT);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, assessment_id TEXT,
                factor_version TEXT, factors_json TEXT, calculated_at TEXT);
        """)


@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="EA01 Carbon Footprint Calculation Service", version="1.0.0", lifespan=lifespan,
              docs_url="/ea01/docs", openapi_url="/ea01/openapi.json", redoc_url=None,
              description="Environmental Agency provider service for NetZero Hub (CSCI927 Part E).")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate(r: CarbonFootprintRequest) -> ValidationResult:
    errors, missing = [], []
    if not r.userId.strip():
        errors.append("userId is required.")
    if r.assessmentEndDate < r.assessmentStartDate:
        errors.append("assessmentEndDate is before assessmentStartDate.")
    if r.assessmentEndDate > date.today():
        errors.append("Assessment period cannot end in the future.")
    if (r.assessmentEndDate - r.assessmentStartDate).days > 366:
        errors.append("Assessment period cannot be longer than one year.")
    for k, hi in LIMITS.items():
        v = getattr(r, k)
        if v is not None and not 0 <= v <= hi:
            errors.append(f"{k} must be between 0 and {hi}.")
    if r.electricityConsumption is None:
        missing.append("energy")
    if r.travelDistance is None:
        missing.append("transport")
    elif r.travelDistance > 0 and r.transportMode is None:
        errors.append("transportMode is required when travelDistance > 0.")
    if r.wasteGenerated is None:
        missing.append("waste")
        if r.recyclingAmount is not None:
            errors.append("recyclingAmount given without wasteGenerated.")
    elif (r.recyclingAmount or 0) > r.wasteGenerated:
        errors.append("recyclingAmount cannot exceed wasteGenerated.")
    if len(missing) == 3:
        errors.append("No activity data supplied for any category.")
    return ValidationResult(valid=not errors, errors=errors, missingCategories=missing)


def calculate(r: CarbonFootprintRequest):
    energy = transport = waste = None
    if r.electricityConsumption is not None:
        energy = r.electricityConsumption * FACTORS["electricity_kwh"]
    if r.travelDistance is not None:
        transport = 0.0 if r.travelDistance == 0 else r.travelDistance * FACTORS["transport_km"][r.transportMode.value]
    if r.wasteGenerated is not None:
        rec = r.recyclingAmount or 0.0
        waste = (r.wasteGenerated - rec) * FACTORS["landfill_kg"] + rec * FACTORS["recycling_kg"]
    parts = {"energy": energy, "transport": transport, "waste": waste}
    total = sum(v for v in parts.values() if v is not None)
    breakdown = [{"category": k, "emissions": round(v, 2), "share": round(v / total, 3) if total else 0.0}
                 for k, v in parts.items() if v is not None]
    return total, parts, breakdown


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    resp = await call_next(request)
    resp.headers["X-Response-Time-ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
    resp.headers["X-Service"] = SERVICE
    return resp


@app.get("/ea01/health")
def health():
    return {"service": SERVICE, "status": "UP", "time": now_iso()}


@app.get("/ea01/emission-factors")
def emission_factors():
    return {"version": FACTOR_VERSION, "unit": "kg CO2-e per unit", "factors": FACTORS,
            "note": "Illustrative factors for the prototype; reviewed monthly in a production service."}


@app.post("/ea01/validate", response_model=ValidationResult)
def validate_activity(body: CarbonFootprintRequest, x_case_id: Optional[str] = Header(None)):
    res = validate(body)
    log.log(x_case_id or log.new_case_id(), "Validate activity data", "EA Calculation Engine",
            "valid" if res.valid else "invalid", "; ".join(res.errors))
    return res


@app.post("/ea01/footprints", status_code=201)
def calculate_footprint(body: CarbonFootprintRequest, x_case_id: Optional[str] = Header(None)):
    case = x_case_id or log.new_case_id()
    log.log(case, "Receive carbon footprint request", "EA Calculation Engine", "ok", body.userId)
    if not body.consentGiven:  # precondition: consent current
        log.log(case, "Check user consent", "EA Calculation Engine", "rejected", "consent not current")
        return JSONResponse(status_code=403, headers={"X-Case-Id": case}, content={
            "case_id": case, "status": "CONSENT_REQUIRED", "message": "User consent for activity data is not current."})
    log.log(case, "Check user consent", "EA Calculation Engine", "ok")
    val = validate(body)
    log.log(case, "Validate activity data", "EA Calculation Engine", "valid" if val.valid else "invalid",
            "; ".join(val.errors))
    if not val.valid:
        return JSONResponse(status_code=422, headers={"X-Case-Id": case}, content={
            "case_id": case, "status": "ERROR", "validation": val.model_dump()})
    log.log(case, "Retrieve emission factors", "EA Calculation Engine", "ok", FACTOR_VERSION)
    total, parts, breakdown = calculate(body)
    log.log(case, "Calculate category emissions", "EA Calculation Engine", "ok",
            ",".join(k for k, v in parts.items() if v is not None))
    status = "INCOMPLETE" if val.missingCategories else "COMPLETE"
    aid = "cf-" + uuid.uuid4().hex[:10]
    ts = now_iso()
    resp = {
        "case_id": case, "assessmentId": aid, "userId": body.userId,
        "assessmentPeriod": {"from": body.assessmentStartDate.isoformat(), "to": body.assessmentEndDate.isoformat()},
        "totalCarbonFootprint": round(total, 2),
        "energyEmissions": None if parts["energy"] is None else round(parts["energy"], 2),
        "transportEmissions": None if parts["transport"] is None else round(parts["transport"], 2),
        "wasteEmissions": None if parts["waste"] is None else round(parts["waste"], 2),
        "emissionBreakdown": breakdown, "unit": "kg CO2-e",
        "calculationTimestamp": ts, "status": status, "missingCategories": val.missingCategories,
        "emissionFactorVersion": FACTOR_VERSION,
    }
    if status == "INCOMPLETE":
        resp["warning"] = "Result is incomplete: no data for " + ", ".join(val.missingCategories) + \
                          ". Missing data was not estimated."
    with db() as con:
        con.execute("INSERT INTO assessment VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (aid, case, body.userId, body.assessmentStartDate.isoformat(), body.assessmentEndDate.isoformat(),
                     resp["totalCarbonFootprint"], resp["energyEmissions"], resp["transportEmissions"],
                     resp["wasteEmissions"], status, FACTOR_VERSION, body.model_dump_json(), json.dumps(resp), ts))
        con.execute("INSERT INTO audit(assessment_id,factor_version,factors_json,calculated_at) VALUES (?,?,?,?)",
                    (aid, FACTOR_VERSION, json.dumps(FACTORS), ts))
    log.log(case, "Store result in sustainability history", "EA Calculation Engine", "ok", aid)
    log.log(case, "Return carbon footprint to NetZero Hub", "EA Calculation Engine", status.lower(), aid)
    return JSONResponse(status_code=201, headers={"X-Case-Id": case}, content=resp)


@app.get("/ea01/footprints/history/{user_id}")
def history(user_id: str):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    with db() as con:
        rows = con.execute("SELECT assessment_id, period_from, period_to, total, energy, transport, waste, status, "
                           "factor_version, calculated_at FROM assessment WHERE user_id=? AND calculated_at>=? "
                           "ORDER BY calculated_at", (user_id, cutoff)).fetchall()
    return {"userId": user_id, "retentionMonths": 24, "count": len(rows), "assessments": [dict(r) for r in rows]}


@app.get("/ea01/footprints/{assessment_id}")
def get_assessment(assessment_id: str):
    with db() as con:
        row = con.execute("SELECT response_json FROM assessment WHERE assessment_id=?", (assessment_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Assessment not found.")
    return json.loads(row["response_json"])


@app.get("/ea01/events")
def events(limit: int = Query(100, ge=1, le=5000)):
    return log.tail(limit)
