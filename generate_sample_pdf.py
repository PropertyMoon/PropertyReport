"""
One-off script: generate frontend/sample_report.pdf using mock data.
Run from the PropertyReport/ directory:
    python generate_sample_pdf.py
No API keys required — uses hardcoded mock data only.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dataclasses import field
from orchestrator import PropertyReport
from weasy_generator import render_dashboard_pdf

SUMMARY = """\
# PROPERTY INVESTMENT REPORT
## 35 Pindari Ave, Taylors Lakes VIC 3038

**Report Date:** June 2026
**Property Type:** Residential

<!-- EMAIL_SUMMARY: 35 Pindari Ave, Taylors Lakes sits 120 m from Watergardens Station with a 34-minute CBD train commute; the suburb median sits at $960K and local schools score in the Strong band, making this a compelling long-term hold for professional families. -->

## EXECUTIVE SUMMARY
35 Pindari Ave, Taylors Lakes VIC 3038 is a 4-bedroom, 2-bathroom house on a 612 m² General Residential (GRZ1) lot, situated 120 metres from Watergardens Station on Melbourne's Sunbury Line. The suburb median house price sits at $960,000 — up from $460,000 a decade ago — and the rental yield of 4.5% is solid for the outer west. Local catchment schools score in the Strong ICSEA band (average 1,033), and the professional-family demographic underpins consistent owner-occupier demand. Given the station proximity and measured western-corridor infrastructure investment, this property represents a credible long-term capital growth proposition for owner-occupiers, with a selective case for long-hold investors.

### Key Highlights
- 120 m walk to Watergardens Station — 34-minute CBD commute by train on the Sunbury Line, top-tier transit for outer Melbourne
- Suburb median $960K with uninterrupted 5-year growth; no speculative spikes or corrections in the price history
- Catchment schools average ICSEA 1,033 — above state average, anchoring family demand in the precinct
- Western Ring Road and Calder Freeway within 8 minutes by car; Melbourne Airport accessible in ~25 minutes

### Primary Concerns
- Safety percentile of 32 — property crime tracks modestly above state median; verify security measures on inspection
- Gross rental yield (~2.8%) limits cash-flow appeal; investor case rests on capital growth, not income

### Indicative Suitability
- Owner-occupiers: **HIGH** — train access, school catchment, established family character
- Investors: **MODERATE** — long-hold capital growth thesis; not a yield play

## PROPERTY SNAPSHOT
The subject property sits on a 612 m² GRZ1 lot — the dominant zoning in Taylors Lakes, permitting up to 2 storeys and sensitively-designed second dwellings. The 1990s build is in renovation-ready condition; kitchen and bathroom updates represent the most direct value-add path without requiring council approval beyond standard building permits.

### Development Outlook
- Battle-axe subdivision is achievable given the lot area, subject to Brimbank Council stormwater requirements and 17.4 m frontage
- A front/rear two-dwelling development is also feasible; verify setback rules under the GRZ1 schedule
- Knockdown-rebuild economics stack up; land value in this pocket supports rebuild at current construction costs

## MARKET ANALYSIS
Taylors Lakes has delivered steady, uninterrupted appreciation over the past decade, driven by station proximity, school catchment quality, and sustained demand from professional families priced out of inner-ring suburbs.

### Pricing & Rental
- Suburb median house price: $960,000 (June 2026)
- Comparable recent sales range from $775K (3-bed) to $942K (4-bed), broadly consistent with the suburb median
- Median weekly rent for houses: $550/week — gross yield approximately 2.8% at current median
- The rental market is tight; Watergardens Station proximity drives strong demand from renting professionals

### Comparable Sales (Last 3 Months)
- 13 Pindari Ave — 3 bed / 2 bath — $775,000 (May 2026)
- 28 Cloudburst Ave — 4 bed / 2 bath — $942,000 (April 2026)
- 7 Windrow Crescent — 4 bed / 2 bath — $900,000 (April 2026)

### 5-Year Outlook
Continued moderate growth supported by the western corridor's infrastructure pipeline. The Robertsons Road infill (~175 new lots) adds short-term supply but validates long-term demand. Watergardens activity centre densification remains a decade-scale tailwind.

## SUBURB PROFILE
Taylors Lakes is a mature, low-density suburb 23 km north-west of Melbourne's CBD, housing approximately 15,000 residents. The dominant household type is couples with children in professional occupations, with median household income above the outer-suburban average.

### Who's Moving Here
Professional families and older couples are the primary inbound cohort, attracted by the train commute, relative affordability versus inner-ring alternatives, and established school catchments. The suburb is also a destination for Indian-Australian households, the fastest-growing cultural segment in the precinct.

### Household & Amenities
- Watergardens Town Centre (400 m): major retail, dining, cinema, gym
- Watergardens Medical Centre (1.0 km): GP, allied health
- Sunshine Hospital (8.5 km): nearest major public hospital
- Taylors Lakes shared-use path (Stage 1 complete): cycling and walking infrastructure improving
- 86% owner-occupancy rate — one of the highest in Melbourne's outer west

