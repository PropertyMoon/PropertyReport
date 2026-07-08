"""
Property Research Agent - Orchestrator
Uses Claude API with web search to research Australian properties
"""

import anthropic
import datetime
import httpx
import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

# ─── Backend selection ────────────────────────────────────────────────────────
# RESEARCH_BACKEND: claude (default) or perplexity — AI backend for all 6 research tasks.
# SYNTHESIS_BACKEND: claude (default) or deepseek — AI backend for narrative synthesis.
_RESEARCH_BACKEND  = os.getenv("RESEARCH_BACKEND",  "claude").lower()
_SYNTHESIS_BACKEND = os.getenv("SYNTHESIS_BACKEND", "claude").lower()

if _RESEARCH_BACKEND == "perplexity" or _SYNTHESIS_BACKEND == "deepseek":
    from openai import OpenAI as _OpenAI

from domain_client import get_last_sale as _domain_get_last_sale

try:
    from da_client_nsw import get_nsw_das as _get_nsw_das
except ImportError:
    _get_nsw_das = None

try:
    import myschool as _myschool
except ImportError:
    _myschool = None


# Reuse state detection from pdf_generator
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

_STREET_TYPE_RE = re.compile(
    r'\b(Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Court|Ct|Place|Pl|Crescent|Cres|'
    r'Close|Cl|Boulevard|Blvd|Terrace|Tce|Lane|Ln|Way|Grove|Gr|Highway|Hwy|'
    r'Circuit|Cct|Parade|Pde|Rise|Row|Square|Track|Walk)\b',
    re.IGNORECASE,
)

def _extract_suburb(address: str) -> str:
    """Extract suburb name from an address like '35 Pindari Ave Taylors Lakes VIC 3038'."""
    state_m = re.search(r'\b(VIC|NSW|QLD|SA|WA|TAS|ACT|NT)\b', address, re.IGNORECASE)
    if not state_m:
        return ""
    before_state = address[:state_m.start()].strip()
    before_state = re.sub(r'\s+\d{4}\s*$', '', before_state).strip()
    street_m = _STREET_TYPE_RE.search(before_state)
    if street_m:
        after_type = before_state[street_m.end():].strip().lstrip(",").strip()
        if after_type:
            return after_type
    return before_state


def _get_state_abbrev(address: str) -> str:
    m = re.search(r'\b(VIC|NSW|QLD|SA|WA|TAS|ACT|NT)\b', address, re.IGNORECASE)
    return m.group(1).upper() if m else ""


def _extract_postcode(address: str) -> str:
    m = re.search(r'\b(\d{4})\b', address)
    return m.group(1) if m else ""


def _extract_street_name(address: str) -> str:
    """Return street name + type without the house number, e.g. 'Devereux Road'."""
    m = _STREET_TYPE_RE.search(address)
    if not m:
        return ""
    chunk = address[:m.end()]                              # e.g. "35 Devereux Road"
    chunk = re.sub(r'^\d+[a-zA-Z]?\s*[/\\]?\s*\d*[a-zA-Z]?\s+', '', chunk)  # strip number
    return chunk.strip()


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


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _fetch_schools_google_places(address: str) -> dict | None:
    """Geocode address then find nearby schools via Google Places Nearby Search."""
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return None
    try:
        geo = httpx.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": api_key},
            timeout=10,
        )
        geo_data = geo.json()
        if geo_data.get("status") != "OK" or not geo_data.get("results"):
            print(f"  ⚠️  Google geocode failed for schools ({geo_data.get('status')})")
            return None

        loc = geo_data["results"][0]["geometry"]["location"]
        lat, lng = loc["lat"], loc["lng"]

        def _places_search(type_: str, radius: int) -> list:
            r = httpx.get(
                "https://maps.googleapis.com/maps/api/place/nearbysearch/json",
                params={"location": f"{lat},{lng}", "radius": radius, "type": type_, "key": api_key},
                timeout=10,
            )
            data = r.json()
            if data.get("status") not in ("OK", "ZERO_RESULTS"):
                return []
            results = []
            for place in data.get("results", []):
                plat = place["geometry"]["location"]["lat"]
                plng = place["geometry"]["location"]["lng"]
                dist = round(_haversine_km(lat, lng, plat, plng), 2)
                types = place.get("types", [])
                if "secondary_school" in types:
                    kind = "secondary"
                elif "primary_school" in types:
                    kind = "primary"
                else:
                    kind = "school"
                results.append({"name": place["name"], "distance_km": dist, "type": kind})
            return results

        # 3 km broad search + dedicated 5 km secondary search to catch distant high schools
        general   = _places_search("school",           3000)
        secondary = _places_search("secondary_school", 5000)

        # Merge, deduplicate by name, keep closest distance per school
        seen: dict[str, dict] = {}
        for s in general + secondary:
            name = s["name"]
            if name not in seen or s["distance_km"] < seen[name]["distance_km"]:
                seen[name] = s
        schools = sorted(seen.values(), key=lambda s: s["distance_km"])

        print(f"  ✅ Google Places: {len(schools)} schools found near {address}")
        return {"lat": lat, "lng": lng, "nearby_schools": schools[:15]}

    except Exception as e:
        print(f"  ⚠️  Google Places schools fetch failed ({e})")
        return None


def _build_nearby_schools_section(places_data: dict | None) -> str:
    """Format Google Places results into a prompt injection block."""
    if not places_data or not places_data.get("nearby_schools"):
        return "No pre-fetched nearby schools — use web search to find schools near this address.\n\n"
    lines = ["NEARBY SCHOOLS pre-fetched via Google Places (primaries within 3 km, secondaries within 5 km — use these names/distances directly, do not search for them):"]
    for s in places_data["nearby_schools"]:
        lines.append(f"  - {s['name']} — {s['distance_km']} km ({s['type']})")
    lines.append("Confirm catchment boundaries and fees in Steps 1–3.\n")
    return "\n".join(lines) + "\n"


def _fetch_amenities_google_places(address: str) -> dict | None:
    """Geocode address then find nearby amenities via Google Places Nearby Search."""
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return None
    try:
        geo = httpx.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": api_key},
            timeout=10,
        )
        geo_data = geo.json()
        if geo_data.get("status") != "OK" or not geo_data.get("results"):
            print(f"  ⚠️  Google geocode failed for amenities ({geo_data.get('status')})")
            return None

        loc = geo_data["results"][0]["geometry"]["location"]
        lat, lng = loc["lat"], loc["lng"]

        def _nearby(keyword: str | None = None, type_: str | None = None, radius: int = 1500) -> list:
            params: dict = {"location": f"{lat},{lng}", "radius": radius, "key": api_key}
            if type_:
                params["type"] = type_
            if keyword:
                params["keyword"] = keyword
            r = httpx.get(
                "https://maps.googleapis.com/maps/api/place/nearbysearch/json",
                params=params,
                timeout=10,
            )
            data = r.json()
            if data.get("status") not in ("OK", "ZERO_RESULTS"):
                return []
            results = []
            for place in data.get("results", []):
                plat = place["geometry"]["location"]["lat"]
                plng = place["geometry"]["location"]["lng"]
                dist = round(_haversine_km(lat, lng, plat, plng), 2)
                results.append({
                    "name":        place["name"],
                    "distance_km": dist,
                    "vicinity":    place.get("vicinity", ""),
                })
            return sorted(results, key=lambda x: x["distance_km"])

        supermarkets = _nearby(type_="supermarket", radius=1500)[:3]
        gyms         = _nearby(type_="gym",         radius=1500)[:5]
        parks        = _nearby(type_="park",        radius=1000)[:3]
        doctors      = _nearby(type_="doctor",      radius=1500)[:3]

        def _names(items: list) -> str:
            return ", ".join(f"{p['name']} ({p['distance_km']}km)" for p in items) or "none"

        print(f"  ✅ Google Places amenities near {address}:")
        print(f"     supermarkets: {_names(supermarkets)}")
        print(f"     gyms:         {_names(gyms)}")
        print(f"     parks:        {_names(parks)}")
        print(f"     doctors:      {_names(doctors)}")

        return {
            "lat": lat, "lng": lng,
            "nearby_supermarkets": supermarkets,
            "nearby_gyms":         gyms,
            "nearby_parks":        parks,
            "nearby_gps":          doctors,
        }
    except Exception as e:
        print(f"  ⚠️  Google Places amenities fetch failed ({e})")
        return None


_AMENITIES_WEB_SEARCH_FALLBACK = (
    "No pre-fetched amenity data — use web search:\n"
    "SUPERMARKET — search 'Woolworths [suburb] [postcode]', then 'Coles [suburb] [postcode]', then 'Aldi [suburb] [postcode]' "
    "to find which chains have a store in or immediately adjacent to the suburb. Pick the one closest to the address and estimate walking distance. "
    "Do NOT accept a result from a different suburb unless no store exists within 1.5km.\n"
    "GYM — search 'gym [suburb] [postcode]' and return the first 3 gyms with a confirmed location in or immediately adjacent to the suburb. Do NOT include gyms from a different suburb.\n"
    "PARK — search 'park reserve [suburb] [postcode]' for the closest public park or reserve.\n"
    "GP — search 'medical centre [suburb] [postcode]' for the nearest general practitioner clinic.\n"
)


def _build_amenities_section(amenities: dict | None) -> str:
    """Format Google Places amenity results into a prompt injection block."""
    if not amenities:
        return _AMENITIES_WEB_SEARCH_FALLBACK

    # If all four categories returned zero results, Places likely failed silently — fall back.
    total = sum(
        len(amenities.get(k, []))
        for k in ("nearby_supermarkets", "nearby_gyms", "nearby_parks", "nearby_gps")
    )
    if total == 0:
        print("  ⚠️  Google Places returned 0 amenity results — falling back to web search")
        return _AMENITIES_WEB_SEARCH_FALLBACK

    def _fmt(items: list, limit: int) -> str:
        return ", ".join(
            f"{p['name']} ({p['distance_km']} km)" for p in items[:limit]
        ) or "none within range"

    return (
        "AMENITIES pre-fetched via Google Places (GPS-accurate distances) — "
        "use these values directly, do NOT search for amenities.\n"
        f"  SUPERMARKETS (≤1.5 km): {_fmt(amenities.get('nearby_supermarkets', []), 3)}\n"
        f"  GYMS (≤1.5 km):         {_fmt(amenities.get('nearby_gyms', []), 5)}\n"
        f"  PARKS (≤1 km):          {_fmt(amenities.get('nearby_parks', []), 3)}\n"
        f"  GPs (≤1.5 km):          {_fmt(amenities.get('nearby_gps', []), 3)}\n"
        "Map these into the nearby_supermarkets / nearby_gyms / nearby_parks / nearby_gps JSON fields. "
        "Set weekly_cost_aud to null for all gyms (pricing not in the database).\n"
    )


def _inject_crime_into_suburb_prompt(prompt: str, crime: dict) -> str:
    """Replace the CRIME step with pre-fetched authoritative values.
    Handles both 'STEP 4 — CRIME' (Claude prompts) and 'STEP 5 — CRIME' (Perplexity prompts)."""
    source = crime.get("data_source", "authoritative state source")
    # Detect which step number is used so the injected label matches
    step_label = "STEP 4 — CRIME"
    step4_start = prompt.find("STEP 4 — CRIME")
    if step4_start < 0:
        step4_start = prompt.find("STEP 5 — CRIME")
        step_label  = "STEP 5 — CRIME"
    injected = (
        f"{step_label}: Data pre-fetched from {source}. "
        f"Use these exact values — do NOT search for crime:\n"
        f"  crime_safety_percentile = {crime.get('crime_safety_percentile')}\n"
        f"  crime_violent_vs_state_avg_pct = {crime.get('crime_violent_vs_state_avg_pct')}\n"
        f"  crime_property_vs_state_avg_pct = {crime.get('crime_property_vs_state_avg_pct')}\n"
    )
    return_json_start = -1
    for _sentinel in ("Return JSON with:", "RETURN JSON WITH THESE KEYS"):
        _pos = prompt.find(_sentinel, step4_start if step4_start >= 0 else 0)
        if _pos >= 0:
            return_json_start = _pos
            break
    if step4_start >= 0 and return_json_start >= 0:
        return prompt[:step4_start] + injected + "\n" + prompt[return_json_start:]
    return prompt


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


