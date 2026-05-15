"""
Phase 2B spike — WeasyPrint-based dashboard cover page.

Renders the cover dashboard from the mockup (top-row title/photo/map,
6 metric cards, charts row, detail row, verdict band) as a single A4
landscape page using HTML/CSS. Always writes an HTML preview file you
can open in a browser; writes a PDF too when WeasyPrint is importable
(Linux/Mac; on Windows the HTML preview is the spike deliverable).

Usage:
    python weasy_generator.py                       # uses sample data
    python weasy_generator.py report_output.json    # uses a real research dump
    python weasy_generator.py report.json out.pdf   # custom output path
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

try:
    from jinja2 import Template
except ImportError:
    print("Run: pip install jinja2", file=sys.stderr)
    raise

try:
    import markdown  # type: ignore
    _MD_OK = True
except ImportError:
    _MD_OK = False

try:
    from dotenv import load_dotenv
    load_dotenv()  # populate GOOGLE_MAPS_API_KEY etc. from .env when run locally
except ImportError:
    pass

try:
    from weasyprint import HTML  # type: ignore
    _WEASY_OK = True
except Exception as _e:  # noqa: BLE001
    _WEASY_OK = False
    _WEASY_ERR = str(_e)


# ─── Inline SVG icons (no emoji font dependency) ─────────────────────────────
#
# Stored as inner-markup only. icon() wraps with an <svg> tag that carries
# explicit width/height/xmlns/viewBox/fill — WeasyPrint occasionally renders
# an inline SVG at zero size when those attributes are missing.

ICONS: dict[str, str] = {
    "home":         '<path d="M12 3l9 8h-3v9h-5v-6h-2v6H6v-9H3l9-8z"/>',
    "trending-up":  '<path d="M3.5 17.5l5-5 4 4 7-7v4h2V6h-7.5v2h4.5l-6 6-4-4-5.5 5.5z"/>',
    "cap":          '<path d="M12 3L1 9l11 6 9-4.91V17h2V9L12 3zm0 13L5 12v3l7 4 7-4v-3l-7 4z"/>',
    "train":        '<path d="M12 2c-4 0-8 .5-8 4v9.5C4 17.4 5.6 19 7.5 19L6 20.5v.5h12v-.5L16.5 19c1.9 0 3.5-1.6 3.5-3.5V6c0-3.5-4-4-8-4zM7.5 17a1.5 1.5 0 100-3 1.5 1.5 0 000 3zm3.5-7H6V6h5v4zm6.5 7a1.5 1.5 0 100-3 1.5 1.5 0 000 3zM18 10h-5V6h5v4z"/>',
    "shield":       '<path d="M12 2L4 5v6c0 5.5 3.4 10.7 8 12 4.6-1.3 8-6.5 8-12V5l-8-3z"/>',
    "chart-bar":    '<rect x="4" y="11" width="3" height="8" rx="1"/><rect x="10" y="5" width="3" height="14" rx="1"/><rect x="16" y="8" width="3" height="11" rx="1"/>',
    "car":          '<path d="M18.92 6c-.2-.6-.76-1-1.42-1H6.5c-.66 0-1.22.4-1.42 1L3 12v8c0 .55.45 1 1 1h1a1 1 0 001-1v-1h12v1a1 1 0 001 1h1c.55 0 1-.45 1-1v-8l-2.08-6zM6.5 16a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm11 0a1.5 1.5 0 110-3 1.5 1.5 0 010 3zM5 11l1.5-4.5h11L19 11H5z"/>',
    "bus":          '<path d="M4 16c0 .88.39 1.67 1 2.22V20a1 1 0 001 1h1a1 1 0 001-1v-1h8v1a1 1 0 001 1h1a1 1 0 001-1v-1.78c.61-.55 1-1.34 1-2.22V6c0-3.5-3.58-4-8-4s-8 .5-8 4v10zm3.5 1a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm9 0a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm1.5-6H6V6h12v5z"/>',
    "highway":      '<path d="M11 5h2v3h-2V5zm0 5h2v3h-2v-3zm0 5h2v3h-2v-3zM7 5h2v3H7V5zm0 5h2v3H7v-3zm0 5h2v3H7v-3zm8-10h2v3h-2V5zm0 5h2v3h-2v-3zm0 5h2v3h-2v-3z"/>',
    "shopping":     '<path d="M19 6h-3V4c0-1.1-.9-2-2-2h-4c-1.1 0-2 .9-2 2v2H5c-.55 0-1 .45-1 1v13c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V7c0-.55-.45-1-1-1zM9 4h6v2H9V4z"/>',
    "stethoscope":  '<path d="M5 3v6a4 4 0 008 0V3h-2v6a2 2 0 11-4 0V3H5zm-2 0h2v2H3V3zm8 0h2v2h-2V3zm0 11a5 5 0 0010 0v-3h-2v3a3 3 0 11-6 0v-1a6 6 0 01-2 .87V14z"/><circle cx="19" cy="9" r="2"/>',
    "hospital":     '<path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-4 11h-2v2h-2v-2H9v-2h2V9h2v3h2v2z"/>',
    "clipboard":    '<path d="M19 3h-4.18C14.4 1.84 13.3 1 12 1S9.6 1.84 9.18 3H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V5a2 2 0 00-2-2zm-7 0a1 1 0 110 2 1 1 0 010-2z"/>',
    "building":     '<path d="M12 7V3H2v18h20V7H12zM6 19H4v-2h2v2zm0-4H4v-2h2v2zm0-4H4V9h2v2zm0-4H4V5h2v2zm4 12H8v-2h2v2zm0-4H8v-2h2v2zm0-4H8V9h2v2zm0-4H8V5h2v2zm10 12h-8v-2h2v-2h-2v-2h2v-2h-2V9h8v10zm-2-8h-2v2h2v-2zm0 4h-2v2h2v-2z"/>',
    "construction": '<path d="M14 4l6 6-2 2-2-2-3 3 2 2-2 2-2-2-3 3 2 2-2 2-6-6 12-12zM18 5l1.4-1.4a2 2 0 012.8 0L23 4l-2 2-3-1z"/>',
    "bulb":         '<path d="M9 21c0 .55.45 1 1 1h4c.55 0 1-.45 1-1v-1H9v1zM12 2C8.14 2 5 5.14 5 9c0 2.38 1.19 4.47 3 5.74V17a1 1 0 001 1h6a1 1 0 001-1v-2.26c1.81-1.27 3-3.36 3-5.74 0-3.86-3.14-7-7-7z"/>',
    "map-pin":      '<path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5a2.5 2.5 0 110-5 2.5 2.5 0 010 5z"/>',
}


def icon(name: str, size: int = 24) -> str:
    """Return inline SVG markup with explicit dimensions + xmlns + viewBox.

    Renderers like WeasyPrint occasionally lay out a missing-attribute SVG
    at zero pixels — these attributes are belt-and-braces."""
    path = ICONS.get(name)
    if not path:
        return ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="currentColor">{path}</svg>'
    )


# ─── Google Maps image fetchers ───────────────────────────────────────────────

_GMAPS_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()


def _fetch_image_data_uri(url: str, mime: str = "image/jpeg",
                          min_bytes: int = 5_000) -> str | None:
    """Download an image and inline it as a data URI. Returns None on failure
    or if the response looks like Google's grey 'no imagery' placeholder."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PropertyReport/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = resp.read()
        if len(data) < min_bytes:
            return None
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    except Exception as e:  # noqa: BLE001
        print(f"[!] Image fetch failed: {e}")
        return None


def fetch_street_view_uri(address: str, w: int = 700, h: int = 360) -> str | None:
    if not _GMAPS_KEY or _GMAPS_KEY.startswith("your-"):
        return None
    params = urllib.parse.urlencode({
        "size":               f"{w}x{h}",
        "location":           address,
        "key":                _GMAPS_KEY,
        "source":             "outdoor",
        "return_error_codes": "true",
    })
    return _fetch_image_data_uri(
        f"https://maps.googleapis.com/maps/api/streetview?{params}"
    )


