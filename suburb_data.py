"""
Shared suburb-level data sources.

Consolidates the state government source URLs and the median-price / crime
MCP clients that used to live only in orchestrator.py. Used by the research
pipeline (orchestrator.py) and the suburb comparator (compare_suburbs.py).

Note: pdf_generator.py has its own separate, smaller _STATE_SOURCES used only
for the PDF cover page's display label — deliberately left as-is, since it
formats state names differently for that specific purpose (see CLAUDE.md).
"""

import os
import re
import httpx

_STATE_SOURCES = {
    "VIC": {"label": "Victoria",           "planning": "planning.vic.gov.au",   "crime": "crimestats.vic.gov.au",                 "flood": "vicfloodmap.com.au",          "transport": "ptv.vic.gov.au",        "catchment": "findmyschool.vic.gov.au"},
    "NSW": {"label": "New South Wales",    "planning": "planning.nsw.gov.au",   "crime": "bocsar.nsw.gov.au",                     "flood": "floodplanning.nsw.gov.au",    "transport": "transportnsw.info",     "catchment": "schoolfinder.education.nsw.gov.au"},
    "QLD": {"label": "Queensland",         "planning": "dsdilgp.qld.gov.au",    "crime": "police.qld.gov.au/maps-and-statistics", "flood": "floodcheck.qld.gov.au",       "transport": "translink.com.au",      "catchment": "schoolfinder.education.qld.gov.au"},
    "SA":  {"label": "South Australia",    "planning": "plan.sa.gov.au",        "crime": "police.sa.gov.au/services-and-stats",   "flood": "environment.sa.gov.au/flood", "transport": "adelaidemetro.com.au",  "catchment": "education.sa.gov.au/find-a-school"},
    "WA":  {"label": "Western Australia",  "planning": "planning.wa.gov.au",    "crime": "police.wa.gov.au/crime-statistics/suburb-crime-data", "flood": "planning.wa.gov.au/flood",    "transport": "transperth.wa.gov.au",  "catchment": "det.wa.edu.au/schoolsonline/local_intake_school.do"},
    "TAS": {"label": "Tasmania",           "planning": "listmap.tas.gov.au",    "crime": "justice.tas.gov.au/crime-statistics",   "flood": "dpipwe.tas.gov.au/flood",     "transport": "metrotas.com.au",       "catchment": "education.tas.gov.au/parents-carers/find-a-school"},
    "ACT": {"label": "ACT",                "planning": "actmapi.act.gov.au",    "crime": "police.act.gov.au/crime-statistics",    "flood": "esa.act.gov.au/flood",        "transport": "transport.act.gov.au",  "catchment": "education.act.gov.au/public-school-enrolment/find-your-local-school"},
    "NT":  {"label": "Northern Territory", "planning": "planning.nt.gov.au",    "crime": "pfes.nt.gov.au/crime-statistics",       "flood": "nt.gov.au/emergency/flood",   "transport": "nt.gov.au/driving-transport/public-transport", "catchment": "education.nt.gov.au/enrolment/find-a-school"},
}
_DEFAULT_STATE = {"label": "Australia", "planning": "planning.gov.au", "crime": "aic.gov.au", "flood": "ga.gov.au/flood", "transport": "transportnsw.info", "catchment": "myschool.edu.au"}

_CRIME_MCP_URL = os.getenv(
    "CRIME_MCP_URL",
    "https://au-crime-mcp-production.up.railway.app/suburb-crime",
)
_MEDIAN_MCP_URL = os.getenv(
    "MEDIAN_MCP_URL",
    "https://au-median-price-mcp-production.up.railway.app/suburb-median",
)
_COMPARABLE_SALES_MCP_URL = os.getenv(
    "COMPARABLE_SALES_MCP_URL",
    "https://au-median-price-mcp-production.up.railway.app/comparable-sales",
)

_STATE_RE = re.compile(r'\b(VIC|NSW|QLD|SA|WA|TAS|ACT|NT)\b', re.IGNORECASE)


def _get_state(address: str) -> dict:
    m = _STATE_RE.search(address)
    return _STATE_SOURCES.get(m.group(1).upper(), _DEFAULT_STATE) if m else _DEFAULT_STATE


def _fetch_crime_data(suburb: str, state: str) -> dict | None:
    """Call the au-crime-mcp /suburb-crime endpoint. Returns None on failure."""
    if not suburb or not state:
        return None
    try:
        r = httpx.get(_CRIME_MCP_URL, params={"suburb": suburb, "state": state}, timeout=60)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  ⚠️  Crime MCP unavailable ({e}), falling back to web search")
    return None


def _fetch_median_price_data(suburb: str, state: str, postcode: str = "") -> dict | None:
    """Call the au-median-price-mcp /suburb-median endpoint. Returns None on failure."""
    if not suburb or not state:
        return None
    try:
        params = {"suburb": suburb, "state": state}
        if postcode:
            params["postcode"] = postcode
        r = httpx.get(_MEDIAN_MCP_URL, params=params, timeout=60)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  ⚠️  Median Price MCP unavailable ({e}), falling back to web search")
    return None


def _fetch_comparable_sales(suburb: str, state: str, postcode: str, street: str) -> dict | None:
    """Call /comparable-sales on the median-price MCP. Returns None on failure."""
    if not suburb or not state or not postcode:
        return None
    try:
        params = {"suburb": suburb, "state": state, "postcode": postcode}
        if street:
            params["street"] = street
        r = httpx.get(_COMPARABLE_SALES_MCP_URL, params=params, timeout=90)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  ⚠️  Comparable Sales MCP unavailable ({e})")
    return None


# ─── Capital city CBD coordinates, for commute-time lookups ───────────────────
_CBD_COORDS = {
    "VIC": (-37.8136, 144.9631),   # Melbourne
    "NSW": (-33.8688, 151.2093),   # Sydney
    "QLD": (-27.4698, 153.0251),   # Brisbane
    "SA":  (-34.9285, 138.6007),   # Adelaide
    "WA":  (-31.9523, 115.8613),   # Perth
    "TAS": (-42.8821, 147.3272),   # Hobart
    "ACT": (-35.2809, 149.1300),   # Canberra
    "NT":  (-12.4634, 130.8456),   # Darwin
}


def _fetch_commute_time(lat: float, lng: float, state: str) -> dict | None:
    """
    Driving time from (lat, lng) to that state's CBD via Google Distance Matrix.
    Returns {"commute_cbd_mins": int, "cbd_label": str} or None on failure.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    cbd = _CBD_COORDS.get(state.upper()) if state else None
    if not api_key or not cbd or lat is None or lng is None:
        return None
    try:
        r = httpx.get(
            "https://maps.googleapis.com/maps/api/distancematrix/json",
            params={
                "origins": f"{lat},{lng}",
                "destinations": f"{cbd[0]},{cbd[1]}",
                "mode": "driving",
                "key": api_key,
            },
            timeout=10,
        )
        data = r.json()
        element = data["rows"][0]["elements"][0]
        if element.get("status") != "OK":
            return None
        seconds = element["duration"]["value"]
        return {
            "commute_cbd_mins": round(seconds / 60),
            "cbd_label": _STATE_SOURCES.get(state.upper(), _DEFAULT_STATE)["label"],
        }
    except Exception as e:
        print(f"  ⚠️  Distance Matrix unavailable ({e})")
        return None