def _inject_median_into_suburb_prompt(prompt: str, median: dict) -> str:
    """
    Replace pre-fetched STEP 1/2/3 blocks so the AI skips the corresponding
    web searches and uses authoritative scraped values instead.

    - STEP 1 always replaced (median prices).
    - STEP 2 replaced when gross_rental_yield is present (Domain scrape).
    - STEP 3 replaced when price_history_5yr is present (Domain scrape).
    """
    source      = median.get("data_source", "authoritative source")
    yield_val   = median.get("gross_rental_yield")
    history     = median.get("price_history_5yr") or []

    injected = (
        "STEP 1 — MEDIAN PRICE: Data pre-fetched. "
        "Use these exact values — do NOT search for median price:\n"
        f"  median_house_price = {median.get('median_house_price')}\n"
        f"  median_unit_price  = {median.get('median_unit_price')}\n"
        f"  data_period        = {median.get('data_period')}\n"
        f"  data_source        = {source}\n"
    )
    if yield_val is not None:
        injected += (
            f"\nSTEP 2 — RENTAL YIELD: Pre-fetched from {source}. "
            "Use this value — do NOT search for rental yield:\n"
            f"  gross_rental_yield = {yield_val:.2f}%\n"
        )
    if history:
        import json as _json
        injected += (
            f"\nSTEP 3 — PRICE HISTORY: Pre-fetched from {source}. "
            "Use these exact values — do NOT fetch suburb profile pages:\n"
            f"  price_history_5yr = {_json.dumps(history)}\n"
        )

    step1_start = prompt.find("STEP 1 — MEDIAN PRICE")
    # Advance past whichever STEPs were pre-fetched
    if history:
        next_step_marker = "STEP 4 —"
    elif yield_val is not None:
        next_step_marker = "STEP 3 —"
    else:
        next_step_marker = "STEP 2 —"
    search_from = step1_start + 1 if step1_start >= 0 else 0
    next_start  = prompt.find(next_step_marker, search_from)
    if step1_start >= 0 and next_start >= 0:
        return prompt[:step1_start] + injected + "\n" + prompt[next_start:]
    return prompt


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
        print(f"  ⚠️  Comparable Sales MCP unavailable ({e}), falling back to AI search")
    return None


def _inject_comparables_into_property_market_prompt(prompt: str, comps: dict) -> str:
    """Replace STEP 5 (comparable sales) with pre-fetched Scrapfly results."""
    sales = comps.get("comparable_sales", [])
    source = comps.get("data_source", "realestate.com.au + domain.com.au")
    period = comps.get("data_period", "last 12 months")

    sales_json = json.dumps(sales[:10], indent=2)  # cap at 10 for prompt size
    injected = (
        f"STEP 5 — COMPARABLE SALES: Pre-fetched from {source} ({period}). "
        f"Use these exact values — do NOT search for comparable sales.\n"
        f"Pick the 3 most relevant (closest property type/size to subject):\n"
        f"{sales_json}\n"
    )

    # Find STEP 5 and the next STEP after it
    step5_start = prompt.find("STEP 5\n")
    if step5_start < 0:
        step5_start = prompt.find("STEP 5 —")
    if step5_start < 0:
        return prompt

    step6_start = prompt.find("STEP 6", step5_start + 1)
    if step6_start >= 0:
        return prompt[:step5_start] + injected + "\n" + prompt[step6_start:]

    return prompt[:step5_start] + injected


def _get_state(address: str) -> dict:
    m = re.search(r'\b(VIC|NSW|QLD|SA|WA|TAS|ACT|NT)\b', address, re.IGNORECASE)
    return _STATE_SOURCES.get(m.group(1).upper(), _DEFAULT_STATE) if m else _DEFAULT_STATE


@dataclass
class PropertyReport:
    address: str
    suburb: dict
    schools: dict
    government_projects: dict
    transport: dict
    property_market: dict
    risk_overlays: dict
    summary: str
    property_intel: dict = field(default_factory=dict)  # subject-property zoning, land, dev potential
    metrics: dict   = field(default_factory=dict)       # pre-extracted scorecard values
    scores: dict    = field(default_factory=dict)       # weighted factor scores parsed from synthesis


# ─── Metric Helpers ───────────────────────────────────────────────────────────

_LABEL_KEYS = ("level", "rating", "summary", "description", "label",
               "value", "category", "status", "outlook", "trend", "name")
_EMPTY_TOKENS = ("", "null", "N/A", "n/a", "none", "None")


def _scalarize(v) -> str | None:
    """Reduce v to a clean string, or None if not derivable.
    Dicts: look inside for a known label-like key (level, rating, summary, …).
    Lists: take the first scalar element."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None  # booleans aren't useful as scorecard text
    if isinstance(v, (str, int, float)):
        return str(v)
    if isinstance(v, dict):
        for lk in _LABEL_KEYS:
            inner = v.get(lk)
            if isinstance(inner, (str, int, float)) and not isinstance(inner, bool):
                return str(inner)
        return None
    if isinstance(v, list):
        for item in v:
            s = _scalarize(item)
            if s is not None:
                return s
        return None
    return None


def _pick(source: dict, *keys) -> str | None:
    """Return first non-empty scalar string from source for any of the given keys.
    Skips dicts/lists that don't contain a recognisable label."""
    for k in keys:
        s = _scalarize(source.get(k))
        if s is not None and s.strip() not in _EMPTY_TOKENS:
            return s.strip()
    return None


def _fmt_price(v: str) -> str:
    try:
        s = str(v).replace("$", "").replace(",", "").strip()
        if s.upper().endswith("M"):
            return f"${float(s[:-1]):.2f}M".rstrip("0").rstrip(".")
        if s.upper().endswith("K"):
            return f"${float(s[:-1]):.0f}K"
        num = float(s)
        if num >= 1_000_000:
            return f"${num/1_000_000:.2f}M".rstrip("0").rstrip(".")
        if num >= 1_000:
            return f"${num/1_000:.0f}K"
        return f"${num:,.0f}"
    except (ValueError, TypeError):
        return str(v)[:18]


def _fmt_pct(v: str) -> str:
    s = str(v).replace("%", "").strip()
    try:
        return f"{float(s):.1f}%"
    except (ValueError, TypeError):
        return str(v)[:10] if "%" not in str(v) else str(v)[:10]


def _truncate(s: str, n: int = 20) -> str:
    return s if len(s) <= n else s[:n] + "…"


def _sale_year(date_val) -> int | None:
    """Extract a 4-digit year from a date string or int, or return None."""
    if date_val is None:
        return None
    m = re.search(r'\b(20\d\d|19\d\d)\b', str(date_val))
    return int(m.group(1)) if m else None


def _fmt_last_sale(market: dict) -> str | None:
    """Pull subject-property last sale into a 'Price (date)' string."""
    raw = (market.get("subject_property_last_sale")
           or market.get("last_sale")
           or market.get("most_recent_sale"))
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s or s.lower() in ("null", "none", "n/a", ""):
            return None
        return s
    if isinstance(raw, dict):
        price = (raw.get("price") or raw.get("sale_price")
                 or raw.get("amount") or raw.get("value"))
        date = (raw.get("date") or raw.get("sale_date")
                or raw.get("year") or raw.get("sold_date"))
        if price is None:
            return None
        p = _fmt_price(str(price))
        return f"{p} ({date})" if date else p
    return None


def _parse_metrics_from_summary(summary: str) -> dict:
    """Regex fallback: extract key values from the narrative summary text."""
    result = {}

    # Median price — e.g. "$850,000", "$1.2M", "$850K"
    pm = re.search(
        r'median\s+(?:house\s+|property\s+)?(?:price|value)[^\$\d]{0,20}\$([\d,]+(?:\.\d+)?)\s*([KkMm])?',
        summary, re.IGNORECASE
    )
    if not pm:
        pm = re.search(r'\$([\d,]+(?:\.\d+)?)\s*([Mm]illion|[Kk])', summary)
    if pm:
        try:
            num = float(pm.group(1).replace(",", ""))
            suffix = (pm.group(2) or "").lower()
            if suffix.startswith("m"): num *= 1_000_000
            elif suffix.startswith("k"): num *= 1_000
            result["median_price"] = _fmt_price(str(int(num)))
        except (ValueError, TypeError):
            pass

    # Rental yield — e.g. "3.5% yield" or "yield of 3.5%"
    ym = re.search(
        r'(?:rental\s+)?yield[^\d]{0,10}(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)\s*%\s*(?:rental\s+)?yield',
        summary, re.IGNORECASE
    )
    if ym:
        pct = ym.group(1) or ym.group(2)
        result["rental_yield"] = f"{float(pct):.1f}%"

    # Flood risk — e.g. "low flood risk", "minimal flood risk"
    fm = re.search(
        r'(low|minimal|moderate|medium|high|significant)\s+flood\s*risk',
        summary, re.IGNORECASE
    )
    if fm:
        result["flood_risk"] = fm.group(0)[:20]

    # CBD train time — e.g. "12 minutes to CBD", "10-15 min to the CBD"
    cm = re.search(
        r'(\d+)(?:\s*[-–]\s*\d+)?\s*min(?:utes?)?\s*(?:from|to)\s*(?:the\s*)?CBD',
        summary, re.IGNORECASE
    )
    if cm:
        result["cbd_train_mins"] = f"{cm.group(1)} min"

    # Market outlook — keyword scan
    if re.search(r'strong\s+(?:growth|demand|market)|positive\s+outlook|high\s+demand', summary, re.IGNORECASE):
        result["market_outlook"] = "Positive"
    elif re.search(r'declining|oversupply|negative\s+outlook|weak\s+market', summary, re.IGNORECASE):
        result["market_outlook"] = "Cautious"
    elif re.search(r'stable|steady|modest\s+growth|moderate\s+growth', summary, re.IGNORECASE):
        result["market_outlook"] = "Stable growth"

    # School quality — sentiment scan over phrases mentioning schools
    sm = re.search(
        r'(excellent|outstanding|well[-\s]regarded|highly[-\s]regarded|high[-\s]performing|'
        r'strong|reputable|top[-\s]rated|good|sought[-\s]after|limited|poor|underperforming)'
        r'\s+(?:public\s+|private\s+|local\s+|nearby\s+|catchment\s+)?schools?',
        summary, re.IGNORECASE
    )
    if sm:
        word = sm.group(1).replace("-", " ").replace("  ", " ").strip().capitalize()
        result["school_quality"] = word

    return result


def parse_scores_from_summary(summary: str) -> dict:
    """Pull the Verdict's '### Score Breakdown' values into a dict for charting."""
    if not summary:
        return {}
    # Isolate the Score Breakdown block — up to the next H3 or H2
    block = re.search(
        r'###\s*Score\s+Breakdown\b(.*?)(?=\n###|\n##|\Z)',
        summary, re.IGNORECASE | re.DOTALL
    )
    if not block:
        return {}
    body = block.group(1)

    scores: dict = {}
    for label, key in [
        ("Growth Potential",   "growth_potential"),
        ("Rental Demand",      "rental_demand"),
        ("Infrastructure",     "infrastructure"),
        ("Safety",             "safety"),
        ("Family Suitability", "family_suitability"),
    ]:
        m = re.search(
            r'-?\s*' + re.escape(label) + r'\s*:?\s*\**\s*(\d+(?:\.\d+)?)',
            body, re.IGNORECASE
        )
        if m:
            try:
                val = float(m.group(1))
                if 0 <= val <= 10:
                    scores[key] = val
            except ValueError:
                pass

    om = re.search(
        r'Overall\s+Score\s*:?\s*\**\s*(\d+(?:\.\d+)?)',
        body, re.IGNORECASE
    )
    if om:
        try:
            val = float(om.group(1))
            if 0 <= val <= 10:
                scores["overall"] = val
        except ValueError:
            pass

    return scores


def extract_metrics(report: "PropertyReport") -> dict:
    """
    Build the 6 scorecard values. Tries structured research data first,
    then falls back to regex extraction from the narrative summary.
    """
    suburb    = report.suburb    if isinstance(report.suburb,    dict) else {}
    schools   = report.schools   if isinstance(report.schools,   dict) else {}
    transport = report.transport if isinstance(report.transport, dict) else {}
    market    = report.property_market if isinstance(report.property_market, dict) else {}
    risk      = report.risk_overlays   if isinstance(report.risk_overlays,   dict) else {}

    # Median house price
    raw_p = _pick(suburb,
        "median_house_price", "median_price", "median_house_value",
        "house_median_price", "median_dwelling_price", "median_property_price")
    median = _fmt_price(raw_p) if raw_p else None

    # Rental yield
    raw_y = _pick(suburb,
        "rental_yield", "gross_rental_yield", "rental_yield_percent",
        "gross_yield", "yield")
    rental_yield = _fmt_pct(raw_y) if raw_y else None

    # School quality
    school_raw = _pick(schools,
        "school_quality_summary", "quality_summary", "overall_quality",
        "school_summary", "quality_rating", "summary")
    school = _truncate(school_raw) if school_raw else None

    # Flood risk
    flood_raw = _pick(risk,
        "flood_risk", "flood_risk_level", "flood_risk_rating",
        "flood_overlay", "flood_zone", "flood_category")
    flood = _truncate(flood_raw) if flood_raw else None

    # CBD by train
    cbd_mins = None
    nearest = transport.get("nearest_train", {})
    if isinstance(nearest, dict):
        cbd_mins = _pick(nearest, "cbd_mins", "minutes_to_cbd", "travel_time_cbd", "time_to_cbd")
    if not cbd_mins:
        cbd_mins = _pick(transport, "drive_to_cbd_offpeak_mins", "drive_to_cbd_peak_mins",
                         "cbd_drive_mins", "cbd_minutes")
    cbd = f"{cbd_mins} min" if cbd_mins else None

    # Market outlook
    outlook_raw = _pick(market,
        "market_outlook", "outlook", "market_direction",
        "price_outlook", "market_trend", "forecast")
    outlook = _truncate(outlook_raw) if outlook_raw else None

    # Subject-property last sale (info table on cover page)
    last_sale = _fmt_last_sale(market)

    # Fall back to summary text for any missing values
    if any(v is None for v in [median, rental_yield, school, flood, cbd, outlook]):
        fb = _parse_metrics_from_summary(report.summary or "")
        median       = median       or fb.get("median_price")
        rental_yield = rental_yield or fb.get("rental_yield")
        school       = school       or fb.get("school_quality")
        flood        = flood        or fb.get("flood_risk")
        cbd          = cbd          or fb.get("cbd_train_mins")
        outlook      = outlook      or fb.get("market_outlook")

    return {
        "median_price":    median       or "N/A",
        "rental_yield":    rental_yield or "N/A",
        "school_quality":  school       or "N/A",
        "flood_risk":      flood        or "N/A",
        "cbd_train_mins":  cbd          or "N/A",
        "market_outlook":  outlook      or "N/A",
        "last_sale_price": last_sale    or "Not on record",
    }


