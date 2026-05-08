"""
Property Research Agent - Orchestrator
Uses Claude API with web search to research Australian properties
"""

import anthropic
import json
import re
import time
from dataclasses import dataclass, field

# Reuse state detection from pdf_generator
_STATE_SOURCES = {
    "VIC": {"label": "Victoria",             "planning": "planning.vic.gov.au",   "crime": "crimestats.vic.gov.au",                 "flood": "vicfloodmap.com.au"},
    "NSW": {"label": "New South Wales",      "planning": "planning.nsw.gov.au",   "crime": "bocsar.nsw.gov.au",                     "flood": "floodplanning.nsw.gov.au"},
    "QLD": {"label": "Queensland",           "planning": "dsdilgp.qld.gov.au",    "crime": "police.qld.gov.au/maps-and-statistics", "flood": "floodcheck.qld.gov.au"},
    "SA":  {"label": "South Australia",      "planning": "plan.sa.gov.au",         "crime": "police.sa.gov.au/services-and-stats",  "flood": "environment.sa.gov.au/flood"},
    "WA":  {"label": "Western Australia",    "planning": "planning.wa.gov.au",     "crime": "police.wa.gov.au/crime-statistics",    "flood": "planning.wa.gov.au/flood"},
    "TAS": {"label": "Tasmania",             "planning": "listmap.tas.gov.au",     "crime": "justice.tas.gov.au/crime-statistics",  "flood": "dpipwe.tas.gov.au/flood"},
    "ACT": {"label": "ACT",                  "planning": "actmapi.act.gov.au",     "crime": "police.act.gov.au/crime-statistics",   "flood": "esa.act.gov.au/flood"},
    "NT":  {"label": "Northern Territory",   "planning": "planning.nt.gov.au",     "crime": "pfes.nt.gov.au/crime-statistics",      "flood": "nt.gov.au/emergency/flood"},
}
_DEFAULT_STATE = {"label": "Australia", "planning": "planning.gov.au", "crime": "aic.gov.au", "flood": "ga.gov.au/flood"}

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
    metrics: dict = field(default_factory=dict)  # pre-extracted scorecard values


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
        "median_price":   median       or "N/A",
        "rental_yield":   rental_yield or "N/A",
        "school_quality": school       or "N/A",
        "flood_risk":     flood        or "N/A",
        "cbd_train_mins": cbd          or "N/A",
        "market_outlook": outlook      or "N/A",
    }


# ─── Research Prompts ────────────────────────────────────────────────────────

RESEARCH_TASKS = {
    "suburb": "Property: {address}\nState: {state}\nReturn JSON with: suburb, postcode, median_house_price, median_unit_price, price_growth_5yr, rental_yield, demographics, crime_rating, key_amenities, liveability_score. Use {crime_url} for crime data.",

    "schools": "Property: {address}\nReturn JSON with: primary_schools (name, distance_km, icsea), secondary_schools (name, distance_km, icsea), private_schools (name, distance_km), in_catchment_zone, school_quality_summary. Use myschool.edu.au.",

    "government_projects": "Property: {address}\nState: {state}\nReturn JSON with: transport_projects, federal_investment, council_developments, zoning_changes, impact_on_value (positive/negative), project_timelines. Use {planning_url} for planning data.",

    "transport": "Property: {address}\nReturn JSON with: nearest_train (name, distance_km, line, cbd_mins), bus_routes, tram_access, drive_to_cbd_peak_mins, drive_to_cbd_offpeak_mins, walkability_score, cycling_infrastructure.",

    "property_market": "Property: {address}\nReturn JSON with: recent_sales (last 6 months), days_on_market, auction_clearance_rate, price_per_sqm, best_pockets, market_outlook. Use realestate.com.au and domain.com.au.",

    "risk_overlays": "Property: {address}\nState: {state}\nReturn JSON with: flood_risk, bushfire_bal_rating, heritage_overlay, landscape_overlay, subdivision_potential, noise_concerns, contamination_flags. Use {planning_url} and {flood_url}.",
}


# ─── JSON Extraction ──────────────────────────────────────────────────────────

def _parse_json(text: str, label: str = "") -> dict:
    """
    Robustly extract a JSON object from a model response.
    Handles: plain JSON, markdown fences, JSON embedded in prose.
    """
    # 1. Strip markdown code fences
    clean = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()

    # 2. Try parsing the whole cleaned string first
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # 3. Find the outermost {...} block and try that
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

    # 4. Give up — store raw text so summary extraction can still use it
    print(f"  ⚠️  Could not parse JSON for {label}, storing raw text")
    return {"raw_text": text, "parse_error": True}


# ─── Individual Research Agent ────────────────────────────────────────────────

def run_research_task(client: anthropic.Anthropic, task_name: str, address: str) -> dict:
    """Run a single research task using Claude with web search."""

    print(f"  🔍 Researching {task_name}...")

    state  = _get_state(address)
    prompt = RESEARCH_TASKS[task_name].format(
        address=address,
        state=state["label"],
        planning_url=state["planning"],
        crime_url=state["crime"],
        flood_url=state["flood"],
    )

    for attempt in range(4):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2000,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
                system="Australian property researcher. Respond with valid JSON only. Use null for missing data.",
                messages=[{"role": "user", "content": prompt}]
            )
            break
        except anthropic.RateLimitError:
            if attempt == 3:
                raise
            wait = 60 * (attempt + 1)
            print(f"  ⏳ Rate limited on {task_name}, retrying in {wait}s...")
            time.sleep(wait)
    
    # Extract text from response (may include tool use blocks)
    full_text = ""
    for block in response.content:
        if block.type == "text":
            full_text += block.text
    
    return _parse_json(full_text, task_name)


# ─── Synthesis Agent ─────────────────────────────────────────────────────────

def synthesise_report(client: anthropic.Anthropic, address: str, research_data: dict) -> str:
    """Take all research data and synthesise into a buyer-friendly narrative report."""

    print("  ✍️  Synthesising final report...")
    time.sleep(60)  # let rate limit window reset after research tasks

    prompt = (
        f"Address: {address}\n"
        f"Data: {json.dumps(research_data, separators=(',', ':'))}\n\n"
        "Write a property report with sections: Executive Summary, Suburb Profile, "
        "Schools, Infrastructure, Transport, Market Analysis, Risk Assessment, Verdict. "
        "Be specific with numbers."
    )

    for attempt in range(4):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=3000,
                system="Senior Australian property analyst. Write clear, concise investment reports.",
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text
        except anthropic.RateLimitError:
            if attempt == 3:
                raise
            wait = 60 * (attempt + 1)
            print(f"  ⏳ Rate limited on synthesis, retrying in {wait}s...")
            time.sleep(wait)


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
    
    # Initialise client
    if api_key:
        client = anthropic.Anthropic(api_key=api_key)
    else:
        client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY env var
    
    print(f"\n🏠 Starting property research for: {address}")
    print("=" * 60)
    
    # Run all research tasks
    research_data = {}
    
    for task_name in RESEARCH_TASKS.keys():
        try:
            research_data[task_name] = run_research_task(client, task_name, address)
        except Exception as e:
            print(f"  ❌ Error in {task_name}: {e}")
            research_data[task_name] = {"error": str(e)}
    
    print("\n📝 All research complete. Generating report...")
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
    )
    report.metrics = extract_metrics(report)
    
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
        },
        "summary": report.summary
    }
    
    with open("report_output.json", "w") as f:
        json.dump(output, f, indent=2)
    
    print("\n📁 Raw data saved to report_output.json")