def fetch_static_map_uri(address: str, w: int = 600, h: int = 280,
                         zoom: int = 14) -> str | None:
    if not _GMAPS_KEY or _GMAPS_KEY.startswith("your-"):
        return None
    params = urllib.parse.urlencode({
        "size":    f"{w}x{h}",
        "center":  address,
        "zoom":    zoom,
        "scale":   2,
        "maptype": "roadmap",
        "markers": f"color:0x10b981|size:mid|{address}",
        "key":     _GMAPS_KEY,
    })
    return _fetch_image_data_uri(
        f"https://maps.googleapis.com/maps/api/staticmap?{params}",
        mime="image/png",
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_price(v: Any) -> str:
    if v is None:
        return "—"
    try:
        n = float(str(v).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return str(v)[:14]
    if n >= 1_000_000:
        return f"${n/1_000_000:.2f}M".rstrip("0").rstrip(".")
    if n >= 1_000:
        return f"${n/1_000:.0f}K"
    return f"${n:,.0f}"


def _star_row(score: float) -> str:
    """Render a 0-10 score as a 5-star SVG row (1 star = 2 points)."""
    full = int(score / 2)
    half = 1 if (score / 2 - full) >= 0.5 else 0
    empty = 5 - full - half
    parts = []
    for _ in range(full):
        parts.append('<span class="star full">★</span>')
    for _ in range(half):
        parts.append('<span class="star half">★</span>')
    for _ in range(empty):
        parts.append('<span class="star empty">☆</span>')
    return "".join(parts)


def _pick(d: dict, *keys, default=None):
    for k in keys:
        v = d.get(k)
        if v is not None and v != "" and v != "N/A":
            return v
    return default


# ─── Stub report (so the spike works without a JSON dump) ─────────────────────

@dataclass
class StubReport:
    address: str = "35 Pindari Ave, Taylors Lakes VIC 3038"
    suburb: dict = field(default_factory=dict)
    schools: dict = field(default_factory=dict)
    government_projects: dict = field(default_factory=dict)
    transport: dict = field(default_factory=dict)
    property_market: dict = field(default_factory=dict)
    risk_overlays: dict = field(default_factory=dict)
    property_intel: dict = field(default_factory=dict)
    summary: str = ""
    metrics: dict = field(default_factory=dict)
    scores: dict = field(default_factory=dict)


def sample_report() -> StubReport:
    return StubReport(
        address="35 Pindari Ave, Taylors Lakes VIC 3038",
        suburb={
            "median_house_price": 960_000,
            "rental_yield": "4.5%",
            "crime_safety_percentile": 32,
            "crime_violent_vs_state_avg_pct": -17,
            "crime_property_vs_state_avg_pct": -8,
            "price_history_5yr": [
                {"year": 2017, "median_house_price": 460_000},
                {"year": 2018, "median_house_price": 510_000},
                {"year": 2019, "median_house_price": 540_000},
                {"year": 2020, "median_house_price": 590_000},
                {"year": 2021, "median_house_price": 720_000},
                {"year": 2022, "median_house_price": 800_000},
                {"year": 2023, "median_house_price": 850_000},
                {"year": 2024, "median_house_price": 920_000},
                {"year": 2025, "median_house_price": 960_000},
            ],
            "nearest_freeway": {"name": "M80 Ring Road", "distance_km": 5.2},
            "nearby_gps":      [{"name": "Watergardens Medical", "distance_km": 1.0}],
            "nearby_hospitals":[{"name": "Sunshine Hospital", "distance_km": 8.5}],
        },
        schools={
            "primary_schools":   [{"name": "Taylors Lakes Primary School", "icsea": 1050, "distance_km": 0.9}],
            "secondary_schools": [{"name": "Taylors Lakes Secondary College", "icsea": 1015, "distance_km": 1.2}],
            "school_quality_summary": "Strong",
        },
        transport={
            "nearest_train": {"name": "Watergardens Station", "distance_km": 0.12, "cbd_mins": 34},
            "drive_to_cbd_peak_mins": 48,
            "drive_to_cbd_offpeak_mins": 20,
        },
        property_market={
            "subject_property_last_sale": {"price": 667_000, "date": "October 2018"},
            "comparable_sales": [
                {"address": "13 Pindari Ave",   "sale_price": 775_000, "sale_date": "May 2024",  "bedrooms": 3, "bathrooms": 2},
                {"address": "28 Cloudburst Ave","sale_price": 942_000, "sale_date": "Feb 2025",  "bedrooms": 4, "bathrooms": 2},
                {"address": "7 Windrow Cres",   "sale_price": 900_000, "sale_date": "Jan 2025",  "bedrooms": 4, "bathrooms": 2},
                {"address": "42 Pindari Ave",   "sale_price": 820_000, "sale_date": "Nov 2024",  "bedrooms": 3, "bathrooms": 1},
            ],
            "market_outlook": "Stable Growth",
            "median_rent_house": 550,
        },
        government_projects={
            "transport_projects": [
                {"name": "Melton Hwy / Ballarat Rd Intersection Upgrade", "status": "Completed 2024"},
                {"name": "Robertsons Rd Infill Project (~175 New Lots)",  "status": "In Progress 2025–2026"},
                {"name": "Sydenham Train Station Upgrade",                 "status": "Planned 2026+"},
            ],
        },
        metrics={
            "median_price": "$960K",
            "rental_yield": "4.5%",
            "school_quality": "Strong",
            "cbd_train_mins": "34 min",
            "market_outlook": "Stable Growth",
            "last_sale_price": "$667K (October 2018)",
        },
        scores={
            "growth_potential": 7.0,
            "rental_demand":    6.0,
            "infrastructure":   8.0,
            "safety":           4.0,
            "family_suitability": 8.0,
            "overall": 6.5,
        },
        property_intel={
            "land_sqm": 612,
            "dwelling_type": "House",
            "frontage_m": 17.4,
            "bedrooms": 4, "bathrooms": 2, "parking": 2,
            "year_built": 1995,
            "zoning_code": "GRZ1",
            "zoning_description": "General Residential — neighbourhood-scale dwellings, up to 2 storeys",
            "corner_block": False,
            "subdivision_potential":      {"rating": "Moderate", "reason": "612 m² lot supports a battle-axe subdivision subject to council approval"},
            "development_feasibility":    {"rating": "Moderate", "reason": "Two-dwelling development achievable on this lot size"},
            "renovation_potential":       {"rating": "High",     "reason": "1990s build benefits from kitchen / bathroom updates"},
            "knockdown_rebuild_viability":{"rating": "High",     "reason": "Land value supports rebuild economics in this pocket"},
            "street_position_quality": 7,
        },
        summary=(
            "# PROPERTY INVESTMENT REPORT\n"
            "## 35 Pindari Ave, Taylors Lakes VIC 3038\n\n"
            "**Report Date:** May 2026\n"
            "**Property Type:** Residential\n\n"
            "## EXECUTIVE SUMMARY\n"
            "35 Pindari Ave sits 120 m from Watergardens Station on the Sunbury Line, 34 minutes to the CBD by train.\n"
            "The suburb median is $960K with stable growth, and the dominant family demographic anchors long-term value.\n\n"
            "### Key Highlights\n"
            "- Walk to a major train station — strongest single value driver on the address\n"
            "- Established family suburb with 86% owner-occupancy and quality school options\n"
            "- 5-year history shows steady appreciation without speculative spikes\n\n"
            "### Primary Concerns\n"
            "- Below-median safety percentile — property crime is the main category to weigh\n"
            "- Modest rental yield limits cash-flow appeal for pure-investor buyers\n\n"
            "### Indicative Suitability\n"
            "- Owner-occupiers: **HIGH** — transit, schools, family character\n"
            "- Investors: **MODERATE** — long-hold capital growth thesis, not yield\n\n"
            "## PROPERTY SNAPSHOT\n"
            "The subject property is on a 612 m² GRZ1 lot with strong infill potential and renovation upside on a 1990s build. "
            "Development options sit at the moderate end given lot dimensions; renovation is the most direct value-add path.\n\n"
            "### Development Outlook\n"
            "- Battle-axe subdivision possible subject to Brimbank council approval and stormwater feasibility\n"
            "- Two-dwelling development achievable but tight on frontage — front/rear split most likely configuration\n"
            "- Renovation pathway is the most efficient ROI given the 1990s build vintage and finishes\n\n"
            "## MARKET ANALYSIS\n"
            "Taylors Lakes has tracked a steady upward path over five years; the price-history bar chart on the cover shows the trajectory and the comparable sales table benchmarks the address.\n\n"
            "### Pricing & Rental\n"
            "- Suburb median sits around $960K with consistent year-on-year gains, no major spikes or corrections\n"
            "- Gross house yield in the high 2% range; unit yield meaningfully stronger if a smaller-dwelling investment is preferred\n"
            "- Vacancy is tight thanks to station proximity and owner-occupier dominance\n\n"
            "### 5-Year Outlook\n"
            "- Continued moderate growth supported by western-corridor infrastructure spend\n"
            "- Watergardens activity centre densification a long-term tailwind, not a short-term catalyst\n\n"
            "## SUBURB PROFILE\n"
            "Taylors Lakes is a mature, low-density suburb in Melbourne's north-west — 23 km from the CBD, 15,000 residents, predominantly couples-with-children households on professional incomes.\n\n"
            "### Who's Moving Here\n"
            "Professional families and older couples are the dominant inbound cohort, drawn by relative value versus inner-ring alternatives, the school catchment, and the Sunbury Line commute. Indian-Australian households are the fastest-growing cultural segment in the suburb.\n\n"
            "### What This Suburb Is Becoming\n"
            "Taylors Lakes is consolidating rather than transforming — it is absorbing population pressure from Melbourne's western growth corridor as households trade lot size for accessibility. The Robertsons Road infill and Melton Highway upgrades signal measured densification.\n\n"
            "### Household & Amenities\n"
            "- Predominant household type: couples with children; primary occupation class: professional\n"
            "- Median household income comfortably above outer-suburban average\n"
            "- Watergardens Town Centre anchors retail, with the Taylors Lakes shared-use path improving active transport\n\n"
            "## SCHOOLS CATCHMENT\n"
            "Local primary and secondary catchment schools both sit at or above the state average ICSEA band — the cover ICSEA chart shows the individual scores. The professional-family demographic correlates with stable school performance.\n\n"
            "## INFRASTRUCTURE & DEVELOPMENT\n"
            "- Sunshine Avenue intersection upgrades (part of a $117M state program) currently in progress\n"
            "- Brimbank Planning Scheme Amendment GC51 enables the Robertsons Road / McCubbin Drive infill (~175 lots)\n"
            "- Taylors Lakes shared-user path Stage 1 complete, Stage 2 underway\n\n"
            "## TRANSPORT CONNECTIVITY\n\n"
            "### Public Transport\n"
            "- Watergardens Station 120 m from the property; 34-minute CBD train commute on the Sunbury Line\n"
            "- Five bus routes through the Watergardens interchange connect Keilor, St Albans, Caroline Springs\n\n"
            "### Car Travel\n"
            "- CBD drive 20 min off-peak / ~48 min peak via Calder Freeway and the Western Ring Road\n"
            "- M80 Ring Road accessible within 8 minutes by car\n\n"
            "## RISK ASSESSMENT\n\n"
            "### Crime & Safety\n"
            "Taylors Lakes sits at the 32nd safety percentile in Victoria; violent crime tracks 17% below the state average. The cover crime gauge renders the percentile and category deltas.\n\n"
            "### Environmental & Planning\n"
            "- Melbourne Airport Environs Overlay applies to this precinct — confirm specific noise-contour schedule via VicPlan\n"
            "- Verification recommended for: flood overlay status (vicfloodmap.com.au / Brimbank Council property report)\n"
            "- Verification recommended for: heritage overlay applicability (planning.vic.gov.au)\n\n"
            "### Market Risks\n"
            "- Outer-suburban markets are more sensitive to interest-rate moves than inner-ring comparables\n"
            "- New Robertsons Road infill supply could moderate land-value gains short term\n\n"
            "### Verification Checklist\n"
            "- Flood overlay status (vicfloodmap.com.au or Brimbank Section 32)\n"
            "- Airport Environs noise contour for this address (VicPlan)\n"
            "- School catchment confirmation (findmyschool.vic.gov.au)\n"
            "- Lot dimensions and subdivision rules (Brimbank Council direct)\n\n"
            "## VERDICT\n\n"
            "### Score Breakdown\n"
            "- Growth Potential: **7.0** — steady western corridor demand\n"
            "- Rental Demand: **6.0** — tight vacancy offsets modest yield\n"
            "- Infrastructure: **8.0** — exceptional rail access and active road investment\n"
            "- Safety: **4.0** — property crime moderately above state median\n"
            "- Family Suitability: **8.0** — schools, owner-occupancy, family character\n\n"
            "**Overall Score: 6.5 / 10**\n\n"
            "### Overall Assessment\n"
            "Good long-term prospect for owner-occupiers; selective buy for long-hold investors.\n\n"
            "### Strengths\n"
            "1. Watergardens Station at 120 m — top-tier transit position for outer western Melbourne\n"
            "2. High owner-occupancy and dominant family demographic underpin stable values\n\n"
            "### Weaknesses\n"
            "1. Safety percentile in the lower third — property crime is the main category\n"
            "2. Modest rental yield limits cash-flow appeal for pure-investor strategies\n\n"
            "### Buyer Suitability\n"
            "**Owner-Occupiers:** Strong fit for professional families prioritising train commute and school catchment.\n"
            "**Investors:** Long-hold capital growth thesis only; not a yield play.\n"
            "**Developers:** Battle-axe or front/rear two-dwelling worth modelling against actual lot dimensions.\n\n"
            "### Price Guidance\n"
            "Align expectations with the $1.4M–$1.5M median for comparable houses; the subject lot's land size and 120 m station distance support a premium within that band.\n"
        ),
    )


def load_report_from_json(path: str) -> StubReport:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rd = data.get("research_data", {})
    return StubReport(
        address=data["address"],
        suburb=rd.get("suburb", {}),
        schools=rd.get("schools", {}),
        government_projects=rd.get("government_projects", {}),
        transport=rd.get("transport", {}),
        property_market=rd.get("property_market", {}),
        risk_overlays=rd.get("risk_overlays", {}),
        property_intel=rd.get("property_intel", {}),
        summary=data.get("summary", ""),
        metrics=data.get("metrics", {}),
        scores=data.get("scores", {}),
    )


# ─── Body sections: parse synthesised markdown into structured sections ──────

_SECTION_ICON_KEYS = {
    "executive summary": "clipboard",
    "property snapshot": "home",
    "market analysis":   "trending-up",
    "suburb profile":    "building",
    "schools":           "cap",
    "infrastructure":    "construction",
    "transport":         "train",
    "risk":              "shield",
    "verdict":           "bulb",
}


def _section_icon(title: str) -> str:
    lower = title.lower()
    for key, name in _SECTION_ICON_KEYS.items():
        if key in lower:
            return icon(name)
    return icon("map-pin")


def _section_anchor(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "section"


def _md_to_html(md_text: str) -> str:
    if not md_text.strip():
        return ""
    if _MD_OK:
        return markdown.markdown(md_text, extensions=["extra", "sane_lists"])
    # Hand-rolled fallback so the spike still renders something without the dep.
    lines, html, in_ul = md_text.split("\n"), [], False
    for raw in lines:
        line = raw.strip()
        if line.startswith("### "):
            if in_ul: html.append("</ul>"); in_ul = False
            html.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("- "):
            if not in_ul: html.append("<ul>"); in_ul = True
            inner = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line[2:])
            html.append(f"<li>{inner}</li>")
        elif line:
            if in_ul: html.append("</ul>"); in_ul = False
            inner = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            html.append(f"<p>{inner}</p>")
        else:
            if in_ul: html.append("</ul>"); in_ul = False
    if in_ul: html.append("</ul>")
    return "\n".join(html)


def parse_body_sections(report) -> list[dict]:
    """Slice the synthesised markdown into per-H2 sections for the template.

    Skips the H1 title and the H2 address line, plus the Report Date / Property
    Type metadata since those already appear on the cover dashboard."""
    summary = report.summary or ""
    if not summary.strip():
        return []

    parts = re.split(r"^##\s+(.+?)\s*$", summary, flags=re.MULTILINE)
    addr_norm = re.sub(r"\s+", " ", (report.address or "").strip()).lower()

    sections: list[dict] = []
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body  = parts[i + 1] if (i + 1) < len(parts) else ""

        # Skip the address H2 — the cover title card already shows it
        title_norm = re.sub(r"\s+", " ", title).lower()
        if title_norm == addr_norm or (addr_norm and addr_norm in title_norm):
            continue

        # Strip leading "Report Date: ..." / "Property Type: ..." lines from
        # the body since those metadata lines belong to the cover, not here
        body = re.sub(
            r"^\s*\*\*(Report Date|Property Type)\*\*:.*?$\n?",
            "", body, flags=re.MULTILINE | re.IGNORECASE,
        )

        sections.append({
            "title":  title.title() if title.isupper() else title,
            "icon":   _section_icon(title),
            "anchor": _section_anchor(title),
            "body_html": _md_to_html(body),
        })
    return sections


def render_property_snapshot_html(report) -> str:
    """Render the subject-property data table for the Property Snapshot section."""
    intel = report.property_intel if isinstance(report.property_intel, dict) else {}
    if not intel:
        return ""

    def _num(key, suffix=""):
        v = intel.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        n = float(v)
        return f"{int(n)}{suffix}" if n == int(n) else f"{n:.1f}{suffix}"

    def _str(key):
        v = intel.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else None

    def _potential(key):
        raw = intel.get(key)
        if isinstance(raw, dict):
            rating = raw.get("rating") or raw.get("level") or raw.get("score")
            reason = raw.get("reason") or raw.get("description")
            if rating:
                return f"<strong>{rating}</strong>" + (f" — {reason}" if reason else "")
        elif isinstance(raw, str) and raw.strip():
            return raw.strip()
        return None

    rows: list[tuple[str, str]] = []
    def _add(label, value):
        if value:
            rows.append((label, value))

    _add("Land Size",     _num("land_sqm", " m²"))
    _add("Dwelling Type", _str("dwelling_type"))
    _add("Frontage",      _num("frontage_m", " m"))
    config = " · ".join(filter(None, [
        f"{_num('bedrooms')}br"    if _num("bedrooms")  else None,
        f"{_num('bathrooms')}ba"   if _num("bathrooms") else None,
        f"{_num('parking')}c"      if _num("parking")   else None,
    ]))
    _add("Configuration", config or None)
    _add("Year Built",    _num("year_built"))
    _add("Corner Block",  "Yes" if intel.get("corner_block") is True else None)
    _add("Zoning",        " — ".join(filter(None, [_str("zoning_code"), _str("zoning_description")])) or None)
    _add("Subdivision Potential",       _potential("subdivision_potential"))
    _add("Development Feasibility",     _potential("development_feasibility"))
    _add("Renovation Potential",        _potential("renovation_potential"))
    _add("Knockdown / Rebuild",         _potential("knockdown_rebuild_viability"))
    _add("Street Position",             _num("street_position_quality", " / 10"))

    if not rows:
        return ""

    body = "".join(f"<tr><th>{label}</th><td>{value}</td></tr>" for label, value in rows)
    return f'<table class="snapshot-table">{body}</table>'


# ─── View-model: shape report → simple dict the template can read cleanly ────

def build_view(report) -> dict:
    s = report.suburb if isinstance(report.suburb, dict) else {}
    sc = report.scores if isinstance(report.scores, dict) else {}
    mk = report.property_market if isinstance(report.property_market, dict) else {}
    tr = report.transport if isinstance(report.transport, dict) else {}
    sch = report.schools if isinstance(report.schools, dict) else {}
    gov = report.government_projects if isinstance(report.government_projects, dict) else {}

    # Metric tiles
    metrics = report.metrics or {}
    median = metrics.get("median_price") or _fmt_price(_pick(s, "median_house_price", "median_price"))
    rental = metrics.get("rental_yield") or _pick(s, "rental_yield", default="—")
    schools_quality = metrics.get("school_quality") or _pick(sch, "school_quality_summary", default="—")
    train_label = metrics.get("cbd_train_mins") or "—"
    train_station = _pick(tr.get("nearest_train", {}) if isinstance(tr.get("nearest_train"), dict) else {},
                          "name", default="Nearest Station")
    pct = s.get("crime_safety_percentile")
    pct_val = int(pct) if isinstance(pct, (int, float)) and not isinstance(pct, bool) else None
    market_outlook = metrics.get("market_outlook") or _pick(mk, "market_outlook", default="—")

    # 5-year price growth points
    raw_hist = s.get("price_history_5yr") or []
    history: list[tuple[int, float]] = []
    for entry in raw_hist:
        if not isinstance(entry, dict):
            continue
        try:
            yr = int(str(entry.get("year") or "")[:4])
            pr = float(str(entry.get("median_house_price") or entry.get("median_price") or 0))
        except (ValueError, TypeError):
            continue
        if yr and pr:
            history.append((yr, pr))
    history.sort()

    # Comparable sales (max 4 for the table card)
    comps = []
    for s_row in (mk.get("comparable_sales") or [])[:4]:
        if not isinstance(s_row, dict):
            continue
        comps.append({
            "address":  (s_row.get("address") or "").strip(),
            "date":     str(s_row.get("sale_date") or "—"),
            "price":    _fmt_price(s_row.get("sale_price") or s_row.get("price")),
            "distance": s_row.get("distance_m") or "—",
        })

    # Last sale (subject property)
    last_sale_obj = mk.get("subject_property_last_sale") or {}
    if isinstance(last_sale_obj, dict):
        last_sale = {
            "price": _fmt_price(last_sale_obj.get("price")),
            "date":  str(last_sale_obj.get("date") or "—"),
        }
    else:
        last_sale = {"price": "—", "date": "—"}

    # Scorecard rows
    factor_labels = [
        ("growth_potential",   "Growth Potential",  "📈"),
        ("rental_demand",      "Rental Demand",     "💰"),
        ("infrastructure",     "Infrastructure",    "🏗️"),
        ("safety",             "Safety",            "🛡️"),
        ("family_suitability", "Family Suitability","👨‍👩‍👧"),
    ]
    scorecard = []
    for key, label, _ in factor_labels:
        v = sc.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            scorecard.append({"label": label, "score": float(v), "stars": _star_row(float(v))})
    overall = sc.get("overall")
    overall_score = float(overall) if isinstance(overall, (int, float)) and not isinstance(overall, bool) else None
    overall_label = _overall_label(overall_score)

    # Transit
    def _mins(v, suffix=""):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return f"{int(v)} min{suffix}"
        return None

    transit = []
    nt = tr.get("nearest_train") or {}
    if isinstance(nt, dict) and nt.get("name"):
        right = _mins(nt.get("cbd_mins"), "  ·  By Train") or "By Train"
        transit.append({"icon": icon("train"), "color": "blue",
                        "label": nt["name"], "right": right})

    cbd_off = _mins(tr.get("drive_to_cbd_offpeak_mins"), "  ·  Off-peak")
    cbd_pk  = _mins(tr.get("drive_to_cbd_peak_mins"),    "  ·  Peak")
    if cbd_off or cbd_pk:
        transit.append({"icon": icon("car"), "color": "amber",
                        "label": "Melbourne CBD", "right": cbd_off or cbd_pk})

    routes = tr.get("bus_routes") or []
    route_labels: list[str] = []
    for r in routes if isinstance(routes, list) else []:
        if isinstance(r, dict):
            n = r.get("route") or r.get("number") or r.get("name")
            if n:
                route_labels.append(str(n))
        elif isinstance(r, (str, int)):
            route_labels.append(str(r))
    if route_labels:
        shown = ", ".join(route_labels[:5])
        transit.append({"icon": icon("bus"), "color": "violet",
                        "label": "Bus Routes",
                        "right": shown[:48] + ("…" if len(shown) > 48 else "")})

    fwy_km = (s.get("nearest_freeway") or {}).get("distance_km") if isinstance(s.get("nearest_freeway"), dict) else None
    if isinstance(fwy_km, (int, float)) and not isinstance(fwy_km, bool):
        fwy_name = (s.get("nearest_freeway") or {}).get("name") or "Nearest Freeway"
        transit.append({"icon": icon("highway"), "color": "rose",
                        "label": fwy_name,
                        "right": f"{fwy_km:.1f} km  ·  By Car"})

    # Schools
    schools_rows = []
    for entry in (sch.get("primary_schools") or [])[:1]:
        if isinstance(entry, dict) and entry.get("name"):
            schools_rows.append({
                "name": entry["name"], "tier": "Primary",
                "distance": f"{entry.get('distance_km','—')} km",
                "icsea": entry.get("icsea") or "—",
            })
    for entry in (sch.get("secondary_schools") or [])[:1]:
        if isinstance(entry, dict) and entry.get("name"):
            schools_rows.append({
                "name": entry["name"], "tier": "Secondary",
                "distance": f"{entry.get('distance_km','—')} km",
                "icsea": entry.get("icsea") or "—",
            })

    # Infrastructure pipeline
    pipeline = []
    raw_proj = gov.get("transport_projects") or []
    for proj in raw_proj[:3]:
        if isinstance(proj, dict) and proj.get("name"):
            pipeline.append({"name": proj["name"], "status": proj.get("status") or proj.get("timeline") or "—"})

    # Map legend (real research data, used alongside the Google Static Map)
    def _dist(d):
        if not isinstance(d, (int, float)) or isinstance(d, bool):
            return ""
        return f"{d*1000:.0f} m" if d < 1 else f"{d:.1f} km"

    map_legend = []
    nt = tr.get("nearest_train") or {}
    if isinstance(nt, dict) and nt.get("name"):
        map_legend.append({"icon": icon("train"), "color": "blue",
                           "label": nt["name"], "detail": _dist(nt.get("distance_km"))})
    fwy = s.get("nearest_freeway") or {}
    if isinstance(fwy, dict) and fwy.get("name"):
        map_legend.append({"icon": icon("highway"), "color": "amber",
                           "label": fwy["name"], "detail": _dist(fwy.get("distance_km"))})
    pri = (sch.get("primary_schools") or [{}])[0] or {}
    if isinstance(pri, dict) and pri.get("name"):
        map_legend.append({"icon": icon("cap"), "color": "violet",
                           "label": pri["name"], "detail": _dist(pri.get("distance_km"))})
    sec = (sch.get("secondary_schools") or [{}])[0] or {}
    if isinstance(sec, dict) and sec.get("name"):
        map_legend.append({"icon": icon("cap"), "color": "violet",
                           "label": sec["name"], "detail": _dist(sec.get("distance_km"))})
    hosp = (s.get("nearby_hospitals") or [{}])[0] or {}
    if isinstance(hosp, dict) and hosp.get("name"):
        map_legend.append({"icon": icon("hospital"), "color": "rose",
                           "label": hosp["name"], "detail": _dist(hosp.get("distance_km"))})
    gp = (s.get("nearby_gps") or [{}])[0] or {}
    if isinstance(gp, dict) and gp.get("name"):
        map_legend.append({"icon": icon("stethoscope"), "color": "rose",
                           "label": gp["name"], "detail": _dist(gp.get("distance_km"))})
    map_legend = map_legend[:5]

    # Real Google images (returns None if key missing or image is a placeholder).
    # Static-map fetch was dropped from the cover — keeping the helper for
    # potential future use but no longer paying the network round-trip.
    photo_uri = fetch_street_view_uri(report.address)
    map_uri   = None

    return {
        "address":    report.address,
        "suburb_name": _pick(s, "suburb", default=""),
        "report_date": datetime.now().strftime("%d %b %Y"),
        "metrics": {
            "median": median,
            "rental_yield": rental,
            "schools": schools_quality,
            "train_label": train_label,
            "train_station": train_station,
            "crime_percentile": pct_val if pct_val is not None else "—",
            "market_outlook": market_outlook,
        },
        "history":     _history_chart(history),
        "rental":      _rental_chart(s, mk),
        "comparables": comps,
        "last_sale":   last_sale,
        "scorecard":   scorecard,
        "overall_score": overall_score,
        "overall_label": overall_label,
        "overall_stars": _star_row(overall_score) if overall_score else "",
        "transit":     transit,
        "schools_rows": schools_rows,
        "crime":       _crime_summary(s),
        "metric_icons": {
            "median":  icon("home"),
            "yield":   icon("trending-up"),
            "schools": icon("cap"),
            "train":   icon("train"),
            "crime":   icon("shield"),
            "market":  icon("chart-bar"),
        },
        "pipeline":   pipeline,
        "map_legend": map_legend,
        "photo_uri":  photo_uri,
        "map_uri":    map_uri,
        "body_sections":      parse_body_sections(report),
        "property_snapshot_html": render_property_snapshot_html(report),
        "school_chart_svg":   _school_chart_svg(sch),
        "score_chart_svg":    _score_chart_svg(sc),
        "disclaimer": (
            "This report was generated by artificial intelligence using publicly available "
            "Australian data sources. It is a research aid only and does not constitute "
            "financial, legal, or investment advice."
        ),
    }


def _crime_summary(suburb: dict) -> dict:
    """Return a plain-English crime comparison vs the state average.
    Drops the percentile gauge — users were confused by percentile numbers.
    Instead returns two phrases like '17% lower' / '8% higher' with colour
    hints, plus an overall headline ('Lower than VIC average' / 'Around VIC
    average' / 'Higher than VIC average')."""
    violent = suburb.get("crime_violent_vs_state_avg_pct")
    prop    = suburb.get("crime_property_vs_state_avg_pct")

    def _row(label: str, val):
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            return {"label": label, "available": False}
        v = int(val)
        if v <= -5:
            color, phrase = "delta-green", f"{abs(v)}% lower"
        elif v >= 25:
            color, phrase = "delta-red",   f"{v}% higher"
        elif v >= 5:
            color, phrase = "delta-amber", f"{v}% higher"
        else:
            color, phrase = "delta-grey",  "around average"
        return {"label": label, "available": True, "delta": v,
                "color": color, "phrase": phrase}

    rows = [_row("Violent crime", violent), _row("Property crime", prop)]

    # Headline summarising the two rows
    avail = [r for r in rows if r["available"]]
    if not avail:
        headline = ""
    else:
        avg_delta = sum(r["delta"] for r in avail) / len(avail)
        if avg_delta <= -5:
            headline = "Lower than VIC average"
        elif avg_delta >= 5:
            headline = "Higher than VIC average"
        else:
            headline = "Around VIC average"

    return {"headline": headline, "rows": rows}


def _overall_label(score: float | None) -> str:
    if score is None:
        return ""
    if score >= 8.0: return "Strong Buy"
    if score >= 6.5: return "Good Long-Term Prospect"
    if score >= 5.0: return "Hold / Selective Buy"
    return "Approach with Caution"


# ─── Charts (hand-rolled SVG so we don't take a matplotlib dep) ───────────────

def _history_chart(history: list[tuple[int, float]]) -> str:
    """SVG line chart of 5-year median price history."""
    if len(history) < 2:
        return '<div class="chart-empty">Price history unavailable</div>'

    years = [h[0] for h in history]
    prices = [h[1] for h in history]
    p_min, p_max = min(prices), max(prices)
    span = (p_max - p_min) or 1
    W, H = 380, 180
    PAD_L, PAD_R, PAD_T, PAD_B = 40, 12, 20, 28

    def x(i):
        return PAD_L + i * (W - PAD_L - PAD_R) / max(len(history) - 1, 1)
    def y(p):
        return PAD_T + (H - PAD_T - PAD_B) * (1 - (p - p_min) / span)

    pts = " ".join(f"{x(i):.1f},{y(p):.1f}" for i, p in enumerate(prices))
    dots = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y(p):.1f}" r="3.2" fill="#2563eb"/>'
        for i, p in enumerate(prices)
    )
    # Last-point callout
    last_x, last_y = x(len(prices) - 1), y(prices[-1])
    last_lbl = _fmt_price(prices[-1])
    last_callout = (
        f'<g><rect x="{last_x - 28}" y="{last_y - 24}" rx="4" ry="4" '
        f'width="56" height="18" fill="#2563eb"/>'
        f'<text x="{last_x}" y="{last_y - 11}" font-size="11" font-weight="600" '
        f'fill="white" text-anchor="middle">{last_lbl}</text></g>'
    )
    # Y-axis labels
    y_labels = []
    for i in range(5):
        v = p_min + span * (4 - i) / 4
        yy = PAD_T + (H - PAD_T - PAD_B) * i / 4
        y_labels.append(
            f'<text x="{PAD_L - 6}" y="{yy + 4}" font-size="9" fill="#94a3b8" text-anchor="end">{_fmt_price(v)}</text>'
        )
    # X-axis labels
    x_labels = []
    for i, yr in enumerate(years):
        x_labels.append(
            f'<text x="{x(i):.1f}" y="{H - 8}" font-size="9" fill="#94a3b8" text-anchor="middle">{yr}</text>'
        )

    return f'''<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" class="chart-svg">
  <polyline fill="none" stroke="#2563eb" stroke-width="2" points="{pts}"/>
  {dots}
  {last_callout}
  {"".join(y_labels)}
  {"".join(x_labels)}
</svg>'''


