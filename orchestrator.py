"""
Property Research Agent - Orchestrator
Uses Claude API with web search to research Australian properties
"""

import anthropic
import datetime
import httpx
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

# ─── Backend selection ────────────────────────────────────────────────────────
# Set RESEARCH_BACKEND=perplexity to route all research tasks through Perplexity
# sonar-pro (built-in web search). Default is claude (Anthropic web_search tool).
_RESEARCH_BACKEND = os.getenv("RESEARCH_BACKEND", "claude").lower()

if _RESEARCH_BACKEND == "perplexity":
    from openai import OpenAI as _OpenAI

from domain_client import get_last_sale as _domain_get_last_sale

try:
    from da_client_nsw import get_nsw_das as _get_nsw_das
except ImportError:
    _get_nsw_das = None


# Reuse state detection from pdf_generator
_STATE_SOURCES = {
    "VIC": {"label": "Victoria",           "planning": "planning.vic.gov.au",   "crime": "crimestats.vic.gov.au",                 "flood": "vicfloodmap.com.au",          "transport": "ptv.vic.gov.au"},
    "NSW": {"label": "New South Wales",    "planning": "planning.nsw.gov.au",   "crime": "bocsar.nsw.gov.au",                     "flood": "floodplanning.nsw.gov.au",    "transport": "transportnsw.info"},
    "QLD": {"label": "Queensland",         "planning": "dsdilgp.qld.gov.au",    "crime": "police.qld.gov.au/maps-and-statistics", "flood": "floodcheck.qld.gov.au",       "transport": "translink.com.au"},
    "SA":  {"label": "South Australia",    "planning": "plan.sa.gov.au",        "crime": "police.sa.gov.au/services-and-stats",   "flood": "environment.sa.gov.au/flood", "transport": "adelaidemetro.com.au"},
    "WA":  {"label": "Western Australia",  "planning": "planning.wa.gov.au",    "crime": "police.wa.gov.au/crime-statistics",     "flood": "planning.wa.gov.au/flood",    "transport": "transperth.wa.gov.au"},
    "TAS": {"label": "Tasmania",           "planning": "listmap.tas.gov.au",    "crime": "justice.tas.gov.au/crime-statistics",   "flood": "dpipwe.tas.gov.au/flood",     "transport": "metrotas.com.au"},
    "ACT": {"label": "ACT",                "planning": "actmapi.act.gov.au",    "crime": "police.act.gov.au/crime-statistics",    "flood": "esa.act.gov.au/flood",        "transport": "transport.act.gov.au"},
    "NT":  {"label": "Northern Territory", "planning": "planning.nt.gov.au",    "crime": "pfes.nt.gov.au/crime-statistics",       "flood": "nt.gov.au/emergency/flood",   "transport": "nt.gov.au/driving-transport/public-transport"},
}
_DEFAULT_STATE = {"label": "Australia", "planning": "planning.gov.au", "crime": "aic.gov.au", "flood": "ga.gov.au/flood", "transport": "transportnsw.info"}