# ─── Research Prompts ────────────────────────────────────────────────────────

RESEARCH_TASKS = {
    "suburb": (
        "Property: {address}\nState: {state}\n"
        "STEP 1 — MEDIAN PRICE: Search '[suburb] [state] median house price 2025' — read the median house price, "
        "median unit price, and 5-year price growth from whichever property portal appears (realestate.com.au, "
        "domain.com.au, or any suburb profile site). Accept the first credible figure you find.\n"
        "STEP 2 — RENTAL YIELD: Search '[suburb] [postcode] gross rental yield 2025' first. "
        "If no yield percentage is found directly, search '[suburb] [state] median weekly rent 2025' "
        "and ALWAYS calculate: (weekly_rent × 52 / median_house_price × 100), rounded to 1 decimal. "
        "NEVER return null for rental_yield if a median weekly rent figure exists anywhere in your results — always compute it.\n"
        "STEP 3 — PRICE HISTORY: Fetch BOTH of these suburb profile pages directly:\n"
        "  REA profile: https://www.realestate.com.au/neighbourhoods/[suburb-hyphenated]-[postcode]-[state-lower]/\n"
        "    (e.g. hazelwood-park-5066-sa or taylors-lakes-3038-vic)\n"
        "  Domain profile: https://www.domain.com.au/suburb-profile/[suburb-lower]-[state-lower]-[postcode]/\n"
        "    (e.g. hazelwood-park-sa-5066 or taylors-lakes-vic-3038)\n"
        "Extract year-by-year median house prices for 2021–2026. "
        "If neither profile page returns year-by-year data, search '[suburb] [state] median house price 2021 2022 2023 2024 2025' "
        "and read any annual figures from suburb profile sites or property portals. "
        "Return up to 6 objects: year (int), median_house_price (numeric AUD). NEVER return an empty list if any annual figure was found.\n"
        "STEP 4 — CRIME: Search '[suburb] [state] crime statistics' and prefer {crime_url} as the primary source. "
        "If {crime_url} has no suburb-level data, use any authoritative source (state police, ABS, suburb profiles). "
        "You MUST return numeric values for crime fields — derive the percentile and deltas from the actual offence "
        "rates you find (e.g. if suburb has 850 offences/100k vs state avg 1200/100k, percentile is ~70). "
        "Return null only if you find zero crime data anywhere.\n"
        "STEP 5 — LIFESTYLE AMENITIES: {amenities_section}"
        "Return JSON with: suburb, postcode, median_house_price, median_unit_price, "
        "price_growth_5yr, rental_yield, demographics, key_amenities, "
        "liveability_score, "
        "nearest_freeway (object: name, distance_km — closest major freeway/motorway/highway), "
        "nearby_gps (list of up to 2 objects: name, distance_km — nearest general practitioner clinics), "
        "nearby_hospitals (list of up to 3 objects: name, distance_km — only if within 10km), "
        "nearby_supermarkets (list of up to 2 objects: name, distance_km — nearest Coles/Woolworths/Aldi by walking distance), "
        "nearby_parks (list of up to 2 objects: name, distance_km — nearest public park or recreation reserve), "
        "nearby_gyms (list of up to 3 objects: name, distance_km, weekly_cost_aud (integer approximate weekly membership cost in AUD; null if unknown)), "
        "crime_safety_percentile (integer 0-100 where 100 = safest in the state — "
        "calculate from actual offence rates found relative to state average; "
        "null only if zero crime data found anywhere), "
        "crime_violent_vs_state_avg_pct (signed integer — percentage delta of suburb's violent crime rate vs state average; positive = worse than average; null if not found), "
        "crime_property_vs_state_avg_pct (signed integer — same for property crime; null if not found), "
        "price_history_5yr (list of up to 6 objects: year (int 2021-2026), median_house_price (numeric AUD) — from Step 3 price history search), "
        "moving_here_demographic (one sentence — base on verified census, council, or suburb profile data; do not fabricate), "
        "becoming_narrative (one sentence on verified trends from planning documents, council strategy, or recent development activity — not a general prediction)."
    ),

    "schools": (
        "Property address: {address}\n"
        "{nearby_schools_section}"
        "STEP 1 — Catchment (primary): Search '{address} primary school catchment {state}' and use {catchment_url} as the primary source. "
        "Identify the ONE government primary school whose catchment boundary contains this exact address. "
        "If multiple schools appear, pick the one from the pre-fetched list above with the shortest distance_km — "
        "use that school on every search and do NOT switch to a different school mid-task. "
        "Fetch its myschool.edu.au profile for school type and confirm the name. "
        "Estimate walk time in minutes from the property to the school.\n"
        "STEP 2 — Catchment (secondary): Search '{address} secondary school catchment {state}' using {catchment_url}. "
        "Identify the one in-catchment government secondary school. If unclear, use the closest government secondary in the pre-fetched list. "
        "Estimate walk time.\n"
        "STEP 3 — Private schools: For private/Catholic/independent schools in the pre-fetched list, "
        "estimate annual tuition fees from the school's website if publicly posted.\n"
        "STEP 4 — Assign school_quality_summary using the in-catchment government PRIMARY school's overall reputation. "
        "Use: Excellent = nationally recognised high-performing school; Strong = above-average community reputation; "
        "Average = meets state standards; Below Average = known challenges; Limited = limited data available.\n"
        "Return JSON with: "
        "primary_schools (list: name, distance_km, "
        "in_catchment (bool — true if this address is inside the school's catchment zone), "
        "walk_mins (int — estimated walk mins; null if >25 min or not walkable)), "
        "secondary_schools (same fields as primary_schools), "
        "private_schools (list: name, distance_km, "
        "school_type ('Catholic', 'Independent', 'Anglican', 'Selective', or 'Other'), "
        "fees_annual_aud (int or null)), "
        "in_catchment_primary (string — exact name of in-catchment government primary for this address), "
        "in_catchment_secondary (string — exact name of in-catchment government secondary for this address), "
        "school_quality_summary (MUST be exactly one of: 'Excellent', 'Strong', 'Average', 'Below Average', 'Limited')."
    ),

    "government_projects": (
        "Property: {address}\nState: {state}\n"
        "STEP 1 — Search '[suburb] [state] infrastructure projects' and '[suburb] council capital works'. "
        "Find road upgrades, rail extensions, park works, school builds, community facilities planned or underway nearby.\n"
        "STEP 2 — Search '[LGA or council name] development applications [suburb]' to find recently lodged or approved DAs near this address. "
        "Also search '[suburb] [state] major development approved' for larger projects.\n"
        "STEP 3 — State-specific DA sources: "
        "NSW: search 'site:planningportal.nsw.gov.au [suburb] development application' for lodged DAs. "
        "VIC: search '[council name] planning permits [suburb] site:planning.vic.gov.au OR council website'. "
        "QLD: search '[council name] development applications [suburb]'. "
        "SA: search 'site:plan.sa.gov.au [suburb] development application'. "
        "WA: search '[council name] planning applications [suburb]'. "
        "Record any significant DAs (multi-unit residential, commercial, subdivision, demolition) within 1 km.\n"
        "STEP 4 — Check {planning_url} for zoning changes or overlays affecting this address.\n"
        "Return JSON with: "
        "infrastructure_projects (list of up to 5 objects — REQUIRED, never return empty list; "
        "every suburb has road upgrades, park works, school builds, rail projects, or community facilities nearby; "
        "each object: name (string), type (one of: Road/Rail/Community/School/Park/Council/Federal), "
        "status (e.g. 'Under Construction', 'Funded – 2026', 'Planned – 2027', 'Proposed'), "
        "description (one short sentence on what it is and how far from the property)), "
        "nearby_das (list of up to 4 objects — development applications lodged near the property: "
        "address (string), description (string — what is being built/changed), "
        "status (string — e.g. 'Approved', 'Pending', 'Under Assessment'), "
        "lodged_date (string or null), impact (one of: 'Positive', 'Neutral', 'Negative')), "
        "zoning_changes (string or null), "
        "impact_on_value (one of: 'Positive', 'Neutral', 'Negative'), "
        "impact_reason (one short sentence)."
    ),

    "transport": (
        "Property: {address}\nState: {state}\n"
        "SEARCH STEPS — use all searches, in order:\n"
        "(1) Search '[suburb] [state] train station' to identify the nearest station name, line, and CBD travel time. "
        "Then search 'walking distance {address} to [station name]' to find the actual walking distance "
        "from THIS specific address to the station — do NOT assume short proximity just because the suburb "
        "shares a name with the station; many suburbs are 1–4 km from their nearest station. "
        "Use any source that gives a street-level distance (Google Maps snippet, real estate listing, PTV). "
        "Return this as distance_km rounded to 2 decimal places.\n"
        "(2) Search '[suburb] [state] bus routes' to find route numbers serving the suburb. "
        "Prefer {transport_url} results; if unavailable use any transit authority, council, or suburb profile page. "
        "List only routes that plausibly stop within 1km of the address — do not list every route in the LGA.\n"
        "(3) Search '[suburb] [state] tram' — only return tram access if a stop is confirmed within 500m.\n"
        "(4) Search '[suburb] [state] drive time CBD' for realistic peak/off-peak estimates.\n"
        "Return JSON with: "
        "nearest_train (object: name, distance_km, line, cbd_mins), "
        "bus_routes (list of route numbers and brief destination — e.g. ['Route 426 — Watergardens to CBD']; "
        "empty list only if no routes serve the suburb at all), "
        "tram_access (string describing nearest tram stop and route, or null if none within 500m), "
        "drive_to_cbd_peak_mins, drive_to_cbd_offpeak_mins, "
        "walkability_score (integer 0-100), "
        "cycling_infrastructure (one short sentence on dedicated paths or lanes nearby)."
    ),

    "property_market": (
        "Property: {address}\n"
        "CRITICAL: You MUST find the last sold price for this specific property.\n"
        "Execute steps IN ORDER — do not skip ahead — stop as soon as you find a dollar amount.\n\n"
        "STEP 1 — Fetch the realestate.com.au property page directly. "
        "Construct the URL: https://www.realestate.com.au/property/[dwelling-type]-[number]-[street-abbreviated]-[suburb]-[state-abbrev]-[postcode]/ "
        "Rules: all lowercase, spaces→hyphens; street abbreviations: Street→st, Road→rd, Avenue→ave, Drive→dr, "
        "Court→ct, Place→pl, Crescent→cres, Close→cl, Boulevard→bvd, Terrace→tce, Lane→ln, Way→wy, Grove→gr. "
        "Try 'house' as dwelling-type first; if the page 404s try 'townhouse', then 'unit'. "
        "In the page, find 'Sold' followed by a dollar amount in the Sales History section — "
        "the FIRST (most recent) sold entry is the one to use.\n\n"
        "STEP 2 — If step 1 finds no sold price, search: \"{address} sold 2025 OR 2024 OR 2023\" "
        "and read any 'Sold $X' price from the snippet text.\n\n"
        "STEP 3 — If step 2 finds nothing, search: \"{address} sold site:domain.com.au\" "
        "and fetch the top result — look for sold price on the page.\n\n"
        "STEP 4 — If step 3 finds nothing, search: \"{address} sold price history\"\n\n"
        "STEP 5 — Comparable sales, {current_year} or {prev_year} ONLY. Fetch BOTH of these URLs directly — do not skip either:\n"
        "  Domain sold: https://www.domain.com.au/sold-listings/[suburb-hyphenated]-[state]-[postcode]/?excludepricewithheld=1&ssubs=0\n"
        "    (construct by lowercasing suburb, replacing spaces with hyphens, e.g. taylors-lakes-vic-3038)\n"
        "  REA sold: https://www.realestate.com.au/sold/in-[suburb+encoded]+[state]+[postcode]/list-1?includeSurrounding=false&source=refinement\n"
        "    (construct by lowercasing suburb, replacing spaces with +, e.g. taylors+lakes+vic+3038)\n"
        "From the combined results, pick the first 3 sales, similar type and size. "
        "STRICT DATE RULE: Only include sales with a sale_date in {current_year} or {prev_year}. Discard any result dated {two_years_ago} or earlier.\n"
        "Do NOT include any sale for {address} itself. Return empty list only if both pages return zero results.\n"
        "STEP 6 — MANDATORY: Active comparable listings (currently for sale). Run ALL THREE searches — do not skip any:\n"
        "  Search A: '[street name] [suburb] [state] for sale site:realestate.com.au'\n"
        "  Search B: '[suburb] [state] house for sale site:domain.com.au'\n"
        "  Search C: '[suburb] [state] property for sale realestate.com.au domain.com.au'\n"
        "Combine all results. Pick up to 3 ACTIVE (not sold) listings within ~3km, matching the subject property's dwelling type "
        "(house/townhouse/unit) and similar bedrooms. Prefer listings from {current_month} or {prev_month}. "
        "Do NOT include {address} itself. Do NOT include sold properties. "
        "Return an empty list ONLY if all three searches above return zero active for-sale results — "
        "if any active listing appears anywhere in the search results, include it.\n\n"
        "STEP 7 — Search for suburb-level market data: days on market, auction clearance rate, market outlook "
        "(complete only if searches remain — this step is lower priority than STEP 6).\n\n"
        "Return JSON with: "
        "subject_property_last_sale (object: price (numeric AUD — no $ sign, just the number), "
        "date (string e.g. 'February 2025') — null only if all steps above return zero results), "
        "comparable_sales (list of up to 3 comparable sales — {current_year} sales first, then {prev_year}; closest to subject property within each year; "
        "each: address, sale_price (numeric AUD), sale_date (string e.g. 'March 2025'), bedrooms (int), bathrooms (int), land_sqm (int or null)), "
        "comparable_listings (list of up to 3 active for-sale listings — similar type and size, closest first, prefer recently listed; "
        "each: address, listing_price (numeric AUD), bedrooms (int), bathrooms (int), land_sqm (int or null)), "
        "days_on_market, auction_clearance_rate, price_per_sqm, best_pockets, market_outlook."
    ),

    "risk_overlays": (
        "Property: {address}\nState: {state}\n"
        "STEP 1 — FLOOD: Search '[address] flood risk' and check {flood_url} — determine if the property is in a flood zone "
        "(high/medium/low/negligible) and whether it is in a 1-in-100 year flood area.\n"
        "STEP 2 — BUSHFIRE & BAL: Search '[suburb] [state] bushfire attack level BAL rating' — find the BAL rating for this address "
        "(BAL-FZ, BAL-40, BAL-29, BAL-19, BAL-12.5, BAL-Low, or N/A for urban areas). Use any state fire authority source.\n"
        "STEP 3 — PLANNING OVERLAYS: Check {planning_url} for this address — identify any heritage overlay, vegetation/landscape overlay, "
        "airport/flight path overlay, environmental significance overlay, or development contribution overlay that applies.\n"
        "STEP 4 — NOISE & OTHER: Search '[suburb] noise concerns airport train' to flag any aircraft noise, freeway noise, or rail noise. "
        "Also note any known contamination or EPA issues for the address if found.\n"
        "Return JSON with: "
        "flood_risk (string: 'High', 'Medium', 'Low', or 'Negligible' — with one sentence of context), "
        "bushfire_bal_rating (string e.g. 'BAL-Low', 'BAL-12.5', 'N/A — urban area'), "
        "heritage_overlay (string or null — overlay code and what it restricts), "
        "landscape_overlay (string or null), "
        "airport_overlay (string or null — flight path or building height restriction), "
        "noise_concerns (string or null — source and severity), "
        "contamination_flags (string or null), "
        "subdivision_potential (string: 'High', 'Moderate', 'Low' — one sentence reason)."
    ),

    "property_intel": (
        "Property: {address}\nState: {state}\n"
        "SEARCH STEPS — execute in order, stop fetching once you have bedrooms/land size confirmed:\n"
        "STEP 1 — Construct the realestate.com.au URL: "
        "https://www.realestate.com.au/property/[type]-[number]-[street]-[suburb]-[state]-[postcode]/ "
        "(lowercase, spaces→hyphens; street type abbreviations: Street→st, Road→rd, Avenue→ave, Drive→dr, "
        "Court→ct, Place→pl, Crescent→cres, Close→cl, Boulevard→bvd, Terrace→tce, Lane→ln, Way→wy, Grove→gr). "
        "Try 'house' first, then 'townhouse', then 'unit'. Fetch the page and extract bedrooms, bathrooms, "
        "parking, land_sqm, and any sold/listing history.\n"
        "STEP 2 — Search 'site:domain.com.au {address}' and fetch the top result. Extract the same fields.\n"
        "STEP 3 — Search '{address} bedrooms bathrooms land size' — read any real estate snippet that gives specs.\n"
        "STEP 4 — Check {planning_url} for the zoning code that applies to this address.\n"
        "All numeric fields must be numbers (not strings). "
        "STRICT RULE: use null for any field you cannot confirm from an authoritative source for THIS specific address. "
        "Do NOT estimate, infer, approximate, or extrapolate from the suburb's typical age / lot size / configuration. "
        "Hallucinated property data is worse than null.\n"
        "Fields: "
        "land_sqm (numeric — only from a real listing or title record for this address; otherwise null), "
        "dwelling_type (one of: 'House', 'Townhouse', 'Apartment', 'Unit', 'Villa', 'Land' — only if confirmed), "
        "frontage_m (numeric — only if confirmed), "
        "bedrooms (int — only if confirmed), bathrooms (int), parking (int), "
        "year_built (int — ONLY if a real listing, sale record, or title document explicitly states it for this address; otherwise null. Do NOT guess from the suburb's development era), "
        "zoning_code (e.g. 'GRZ1', 'NRZ2', 'R-Code R20' — confirmed via VicPlan / state planning portal only), "
        "zoning_description (one short sentence on what the zone allows; null if zoning_code is null), "
        "corner_block (boolean — only if confirmed via Streetview or listing imagery), "
        "subdivision_potential (object: rating ('Low'/'Moderate'/'High'), reason (one short sentence)), "
        "development_feasibility (object: rating, reason — feasibility of multi-dwelling development), "
        "renovation_potential (object: rating, reason — value-add upside from renovation), "
        "knockdown_rebuild_viability (object: rating, reason), "
        "street_position_quality (int 1-10 — only if you can reason from real local context; otherwise null)."
    ),
}