def _rental_chart(suburb: dict, market: dict) -> str:
    """SVG bar chart approximating rental yield trend.
    Uses synthetic year-over-year data anchored on the current yield."""
    try:
        cur = float(str(suburb.get("rental_yield", "4.5")).replace("%", "").strip())
    except (ValueError, TypeError):
        cur = 4.5
    median_rent = market.get("median_rent_house") or 550
    if isinstance(median_rent, str):
        try:
            median_rent = float(median_rent.replace("$", "").replace(",", ""))
        except (ValueError, TypeError):
            median_rent = 550
    years = list(range(2020, 2026))
    yields = [max(3.0, cur - 0.5 + 0.1 * i) for i in range(len(years))]
    rents  = [int(median_rent * (0.85 + 0.03 * i)) for i in range(len(years))]

    W, H = 380, 180
    PAD_L, PAD_R, PAD_T, PAD_B = 38, 32, 20, 28
    bw = (W - PAD_L - PAD_R) / len(years) * 0.55

    def bx(i):
        return PAD_L + i * (W - PAD_L - PAD_R) / len(years) + bw * 0.4
    rent_max = max(rents) * 1.1
    yield_max = 6.0

    bars = ""
    for i, r in enumerate(rents):
        h = (H - PAD_T - PAD_B) * (r / rent_max)
        bars += f'<rect x="{bx(i):.1f}" y="{H - PAD_B - h:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="#86efac" rx="2"/>'

    # Yield line on secondary axis
    def lx(i):
        return PAD_L + i * (W - PAD_L - PAD_R) / len(years) + (W - PAD_L - PAD_R) / len(years) / 2
    def ly(yv):
        return PAD_T + (H - PAD_T - PAD_B) * (1 - yv / yield_max)
    pts = " ".join(f"{lx(i):.1f},{ly(y):.1f}" for i, y in enumerate(yields))
    dots = "".join(f'<circle cx="{lx(i):.1f}" cy="{ly(y):.1f}" r="2.8" fill="#059669"/>'
                   for i, y in enumerate(yields))

    x_labels = "".join(
        f'<text x="{lx(i):.1f}" y="{H - 8}" font-size="9" fill="#94a3b8" text-anchor="middle">{yr}</text>'
        for i, yr in enumerate(years)
    )
    return f'''<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" class="chart-svg">
  {bars}
  <polyline fill="none" stroke="#059669" stroke-width="2" points="{pts}"/>
  {dots}
  {x_labels}
  <text x="{PAD_L - 4}" y="{PAD_T + 4}" font-size="9" fill="#94a3b8" text-anchor="end">${int(rent_max)}</text>
  <text x="{W - PAD_R + 4}" y="{PAD_T + 4}" font-size="9" fill="#059669" text-anchor="start">{int(yield_max)}%</text>
</svg>'''


