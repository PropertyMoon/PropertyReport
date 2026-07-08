"""
Suburb reference/cache database for the suburb comparator feature.

Separate SQLite file from jobs.db — this holds long-lived reference data
(ABS Census demographics) and a short-lived live-data cache (median price,
crime, commute time), not transactional job state.
"""

import csv
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

SUBURB_DB_PATH = os.getenv("SUBURB_DB_PATH", "suburb_data.db")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ABS_CSV_PATH = os.path.join(_HERE, "data", "abs_suburb_demographics.csv")

SUBURB_CACHE_TTL_PRICE_CRIME = int(os.getenv("SUBURB_CACHE_TTL_PRICE_CRIME", str(30 * 24 * 3600)))   # 30 days
SUBURB_CACHE_TTL_COMMUTE     = int(os.getenv("SUBURB_CACHE_TTL_COMMUTE", str(180 * 24 * 3600)))       # 180 days
SUBURB_CACHE_TTL_NEGATIVE    = int(os.getenv("SUBURB_CACHE_TTL_NEGATIVE", str(15 * 60)))              # 15 min — avoid hammering a down/slow upstream


@contextmanager
def get_suburb_db():
    conn = sqlite3.connect(SUBURB_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_suburb_db():
    with get_suburb_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS abs_demographics (
                suburb                          TEXT NOT NULL,
                state                           TEXT NOT NULL,
                sal_code                        TEXT,
                census_year                     INTEGER NOT NULL,
                median_age                      INTEGER,
                median_household_income_weekly  INTEGER,
                avg_household_size              REAL,
                owner_occupied_pct              REAL,
                renter_pct                      REAL,
                household_composition_json      TEXT,
                PRIMARY KEY (suburb, state, census_year)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS live_cache (
                suburb        TEXT NOT NULL,
                state         TEXT NOT NULL,
                metric_type   TEXT NOT NULL,
                payload_json  TEXT NOT NULL,
                fetched_at    TEXT NOT NULL,
                PRIMARY KEY (suburb, state, metric_type)
            )
        """)
        row = db.execute("SELECT COUNT(*) AS n FROM abs_demographics").fetchone()
        if row["n"] == 0:
            _seed_abs_demographics(db)


def _seed_abs_demographics(db: sqlite3.Connection):
    """Bulk-load the committed ABS extract on first run (self-heals across redeploys)."""
    if not os.path.exists(_ABS_CSV_PATH):
        return
    with open(_ABS_CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [
            (
                r["suburb"].strip().upper(), r["state"].strip().upper(), r.get("sal_code") or None, int(r["census_year"]),
                _int_or_none(r.get("median_age")),
                _int_or_none(r.get("median_household_income_weekly")),
                _float_or_none(r.get("avg_household_size")),
                _float_or_none(r.get("owner_occupied_pct")),
                _float_or_none(r.get("renter_pct")),
                r.get("household_composition_json") or None,
            )
            for r in reader
        ]
    db.executemany(
        """INSERT OR REPLACE INTO abs_demographics
           (suburb, state, sal_code, census_year, median_age, median_household_income_weekly,
            avg_household_size, owner_occupied_pct, renter_pct, household_composition_json)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )


def _int_or_none(v) -> Optional[int]:
    return int(float(v)) if v not in (None, "") else None


def _float_or_none(v) -> Optional[float]:
    return float(v) if v not in (None, "") else None


def abs_demographics_get(suburb: str, state: str) -> Optional[dict]:
    with get_suburb_db() as db:
        row = db.execute(
            "SELECT * FROM abs_demographics WHERE suburb=? AND state=? ORDER BY census_year DESC LIMIT 1",
            (suburb.strip().upper(), state.strip().upper()),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("household_composition_json"):
            d["household_composition"] = json.loads(d["household_composition_json"])
        return d


def abs_demographics_upsert(record: dict):
    """Used only by the one-off ingestion script."""
    with get_suburb_db() as db:
        db.execute(
            """INSERT OR REPLACE INTO abs_demographics
               (suburb, state, sal_code, census_year, median_age, median_household_income_weekly,
                avg_household_size, owner_occupied_pct, renter_pct, household_composition_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                record["suburb"].strip().upper(), record["state"].strip().upper(),
                record.get("sal_code"), record["census_year"],
                record.get("median_age"), record.get("median_household_income_weekly"),
                record.get("avg_household_size"), record.get("owner_occupied_pct"),
                record.get("renter_pct"), json.dumps(record["household_composition"]) if record.get("household_composition") else None,
            ),
        )


def live_cache_get(suburb: str, state: str, metric_type: str, max_age_seconds: int) -> Optional[dict]:
    with get_suburb_db() as db:
        row = db.execute(
            "SELECT payload_json, fetched_at FROM live_cache WHERE suburb=? AND state=? AND metric_type=?",
            (suburb.strip().upper(), state.strip().upper(), metric_type),
        ).fetchone()
        if not row:
            return None
        fetched_at = datetime.fromisoformat(row["fetched_at"])
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        if age > max_age_seconds:
            return None
        return json.loads(row["payload_json"])


def live_cache_get_raw(suburb: str, state: str, metric_type: str) -> Optional[tuple[dict, float]]:
    """Like live_cache_get but ignores TTL — returns (payload, age_seconds) so the
    caller can apply a different TTL for a cached failure vs. a cached success
    (e.g. don't hammer a currently-down upstream on every request)."""
    with get_suburb_db() as db:
        row = db.execute(
            "SELECT payload_json, fetched_at FROM live_cache WHERE suburb=? AND state=? AND metric_type=?",
            (suburb.strip().upper(), state.strip().upper(), metric_type),
        ).fetchone()
        if not row:
            return None
        fetched_at = datetime.fromisoformat(row["fetched_at"])
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        return json.loads(row["payload_json"]), age


def live_cache_set(suburb: str, state: str, metric_type: str, payload: dict):
    with get_suburb_db() as db:
        db.execute(
            """INSERT OR REPLACE INTO live_cache (suburb, state, metric_type, payload_json, fetched_at)
               VALUES (?,?,?,?,?)""",
            (
                suburb.strip().upper(), state.strip().upper(), metric_type,
                json.dumps(payload), datetime.now(timezone.utc).isoformat(),
            ),
        )