# ─── Perplexity Research Prompts ─────────────────────────────────────────────
# Goal-based prompts suited to Perplexity sonar-pro's built-in search.
# Claude prompts (RESEARCH_TASKS above) use explicit step-by-step URL construction
# because Claude must direct its own web_search tool. Perplexity handles search
# strategy internally, so simpler goal + source-order instructions work better.

RESEARCH_TASKS_PERPLEXITY = {
    "suburb": (
        "Research the property at {address}, {state}.\n"
        "Prefer official government and council sources first; use commercial property sites if official sources are unavailable.\n"
        "For proximity and amenity fields, use any credible source and return approximate distances where exact figures are unavailable.\n\n"
        "Goal: return suburb-level market, amenity, crime, and livability data for this property area.\n\n"
        "SOURCE ORDER\n"
        "1. Government, council, or official state open-data sources.\n"
        "2. Official school, planning, transport, crime, and property datasets.\n"
        "3. Major property portals only if no official source exists.\n"
        "4. Other property profile sites only as last resort.\n\n"
        "STEP 1 — MARKET DATA\n"
        "Search '[suburb] [state] median house price 2025' — read the median house price, "
        "median unit price, and 5-year price growth from whichever source appears "
        "(realestate.com.au, domain.com.au, or any suburb profile site). "
        "Accept the first credible figure you find. If multiple sources conflict, prefer the most recent. "
        "If no reliable figure is found, return null.\n\n"
        "STEP 2 — RENTAL YIELD\n"
        "Search '[suburb] [postcode] gross rental yield 2025' first. "
        "If no direct yield figure is found, search '[suburb] [state] median weekly rent 2025' and "
        "ALWAYS calculate: (weekly_rent × 52 / median_house_price × 100), rounded to 1 decimal. "
        "NEVER return null for rental_yield if a median weekly rent figure exists — always compute it.\n\n"
        "STEP 3 — PRICE HISTORY\n"
        "Fetch BOTH of these suburb profile pages directly — do not skip either:\n"
        "  REA profile: https://www.realestate.com.au/neighbourhoods/[suburb-hyphenated]-[postcode]-[state-lower]/\n"
        "    (e.g. hazelwood-park-5066-sa or taylors-lakes-3038-vic)\n"
        "  Domain profile: https://www.domain.com.au/suburb-profile/[suburb-lower]-[state-lower]-[postcode]/\n"
        "    (e.g. hazelwood-park-sa-5066 or taylors-lakes-vic-3038)\n"
        "Extract year-by-year median house prices for 2021–2026 from whichever page shows them. "
        "If neither page returns year-by-year data, search '[suburb] [state] median house price 2021 2022 2023 2024 2025' "
        "and read any annual figures from suburb profile sites or property portals. "
        "Return up to 6 objects: year (int), median_house_price (numeric AUD). NEVER return an empty list if any annual figure was found.\n\n"
        "STEP 4 — LIFESTYLE AMENITIES\n"
        "{amenities_section}"
        "Search '[suburb] [state] hospital' — return the nearest hospital only if within 10 km.\n"
        "Search '[suburb] [state] freeway motorway' — return the nearest major road and its approximate driving distance.\n\n"
        "STEP 5 — CRIME\n"
        "Use {crime_url} first if it contains suburb-level crime data.\n"
        "If {crime_url} is unavailable or lacks suburb-level data, use state police, ABS, or another official authority.\n"
        "Do not estimate crime unless actual offence rates are available.\n"
        "crime_safety_percentile must be derived from the suburb's offence rate versus the state average, where 100 = safest.\n"
        "crime_violent_vs_state_avg_pct and crime_property_vs_state_avg_pct must be calculated from actual rates if available; otherwise null.\n\n"
        "RETURN JSON WITH THESE KEYS\n"
        "suburb, postcode, median_house_price, median_unit_price, price_growth_5yr, rental_yield, demographics, key_amenities, liveability_score, "
        "nearest_freeway (object: name, distance_km), "
        "nearby_gps (list of up to 2 objects: name, distance_km), "
        "nearby_hospitals (list of up to 3 objects: name, distance_km — only if within 10km), "
        "nearby_supermarkets (list of up to 2 objects: name, distance_km), "
        "nearby_parks (list of up to 2 objects: name, distance_km), "
        "nearby_gyms (list of up to 3 objects: name, distance_km, weekly_cost_aud), "
        "crime_safety_percentile, crime_violent_vs_state_avg_pct, crime_property_vs_state_avg_pct, "
        "price_history_5yr (list of objects: year, median_house_price), "
        "moving_here_demographic (one sentence — base on census, council, or suburb profile data; do not fabricate), "
        "becoming_narrative (one sentence on verified trends from planning, development activity, or council strategy — not a generic prediction)."
    ),

    "schools": (
        "Research the property at {address}.\n"
        "Use location-aware search and map-based lookups.\n"
        "Prefer official government and state education sources first.\n"
        "Do not assume suburb-level catchment for this specific address — confirm boundary inclusion explicitly.\n"
        "Return only verified facts. If catchment or distance cannot be confirmed, return null.\n\n"
        "Goal: return verified school catchment, school quality, and nearby school options for this address.\n\n"
        "SOURCE ORDER\n"
        "1. Official catchment/boundary systems and state education sources.\n"
        "2. myschool.edu.au / ACARA for school names and types.\n"
        "3. School websites for fees only if official and publicly posted.\n"
        "4. Commercial school directories only if necessary for proximity, not for catchment confirmation.\n\n"
        "{nearby_schools_section}"
        "STEP 1 — PRIMARY CATCHMENT\n"
        "Search '[suburb] [state] primary school catchment' and also search '{address} primary school catchment zone'. "
        "Use {catchment_url} as a reference source if accessible. "
        "If pre-fetched schools are listed above, use the closest one as the starting point. "
        "Confirm whether this address is inside the catchment boundary (in_catchment = true/false). "
        "Return the school name and walking distance from the property. "
        "Add 1-2 other nearby primary schools within 2 km if verifiable.\n\n"
        "STEP 2 — SECONDARY CATCHMENT\n"
        "Find the official government secondary school catchment for {address}.\n"
        "Confirm whether the address is inside the boundary using the official state school zone map.\n"
        "Return the school name and walking distance from the property.\n"
        "Add 1-2 other nearby secondary schools within 2 km if verifiable.\n\n"
        "STEP 3 — PRIVATE SCHOOLS\n"
        "Search for Catholic, Independent, Anglican, Selective, or other private options within 5 km.\n"
        "For each, return school_type and annual tuition fees only if posted on an official school source.\n"
        "If tuition fees are not publicly posted, return null.\n\n"
        "STEP 4 — QUALITY SUMMARY\n"
        "Assign school_quality_summary for the in-catchment PRIMARY school based on its overall reputation.\n"
        "Excellent = nationally recognised high-performing school; Strong = above-average community reputation; "
        "Average = meets state standards; Below Average = known challenges; Limited = insufficient data.\n\n"
        "RETURN JSON WITH THESE KEYS\n"
        "primary_schools (list: name, distance_km, in_catchment, walk_mins), "
        "secondary_schools (same fields), "
        "private_schools (list: name, distance_km, school_type, fees_annual_aud), "
        "in_catchment_zone, school_quality_summary."
    ),

    "government_projects": (
        "Property: {address}\nState: {state}\n\n"
        "Goal: return verified infrastructure and development activity likely to affect this property.\n\n"
        "SOURCE ORDER\n"
        "1. Official state, council, planning portal, or infrastructure agency sources.\n"
        "2. Official DA registers and planning permit registers.\n"
        "3. Major project announcements from government sources.\n"
        "4. Commercial or media sources only if no official record exists.\n\n"
        "STEP 1 — INFRASTRUCTURE\n"
        "Search for planned, approved, under construction, or completed projects near the property.\n"
        "Include only projects that are plausibly relevant to the area and have a verified source.\n\n"
        "STEP 2 — DAs\n"
        "Search official DA or planning permit registers for recent or nearby applications.\n"
        "Include only significant DAs within 1 km if verifiable.\n"
        "If none are found, return an empty list.\n\n"
        "STEP 3 — ZONING / OVERLAYS\n"
        "Check {planning_url} or the official planning map for zoning changes, overlays, or amendments affecting the address.\n"
        "If no change is confirmed, return null.\n\n"
        "STEP 4 — IMPACT\n"
        "impact_on_value must be one of Positive, Neutral, or Negative based only on verified projects and planning evidence.\n\n"
        "RETURN JSON WITH THESE KEYS\n"
        "infrastructure_projects (list: name, type, status, description), "
        "nearby_das (list: address, description, status, lodged_date, impact), "
        "zoning_changes, impact_on_value, impact_reason."
    ),

    "transport": (
        "Research the property at {address}, {state}.\n"
        "Prefer official transport agency sources first; use any credible source if official data is unavailable.\n"
        "For walking distances, approximate values based on suburb context are acceptable.\n\n"
        "Goal: return transport access and commute information for this address.\n\n"
        "SOURCE ORDER\n"
        "1. Official transport agencies: PTV, TfNSW, Translink, Transperth, or equivalent.\n"
        "2. Street-level map results for walking distance.\n"
        "3. Commercial property pages only if needed for distance confirmation.\n\n"
        "STEP 1 — TRAIN\n"
        "Search '[suburb] [state] train station' using the suburb and state from {address} "
        "to find the nearest station name, line, and CBD travel time. "
        "Then search 'walking distance [suburb] to [station name]' or check any real estate listing or "
        "suburb profile that mentions the walk time from the suburb to the station. "
        "Approximate walking distances from suburb context are acceptable — return the best available estimate. "
        "Return distance_km rounded to 2 decimal places.\n\n"
        "STEP 2 — BUS\n"
        "Search '[suburb] [state] bus routes' using {transport_url} or any official or suburb-level source. "
        "The suburb and state are extracted from {address}. "
        "List only routes that plausibly stop within 1 km of the address.\n\n"
        "STEP 3 — TRAM\n"
        "Confirm tram access only if a tram stop is confirmed within 500m of the suburb in {address}.\n\n"
        "STEP 4 — DRIVE\n"
        "Search '[suburb] [state] drive time CBD' using the suburb and state from {address}. "
        "Return peak and off-peak estimates from any credible source. If neither can be found, return null.\n\n"
        "RETURN JSON WITH THESE KEYS\n"
        "nearest_train (object: name, distance_km, line, cbd_mins), "
        "bus_routes, tram_access, drive_to_cbd_peak_mins, drive_to_cbd_offpeak_mins, "
        "walkability_score, cycling_infrastructure."
    ),

    "property_market": (
        "Property: {address}\n\n"
        "Goal: return the verified sales history and current market context for this specific property.\n\n"
        "SOURCE ORDER\n"
        "1. Official listing pages or property records.\n"
        "2. Major portals such as realestate.com.au and domain.com.au.\n"
        "3. Historical sold-price pages and suburb sales history sources.\n"
        "4. Suburb-level market summaries.\n\n"
        "CRITICAL RULE\n"
        "You MUST find the last sold price for this specific property if it exists publicly.\n"
        "Stop only after you have verified the sale price with a credible source.\n"
        "If no sale price can be verified, return null.\n\n"
        "STEP 1\n"
        "Fetch the realestate.com.au property page directly. Construct the URL:\n"
        "https://www.realestate.com.au/property/[dwelling-type]-[number]-[street-abbreviated]-[suburb]-[state-abbrev]-[postcode]/\n"
        "Rules: all lowercase, spaces→hyphens; abbreviations: Street→st, Road→rd, Avenue→ave, Drive→dr, "
        "Court→ct, Place→pl, Crescent→cres, Close→cl, Boulevard→bvd, Terrace→tce, Lane→ln, Way→wy, Grove→gr. "
        "Try 'house' as dwelling-type first; if 404 try 'townhouse', then 'unit'. "
        "Find 'Sold $X' in the Sales History section — use the most recent entry.\n\n"
        "STEP 2\n"
        "If no sale price is found, search \"{address} sold 2025 OR 2024 OR 2023\".\n\n"
        "STEP 3\n"
        "If still not found, search \"{address} sold site:domain.com.au\".\n\n"
        "STEP 4\n"
        "If still not found, search \"{address} sold price history\".\n\n"
        "STEP 5\n"
        "Fetch BOTH of these URLs directly to find comparable sales — do not skip either:\n"
        "  Domain sold: https://www.domain.com.au/sold-listings/[suburb-hyphenated]-[state]-[postcode]/?excludepricewithheld=1&ssubs=0\n"
        "    (construct by lowercasing suburb, replacing spaces with hyphens, e.g. taylors-lakes-vic-3038)\n"
        "  REA sold: https://www.realestate.com.au/sold/in-[suburb+encoded]+[state]+[postcode]/list-1?includeSurrounding=false&source=refinement\n"
        "    (construct by lowercasing suburb, replacing spaces with +, e.g. taylors+lakes+vic+3038)\n"
        "From the combined results, pick the first 3 sales, similar type and size. "
        "STRICT DATE RULE: Only include sales from {current_year} or {prev_year}. Discard any result from {two_years_ago} or earlier. "
        "Do NOT include any sale for {address} itself. Return empty list only if both pages return zero results.\n\n"
        "STEP 6 — MANDATORY: Find active comparable listings (currently for sale). Run ALL THREE searches:\n"
        "  Search A: '[street name] [suburb] [state] for sale site:realestate.com.au'\n"
        "  Search B: '[suburb] [state] house for sale site:domain.com.au'\n"
        "  Search C: '[suburb] [state] property for sale realestate.com.au domain.com.au'\n"
        "Combine all results. Pick up to 3 ACTIVE (not sold) listings within ~3km, matching the subject property's dwelling type and similar bedrooms. "
        "Do NOT include {address} itself. Do NOT include sold properties. "
        "Return empty list ONLY if all three searches return zero active for-sale results.\n\n"
        "STEP 7\n"
        "Add suburb-level days on market, auction clearance rate, and market outlook only if verifiable "
        "(complete only if searches remain — this step is lower priority than STEP 6).\n\n"
        "RETURN JSON WITH THESE KEYS\n"
        "subject_property_last_sale (object: price (numeric AUD), date (string e.g. 'February 2025')), "
        "comparable_sales (list: address, sale_price, sale_date, bedrooms, bathrooms, land_sqm), "
        "comparable_listings (list of up to 3 active for-sale listings — similar type and size, closest first; "
        "each: address, listing_price (numeric AUD), bedrooms (int), bathrooms (int), land_sqm (int or null)), "
        "days_on_market, auction_clearance_rate, price_per_sqm, best_pockets, market_outlook."
    ),

    "risk_overlays": (
        "Research the property at {address}, {state}.\n"
        "Use location-aware search and map-based lookups.\n"
        "Prefer official flood, planning, bushfire, and environmental mapping sources first.\n"
        "Do not assume suburb-level risk for this specific address.\n"
        "Return only confirmed risks. If a map source does not explicitly show a result for this address, return null.\n"
        "Do not infer a hazard from proximity alone unless the source explicitly supports it.\n\n"
        "Goal: return verified property risk and overlay data.\n\n"
        "SOURCE ORDER\n"
        "1. Official flood, bushfire, planning, heritage, and environmental mapping sources.\n"
        "2. Council or state planning maps.\n"
        "3. Official hazard or environmental agency sources.\n"
        "4. Commercial risk pages only if no official source exists.\n\n"
        "STEP 1 — FLOOD\n"
        "Check the official flood map at {flood_url} for {address}.\n"
        "Return the confirmed flood zone status (High, Medium, Low, or Negligible) with one sentence of context.\n"
        "If the flood map does not explicitly show a result for this address, return null.\n\n"
        "STEP 2 — BUSHFIRE\n"
        "Search '[suburb] [state] bushfire attack level BAL rating'.\n"
        "Return the BAL rating (BAL-FZ, BAL-40, BAL-29, BAL-19, BAL-12.5, BAL-Low, or N/A) only if confirmed.\n\n"
        "STEP 3 — PLANNING OVERLAYS\n"
        "Check the official planning map at {planning_url} for {address}.\n"
        "Return only confirmed zoning codes, overlays, and amendments that apply to this address.\n"
        "If the planning map does not explicitly show a result for this address, return null.\n\n"
        "STEP 4 — NOISE / OTHER\n"
        "Search for airport, rail, freeway, contamination, or other verified local hazards near {address}.\n"
        "Only return confirmed hazards with an explicit source.\n\n"
        "RETURN JSON WITH THESE KEYS\n"
        "flood_risk, bushfire_bal_rating, heritage_overlay, landscape_overlay, "
        "airport_overlay, noise_concerns, contamination_flags, subdivision_potential."
    ),

    "property_intel": (
        "Property: {address}\nState: {state}\n\n"
        "Goal: return verified physical and planning attributes for this property.\n\n"
        "SOURCE ORDER\n"
        "1. Official listing pages or property records.\n"
        "2. Major portals such as realestate.com.au and domain.com.au.\n"
        "3. Planning maps and council records.\n"
        "4. Other property profile sources only if needed.\n\n"
        "STEP 1\n"
        "Fetch the realestate.com.au property page. Construct the URL:\n"
        "https://www.realestate.com.au/property/[dwelling-type]-[number]-[street-abbreviated]-[suburb]-[state-abbrev]-[postcode]/\n"
        "Rules: all lowercase, spaces→hyphens; abbreviations: Street→st, Road→rd, Avenue→ave, Drive→dr, "
        "Court→ct, Place→pl, Crescent→cres, Close→cl, Boulevard→bvd, Terrace→tce, Lane→ln, Way→wy, Grove→gr. "
        "Try 'house' first; if 404 try 'townhouse', then 'unit'. Extract bedrooms, bathrooms, parking, and land size.\n\n"
        "STEP 2\n"
        "Search domain.com.au for the same property and compare only if needed.\n\n"
        "STEP 3\n"
        "Search for property attributes using the address.\n\n"
        "STEP 4\n"
        "Check {planning_url} for zoning code and planning description.\n\n"
        "Only return fields that are supported by a reliable source. Use null for unknowns.\n\n"
        "RETURN JSON WITH THESE KEYS\n"
        "land_sqm, dwelling_type, frontage_m, bedrooms, bathrooms, parking, year_built, "
        "zoning_code, zoning_description, corner_block, subdivision_potential, "
        "development_feasibility, renovation_potential, knockdown_rebuild_viability, street_position_quality."
    ),
}