def _school_chart_svg(schools: dict) -> str:
    """Horizontal bar chart of ICSEA scores for up to 6 nearby schools."""
    entries = []
    for tier, key in (("Pri", "primary_schools"), ("Sec", "secondary_schools")):
        for s in (schools.get(key) or [])[:4]:
            if not isinstance(s, dict):
                continue
            icsea = s.get("icsea")
            if isinstance(icsea, bool) or not isinstance(icsea, (int, float)):
                continue
            if not (800 <= float(icsea) <= 1300):
                continue
            name = (s.get("name") or "").strip()
            if not name:
                continue
            entries.append((f"{name[:30]} ({tier})", int(icsea)))
    if not entries:
        return ""

    entries = entries[:6]
    W = 600
    bar_h = 26
    label_w = 240
    bar_max = W - label_w - 70
    H = 36 + len(entries) * bar_h
    val_min, val_span = 850, 350  # 850 → 1200

    parts = []
    ref_x = label_w + bar_max * (1000 - val_min) / val_span
    parts.append(
        f'<line x1="{ref_x}" y1="20" x2="{ref_x}" y2="{H - 6}" '
        f'stroke="#94a3b8" stroke-dasharray="3 3" stroke-width="0.8"/>'
        f'<text x="{ref_x + 4}" y="14" font-size="9" fill="#94a3b8">Nat. avg (1000)</text>'
    )
    for i, (name, score) in enumerate(entries):
        y = 28 + i * bar_h
        bw = bar_max * (score - val_min) / val_span
        color = "#10b981" if score >= 1000 else "#f59e0b"
        parts.append(f'<text x="0" y="{y + 14}" font-size="11" fill="#334155">{name}</text>')
        parts.append(f'<rect x="{label_w}" y="{y + 3}" width="{bw:.1f}" height="14" fill="{color}" rx="2"/>')
        parts.append(f'<text x="{label_w + bw + 6}" y="{y + 14}" font-size="11" font-weight="600" fill="#0f172a">{score}</text>')

    return (
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
        f'class="chart-svg chart-body">{"".join(parts)}</svg>'
    )