## SCHOOLS CATCHMENT
Catchment schools sit at or above the state ICSEA average, reflecting the professional-family demographic that anchors demand in Taylors Lakes.

| School | Type | ICSEA | Distance |
|---|---|---|---|
| Taylors Lakes Primary School | Gov Primary | 1,050 | 0.9 km |
| Taylors Lakes Secondary College | Gov Secondary | 1,015 | 1.2 km |

**School Quality Summary:** Strong — ICSEA average of 1,033, above the national mean of 1,000.

Private alternatives within 10 km include Overnewton Anglican Community College (Caroline Springs) and St Bernard's College (Essendon).

## INFRASTRUCTURE & DEVELOPMENT
- **Melton Hwy / Ballarat Rd Intersection Upgrade** — Completed 2024; reduces peak congestion on the main western arterial
- **Robertsons Rd Infill Project (~175 New Lots)** — In progress 2025–2026; Brimbank Planning Scheme Amendment GC51
- **Sydenham Train Station Upgrade** — Planned 2026+; capacity improvement on the Sunbury Line
- **Taylors Lakes Shared-User Path Stage 2** — Underway; active-transport improvement within the suburb

## TRANSPORT CONNECTIVITY

### Public Transport
- Watergardens Station (Sunbury Line): 120 m walk — **34-minute CBD commute**
- Five bus routes through the Watergardens interchange connect Keilor, St Albans, and Caroline Springs
- 400-series buses run to Sunshine and Broadmeadows via Keilor Road

### Car Travel
- CBD: 20 min off-peak / ~48 min peak via Calder Freeway and Western Ring Road
- M80 Ring Road: 5.2 km (8 min)
- Melbourne Airport: ~25 min via Airport Drive
- Watergardens Town Centre: 400 m / 1 min

## RISK ASSESSMENT

### Crime & Safety
Taylors Lakes sits at the **32nd safety percentile** in Victoria. Violent crime is **17% below** the state average; property crime is **8% above**. The crime cover gauge renders these comparisons. Practical implication: security upgrades (cameras, sensor lights, secure garage) are a sensible inclusion on any renovation plan.

### Environmental & Planning
- **Melbourne Airport Environs Overlay (MAEO)** applies to this precinct — confirm the specific noise-contour schedule for this address via VicPlan before proceeding
- **Flood risk:** No floodway shown on current Brimbank mapping; recommend independent verification via vicfloodmap.com.au
- **Heritage overlay:** Not indicated for this address; verify via planning.vic.gov.au before any significant works
- **Bushfire Attack Level (BAL):** Not applicable — inner suburban location

### Market Risks
- Outer-suburban markets are more sensitive to interest-rate movements than inner-ring comparables
- New Robertsons Road infill supply (~175 lots) could moderate land-value gains in the short term
- Any future Sunbury Line capacity constraint would disproportionately affect station-proximate properties in this corridor

### Verification Checklist
- Flood overlay (vicfloodmap.com.au or Brimbank Section 32 vendor statement)
- Airport Environs noise contour for this address (VicPlan)
- School catchment confirmation (findmyschool.vic.gov.au)
- Lot dimensions and subdivision feasibility (Brimbank Council or private surveyor)

## VERDICT

### Score Breakdown
- Growth Potential: **7.0 / 10** — steady western corridor demand backed by infrastructure
- Rental Demand: **6.0 / 10** — tight vacancy; modest gross yield
- Infrastructure: **8.0 / 10** — exceptional rail access and active road investment
- Safety: **4.0 / 10** — property crime above state median; manageable with basic security measures
- Family Suitability: **8.0 / 10** — schools, owner-occupancy dominance, family character

**Overall Score: 6.5 / 10**

### Overall Assessment
35 Pindari Ave is a sound long-term prospect for owner-occupiers and a selective buy for long-hold investors focused on capital growth over yield. The 120 m station walk is the standout attribute — it anchors the address firmly in the top tier for the outer-western Melbourne market.

### Strengths
1. Watergardens Station at 120 m — best-in-suburb transit position and primary value driver
2. Strong family demographic and 86% owner-occupancy underpin price stability
3. Established suburb with track record of moderate, consistent capital growth
4. Development optionality (subdivision / dual-dwelling) offers future value levers

### Weaknesses
1. Safety percentile in the lower third — property crime is the key category to manage
2. Modest rental yield (~2.8%) makes this unsuitable as a high-income investment property
3. Airport Environs Overlay requires noise-contour verification before purchase

### Buyer Suitability
**Owner-Occupiers:** Strong fit for professional families who commute to the CBD by train and value an established school catchment.
**Investors:** Long-hold capital growth strategy only; do not buy on yield expectations.
**Developers:** Battle-axe or front/rear two-dwelling development is worth feasibility modelling against actual lot dimensions and current GRZ1 schedule requirements.

### Price Guidance
Comparable 4-bedroom sales in the $900K–$942K range over the past 3 months indicate the subject property should be benchmarked in the $880K–$950K band, with the 120 m station proximity justifying the upper end for a well-presented property.

## DATA SOURCES