_CRIME_MCP_URL = os.getenv(
    "CRIME_MCP_URL",
    "https://au-crime-mcp-production.up.railway.app/suburb-crime",
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


def _inject_crime_into_suburb_prompt(prompt: str, crime: dict) -> str:
    """Replace STEP 4 crime search block with pre-fetched authoritative values."""
    source = crime.get("data_source", "authoritative state source")
    injected = (
        f"STEP 4 — CRIME: Data pre-fetched from {source}. "
        f"Use these exact values — do NOT search for crime:\n"
        f"  crime_safety_percentile = {crime.get('crime_safety_percentile')}\n"
        f"  crime_violent_vs_state_avg_pct = {crime.get('crime_violent_vs_state_avg_pct')}\n"
        f"  crime_property_vs_state_avg_pct = {crime.get('crime_property_vs_state_avg_pct')}\n"
    )
    step4_start = prompt.find("STEP 4 — CRIME:")
    return_json_start = prompt.find("Return JSON with:", step4_start if step4_start >= 0 else 0)
    if step4_start >= 0 and return_json_start >= 0:
        return prompt[:step4_start] + injected + "\n" + prompt[return_json_start:]
    return prompt


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
        "STEP 2 — RENTAL YIELD: Search '[suburb] [postcode] gross rental yield 2025' — read the rental yield "
        "percentage from whichever source appears. If no yield figure is found, search '[suburb] [state] median weekly rent' "
        "and calculate yield as (weekly_rent × 52 / median_house_price × 100).\n"
        "STEP 3 — PRICE HISTORY: Search '[suburb] [state] median house price history site:realestate.com.au OR site:domain.com.au' "
        "— find year-by-year median prices from 2021 to 2025.\n"
        "STEP 4 — CRIME: Search '[suburb] [state] crime statistics' and prefer {crime_url} as the primary source. "
        "If {crime_url} has no suburb-level data, use any authoritative source (state police, ABS, suburb profiles). "
        "You MUST return numeric values for crime fields — derive the percentile and deltas from the actual offence "
        "rates you find (e.g. if suburb has 850 offences/100k vs state avg 1200/100k, percentile is ~70). "
        "Return null only if you find zero crime data anywhere.\n"
        "STEP 5 — LIFESTYLE AMENITIES: Search '[address] nearest supermarket' to find the closest Coles/Woolworths/Aldi "
        "and its walking distance. Search 'nearest gym to [address]' to find the geographically closest gym regardless "
        "of brand and its approximate weekly membership cost. Search '[address] nearest park reserve' for the closest public park.\n"
        "Return JSON with: suburb, postcode, median_house_price, median_unit_price, "
        "price_growth_5yr, rental_yield, demographics, key_amenities, "
        "liveability_score, "
        "nearest_freeway (object: name, distance_km — closest major freeway/motorway/highway), "
        "nearby_gps (list of up to 2 objects: name, distance_km — nearest general practitioner clinics), "
        "nearby_hospitals (list of up to 3 objects: name, distance_km — only if within 10km), "
        "nearby_supermarkets (list of up to 2 objects: name, distance_km — nearest Coles/Woolworths/Aldi by walking distance), "
        "nearby_parks (list of up to 2 objects: name, distance_km — nearest public park or recreation reserve), "
        "nearby_gyms (list of up to 2 objects: name, distance_km, weekly_cost_aud (integer approximate weekly membership cost in AUD; null if unknown)), "
        "crime_safety_percentile (integer 0-100 where 100 = safest in the state — "
        "calculate from actual offence rates found relative to state average; "
        "null only if zero crime data found anywhere), "
        "crime_violent_vs_state_avg_pct (signed integer — percentage delta of suburb's violent crime rate vs state average; positive = worse than average; null if not found), "
        "crime_property_vs_state_avg_pct (signed integer — same for property crime; null if not found), "
        "price_history_5yr (list of up to 6 objects: year (int 2021-2026), median_house_price (numeric AUD) — from Step 3 price history search), "
        "moving_here_demographic (one sentence describing who is moving to the suburb), "
        "becoming_narrative (one sentence on what this suburb is becoming over the next 5 years)."
    ),

    "schools": (
        "Property: {address}\n"
        "STEP 1 — Search '[suburb] [state] primary school catchment zone' to identify the in-catchment government primary school. "
        "Confirm whether THIS specific address falls inside that school's boundary (in_catchment = true/false). "
        "Fetch its myschool.edu.au profile for ICSEA and latest NAPLAN results (reading/numeracy percentile vs national average). "
        "Estimate the walk time in minutes from the property to the school. "
        "Also note 1-2 other nearby primary schools within 2 km.\n"
        "STEP 2 — Search '[suburb] [state] secondary school catchment zone' to identify the in-catchment secondary school. "
        "Confirm catchment status for this address. Fetch myschool.edu.au for ICSEA and NAPLAN. "
        "Estimate walk time. Note 1-2 other nearby secondary schools.\n"
        "STEP 3 — Search '[suburb] [state] private schools' for independent/Catholic/Anglican/selective options within 5 km. "
        "For each: fetch myschool.edu.au ICSEA where available, identify school_type (Catholic / Independent / Anglican / Selective / Other), "
        "and estimate annual tuition fees from the school's website if findable.\n"
        "STEP 4 — Review ICSEA scores. Assign school_quality_summary based on the in-catchment schools' average ICSEA.\n"
        "Return JSON with: "
        "primary_schools (list of objects: name, distance_km, icsea (int or null), "
        "in_catchment (boolean — true if this address is inside the school's catchment zone), "
        "walk_mins (int — estimated walk time in minutes; null if more than 25 min or walk not practical), "
        "naplan_reading_pct (int 1–100 — NAPLAN reading percentile rank vs national average from myschool.edu.au; null if not found), "
        "naplan_numeracy_pct (int 1–100 — same for numeracy; null if not found)), "
        "secondary_schools (same fields as primary_schools), "
        "private_schools (list: name, distance_km, icsea (int or null), "
        "school_type (string — 'Catholic', 'Independent', 'Anglican', 'Selective', or 'Other'), "
        "fees_annual_aud (int — approximate annual tuition fees AUD; null if unknown), "
        "naplan_reading_pct (int or null), naplan_numeracy_pct (int or null)), "
        "in_catchment_zone (one sentence describing the catchment boundary for this address), "
        "school_quality_summary (MUST be exactly one of: 'Excellent', 'Strong', 'Average', 'Below Average', 'Limited' — "
        "≥1080 avg ICSEA = Excellent, 1040–1079 = Strong, 1000–1039 = Average, 960–999 = Below Average, <960 = Limited)."
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
        "(1) Search '[suburb] [state] train station' to find the nearest train station, line name, and CBD travel time. "
        "Prefer results from {transport_url} but accept any authoritative source.\n"
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
        "STEP 5 — Search for comparable sales from TODAY'S market — completely independent of when the subject property "
        "was last sold. Start with the most recent month and work backwards: "
        "search '[suburb] [state] house sold {current_month} site:realestate.com.au OR site:domain.com.au'. "
        "If fewer than 2-3 results, search the previous month, then the month before, continuing backwards "
        "month by month until you have 2-3 comparable sales. Stop as soon as you reach 2-3 results. "
        "Pick properties most similar in type (house/unit/townhouse) and approximate size to the subject property. "
        "Do NOT use the subject property's own sale history as a comparable. "
        "Return empty list only if you find zero results after searching back 6 months.\n"
        "STEP 6 — Search for suburb-level market data (days on market, clearance rate, outlook).\n\n"
        "Return JSON with: "
        "subject_property_last_sale (object: price (numeric AUD — no $ sign, just the number), "
        "date (string e.g. 'February 2025') — null only if all steps above return zero results), "
        "comparable_sales (list of up to 3 comparable sales — similar type and size, "
        "most recently sold first; "
        "each: address, sale_price (numeric AUD), sale_date (string e.g. 'March 2025'), bedrooms (int), bathrooms (int), land_sqm (int or null)), "
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
        "Return JSON with property-specific details for THIS exact address "
        "(use realestate.com.au, domain.com.au, council records, {planning_url}). "
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
    "property_market":     10,
    "government_projects": 5,
    "suburb":              6,
    "schools":             4,
    "transport":           4,
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


def _run_research_task_claude(client: anthropic.Anthropic, task_name: str, prompt: str) -> str:
    """Execute a research task via Claude with the web_search tool. Returns raw text."""
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
                    {"role": "system", "content": _RESEARCH_SYSTEM_PROMPT},
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
    current_month = datetime.datetime.now().strftime("%B %Y")
    prompt = RESEARCH_TASKS[task_name].format(
        address=address,
        state=state["label"],
        planning_url=state["planning"],
        crime_url=state["crime"],
        flood_url=state["flood"],
        transport_url=state["transport"],
        current_month=current_month,
    )

    # Pre-fetch crime data for suburb task — inject into prompt so the AI
    # skips the crime web search and uses authoritative values instead.
    crime_data = None
    if task_name == "suburb":
        suburb_name  = _extract_suburb(address)
        state_abbrev = _get_state_abbrev(address)
        crime_data   = _fetch_crime_data(suburb_name, state_abbrev)
        if crime_data and crime_data.get("coverage") == "available":
            prompt = _inject_crime_into_suburb_prompt(prompt, crime_data)
            print(f"  ✅ Crime MCP: {suburb_name} {state_abbrev} — percentile={crime_data.get('crime_safety_percentile')}")
        else:
            crime_data = None  # not available — let AI search

    if _RESEARCH_BACKEND == "perplexity":
        full_text = _run_research_task_perplexity(task_name, prompt)
    else:
        full_text = _run_research_task_claude(client, task_name, prompt)

    if task_name == "property_market":
        print(f"  📄 [DEBUG] raw model output:\n{full_text[:3000]}")
        parsed = _parse_json(full_text, task_name)
        print(f"  📊 [DEBUG] subject_property_last_sale = {parsed.get('subject_property_last_sale')}")
        return parsed

    result = _parse_json(full_text, task_name)

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
  · ICSEA bar chart (## SCHOOLS CATCHMENT) — individual school scores
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

<!-- EMAIL_SUMMARY: [Exactly 2 plain-English sentences summarising the property's location, headline data, and investment/lifestyle stance. No markdown, no bullets, no asterisks.] -->

## PROPERTY SNAPSHOT
[1 short sentence framing the subject-property situation; the data table renders specifics]

### Development Outlook
- [MAX 3 bullets, each ≤18 words. Skip bullets that have nothing real to say.]

## MARKET ANALYSIS
[1 short sentence referencing the price-history chart + comparable sales table]

### Pricing & Rental
- [MAX 3 bullets]

### 5-Year Outlook
- [MAX 3 bullets]

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
[MAX 2 short sentences — the school detail table renders catchment status, ICSEA, NAPLAN percentiles, and proximity for each school. Reference the qualitative picture only (e.g. in-catchment schools above national average, walkable from the property) — do NOT list individual school names or numeric scores.]

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

[FORMAT IS STRICT: exact labels, score wrapped in ** **, value 0.0-10.0. PDF parses these into a chart — deviation breaks rendering. Safety scale: 10 = safest, 0 = worst.]

### Overall Assessment
[MAX 2 short sentences — buy / hold / avoid leaning.]

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
[MAX 2 short sentences OR 2-3 bullets — not both.]

After completing every section above, output exactly this token on its own line and STOP:

<<END_B>>

Do not write anything else after the sentinel.
"""


_SYNTHESIS_MODEL = "claude-sonnet-4-6"


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


def _trim_at_sentinel(text: str, sentinel: str) -> str:
    """Cut off everything from the sentinel onward — guards against the model
    overrunning into sections that belong to the other chunk."""
    idx = text.find(sentinel)
    return (text[:idx] if idx >= 0 else text).rstrip()


def synthesise_report(client: anthropic.Anthropic, address: str, research_data: dict) -> str:
    """Synthesise all research data into a buyer-friendly narrative report."""

    print("  ✍️  Synthesising report (2 parallel chunks on Sonnet 4.6)...")

    today_str   = datetime.datetime.now().strftime("%B %Y")
    user_prompt = (
        f"Address: {address}\n"
        f"Today's date: {today_str}\n"
        f"Data: {json.dumps(research_data, separators=(',', ':'))}\n\n"
        f"Write your assigned sections. Replace [ADDRESS] with the address above. "
        f"Replace [Month YYYY] with '{today_str}'. "
        "End with the sentinel exactly as instructed."
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        f_a = executor.submit(_synth_chunk, client, user_prompt, _SYNTHESIS_SYSTEM_A, 2500, "synthesis-A")
        f_b = executor.submit(_synth_chunk, client, user_prompt, _SYNTHESIS_SYSTEM_B, 2500, "synthesis-B")
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

    with ThreadPoolExecutor(max_workers=len(RESEARCH_TASKS) + 2) as executor:
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