def _score_chart_svg(scores: dict) -> str:
    """Horizontal bar breakdown with hero overall value for the Verdict body."""
    factors = [
        ("Growth Potential",   "growth_potential"),
        ("Rental Demand",      "rental_demand"),
        ("Infrastructure",     "infrastructure"),
        ("Safety",             "safety"),
        ("Family Suitability", "family_suitability"),
    ]
    bars = [(label, scores.get(key)) for label, key in factors
            if isinstance(scores.get(key), (int, float)) and not isinstance(scores.get(key), bool)]
    if not bars:
        return ""
    overall = scores.get("overall")

    W = 600
    bar_h = 28
    label_w = 200
    bar_max = W - label_w - 70
    H = 40 + len(bars) * bar_h
    parts = []
    if isinstance(overall, (int, float)) and not isinstance(overall, bool):
        parts.append(
            f'<text x="{W}" y="22" text-anchor="end" font-size="22" font-weight="700" '
            f'fill="#0f172a">{float(overall):.1f}<tspan font-size="12" fill="#94a3b8"> / 10</tspan></text>'
            f'<text x="{W}" y="36" text-anchor="end" font-size="9" fill="#94a3b8">Overall Score</text>'
        )
    for i, (label, val) in enumerate(bars):
        y = 50 + i * bar_h
        v = float(val)
        bw = bar_max * (v / 10.0)
        color = "#10b981" if v >= 7 else "#f59e0b" if v >= 5 else "#ef4444"
        parts.append(f'<text x="0" y="{y + 10}" font-size="11" fill="#334155">{label}</text>')
        parts.append(f'<rect x="{label_w}" y="{y}" width="{bar_max}" height="12" fill="#f1f5f9" rx="2"/>')
        parts.append(f'<rect x="{label_w}" y="{y}" width="{bw:.1f}" height="12" fill="{color}" rx="2"/>')
        parts.append(f'<text x="{W}" y="{y + 10}" text-anchor="end" font-size="11" font-weight="600" fill="#0f172a">{v:.1f}</text>')

    return (
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
        f'class="chart-svg chart-body">{"".join(parts)}</svg>'
    )


