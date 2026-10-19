"""Append-only event log (one CSV per service) used later for process mining in Part F.

Each row is one activity of the Part B process, so the log can be mapped back to the BPMN tasks.
"""
import csv
import os
import threading
import uuid
from datetime import datetime, timezone

FIELDS = ["case_id", "activity", "timestamp", "service", "resource", "outcome", "detail"]
_lock = threading.Lock()


class EventLog:
    def __init__(self, path: str, service: str):
        self.path = path
        self.service = service
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(FIELDS)

    @staticmethod
    def new_case_id() -> str:
        return "case-" + uuid.uuid4().hex[:12]

    def log(self, case_id: str, activity: str, resource: str, outcome: str = "ok", detail: str = ""):
        row = [case_id, activity, datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               self.service, resource, outcome, detail]
        with _lock, open(self.path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)

    def tail(self, limit: int = 100):
        with _lock, open(self.path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        return rows[-limit:]