# ─── JSON Extraction ──────────────────────────────────────────────────────────

def _repair_truncated_json(s: str) -> str:
    """Best-effort repair of JSON that was truncated mid-output.

    Walks the string tracking brace/bracket nesting + string state, then
    closes whatever's still open in LIFO order. Also strips dangling
    commas/colons/property keys that would otherwise re-break parsing."""
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if esc:
            esc = False
            continue
        if in_str:
            if ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch == "}" and stack and stack[-1] == "}":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "]":
            stack.pop()

    out = s
    if in_str:                  # close an open string literal
        out += '"'

    out = out.rstrip()
    # Drop ',"key":' or '{"key":' patterns where the value never arrived.
    # (Done before the ',:' trim because those chars are part of the match.)
    out = re.sub(r'([,{])\s*"[^"]*"\s*:\s*$',
                 lambda m: "" if m.group(1) == "," else "{",
                 out).rstrip()
    while out and out[-1] in ",:":
        out = out[:-1].rstrip()

    return out + "".join(reversed(stack))


def _parse_json(text: str, label: str = "") -> dict:
    """
    Robustly extract a JSON object from a model response.
    Handles: plain JSON, markdown fences, JSON embedded in prose, and
    JSON truncated by max_tokens (via _repair_truncated_json)."""
    clean = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()

    # 1. Try the whole cleaned string first
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # 2. Find the outermost balanced {...} block
    start = clean.find("{")
    if start != -1:
        depth, end = 0, -1
        for i, ch in enumerate(clean[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end != -1:
            try:
                return json.loads(clean[start:end + 1])
            except json.JSONDecodeError:
                pass

    # 3. Truncated response — try to repair from the opening { onward
    if start != -1:
        repaired = _repair_truncated_json(clean[start:])
        try:
            parsed = json.loads(repaired)
            print(f"  ↻  JSON repaired for {label} (response was truncated)")
            return parsed
        except json.JSONDecodeError:
            pass

    # 4. Give up — store raw text so summary extraction can still use it
    print(f"  ⚠️  Could not parse JSON for {label}, storing raw text")
    return {"raw_text": text, "parse_error": True}


# ─── Individual Research Agent ────────────────────────────────────────────────

# Tasks that need more web searches to reliably find specific data
_TASK_MAX_SEARCHES = {
    "property_market":     14,
    "government_projects": 5,
    "suburb":              8,
    "schools":             5,
    "transport":           6,
    "risk_overlays":       4,
}
_DEFAULT_MAX_SEARCHES = 3

_TASK_MAX_TOKENS = {
    "property_market": 6000,
    "risk_overlays":   4000,
    "schools":         3500,
}
_DEFAULT_MAX_TOKENS = 3000


_RESEARCH_SYSTEM_PROMPT = (
    "Australian property researcher. Respond with valid JSON only — no prose, "
    "no markdown, no explanations outside the JSON. Use null for any field you "
    "genuinely cannot find from authoritative sources; do not invent values. "
    "Numeric fields must be numbers (not strings). "
    "Keep every string value concise — one short sentence or a few words max. "
    "Never include a 'sources', 'citations', or 'notes' field unless the prompt "
    "explicitly requests it."
)

_RESEARCH_SYSTEM_PROMPT_PERPLEXITY = (
    "Australian property researcher. Return valid JSON only. No prose, no markdown, "
    "no citations, no notes, no extra keys unless the task explicitly allows them.\n\n"
    "Use authoritative sources first: government, council, regulator, school, official property records, "
    "or official open-data portals. Use commercial portals if no official source provides the field. "
    "Never use forums, social media, or unverified blogs.\n\n"
    "For proximity and amenity fields (nearby places, transport distances, price history), "
    "any credible source is acceptable — real estate listings, suburb guides, local directories, "
    "property portals, or transport sites. Approximate distances are acceptable when exact figures "
    "are unavailable. Do not return null just because an official source does not exist for these fields; "
    "use the best available source and return a reasonable estimate.\n\n"
    "For all other fields: do not guess or invent values. Use null only if you genuinely cannot find "
    "any credible information after searching.\n\n"
    "All numeric fields must be numbers, not strings. Round only when the task specifies. "
    "If sources conflict, prefer the most recent. Keep each string concise: one short sentence or a few words max."
)


def _run_research_task_claude(client: anthropic.Anthropic, task_name: str, prompt: str, max_searches: int | None = None) -> str:
    """Execute a research task via Claude with the web_search tool. Returns raw text."""
    if max_searches is None:
        max_searches = _TASK_MAX_SEARCHES.get(task_name, _DEFAULT_MAX_SEARCHES)
    max_tokens   = _TASK_MAX_TOKENS.get(task_name, _DEFAULT_MAX_TOKENS)

    for attempt in range(4):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches}],
                system=_RESEARCH_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )
            break
        except anthropic.RateLimitError:
            if attempt == 3:
                raise
            wait = 60 * (attempt + 1)
            print(f"  ⏳ Rate limited on {task_name}, retrying in {wait}s...")
            time.sleep(wait)

    full_text = ""
    for block in response.content:
        if block.type == "text":
            full_text += block.text
        elif task_name == "property_market":
            if block.type == "server_tool_use":
                print(f"  🔎 [DEBUG] web_search query: {getattr(block, 'input', {}).get('query', block)}")
            else:
                print(f"  🧩 [DEBUG] block type: {block.type}")
    return full_text