# ─── HTML template ────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ view.address }} — PropertyReport</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
@page cover { size: A4 landscape; margin: 8mm; }
@page body  {
  size: A4 portrait;
  margin: 14mm 14mm 16mm 14mm;
  @bottom-left  { content: "PropertyReport"; font-size: 7.5pt; color: #94a3b8; font-family: 'Inter', sans-serif; letter-spacing: 0.6px; }
  @bottom-right { content: "Page " counter(page); font-size: 7.5pt; color: #94a3b8; font-family: 'Inter', sans-serif; }
}
.cover-page { page: cover; }
.body-pages { page: body; page-break-before: always; }

:root {
  --navy: #0f172a;
  --slate: #1e293b;
  --slate-2: #475569;
  --slate-3: #94a3b8;
  --slate-4: #cbd5e1;
  --grey-50: #f8fafc;
  --grey-100: #f1f5f9;
  --grey-200: #e2e8f0;
  --emerald: #10b981;
  --emerald-soft: #d1fae5;
  --amber: #f59e0b;
  --amber-soft: #fef3c7;
  --rose: #e11d48;
  --rose-soft: #ffe4e6;
  --blue: #2563eb;
  --blue-soft: #dbeafe;
  --violet: #7c3aed;
  --violet-soft: #ede9fe;
  --teal: #0891b2;
  --teal-soft: #cffafe;
  --orange: #ea580c;
  --orange-soft: #ffedd5;
}

* { box-sizing: border-box; }
body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  color: var(--slate);
  background: white;
  margin: 0;
  font-size: 10px;
  line-height: 1.35;
  font-feature-settings: "cv02", "cv03", "cv04", "cv11";
}

.dashboard {
  display: grid;
  grid-template-rows: 90px 95px 180px 140px 84px;
  gap: 8px;
}

.row {
  display: grid;
  gap: 8px;
}

.card {
  background: white;
  border: 1px solid var(--grey-200);
  border-radius: 8px;
  padding: 10px 12px;
}

.label {
  font-size: 8.5px;
  font-weight: 700;
  letter-spacing: 0.6px;
  color: var(--slate-3);
  text-transform: uppercase;
  margin: 0 0 6px;
}

/* ── HEADER ROW ── */
.header-row { grid-template-columns: 2.2fr 1fr 2.4fr; }
.title-card {
  background: linear-gradient(135deg, var(--navy) 0%, var(--slate) 100%);
  color: white;
  border: none;
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.title-card .addr {
  font-size: 19px;
  font-weight: 700;
  color: white;
  line-height: 1.15;
  margin-bottom: 4px;
}
.title-card .sub {
  font-size: 10px;
  color: rgba(255,255,255,0.65);
}
.title-card .date {
  font-size: 9px;
  color: rgba(255,255,255,0.55);
  margin-top: 6px;
}
.photo-card {
  border: 1px solid var(--grey-200);
  border-radius: 8px;
  background: linear-gradient(120deg, #cbd5e1 0%, #94a3b8 100%);
  position: relative;
  overflow: hidden;
  padding: 0;
}
.photo-card img {
  width: 100%; height: 100%;
  object-fit: cover;
  display: block;
}
.photo-card .photo-tag {
  position: absolute;
  bottom: 6px;
  left: 8px;
  background: rgba(15,23,42,0.72);
  color: white;
  font-size: 8px;
  padding: 2px 6px;
  border-radius: 3px;
  letter-spacing: 0.3px;
}

.amenities-card { display: flex; flex-direction: column; }
.amenities-grid {
  margin-top: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow: hidden;
}
.amenity-row {
  display: flex; align-items: center; gap: 8px;
  font-size: 9px;
  padding: 3px 0;
  border-bottom: 1px solid var(--grey-100);
}
.amenity-row:last-child { border-bottom: none; }
.amenity-icon {
  width: 20px; height: 20px; border-radius: 4px;
  display: inline-flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.amenity-icon svg { width: 12px; height: 12px; display: block; }
.amenity-text {
  display: flex; align-items: baseline; justify-content: space-between;
  flex: 1; min-width: 0; gap: 8px;
}
.amenity-text strong {
  font-weight: 600; color: var(--navy); font-size: 9.5px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  flex: 1; min-width: 0;
}
.amenity-text .amenity-distance {
  color: var(--slate-3); font-size: 8.5px; flex-shrink: 0; white-space: nowrap;
}

/* ── METRIC ROW ── */
.metric-row { grid-template-columns: repeat(6, 1fr); }
.metric-card {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: flex-start;
  padding: 8px 12px;
  overflow: hidden;
  min-width: 0;
}
.metric-icon {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 4px;
}
.metric-icon svg { width: 16px; height: 16px; display: block; }
.icon-sm svg     { width: 11px; height: 11px; display: block; }
.legend-icon svg { width: 10px; height: 10px; display: block; }
.section-icon svg { width: 16px; height: 16px; display: block; }
.section-icon { color: rgba(255,255,255,0.95); }
.icon-emerald { background: var(--emerald-soft); color: var(--emerald); }
.icon-blue    { background: var(--blue-soft);    color: var(--blue); }
.icon-violet  { background: var(--violet-soft);  color: var(--violet); }
.icon-teal    { background: var(--teal-soft);    color: var(--teal); }
.icon-amber   { background: var(--amber-soft);   color: var(--amber); }
.icon-rose    { background: var(--rose-soft);    color: var(--rose); }
.icon-orange  { background: var(--orange-soft);  color: var(--orange); }

.metric-label {
  font-size: 8px;
  font-weight: 700;
  color: var(--slate-3);
  letter-spacing: 0.6px;
  text-transform: uppercase;
}
.metric-value {
  font-size: 16px;
  font-weight: 700;
  color: var(--navy);
  line-height: 1.15;
  margin-top: 1px;
  /* allow two-line wrap, hide third */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
  word-break: break-word;
  max-width: 100%;
}
.metric-sub {
  font-size: 8.5px;
  color: var(--slate-3);
  margin-top: 1px;
}

/* ── CHART ROW ── */
.chart-row { grid-template-columns: 1.2fr 1.2fr 1.2fr 1.1fr; }
.chart-svg { width: 100%; height: 120px; }
.chart-stats { display: flex; gap: 12px; margin-top: 4px; font-size: 9px; }
.chart-stats .key {
  color: var(--emerald);
  font-weight: 700;
  font-size: 11px;
}
.chart-stats .lbl { color: var(--slate-3); }

.comp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 8.5px;
  margin-top: 2px;
}
.comp-table th {
  text-align: left;
  font-weight: 600;
  color: var(--slate-3);
  text-transform: uppercase;
  font-size: 7.5px;
  padding: 4px 4px;
  border-bottom: 1px solid var(--grey-200);
}
.comp-table td {
  padding: 4px 4px;
  border-bottom: 1px solid var(--grey-100);
  color: var(--slate);
}
.last-sale-box {
  margin-top: 6px;
  background: var(--emerald-soft);
  border-radius: 6px;
  padding: 6px 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 9px;
}
.last-sale-box .star { color: var(--emerald); margin-right: 6px; }
.last-sale-box .price { font-weight: 700; color: var(--navy); }

.scorecard-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 3px 0;
  font-size: 9px;
  border-bottom: 1px solid var(--grey-100);
}
.scorecard-row:last-child { border-bottom: none; }
.scorecard-row .stars { letter-spacing: 1px; }
.star.full  { color: var(--amber); }
.star.half  { color: var(--amber); opacity: 0.6; }
.star.empty { color: var(--grey-200); }
.scorecard-row .score-num {
  font-weight: 700;
  color: var(--navy);
  margin-left: 4px;
  font-size: 10px;
}
.overall-band {
  background: var(--navy);
  color: white;
  border-radius: 6px;
  padding: 6px 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 5px;
}
.overall-band .overall-text {
  font-size: 9px;
  letter-spacing: 0.4px;
}
.overall-band .overall-text strong { display: block; font-size: 10.5px; }
.overall-band .overall-score {
  font-size: 16px;
  font-weight: 700;
}

/* ── DETAIL ROW ── */
.detail-row { grid-template-columns: 1.3fr 1.2fr 1.2fr 1.3fr; }
.row-list .item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 3px 0;
  font-size: 9px;
  border-bottom: 1px solid var(--grey-100);
}
.row-list .item:last-child { border-bottom: none; }
.row-list .item .left { display: flex; align-items: center; gap: 6px; }
.row-list .item .icon-sm {
  width: 18px; height: 18px; border-radius: 4px;
  display: inline-flex; align-items: center; justify-content: center; font-size: 10px;
}
.row-list .item .right {
  font-size: 8.5px;
  color: var(--slate-3);
  text-align: right;
}
.row-list .item .right strong { color: var(--navy); font-weight: 600; }

.school-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 5px 0;
  border-bottom: 1px solid var(--grey-100);
  font-size: 9px;
}
.school-row:last-child { border-bottom: none; }
.school-row .icsea {
  background: var(--violet-soft);
  color: var(--violet);
  padding: 2px 6px;
  border-radius: 4px;
  font-weight: 700;
  font-size: 8.5px;
}
.verify-note {
  font-size: 8px;
  color: var(--slate-3);
  margin-top: 6px;
  background: var(--grey-50);
  padding: 5px 7px;
  border-radius: 4px;
}
.verify-note a { color: var(--blue); text-decoration: none; }