- **Crime statistics:** Crime Statistics Agency Victoria (CSA) — crimestats.vic.gov.au
- **Property market data:** realestate.com.au, domain.com.au
- **School data:** myschool.edu.au (ACARA)
- **Planning & zoning:** planning.vic.gov.au (VicPlan)
- **Flood & environmental risk:** vicfloodmap.com.au, Melbourne Water
- **Transport:** Public Transport Victoria (ptv.vic.gov.au)
"""

report = PropertyReport(
    address="35 Pindari Ave, Taylors Lakes VIC 3038",
    suburb={
        "median_house_price": 960_000,
        "rental_yield": "4.5%",
        "crime_safety_percentile": 32,
        "crime_violent_vs_state_avg_pct": -17,
        "crime_property_vs_state_avg_pct": 8,
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
        "nearest_freeway":  {"name": "M80 Ring Road", "distance_km": 5.2},
        "nearby_gps":       [{"name": "Watergardens Medical", "distance_km": 1.0}],
        "nearby_hospitals": [{"name": "Sunshine Hospital", "distance_km": 8.5}],
        "crime_trend_3yr": [
            {"assault": 45, "break_enter": 120, "vehicle_theft": 85, "theft": 210},
            {"assault": 48, "break_enter": 115, "vehicle_theft": 80, "theft": 195},
            {"assault": 42, "break_enter": 108, "vehicle_theft": 72, "theft": 185},
        ],
        "crime_trend_years": [2023, 2024, 2025],
    },
    schools={
        "primary_schools":   [{"name": "Taylors Lakes Primary School",    "icsea": 1050, "distance_km": 0.9}],
        "secondary_schools": [{"name": "Taylors Lakes Secondary College", "icsea": 1015, "distance_km": 1.2}],
        "school_quality_summary": "Strong",
    },
    transport={
        "nearest_train":            {"name": "Watergardens Station", "distance_km": 0.12, "cbd_mins": 34},
        "drive_to_cbd_peak_mins":   48,
        "drive_to_cbd_offpeak_mins": 20,
    },
    property_market={
        "subject_property_last_sale": {"price": 667_000, "date": "October 2018"},
        "comparable_sales": [
            {"address": "13 Pindari Ave",    "sale_price": 775_000, "sale_date": "May 2026",   "bedrooms": 3, "bathrooms": 2},
            {"address": "28 Cloudburst Ave", "sale_price": 942_000, "sale_date": "April 2026", "bedrooms": 4, "bathrooms": 2},
            {"address": "7 Windrow Crescent","sale_price": 900_000, "sale_date": "April 2026", "bedrooms": 4, "bathrooms": 2},
        ],
        "market_outlook":      "Stable Growth",
        "median_rent_house":   550,
    },
    government_projects={
        "transport_projects": [
            {"name": "Melton Hwy / Ballarat Rd Intersection Upgrade",      "status": "Completed 2024"},
            {"name": "Robertsons Rd Infill Project (~175 New Lots)",        "status": "In Progress 2025–2026"},
            {"name": "Sydenham Train Station Upgrade",                      "status": "Planned 2026+"},
        ],
    },
    risk_overlays={
        "flood_risk":          "Low — no floodway on current Brimbank mapping; verify via vicfloodmap.com.au",
        "bushfire_bal_rating": "N/A — inner suburban location",
        "heritage_overlay":    "Not indicated; verify via planning.vic.gov.au",
        "airport_overlay":     "Melbourne Airport Environs Overlay applies — confirm noise contour via VicPlan",
        "noise_concerns":      "Airport noise contour zone; recommend checking specific contour schedule for this address",
    },
    property_intel={
        "land_sqm":       612,
        "dwelling_type":  "House",
        "frontage_m":     17.4,
        "bedrooms":       4,
        "bathrooms":      2,
        "parking":        2,
        "year_built":     1995,
        "zoning_code":    "GRZ1",
        "zoning_description": "General Residential — neighbourhood-scale dwellings, up to 2 storeys",
        "corner_block":   False,
        "subdivision_potential":      {"rating": "Moderate", "reason": "612 m² supports a battle-axe subdivision subject to council approval"},
        "development_feasibility":    {"rating": "Moderate", "reason": "Two-dwelling development achievable on this lot size"},
        "renovation_potential":       {"rating": "High",     "reason": "1990s build benefits from kitchen / bathroom updates"},
        "knockdown_rebuild_viability":{"rating": "High",     "reason": "Land value supports rebuild economics in this pocket"},
        "street_position_quality":    7,
    },
    summary=SUMMARY,
)

report.metrics = {
    "median_price":    "$960K",
    "rental_yield":    "4.5%",
    "school_quality":  "Strong",
    "cbd_train_mins":  "34 min",
    "market_outlook":  "Stable Growth",
    "last_sale_price": "$667K (October 2018)",
}
report.scores = {
    "growth_potential":    7.0,
    "rental_demand":       6.0,
    "infrastructure":      8.0,
    "safety":              4.0,
    "family_suitability":  8.0,
    "overall":             6.5,
}

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "sample_report.pdf")
render_dashboard_pdf(report, out)
print(f"Sample PDF saved to: {out}")
