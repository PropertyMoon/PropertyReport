"""
Suburb comparator — free, no-login widget backing GET /api/v1/compare-suburbs.

Combines: live median price + crime (via suburb_data.py's MCP clients, cached
in suburb_db.py's live_cache), live CBD commute time (Google Distance Matrix,
also cached), and static ABS Census demographics (suburb_db.py's abs_demographics,
seeded once by ingest_abs_census.py). No AI / web-search fallback on a miss —
this must stay fast and free; a missing field just renders as unavailable.
"""

import os
import time
from collections import defaultdict
from typing import Optional

from pydantic import BaseModel

from suburb_data import _fetch_median_price_data, _fetch_crime_data, _fetch_commute_time
from suburb_db import live_cache_get_raw, live_cache_set, abs_demographics_get, \
    SUBURB_CACHE_TTL_PRICE_CRIME, SUBURB_CACHE_TTL_COMMUTE, SUBURB_CACHE_TTL_NEGATIVE

_UNAVAILABLE = {"_unavailable": True}


def _cached_or_fetch(suburb: str, state: str, metric_type: str, positive_ttl: int, fetch_fn) -> Optional[dict]:
    """
    Shared fetch-or-cache logic: a successful fetch is cached for `positive_ttl`;
    a failed fetch is still cached, but only for SUBURB_CACHE_TTL_NEGATIVE, so a
    slow/down upstream (e.g. an MCP outage) doesn't get hit on every request.
    """
    raw = live_cache_get_raw(suburb, state, metric_type)
    if raw is not None:
        payload, age = raw
        if payload.get("_unavailable"):
            if age <= SUBURB_CACHE_TTL_NEGATIVE:
                return None
        elif age <= positive_ttl:
            return payload
        # else: stale (positive or negative) — fall through and refetch

    data = fetch_fn()
    if data:
        live_cache_set(suburb, state, metric_type, data)
        return data
    live_cache_set(suburb, state, metric_type, _UNAVAILABLE)
    return None

# ─── Rate limiter (independent bucket from the checkout flow in api.py) ───────

_rate_buckets: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT  = int(os.getenv("COMPARE_RATE_LIMIT", "20"))
_RATE_WINDOW = int(os.getenv("COMPARE_RATE_WINDOW", "600"))


def check_compare_rate_limit(ip: str) -> bool:
    now = time.monotonic()
    bucket = _rate_buckets[ip]
    _rate_buckets[ip] = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(_rate_buckets[ip]) >= _RATE_LIMIT:
        return False
    _rate_buckets[ip].append(now)
    return True


# ─── Models ────────────────────────────────────────────────────────────────────

class SuburbMetrics(BaseModel):
    suburb: str
    state: str
    median_house_price: Optional[int] = None
    median_unit_price: Optional[int] = None
    gross_rental_yield: Optional[float] = None
    crime_safety_percentile: Optional[int] = None
    commute_cbd_mins: Optional[int] = None
    owner_occupied_pct: Optional[float] = None
    renter_pct: Optional[float] = None
    median_age: Optional[int] = None
    household_composition: Optional[dict] = None
    median_household_income_weekly: Optional[int] = None
    data_flags: dict[str, bool] = {}


class CompareSuburbsResponse(BaseModel):
    suburb_a: SuburbMetrics
    suburb_b: SuburbMetrics
    safer_suburb: Optional[str] = None            # "a" | "b" | None
    faster_commute_suburb: Optional[str] = None    # "a" | "b" | None


# ─── Fetch-or-cache helpers ────────────────────────────────────────────────────

def _get_median_price(suburb: str, state: str, postcode: str) -> Optional[dict]:
    def fetch():
        data = _fetch_median_price_data(suburb, state, postcode)
        if data and data.get("coverage") not in ("unavailable", "no_data"):
            return data
        return None
    return _cached_or_fetch(suburb, state, "median_price", SUBURB_CACHE_TTL_PRICE_CRIME, fetch)


def _get_crime(suburb: str, state: str) -> Optional[dict]:
    return _cached_or_fetch(suburb, state, "crime", SUBURB_CACHE_TTL_PRICE_CRIME,
                             lambda: _fetch_crime_data(suburb, state))


def _get_commute(suburb: str, state: str, lat: Optional[float], lng: Optional[float]) -> Optional[dict]:
    if lat is None or lng is None:
        return None
    return _cached_or_fetch(suburb, state, "commute", SUBURB_CACHE_TTL_COMMUTE,
                             lambda: _fetch_commute_time(lat, lng, state))


def _suburb_metrics(suburb: str, state: str, postcode: str,
                     lat: Optional[float], lng: Optional[float]) -> SuburbMetrics:
    state = state.strip().upper()
    median = _get_median_price(suburb, state, postcode) or {}
    crime = _get_crime(suburb, state) or {}
    commute = _get_commute(suburb, state, lat, lng) or {}
    demo = abs_demographics_get(suburb, state) or {}

    # NOTE: the au-crime-mcp response's exact percentile field name is unconfirmed
    # (no accessible source for that service at implementation time) — try the two
    # most likely names and fall back to None (renders as "—") rather than guessing wrong.
    crime_percentile = crime.get("safety_percentile")
    if crime_percentile is None:
        crime_percentile = crime.get("percentile")

    median_house = median.get("median_house_price")
    median_unit = median.get("median_unit_price")

    return SuburbMetrics(
        suburb=suburb,
        state=state,
        median_house_price=median_house,
        median_unit_price=median_unit,
        gross_rental_yield=median.get("gross_rental_yield"),
        crime_safety_percentile=crime_percentile,
        commute_cbd_mins=commute.get("commute_cbd_mins"),
        owner_occupied_pct=demo.get("owner_occupied_pct"),
        renter_pct=demo.get("renter_pct"),
        median_age=demo.get("median_age"),
        household_composition=demo.get("household_composition"),
        median_household_income_weekly=demo.get("median_household_income_weekly"),
        data_flags={
            "median_price": median_house is not None or median_unit is not None,
            "crime": crime_percentile is not None,
            "commute": commute.get("commute_cbd_mins") is not None,
            "demographics": bool(demo),
        },
    )


def get_suburb_comparison(
    suburb_a: str, state_a: str, postcode_a: str, lat_a: Optional[float], lng_a: Optional[float],
    suburb_b: str, state_b: str, postcode_b: str, lat_b: Optional[float], lng_b: Optional[float],
) -> CompareSuburbsResponse:
    a = _suburb_metrics(suburb_a, state_a, postcode_a, lat_a, lng_a)
    b = _suburb_metrics(suburb_b, state_b, postcode_b, lat_b, lng_b)

    safer = None
    if a.crime_safety_percentile is not None and b.crime_safety_percentile is not None:
        if a.crime_safety_percentile != b.crime_safety_percentile:
            # Lower percentile = safer (less crime relative to other suburbs)
            safer = "a" if a.crime_safety_percentile < b.crime_safety_percentile else "b"

    faster = None
    if a.commute_cbd_mins is not None and b.commute_cbd_mins is not None:
        if a.commute_cbd_mins != b.commute_cbd_mins:
            faster = "a" if a.commute_cbd_mins < b.commute_cbd_mins else "b"

    return CompareSuburbsResponse(
        suburb_a=a, suburb_b=b,
        safer_suburb=safer, faster_commute_suburb=faster,
    )