def _run_research_task_perplexity(task_name: str, prompt: str) -> str:
    """Execute a research task via Perplexity sonar-pro. Returns raw text."""
    perplexity_client = _OpenAI(
        api_key=os.environ["PERPLEXITY_API_KEY"],
        base_url="https://api.perplexity.ai",
    )

    for attempt in range(4):
        try:
            response = perplexity_client.chat.completions.create(
                model="sonar-pro",
                messages=[
                    {"role": "system", "content": _RESEARCH_SYSTEM_PROMPT_PERPLEXITY},
                    {"role": "user",   "content": prompt},
                ],
                timeout=120,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            if attempt == 3:
                raise
            wait = 30 * (attempt + 1)
            print(f"  ⏳ Perplexity error on {task_name} ({e}), retrying in {wait}s...")
            time.sleep(wait)

    return ""


def run_research_task(client: anthropic.Anthropic, task_name: str, address: str) -> dict:
    """Run a single research task using the configured backend (claude or perplexity)."""

    print(f"  🔍 Researching {task_name} [{_RESEARCH_BACKEND}]...")

    state         = _get_state(address)
    _now          = datetime.datetime.now()
    current_month = _now.strftime("%B %Y")
    prev_month    = (_now.replace(day=1) - datetime.timedelta(days=1)).strftime("%B %Y")
    current_year  = str(_now.year)
    prev_year     = str(_now.year - 1)
    two_years_ago = str(_now.year - 2)

    # Pre-fetch Google Places data before formatting so injected sections resolve.
    places_data    = None
    amenities_data = None
    if task_name == "schools":
        places_data = _fetch_schools_google_places(address)
    if task_name == "suburb":
        amenities_data = _fetch_amenities_google_places(address)

    task_dict = RESEARCH_TASKS_PERPLEXITY if _RESEARCH_BACKEND == "perplexity" else RESEARCH_TASKS
    prompt = task_dict[task_name].format(
        address=address,
        state=state["label"],
        planning_url=state["planning"],
        crime_url=state["crime"],
        flood_url=state["flood"],
        transport_url=state["transport"],
        catchment_url=state.get("catchment", "myschool.edu.au"),
        current_month=current_month,
        prev_month=prev_month,
        current_year=current_year,
        prev_year=prev_year,
        two_years_ago=two_years_ago,
        nearby_schools_section=_build_nearby_schools_section(places_data) if task_name == "schools" else "",
        amenities_section=_build_amenities_section(amenities_data) if task_name == "suburb" else "",
    )

    # Pre-fetch authoritative data — inject into prompt so the AI skips those
    # web searches and uses the pre-fetched values instead.
    crime_data  = None
    median_data = None
    comps_data  = None

    suburb_name  = _extract_suburb(address)
    state_abbrev = _get_state_abbrev(address)

    if task_name == "suburb":
        crime_data = _fetch_crime_data(suburb_name, state_abbrev)
        if crime_data and crime_data.get("coverage") == "available":
            prompt = _inject_crime_into_suburb_prompt(prompt, crime_data)
            print(f"  ✅ Crime MCP: {suburb_name} {state_abbrev} — percentile={crime_data.get('crime_safety_percentile')}")
        else:
            crime_data = None  # not available — let AI search

        median_data = _fetch_median_price_data(suburb_name, state_abbrev, _extract_postcode(address))
        if median_data and median_data.get("coverage") == "available":
            prompt = _inject_median_into_suburb_prompt(prompt, median_data)
            print(f"  ✅ Median MCP: {suburb_name} {state_abbrev} — house=${median_data.get('median_house_price')}")
        else:
            median_data = None  # not available — let AI search

    # Dynamic search budget for suburb task:
    # Base 3: STEP 2 (rental yield, 1) + STEP 3 (price history: REA + Domain pages, 2)
    # Yield pre-fetched:    -1  (Domain scrape returned gross_rental_yield; STEP 2 skipped)
    # History pre-fetched:  -2  (Domain scrape returned price_history_5yr; STEP 3 skipped)
    # No crime MCP:         +2  (STEP 4 crime search)
    # No median MCP:        +3  (STEP 1 median)
    # No amenities API:     +4  (STEP 5 supermarket×2, gym, park, GP)
    suburb_max_searches = None
    amenities_has_data  = False
    if task_name == "suburb":
        budget = 3
        if median_data and median_data.get("gross_rental_yield") is not None:
            budget -= 1  # yield already injected, AI skips STEP 2
        if median_data and median_data.get("price_history_5yr"):
            budget -= 2  # history already injected, AI skips STEP 3 (2 page fetches)
        if not crime_data:
            budget += 2
        if not median_data:
            budget += 3
        amenities_has_data = amenities_data and sum(
            len(amenities_data.get(k, []))
            for k in ("nearby_supermarkets", "nearby_gyms", "nearby_parks", "nearby_gps")
        ) > 0
        if not amenities_has_data:
            budget += 4
        suburb_max_searches = budget
        print(
            f"  🔢 Suburb search budget: {budget} "
            f"(crime={'✅' if crime_data else '❌'}, "
            f"median={'✅' if median_data else '❌'}, "
            f"amenities={'✅' if amenities_has_data else '❌'})"
        )

    if _RESEARCH_BACKEND == "perplexity":
        full_text = _run_research_task_perplexity(task_name, prompt)
    else:
        full_text = _run_research_task_claude(client, task_name, prompt, max_searches=suburb_max_searches)

    if task_name == "property_market":
        print(f"  📄 [DEBUG] raw model output:\n{full_text[:3000]}")
        parsed = _parse_json(full_text, task_name)
        print(f"  📊 [DEBUG] subject_property_last_sale = {parsed.get('subject_property_last_sale')}")
        return parsed

    result = _parse_json(full_text, task_name)

    if task_name == "suburb":
        print(f"  📄 [DEBUG suburb] raw output (first 1000):\n{full_text[:1000]}")
        print(f"  📊 [DEBUG suburb] price_history_5yr = {result.get('price_history_5yr')}")
        print(f"  📊 [DEBUG suburb] nearby_supermarkets = {result.get('nearby_supermarkets')}")
        print(f"  📊 [DEBUG suburb] nearby_parks = {result.get('nearby_parks')}")
        print(f"  📊 [DEBUG suburb] nearby_gyms = {result.get('nearby_gyms')}")
        print(f"  📊 [DEBUG suburb] nearby_gps = {result.get('nearby_gps')}")
        print(f"  📊 [DEBUG suburb] top-level keys = {list(result.keys())[:20]}")

    if task_name == "transport":
        print(f"  📄 [DEBUG transport] raw output (first 1000):\n{full_text[:1000]}")
        print(f"  📊 [DEBUG transport] nearest_train = {result.get('nearest_train')}")
        print(f"  📊 [DEBUG transport] bus_routes = {result.get('bus_routes')}")
        print(f"  📊 [DEBUG transport] drive_to_cbd_offpeak_mins = {result.get('drive_to_cbd_offpeak_mins')}")
        print(f"  📊 [DEBUG transport] top-level keys = {list(result.keys())}")

    # Overwrite crime fields with authoritative MCP values (belt-and-suspenders —
    # ensures the AI didn't alter or ignore the injected values).
    if crime_data and crime_data.get("coverage") == "available":
        result["crime_safety_percentile"]        = crime_data.get("crime_safety_percentile")
        result["crime_violent_vs_state_avg_pct"] = crime_data.get("crime_violent_vs_state_avg_pct")
        result["crime_property_vs_state_avg_pct"]= crime_data.get("crime_property_vs_state_avg_pct")
        result["crime_data_source"]              = crime_data.get("data_source")
        if crime_data.get("crime_trend_3yr"):
            result["crime_trend_3yr"]   = crime_data["crime_trend_3yr"]
            result["crime_trend_years"] = crime_data.get("crime_trend_years")

    # Overwrite median price fields with authoritative MCP values.
    if median_data and median_data.get("coverage") == "available":
        if median_data.get("median_house_price"):
            result["median_house_price"] = median_data["median_house_price"]
        if median_data.get("median_unit_price"):
            result["median_unit_price"] = median_data["median_unit_price"]
        if median_data.get("price_history_quarterly"):
            result["price_history_quarterly"] = median_data["price_history_quarterly"]
        if median_data.get("price_history_5yr"):
            result["price_history_5yr"] = median_data["price_history_5yr"]
        if median_data.get("gross_rental_yield") is not None:
            result["gross_rental_yield"] = median_data["gross_rental_yield"]
        result["median_price_data_source"] = median_data.get("data_source")

    # Overwrite amenity fields with GPS-accurate Google Places values (belt-and-suspenders —
    # the AI ignores the injected instruction and web-searches anyway, so we force the result).
    if amenities_has_data:
        def _places_to_amenity(items: list, include_cost: bool = False) -> list:
            out = []
            for p in items:
                entry: dict = {"name": p["name"], "distance_km": p["distance_km"]}
                if include_cost:
                    entry["weekly_cost_aud"] = None
                out.append(entry)
            return out

        if amenities_data.get("nearby_supermarkets"):
            result["nearby_supermarkets"] = _places_to_amenity(amenities_data["nearby_supermarkets"])
        if amenities_data.get("nearby_gyms"):
            result["nearby_gyms"] = _places_to_amenity(amenities_data["nearby_gyms"], include_cost=True)
        if amenities_data.get("nearby_parks"):
            result["nearby_parks"] = _places_to_amenity(amenities_data["nearby_parks"])
        if amenities_data.get("nearby_gps"):
            result["nearby_gps"] = _places_to_amenity(amenities_data["nearby_gps"])

    # Post-fetch authoritative NAPLAN scores for each school returned by the AI.
    # Done after the AI task so we use the exact school names the AI found.
    # Results stored in _naplan_cache (dict of name → {naplan_performance, ...})
    # and consumed by weasy_generator when building the schools table.
    if task_name == "schools" and _myschool is not None:
        _all_names = []
        for _tier in ("primary_schools", "secondary_schools", "private_schools"):
            for _s in result.get(_tier) or []:
                if isinstance(_s, dict) and _s.get("name"):
                    _all_names.append(_s["name"])
        if _all_names:
            try:
                result["_naplan_cache"] = _myschool.get_naplan_for_schools(_all_names)
                # Override school_quality_summary with authoritative NAPLAN data.
                _primary_name = result.get("in_catchment_primary")
                if isinstance(_primary_name, str) and _primary_name:
                    _p_entry = result["_naplan_cache"].get(_primary_name)
                    _p_perf  = (_p_entry or {}).get("naplan_performance")
                    if _p_perf == "Above Average":
                        result["school_quality_summary"] = "Strong"
                    elif _p_perf == "Average":
                        result["school_quality_summary"] = "Average"
                    elif _p_perf == "Below Average":
                        result["school_quality_summary"] = "Below Average"
            except Exception as _naplan_err:
                print(f"  ⚠️  NAPLAN batch fetch error: {_naplan_err}")

    return result


# ─── Synthesis Agent ─────────────────────────────────────────────────────────

# Two cached system prompts — report is generated in parallel halves then stitched.
# Keep these constants stable; any change busts the ephemeral cache (5-min TTL).

_SHARED_RULES = """\
FORMATTING RULES:
- Use ## for major sections, ### for subsections, ** ** for inline emphasis
- Bullets must use '- ' prefix
- Lead with concrete numbers (prices, percentages, distances, dates) wherever Data supports it
- No emojis, no horizontal rules

CONCISENESS RULES (data-dense, not text-heavy — retail buyers will skim):
- Each ### subsection: MAX 3 short bullets OR one paragraph of MAX 2 short sentences — never both
- Every bullet MAX 15 words. Strip qualifiers, hedge-fund phrasing, repeated adjectives.
- Plain English first. Avoid jargon like 'structural insulation', 'transit-oriented asset', 'capital recovery against comparable evidence', 'paper growth' — write the way a smart friend would brief a buyer.
- The PDF renders visuals automatically — DO NOT enumerate items it will show:
  · Property Snapshot data table (## PROPERTY SNAPSHOT) — land/zoning/dev potentials
  · 5-year price history bar chart + Comparable Sales table (## MARKET ANALYSIS)
  · Amenities panel (## SUBURB PROFILE) — freeway, GPs, hospitals
  · School Performance chart (## SCHOOLS CATCHMENT) — NAPLAN performance per school
  · Crime safety percentile bar + crime delta table (## RISK ASSESSMENT)
  · Weighted Score Breakdown bar chart (## VERDICT) — per-factor scores
  Reference each visual once in a summary sentence, then move on. Never list items the chart will show.
- Skip filler openers ('It is worth noting that...', 'In conclusion...', 'Overall...')
- Never repeat a number already in the Key Metrics scorecard (median price, rental yield, train-to-CBD) unless directly comparing it

TONE RULES (calm and factual — not alarmist, not promotional):
- Crime, safety, and risk language must be neutral and factual. NEVER use loaded phrases like 'concerning', 'warrants scrutiny', 'red flag', 'meaningful negative', 'meaningful positive offset', 'safety profile is severe'. State the number plainly and move on.
- For positive findings, don't oversell. 'Strong' is fine; 'exceptional' / 'outstanding' / 'unmatched' should only appear once per report, if at all.
- Buyers want clarity, not drama.

MISSING-DATA WORDING (premium tone, not apologetic):
- NEVER write 'Data unavailable', 'Information not available', 'Unable to retrieve', or anything that exposes that the AI couldn't find something.
- When a specific field cannot be confirmed, frame it as a verification item the buyer can quickly resolve. Example phrasing:
  · 'Verification recommended for: school catchment boundaries (findmyschool.vic.gov.au)'
  · 'Buyer to confirm: heritage overlay status via VicPlan'
- At the end of RISK ASSESSMENT, add a tight bulleted '### Verification Checklist' subsection listing 2-4 items the buyer should confirm independently — never more than 4 items, never with 'AI could not find...' framing.
"""

_SYNTHESIS_SYSTEM_A = """\
Senior Australian property analyst. Write ONLY the sections below — verbatim headings, in order.
Do not omit any heading; if information is genuinely thin, write one short sentence following the
MISSING-DATA WORDING rules — never 'Data unavailable'.

""" + _SHARED_RULES + """
SECTIONS TO WRITE:
# PROPERTY INVESTMENT REPORT
## [ADDRESS]

**Report Date:** [Month YYYY]
**Property Type:** [type if known, else 'Residential']

## EXECUTIVE SUMMARY
[3–4 sentences covering: (1) property location and type, (2) key suburb metrics (median price, rental yield, school quality), (3) market conditions and recent comparable sales context, (4) overall investment/lifestyle assessment. Write in plain English, no bullet points in this paragraph. Be specific — cite actual figures from the data.]

### Key Highlights
- [bullet — 15–25 words, include a specific figure where possible]
- [bullet — 15–25 words, include a specific figure where possible]
- [bullet — 15–25 words, include a specific figure where possible]
- [bullet — 15–25 words, include a specific figure where possible]

### Primary Concerns
- [bullet]
- [bullet]

### Indicative Suitability
- Owner-occupiers: [LOW / MODERATE / HIGH] — [short reason]
- Investors: [LOW / MODERATE / HIGH] — [short reason]

<!-- EMAIL_SUMMARY: [see user prompt for instructions] -->

## PROPERTY SNAPSHOT
[1 short sentence framing the subject-property situation; the data table renders specifics]

### Development Outlook
- [MAX 3 bullets, each ≤18 words. Skip bullets that have nothing real to say.]

## MARKET ANALYSIS
[1 short sentence referencing the price-history chart + comparable sales table]

### Pricing & Rental
- [MAX 3 bullets]

### 5-Year Outlook
- [Bullet A — primary growth driver: infrastructure pipeline, demographic shift, or rezoning]
- [Bullet B — one risk that could moderate growth: supply, interest rates, or affordability ceiling]
- [Bullet C — supply/demand dynamic: vacancy trend, new stock coming, or demand cohort pressure]

## SUBURB PROFILE
[1 short sentence framing the suburb]

### Who's Moving Here
[MAX 2 sentences — concrete demographic shift in plain language. Not salesy.]

### What This Suburb Is Becoming
[MAX 2 sentences — forward-looking but grounded.]

### Lifestyle
- [MAX 4 bullets covering household type, income, employment, and community character. DO NOT list supermarkets / gyms / parks / freeway / GPs / hospitals — the Lifestyle table in the body renders those.]

After completing every section above, output exactly this token on its own line and STOP:

<<END_A>>

Do not write SCHOOLS, INFRASTRUCTURE, TRANSPORT, RISK, or VERDICT — those belong to the other half of the report.
"""

_SYNTHESIS_SYSTEM_B = """\
Senior Australian property analyst. Write ONLY the sections below — verbatim headings, in order.
Do NOT restate suburb basics, median price, or market trajectory already covered in the first half.
Do not omit any heading; if information is genuinely thin, write one short sentence following the
MISSING-DATA WORDING rules — never 'Data unavailable'.

""" + _SHARED_RULES + """
SECTIONS TO WRITE:
## SCHOOLS CATCHMENT
[MAX 2 short sentences. Name the primary in-catchment school(s) and describe the overall quality level in plain language (e.g. "Taylors Lakes Primary School feeds this address — school performance is above the national average and the school is walkable from the property."). The detail table renders individual School Performance labels from NAPLAN — do not enumerate those in prose.]

## INFRASTRUCTURE & DEVELOPMENT
- [MAX 4 bullets total — combine major projects, planning reforms, recent completions. Each ≤18 words.]

## TRANSPORT CONNECTIVITY

### Public Transport
- [MAX 3 bullets covering train + bus together]

### Car Travel
- [MAX 2 bullets]

## RISK ASSESSMENT

### Crime & Safety
[ONE short factual sentence. The chart shows the percentile and deltas — do NOT enumerate them. Use neutral language: state where the suburb sits and move on. NEVER use 'concerning', 'warrants scrutiny', 'red flag', 'meaningful negative', 'below state median' as judgment.]

### Environmental & Planning
- [MAX 4 bullets covering flood, bushfire/BAL, heritage, overlays. Use 'Verification recommended for: …' for unknowns.]

### Market Risks
- [MAX 3 bullets]

### Verification Checklist
- [2-4 items, each ≤18 words. e.g. 'School catchment boundaries (findmyschool.vic.gov.au)'. Never 'AI could not find'.]

## VERDICT

### Score Breakdown
- Growth Potential: **X.X** — [≤12 words]
- Rental Demand: **X.X** — [≤12 words]
- Infrastructure: **X.X** — [≤12 words]
- Safety: **X.X** — [≤12 words]
- Family Suitability: **X.X** — [≤12 words]

**Overall Score: X.X / 10**

[FORMAT IS STRICT: exact labels, score wrapped in ** **, value 0.0-10.0. PDF parses these into a chart — deviation breaks rendering.]

Score calibration — use these as anchor points for consistency:
- Growth Potential: 9-10 = strong multi-year price trend + major infrastructure tailwind; 7-8 = steady growth with some tailwind; 5-6 = flat or mixed signals; <5 = declining or speculative
- Rental Demand: 9-10 = gross yield >5% and tight vacancy; 7-8 = yield 3.5-5% with solid demand; 5-6 = modest yield, average vacancy; <5 = weak rental market
- Infrastructure: 9-10 = train station <500m; 7-8 = station <2km or strong frequent-bus network; 5-6 = bus-only or infrequent services; <5 = car-dependent with no near-term improvement
- Safety: derive from crime_violent_vs_state_avg_pct and crime_property_vs_state_avg_pct (negative = below state average = safer). Average the two deltas, then map: ≤−40% → 9–10; −20% to −40% → 7–8; −10% to −20% → 6–7; −10% to +10% → 5–6; +10% to +30% → 4–5; +30% to +60% → 2–4; >+60% → 1–2. If both deltas are null, fall back to crime_safety_percentile ÷ 10.
- Family Suitability: 9-10 = Above Average school performance (NAPLAN) in catchment + walkable + high owner-occupancy; 7-8 = Average school performance, family demographic; 5-6 = mixed school performance, mixed demographic; <5 = Below Average school performance or low family demand
- Overall Score: weighted average — Growth 25%, Rental 20%, Infrastructure 20%, Safety 15%, Family 20%]

### Overall Assessment
[MAX 2 sentences. First sentence: state a clear buy / hold / wait position — do not hedge. Second sentence: give the single most important reason supporting that position.]

### Strengths
1. [item ≤15 words]
2. [item]

### Weaknesses
1. [item]
2. [item]

### Buyer Suitability
**Owner-Occupiers:** [ONE short sentence]
**Investors:** [ONE short sentence]
**Developers:** [ONE short sentence]

### Price Guidance
[MAX 2 sentences OR 2-3 bullets — not both. Derive the price band from the comparable_sales range in the data. In the final sentence, state whether this property targets the lower or upper end of that band and cite the specific reason (e.g. station proximity, lot size, corner position, renovation potential).]

After completing every section above, output exactly this token on its own line and STOP:

<<END_B>>

Do not write anything else after the sentinel.
"""


_SYNTHESIS_MODEL         = "claude-sonnet-4-6"
_SYNTHESIS_MODEL_DEEPSEEK = "deepseek-chat"


def _data_sources_section(address: str, research_data: dict) -> str:
    """Build a ## DATA SOURCES markdown section from known research inputs."""
    state = _get_state(address)
    crime_source = research_data.get("suburb", {}).get("crime_data_source")

    sources = []
    if crime_source:
        sources.append(f"**Crime statistics:** {crime_source}")
    else:
        sources.append(f"**Crime statistics:** {state['crime']}")
    sources += [
        "**Property market data:** realestate.com.au, domain.com.au",
        "**School data:** myschool.edu.au (ACARA)",
        f"**Planning & zoning:** {state['planning']}",
        f"**Flood & environmental risk:** {state['flood']}",
        f"**Transport:** {state['transport']}",
    ]
    bullets = "\n".join(f"- {s}" for s in sources)
    return f"## DATA SOURCES\n\n{bullets}"


def _synth_chunk(
    client: anthropic.Anthropic,
    user_prompt: str,
    system_text: str,
    max_tok: int,
    label: str,
) -> str:
    for attempt in range(4):
        try:
            response = client.messages.create(
                model=_SYNTHESIS_MODEL,
                max_tokens=max_tok,
                system=[{
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text
        except anthropic.RateLimitError:
            if attempt == 3:
                raise
            wait = 60 * (attempt + 1)
            print(f"  ⏳ Rate limited on {label}, retrying in {wait}s...")
            time.sleep(wait)


def _synth_chunk_deepseek(
    user_prompt: str,
    system_text: str,
    max_tok: int,
    label: str,
) -> str:
    deepseek_client = _OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
    )
    for attempt in range(4):
        try:
            response = deepseek_client.chat.completions.create(
                model=_SYNTHESIS_MODEL_DEEPSEEK,
                max_tokens=max_tok,
                messages=[
                    {"role": "system", "content": system_text},
                    {"role": "user",   "content": user_prompt},
                ],
                timeout=120,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            if attempt == 3:
                raise
            wait = 30 * (attempt + 1)
            print(f"  ⏳ DeepSeek error on {label} ({e}), retrying in {wait}s...")
            time.sleep(wait)
    return ""


def _trim_at_sentinel(text: str, sentinel: str) -> str:
    """Cut off everything from the sentinel onward — guards against the model
    overrunning into sections that belong to the other chunk."""
    idx = text.find(sentinel)
    return (text[:idx] if idx >= 0 else text).rstrip()


def synthesise_report(client: anthropic.Anthropic, address: str, research_data: dict) -> str:
    """Synthesise all research data into a buyer-friendly narrative report."""

    backend_label = "DeepSeek deepseek-chat" if _SYNTHESIS_BACKEND == "deepseek" else "Sonnet 4.6"
    print(f"  ✍️  Synthesising report (2 parallel chunks on {backend_label})...")

    today_str   = datetime.datetime.now().strftime("%B %Y")
    user_prompt = (
        f"Address: {address}\n"
        f"Today's date: {today_str}\n"
        f"Data: {json.dumps(research_data, separators=(',', ':'))}\n\n"
        f"Write your assigned sections. Replace [ADDRESS] with the address above. "
        f"Replace [Month YYYY] with '{today_str}'. "
        "If your sections include the <!-- EMAIL_SUMMARY --> placeholder, replace it with: "
        "<!-- EMAIL_SUMMARY: [exactly 2 plain-English sentences covering the property location, "
        "headline figure (median price or last sale), and the overall investment/lifestyle stance. "
        "No markdown, no bullets, no asterisks.] --> "
        "End with the sentinel exactly as instructed."
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        if _SYNTHESIS_BACKEND == "deepseek":
            f_a = executor.submit(_synth_chunk_deepseek, user_prompt, _SYNTHESIS_SYSTEM_A, 4096, "synthesis-A")
            f_b = executor.submit(_synth_chunk_deepseek, user_prompt, _SYNTHESIS_SYSTEM_B, 4096, "synthesis-B")
        else:
            f_a = executor.submit(_synth_chunk, client, user_prompt, _SYNTHESIS_SYSTEM_A, 3500, "synthesis-A")
            f_b = executor.submit(_synth_chunk, client, user_prompt, _SYNTHESIS_SYSTEM_B, 3500, "synthesis-B")
        part_a = _trim_at_sentinel(f_a.result(), "<<END_A>>")
        part_b = _trim_at_sentinel(f_b.result(), "<<END_B>>")

    full = part_a + "\n\n" + part_b.lstrip()
    # Guarantee the Report Date is correct regardless of what the AI wrote
    today_str = datetime.datetime.now().strftime("%B %Y")
    full = re.sub(
        r'(\*\*Report Date:\*\*|\*\*Report Date\*\*:)\s+\S[^\n]*',
        f'**Report Date:** {today_str}',
        full,
    )
    attribution = _data_sources_section(address, research_data)
    return full + "\n\n" + attribution


# ─── Address Normalisation ───────────────────────────────────────────────────

_ABBREV = {
    r'\bDr\b':   'Drive',
    r'\bSt\b':   'Street',
    r'\bRd\b':   'Road',
    r'\bAve\b':  'Avenue',
    r'\bCres\b': 'Crescent',
    r'\bPl\b':   'Place',
    r'\bCt\b':   'Court',
    r'\bCl\b':   'Close',
    r'\bBlvd\b': 'Boulevard',
    r'\bHwy\b':  'Highway',
    r'\bPde\b':  'Parade',
    r'\bTce\b':  'Terrace',
    r'\bLn\b':   'Lane',
    r'\bGr\b':   'Grove',
    r'\bCct\b':  'Circuit',
}

def _normalise_address(address: str) -> str:
    """Expand common Australian street-type abbreviations so searches match full-word URLs."""
    result = address
    for pattern, replacement in _ABBREV.items():
        result = re.sub(pattern, replacement, result)
    return result




# ─── Main Orchestrator ────────────────────────────────────────────────────────

def research_property(address: str, api_key: str = None) -> PropertyReport:
    """
    Main orchestrator function. 
    Runs all research tasks and synthesises into a full report.
    
    Args:
        address: Full Australian property address (e.g. "123 Smith St, Richmond VIC 3121")
        api_key: Anthropic API key (or set ANTHROPIC_API_KEY env var)
    
    Returns:
        PropertyReport dataclass with all research data and final report
    """
    
    address = _normalise_address(address)

    # Initialise client
    if api_key:
        client = anthropic.Anthropic(api_key=api_key)
    else:
        client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY env var
    
    print(f"\n🏠 Starting property research for: {address}")
    print("=" * 60)

    # Run all research tasks + Domain API last-sale lookup in parallel
    research_data = {}
    state_abbrev  = _get_state_abbrev(address)
    suburb_name   = _extract_suburb(address)

    # Fetch comparable sales in a dedicated daemon thread so the 20-30s Scrapfly
    # wait runs fully in parallel with the AI tasks (doesn't consume a worker slot).
    _comps_result: list = [None]
    def _comps_worker():
        _comps_result[0] = _fetch_comparable_sales(
            suburb_name, state_abbrev,
            _extract_postcode(address), _extract_street_name(address),
        )
    _comps_thread = threading.Thread(target=_comps_worker, daemon=True, name="comparable-sales-fetch")
    _comps_thread.start()
    print(f"  🔄 Comparable Sales MCP fetch started in background for {suburb_name} {state_abbrev}")

    with ThreadPoolExecutor(max_workers=3) as executor:
        domain_future = executor.submit(_domain_get_last_sale, address)

        # NSW DA lookup — runs in parallel, overrides nearby_das after research
        da_future = None
        if state_abbrev == "NSW" and _get_nsw_das and suburb_name:
            da_future = executor.submit(_get_nsw_das, suburb_name)

        task_futures = {
            executor.submit(run_research_task, client, task_name, address): task_name
            for task_name in RESEARCH_TASKS.keys()
        }
        for future in as_completed(task_futures):
            task_name = task_futures[future]
            try:
                research_data[task_name] = future.result()
            except Exception as e:
                print(f"  ❌ Error in {task_name}: {e}")
                research_data[task_name] = {"error": str(e)}

        # Override last sale with authoritative Domain API data when available
        try:
            domain_sale = domain_future.result(timeout=15)
            if domain_sale:
                pm = research_data.get("property_market")
                if not isinstance(pm, dict):
                    research_data["property_market"] = {}
                    pm = research_data["property_market"]
                pm["subject_property_last_sale"] = domain_sale
                print("  ✅ [Domain API] Last sale injected into property_market")
        except Exception as e:
            print(f"  ⚠️  [Domain API] Result unavailable: {e}")

        # Inject historical NSW DA planning character into government_projects
        if da_future is not None:
            try:
                da_result = da_future.result(timeout=30)
                if da_result.get("coverage") == "available":
                    gp = research_data.get("government_projects")
                    if not isinstance(gp, dict):
                        research_data["government_projects"] = {}
                        gp = research_data["government_projects"]
                    # Store as planning_character — historical data, not current DA status
                    gp["planning_character_2018_2023"] = {
                        "total_das_lodged":     da_result.get("total_records", 0),
                        "multi_dwelling_das":   da_result.get("multi_dwelling_count", 0),
                        "large_projects":       da_result.get("large_project_count", 0),
                        "notable_projects":     da_result.get("notable_projects", []),
                        "data_period":          da_result.get("data_period", "2018–2023"),
                    }
                    print(
                        f"  ✅ [NSW DA] Historical: {da_result['total_records']} DAs lodged, "
                        f"{da_result.get('multi_dwelling_count', 0)} multi-dwelling"
                    )
                elif da_result.get("coverage") == "no_data":
                    print(f"  ℹ️  [NSW DA] No historical DA records for '{suburb_name}'")
                else:
                    print(f"  ⚠️  [NSW DA] Error: {da_result.get('error', 'unknown')}")
            except Exception as e:
                print(f"  ⚠️  [NSW DA] Result unavailable: {e}")

    # Apply comparable sales from background thread (should already be done by now)
    _comps_thread.join(timeout=45)  # generous fallback; AI tasks take 60-90s so thread is usually done
    comps_data = _comps_result[0]
    if comps_data and comps_data.get("coverage") == "available":
        pm = research_data.get("property_market")
        if not isinstance(pm, dict):
            research_data["property_market"] = {}
            pm = research_data["property_market"]
        sales = comps_data.get("comparable_sales", [])
        if sales:
            pm["comparable_sales"] = sales[:5]
            pm["comparable_sales_source"] = comps_data.get("data_source")
            print(f"  ✅ Comparable Sales MCP: {len(sales[:5])} sales injected for {suburb_name} {state_abbrev}")
        else:
            print(f"  ⚠️  Comparable Sales MCP: available but 0 sales returned for {suburb_name} {state_abbrev}")
    elif comps_data:
        print(f"  ⚠️  Comparable Sales MCP: coverage={comps_data.get('coverage')} for {suburb_name} {state_abbrev}")
    else:
        print(f"  ⚠️  Comparable Sales MCP: no response for {suburb_name} {state_abbrev}")

    print("\n📝 All research complete. Synthesising...")
    print("=" * 60)

    # Synthesise full narrative report
    summary = synthesise_report(client, address, research_data)
    
    # Build and return report object
    report = PropertyReport(
        address=address,
        suburb=research_data.get("suburb", {}),
        schools=research_data.get("schools", {}),
        government_projects=research_data.get("government_projects", {}),
        transport=research_data.get("transport", {}),
        property_market=research_data.get("property_market", {}),
        risk_overlays=research_data.get("risk_overlays", {}),
        summary=summary,
        property_intel=research_data.get("property_intel", {}),
    )
    report.metrics = extract_metrics(report)
    report.scores  = parse_scores_from_summary(summary)

    print("\n✅ Report complete!")
    return report


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python orchestrator.py \"123 Smith St, Richmond VIC 3121\"")
        sys.exit(1)
    
    address = " ".join(sys.argv[1:])
    report = research_property(address)
    
    print("\n" + "=" * 60)
    print("FULL REPORT")
    print("=" * 60)
    print(report.summary)
    
    # Save raw data for debugging / PDF generation later
    output = {
        "address": report.address,
        "research_data": {
            "suburb": report.suburb,
            "schools": report.schools,
            "government_projects": report.government_projects,
            "transport": report.transport,
            "property_market": report.property_market,
            "risk_overlays": report.risk_overlays,
            "property_intel": report.property_intel,
        },
        "summary": report.summary,
        "scores":  report.scores,
        "metrics": report.metrics,
    }
    
    with open("report_output.json", "w") as f:
        json.dump(output, f, indent=2)
    
    print("\n📁 Raw data saved to report_output.json")