.crime-headline {
  margin-top: 4px;
  font-size: 11px;
  font-weight: 700;
  color: var(--navy);
}
.crime-rows { display: flex; flex-direction: column; gap: 4px; margin-top: 6px; }
.crime-row {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 9px;
  padding: 3px 0;
  border-bottom: 1px solid var(--grey-100);
}
.crime-row:last-child { border-bottom: none; }
.crime-row .crime-label { color: var(--slate-2); }
.crime-delta { font-size: 10.5px; font-weight: 700; }
.delta-green { color: var(--emerald); }
.delta-grey  { color: var(--slate-2); font-weight: 500; }
.delta-amber { color: var(--amber); }
.delta-red   { color: var(--rose); }

.body-crime {
  margin: 8px 0 14px 0;
  padding: 10px 14px;
  background: var(--grey-50);
  border: 1px solid var(--grey-200);
  border-radius: 6px;
}
.body-crime-headline {
  font-size: 11pt; font-weight: 700; color: var(--navy);
  margin-bottom: 4px;
}
.body-crime-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 5px 0;
  font-size: 10pt;
  border-bottom: 1px solid var(--grey-200);
}
.body-crime-row:last-child { border-bottom: none; }

.pipeline-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 5px 0;
  font-size: 9px;
  border-bottom: 1px solid var(--grey-100);
}
.pipeline-row:last-child { border-bottom: none; }
.pipeline-row .icon-sm {
  width: 18px; height: 18px; border-radius: 4px;
  background: var(--grey-100); color: var(--slate-2);
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 10px; margin-right: 6px;
}
.pipeline-row .status {
  font-size: 8.5px; color: var(--slate-3);
}
.pipeline-row.completed .icon-sm { background: var(--emerald-soft); color: var(--emerald); }
.pipeline-row.progress .icon-sm  { background: var(--blue-soft);    color: var(--blue); }
.pipeline-row.planned .icon-sm   { background: var(--orange-soft);  color: var(--orange); }

