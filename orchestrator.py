"""
Property Research Agent - Orchestrator
Uses Claude API with web search to research Australian properties
"""

import anthropic
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _fmt_last_sale(market: dict) -> str | None:
    """Pull subject-property last sale into a 'Price (date)' string."""
    raw = (market.get("subject_property_last_sale")
           or market.get("last_sale")
           or market.get("most_recent_sale"))
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip()
        return s if s and s.lower() not in ("null", "none", "n/a", "") else None
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
        "Return JSON with: suburb, postcode, median_house_price, median_unit_price, "
        "price_growth_5yr, rental_yield, demographics, crime_rating, key_amenities, "
        "liveability_score, "
        "nearest_freeway (object: name, distance_km — closest major freeway/motorway/highway), "
        "nearby_gps (list of up to 2 objects: name, distance_km — nearest general practitioner clinics), "
        "nearby_hospitals (list of up to 3 objects: name, distance_km — only if within 10km), "
        "crime_safety_percentile (integer 0-100 from {crime_url}, where 100 = safest in the state), "
        "crime_violent_vs_state_avg_pct (signed integer — percentage delta of suburb's violent crime rate vs state average; positive = worse), "
        "crime_property_vs_state_avg_pct (signed integer — same for property crime), "
        "price_history_5yr (list of up to 6 objects: year (int 2019-2025), median_house_price (numeric AUD) — annual median for the suburb), "
        "moving_here_demographic (one sentence describing who is moving to the suburb — e.g. 'Young professional families displaced from inner-city; Asian-Australian households drawn by schools'), "
        "becoming_narrative (one sentence on what this suburb is becoming over the next 5 years — gentrification trajectory, infrastructure-driven change, etc.). "
        "Use {crime_url} for crime data and CoreLogic/realestate.com.au median history for prices."
    ),

    "schools": "Property: {address}\nReturn JSON with: primary_schools (name, distance_km, icsea), secondary_schools (name, distance_km, icsea), private_schools (name, distance_km), in_catchment_zone, school_quality_summary. Use myschool.edu.au.",

    "government_projects": "Property: {address}\nState: {state}\nReturn JSON with: transport_projects, federal_investment, council_developments, zoning_changes, impact_on_value (positive/negative), project_timelines. Use {planning_url} for planning data.",

    "transport": "Property: {address}\nReturn JSON with: nearest_train (name, distance_km, line, cbd_mins), bus_routes, tram_access, drive_to_cbd_peak_mins, drive_to_cbd_offpeak_mins, walkability_score, cycling_infrastructure.",

    "property_market": (
        "Property: {address}\n"
        "Return JSON with: "
        "subject_property_last_sale (price, date — for THIS exact address from realestate.com.au sold history or domain.com.au; null if no record), "
        "comparable_sales (list of EXACTLY 2 most recent comparable sales in the same suburb — similar property type, similar size; each object must include: address, sale_price (numeric AUD), sale_date (e.g. 'March 2025'), bedrooms (int), bathrooms (int), land_sqm (int)), "
        "days_on_market, auction_clearance_rate, price_per_sqm, best_pockets, market_outlook. "
        "Use realestate.com.au and domain.com.au sold-history pages."
    ),

    "risk_overlays": "Property: {address}\nState: {state}\nReturn JSON with: flood_risk, bushfire_bal_rating, heritage_overlay, landscape_overlay, subdivision_potential, noise_concerns, contamination_flags. Use {planning_url} and {flood_url}.",

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
                model="claude-sonnet-4-6",
                max_tokens=3000,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
                system=(
                    "Australian property researcher. Respond with valid JSON only — no prose, "
                    "no markdown, no explanations outside the JSON. Use null for any field you "
                    "genuinely cannot find from authoritative sources; do not invent values. "
                    "Numeric fields must be numbers (not strings). "
                    "Keep every string value concise — one short sentence or a few words max. "
                    "Never include a 'sources', 'citations', or 'notes' field unless the prompt "
                    "explicitly requests it."
                ),
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
[1-2 short sentences: where the property is, headline data, one-line stance]

### Key Highlights
- [bullet — max 15 words]
- [bullet]
- [bullet]

### Primary Concerns
- [bullet]
- [bullet]

### Indicative Suitability
- Owner-occupiers: [LOW / MODERATE / HIGH] — [short reason]
- Investors: [LOW / MODERATE / HIGH] — [short reason]

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

### Household & Amenities
- [MAX 4 bullets covering household type, income, employment, key amenities. DO NOT list freeway / GPs / hospitals — the Amenities panel renders those.]

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
[MAX 2 short sentences — the ICSEA chart renders per-school detail. Do NOT enumerate school names with scores.]

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

    user_prompt = (
        f"Address: {address}\n"
        f"Data: {json.dumps(research_data, separators=(',', ':'))}\n\n"
        "Write your assigned sections. Replace [ADDRESS] with the address above. "
        "End with the sentinel exactly as instructed."
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        f_a = executor.submit(_synth_chunk, client, user_prompt, _SYNTHESIS_SYSTEM_A, 2500, "synthesis-A")
        f_b = executor.submit(_synth_chunk, client, user_prompt, _SYNTHESIS_SYSTEM_B, 2500, "synthesis-B")
        part_a = _trim_at_sentinel(f_a.result(), "<<END_A>>")
        part_b = _trim_at_sentinel(f_b.result(), "<<END_B>>")

    return part_a + "\n\n" + part_b.lstrip()


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

    # Run all 6 research tasks in parallel
    research_data = {}

    with ThreadPoolExecutor(max_workers=len(RESEARCH_TASKS)) as executor:
        futures = {
            executor.submit(run_research_task, client, task_name, address): task_name
            for task_name in RESEARCH_TASKS.keys()
        }
        for future in as_completed(futures):
            task_name = futures[future]
            try:
                research_data[task_name] = future.result()
            except Exception as e:
                print(f"  ❌ Error in {task_name}: {e}")
                research_data[task_name] = {"error": str(e)}

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
