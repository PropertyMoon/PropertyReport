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

def _pick(source: dict, *keys) -> str | None:
    """Return first non-empty value from source for any of the given keys."""
    for k in keys:
        v = source.get(k)
        if v is not None and str(v).strip() not in ("", "null", "N/A", "n/a", "none", "None"):
            return str(v).strip()
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


def extract_metrics(report: "PropertyReport") -> dict:
    """
    Build the 6 scorecard values from research data.
    Tries multiple key variants per field so we degrade gracefully
    when Claude returns slightly different schemas.
    """
    suburb   = report.suburb   if isinstance(report.suburb,   dict) else {}
    schools  = report.schools  if isinstance(report.schools,  dict) else {}
    transport = report.transport if isinstance(report.transport, dict) else {}
    market   = report.property_market if isinstance(report.property_market, dict) else {}
    risk     = report.risk_overlays   if isinstance(report.risk_overlays,   dict) else {}

    # Median house price
    raw_p = _pick(suburb,
        "median_house_price", "median_price", "median_house_value",
        "house_median_price", "median_dwelling_price", "median_property_price")
    median = _fmt_price(raw_p) if raw_p else "N/A"

    # Rental yield
    raw_y = _pick(suburb,
        "rental_yield", "gross_rental_yield", "rental_yield_percent",
        "gross_yield", "yield")
    rental_yield = _fmt_pct(raw_y) if raw_y else "N/A"

    # School quality
    school_raw = _pick(schools,
        "school_quality_summary", "quality_summary", "overall_quality",
        "school_summary", "quality_rating", "summary")
    school = _truncate(school_raw) if school_raw else "N/A"

    # Flood risk
    flood_raw = _pick(risk,
        "flood_risk", "flood_risk_level", "flood_risk_rating",
        "flood_overlay", "flood_zone", "flood_category")
    flood = _truncate(flood_raw) if flood_raw else "N/A"

    # CBD by train (minutes)
    cbd_mins = None
    nearest = transport.get("nearest_train", {})
    if isinstance(nearest, dict):
        cbd_mins = _pick(nearest, "cbd_mins", "minutes_to_cbd", "travel_time_cbd", "time_to_cbd")
    if not cbd_mins:
        cbd_mins = _pick(transport, "drive_to_cbd_offpeak_mins", "drive_to_cbd_peak_mins",
                         "cbd_drive_mins", "cbd_minutes")
    cbd = f"{cbd_mins} min" if cbd_mins else "N/A"

    # Market outlook
    outlook_raw = _pick(market,
        "market_outlook", "outlook", "market_direction",
        "price_outlook", "market_trend", "forecast")
    outlook = _truncate(outlook_raw) if outlook_raw else "N/A"

    return {
        "median_price":   median,
        "rental_yield":   rental_yield,
        "school_quality": school,
        "flood_risk":     flood,
        "cbd_train_mins": cbd,
        "market_outlook": outlook,
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
    
    # Parse JSON response
    try:
        # Strip markdown fences if present
        clean = full_text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        # Return raw text if JSON parsing fails
        print(f"  ⚠️  Could not parse JSON for {task_name}, storing raw text")
        return {"raw_text": full_text, "parse_error": True}


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