/* ── VERDICT BAND ── */
.verdict-row { grid-template-columns: 1.4fr 1fr 1fr 1fr 1.2fr; gap: 8px; }
.verdict-band {
  background: var(--navy);
  color: white;
  border-radius: 8px;
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.verdict-band .vd-stars { color: var(--amber); font-size: 11px; letter-spacing: 1px; }
.verdict-band .vd-eyebrow {
  font-size: 8px;
  color: rgba(255,255,255,0.55);
  text-transform: uppercase;
  letter-spacing: 0.6px;
  margin-top: 3px;
}
.verdict-band .vd-title {
  font-size: 13px;
  font-weight: 700;
  margin: 2px 0 4px;
}
.verdict-band .vd-body {
  font-size: 8.5px;
  color: rgba(255,255,255,0.7);
  line-height: 1.45;
}

.verdict-cell {
  padding: 8px 10px;
  background: var(--grey-50);
  border-radius: 8px;
  border: 1px solid var(--grey-200);
  font-size: 9px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.verdict-cell .eyebrow {
  font-size: 8px;
  color: var(--slate-3);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  font-weight: 700;
}
.verdict-cell .title { font-weight: 700; color: var(--navy); font-size: 10px; }
.verdict-cell .body  { color: var(--slate-2); font-size: 8.5px; }
.verdict-cell.risk { background: #fffbeb; border-color: #fde68a; }
.verdict-cell.risk .title { color: var(--amber); }
.next-steps { padding: 8px 10px; background: var(--grey-50); border: 1px solid var(--grey-200); border-radius: 8px; font-size: 8.5px; }
.next-steps .eyebrow {
  font-size: 8px;
  color: var(--slate-3);
  text-transform: uppercase;
  letter-spacing: 0.6px;
  font-weight: 700;
  margin-bottom: 4px;
}
.next-steps ul { list-style: none; margin: 0; padding: 0; }
.next-steps li { padding: 2px 0; color: var(--slate-2); }
.next-steps li::before { content: "✓"; color: var(--emerald); font-weight: 700; margin-right: 6px; }

/* ── BODY PAGES (portrait, prose-heavy) ── */
.body-pages { font-size: 10.5px; line-height: 1.55; color: var(--slate); }
.body-section { margin-bottom: 16px; }
.body-section + .body-section { padding-top: 4px; }
.body-section h2.section-title {
  font-family: 'Inter', sans-serif;
  font-size: 14.5pt;
  font-weight: 700;
  color: white;
  background: linear-gradient(135deg, var(--navy) 0%, var(--slate) 100%);
  padding: 8px 14px;
  border-radius: 6px;
  margin: 0 0 10px 0;
  letter-spacing: 0.2px;
  display: flex;
  align-items: center;
  gap: 10px;
}
.body-section h2 .section-icon {
  font-size: 14pt;
}
.body-section h3 {
  font-size: 10.5pt;
  font-weight: 600;
  color: var(--navy);
  margin: 12px 0 4px;
  letter-spacing: 0.1px;
}
.body-section p {
  margin: 0 0 7px 0;
  color: var(--slate-2);
}
.body-section ul, .body-section ol {
  margin: 4px 0 8px 0;
  padding-left: 18px;
}
.body-section li {
  margin: 2px 0;
  color: var(--slate-2);
}
.body-section li::marker { color: var(--emerald); }
.body-section strong { color: var(--navy); font-weight: 600; }

/* Property Snapshot data table */
.snapshot-table {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0 14px 0;
  font-size: 10pt;
  border: 1px solid var(--grey-200);
  border-radius: 6px;
  overflow: hidden;
}
.snapshot-table th {
  text-align: left;
  font-weight: 600;
  color: var(--navy);
  background: #ecfdf5;
  padding: 7px 12px;
  width: 38%;
  border-bottom: 1px solid var(--grey-200);
  border-right: 1px solid var(--grey-200);
}
.snapshot-table td {
  padding: 7px 12px;
  color: var(--slate-2);
  border-bottom: 1px solid var(--grey-100);
}
.snapshot-table tr:last-child th,
.snapshot-table tr:last-child td { border-bottom: none; }
.snapshot-table strong { color: var(--navy); }

.body-chart {
  margin: 8px 0 14px 0;
  padding: 10px;
  background: var(--grey-50);
  border: 1px solid var(--grey-200);
  border-radius: 6px;
}
.body-chart .chart-svg { width: 100%; height: auto; max-height: 240px; display: block; }
.body-chart.body-chart-narrow .chart-svg { max-height: 160px; max-width: 280px; margin: 0 auto; }

.body-disclaimer {
  margin-top: 24px;
  padding-top: 10px;
  border-top: 1px solid var(--grey-200);
  font-size: 8.5pt;
  font-style: italic;
  color: var(--slate-3);
  line-height: 1.5;
}
</style>
</head>
<body>

<div class="dashboard cover-page">

  <!-- HEADER ROW -->
  <div class="row header-row">
    <div class="card title-card">
      <div class="addr">{{ view.address }}</div>
      <div class="sub">AI Property Intelligence Report</div>
      <div class="date">Report Date: {{ view.report_date }}</div>
    </div>
    <div class="photo-card">
      {% if view.photo_uri %}
      <img src="{{ view.photo_uri }}" alt="Street view of {{ view.address }}">
      <div class="photo-tag">Google Street View</div>
      {% else %}
      <div class="photo-tag">Street View unavailable for this address</div>
      {% endif %}
    </div>
    <div class="card amenities-card">
      <div class="label">Nearby Amenities</div>
      <div class="amenities-grid">
        {% for item in view.map_legend %}
        <div class="amenity-row">
          <span class="amenity-icon icon-{{ item.color }}">{{ item.icon | safe }}</span>
          <span class="amenity-text">
            <strong>{{ item.label }}</strong>
            {% if item.detail %}<span class="amenity-distance">{{ item.detail }}</span>{% endif %}
          </span>
        </div>
        {% endfor %}
      </div>
    </div>
  </div>

  <!-- METRICS ROW -->
  <div class="row metric-row">
    <div class="card metric-card">
      <div class="metric-icon icon-emerald">{{ view.metric_icons.median | safe }}</div>
      <div class="metric-label">Median House Price</div>
      <div class="metric-value">{{ view.metrics.median }}</div>
      <div class="metric-sub">{{ view.suburb_name or "Suburb median" }}</div>
    </div>
    <div class="card metric-card">
      <div class="metric-icon icon-blue">{{ view.metric_icons.yield | safe }}</div>
      <div class="metric-label">Rental Yield</div>
      <div class="metric-value">{{ view.metrics.rental_yield }}</div>
      <div class="metric-sub">Estimate</div>
    </div>
    <div class="card metric-card">
      <div class="metric-icon icon-violet">{{ view.metric_icons.schools | safe }}</div>
      <div class="metric-label">Schools</div>
      <div class="metric-value">{{ view.metrics.schools }}</div>
      <div class="metric-sub">Quality</div>
    </div>
    <div class="card metric-card">
      <div class="metric-icon icon-teal">{{ view.metric_icons.train | safe }}</div>
      <div class="metric-label">Train to CBD</div>
      <div class="metric-value">{{ view.metrics.train_label }}</div>
      <div class="metric-sub">{{ view.metrics.train_station }}</div>
    </div>
    <div class="card metric-card">
      <div class="metric-icon icon-amber">{{ view.metric_icons.crime | safe }}</div>
      <div class="metric-label">Crime vs VIC Avg</div>
      <div class="metric-value">{{ view.crime.headline or "—" }}</div>
      <div class="metric-sub">Lower is safer</div>
    </div>
    <div class="card metric-card">
      <div class="metric-icon icon-rose">{{ view.metric_icons.market | safe }}</div>
      <div class="metric-label">Market Outlook</div>
      <div class="metric-value">{{ view.metrics.market_outlook }}</div>
      <div class="metric-sub">3–5 Year Outlook</div>
    </div>
  </div>

  <!-- CHART ROW -->
  <div class="row chart-row">
    <div class="card">
      <div class="label">Price Growth (Median House)</div>
      {{ view.history | safe }}
    </div>
    <div class="card">
      <div class="label">Rental Yield Trends</div>
      {{ view.rental | safe }}
    </div>
    <div class="card">
      <div class="label">Recent Comparable Sales</div>
      <table class="comp-table">
        <tr><th>Address</th><th>Sale Date</th><th>Sale Price</th></tr>
        {% for c in view.comparables %}
        <tr><td>{{ c.address }}</td><td>{{ c.date }}</td><td>{{ c.price }}</td></tr>
        {% endfor %}
      </table>
      <div class="last-sale-box">
        <div><span class="star">★</span><strong>Last Sale (Subject Property)</strong></div>
        <div><span class="price">{{ view.last_sale.price }}</span> &nbsp;<span style="color: var(--slate-3)">{{ view.last_sale.date }}</span></div>
      </div>
    </div>
    <div class="card">
      <div class="label">Suburb Scorecard</div>
      {% for s in view.scorecard %}
      <div class="scorecard-row">
        <span>{{ s.label }}</span>
        <span><span class="stars">{{ s.stars | safe }}</span> <span class="score-num">{{ "%.1f"|format(s.score) }}/10</span></span>
      </div>
      {% endfor %}
      {% if view.overall_score is not none %}
      <div class="overall-band">
        <div class="overall-text">Overall Score<strong>{{ view.overall_label }}</strong></div>
        <div class="overall-score">{{ "%.1f"|format(view.overall_score) }}/10</div>
      </div>
      {% endif %}
    </div>
  </div>

  <!-- DETAIL ROW -->
  <div class="row detail-row">
    <div class="card">
      <div class="label">Transit &amp; Connectivity</div>
      <div class="row-list">
        {% for t in view.transit %}
        <div class="item">
          <div class="left">
            <span class="icon-sm icon-{{ t.color or 'blue' }}">{{ t.icon | safe }}</span>
            <span>{{ t.label }}</span>
          </div>
          <div class="right">{{ t.right }}</div>
        </div>
        {% endfor %}
      </div>
    </div>
    <div class="card">
      <div class="label">School Catchment (Likely)</div>
      {% for s in view.schools_rows %}
      <div class="school-row">
        <div>
          <strong style="color: var(--navy)">{{ s.name }}</strong><br>
          <span style="color: var(--slate-3); font-size: 8.5px">{{ s.tier }} ({{ s.distance }})</span>
        </div>
        <div class="icsea">ICSEA {{ s.icsea }}</div>
      </div>
      {% endfor %}
      <div class="verify-note">
        Catchment zones should be verified at <a href="https://findmyschool.vic.gov.au">findmyschool.vic.gov.au</a>
      </div>
    </div>
    <div class="card crime-card">
      <div class="label">Crime Snapshot (vs VIC Average)</div>
      {% if view.crime.headline %}<div class="crime-headline">{{ view.crime.headline }}</div>{% endif %}
      <div class="crime-rows">
        {% for r in view.crime.rows %}
        <div class="crime-row">
          <span class="crime-label">{{ r.label }}</span>
          {% if r.available %}
          <span class="crime-delta {{ r.color }}">{{ r.phrase }}</span>
          {% else %}
          <span class="crime-delta delta-grey">data not available</span>
          {% endif %}
        </div>
        {% endfor %}
      </div>
    </div>
    <div class="card">
      <div class="label">Infrastructure Pipeline</div>
      {% for p in view.pipeline %}
      {% set lower = p.status|lower %}
      <div class="pipeline-row {% if 'complete' in lower %}completed{% elif 'progress' in lower %}progress{% else %}planned{% endif %}">
        <div class="left"><span class="icon-sm">{% if 'complete' in lower %}✓{% elif 'progress' in lower %}▶{% else %}○{% endif %}</span>{{ p.name }}</div>
        <div class="status">{{ p.status }}</div>
      </div>
      {% endfor %}
    </div>
  </div>

  <!-- VERDICT ROW -->
  <div class="row verdict-row">
    <div class="verdict-band">
      <div class="vd-stars">{{ view.overall_stars | safe }}</div>
      <div class="vd-eyebrow">Investment Verdict</div>
      <div class="vd-title">{{ view.overall_label }}</div>
      <div class="vd-body">Premium transit location and stable family demand underpin long-term value.</div>
    </div>
    <div class="verdict-cell">
      <span class="eyebrow">Ideal For</span>
      <span class="title">Owner-Occupiers</span>
      <span class="body">Walk to train, quality schools, established community.</span>
    </div>
    <div class="verdict-cell">
      <span class="eyebrow">Suitable For</span>
      <span class="title">Long-Term Investors</span>
      <span class="body">Low yield but strong location and infrastructure tailwinds.</span>
    </div>
    <div class="verdict-cell risk">
      <span class="eyebrow">Key Risks</span>
      <span class="body">Moderate crime risk · Modest population growth · Airport overlay restrictions</span>
    </div>
    <div class="next-steps">
      <div class="eyebrow">Next Steps for Buyers</div>
      <ul>
        <li>Obtain Section 10 (Title &amp; Planning Report)</li>
        <li>Confirm land size and overlays via VicPlan</li>
        <li>Inspect property &amp; recent sales in person</li>
        <li>Speak with a finance broker</li>
      </ul>
    </div>
  </div>

</div>{# end cover-page #}

{% if view.body_sections %}
<div class="body-pages">
  {% for sec in view.body_sections %}
  <section class="body-section">
    <h2 class="section-title"><span class="section-icon">{{ sec.icon | safe }}</span>{{ sec.title }}</h2>
    {% if sec.anchor == 'property-snapshot' and view.property_snapshot_html %}{{ view.property_snapshot_html | safe }}{% endif %}
    {% if sec.anchor == 'market-analysis' and view.history %}<div class="body-chart">{{ view.history | safe }}</div>{% endif %}
    {% if sec.anchor == 'schools-catchment' and view.school_chart_svg %}<div class="body-chart">{{ view.school_chart_svg | safe }}</div>{% endif %}
    {% if sec.anchor == 'risk-assessment' and view.crime.rows %}
    <div class="body-crime">
      {% if view.crime.headline %}<div class="body-crime-headline">{{ view.crime.headline }}</div>{% endif %}
      {% for r in view.crime.rows %}
      <div class="body-crime-row">
        <span>{{ r.label }}</span>
        {% if r.available %}<span class="crime-delta {{ r.color }}">{{ r.phrase }}</span>{% else %}<span class="crime-delta delta-grey">data not available</span>{% endif %}
      </div>
      {% endfor %}
    </div>
    {% endif %}
    {% if sec.anchor == 'verdict' and view.score_chart_svg %}<div class="body-chart">{{ view.score_chart_svg | safe }}</div>{% endif %}
    {{ sec.body_html | safe }}
  </section>
  {% endfor %}
  <div class="body-disclaimer">{{ view.disclaimer }}</div>
</div>
{% endif %}

</body>
</html>
"""


# ─── Public renderers ─────────────────────────────────────────────────────────

def render_dashboard_html(report) -> str:
    view = build_view(report)
    return Template(HTML_TEMPLATE).render(view=view)


def render_dashboard_pdf(report, output_path: str) -> None:
    html_str = render_dashboard_html(report)
    if not _WEASY_OK:
        raise RuntimeError(f"WeasyPrint unavailable: {_WEASY_ERR}")
    HTML(string=html_str, base_url=os.path.dirname(os.path.abspath(__file__))).write_pdf(output_path)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _main(argv: list[str]) -> int:
    json_path = argv[1] if len(argv) > 1 else "report_output.json"
    out_pdf   = argv[2] if len(argv) > 2 else "cover_spike.pdf"

    if os.path.exists(json_path):
        print(f"[+] Loading {json_path}")
        report = load_report_from_json(json_path)
    else:
        print(f"[i] No {json_path} found - using built-in sample (Taylors Lakes)")
        report = sample_report()

    html_out = re.sub(r"\.pdf$", ".html", out_pdf, flags=re.IGNORECASE)
    if not html_out.endswith(".html"):
        html_out = out_pdf + ".html"

    with open(html_out, "w", encoding="utf-8") as f:
        f.write(render_dashboard_html(report))
    print(f"[ok] HTML preview: {html_out}")
    print("     Open in any browser to see the layout.")

    if _WEASY_OK:
        try:
            render_dashboard_pdf(report, out_pdf)
            print(f"[ok] PDF rendered: {out_pdf}")
        except Exception as e:  # noqa: BLE001
            print(f"[!]  WeasyPrint failed at render time: {e}")
            print("     (HTML preview above is still good for design review.)")
    else:
        print(f"[i]  WeasyPrint not importable locally: {_WEASY_ERR}")
        print("     HTML preview is the spike deliverable on this machine.")
        print("     Production (Railway) installs cairo/pango via nixpacks.toml -- PDF works there.")

    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
