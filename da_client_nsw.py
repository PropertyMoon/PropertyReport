"""
NSW Development Applications — ArcGIS REST query client.

Fetches DA records from the NSW Planning Portal's public DA_Tracking MapServer.
No API key required.

Data note: The public ArcGIS layer contains DAs lodged between 2018 and April 2023.
It is used here for historical planning character analysis, not current DA status.
Current/pending DAs are found by the AI research task via web search.

Endpoint:
  https://mapprod3.environment.nsw.gov.au/arcgis/rest/services/
  Planning/DA_Tracking/MapServer/0/query
"""

import httpx

_DA_TRACKING_URL = (
    "https://mapprod3.environment.nsw.gov.au/arcgis/rest/services/"
    "Planning/DA_Tracking/MapServer/0/query"
)

_FIELDS = (
    "STATUS,PRIMARY_ADDRESS,LODGEMENT_DATE,"
    "TYPE_OF_DEVELOPMENT,COST_OF_DEVELOPMENT,"
    "SUBURBNAME,DWELLINGS_TO_BE_CONSTRUCTED,STOREYS_PROPOSED,"
    "TYPE_OF_DEVELOPMENT_GROUPING"
)


def _fmt_cost(cost) -> str | None:
    if cost is None:
        return None
    try:
        c = int(float(cost))
        if c >= 1_000_000:
            return f"${c / 1_000_000:.1f}M"
        if c >= 1_000:
            return f"${c / 1_000:.0f}K"
        return f"${c:,}"
    except (ValueError, TypeError):
        return None


def get_nsw_das(suburb: str) -> dict:
    """
    Fetch historical DA data for a NSW suburb from the Planning Portal DA_Tracking layer.

    Returns dict with:
        coverage:              'available' | 'no_data' | 'error'
        suburb:                str
        total_records:         int — total DAs lodged in the dataset (2018–Apr 2023)
        multi_dwelling_count:  int — DAs with 2+ proposed dwellings
        large_project_count:   int — DAs with 3+ storeys or cost >= $1M
        notable_projects:      list of up to 5 significant DAs (by scale)
        data_period:           str — "2018–2023 (historical)"
    """
    suburb_upper = suburb.strip().upper()

    try:
        # Total record count for this suburb
        r_count = httpx.get(
            _DA_TRACKING_URL,
            params={
                "where": f"SUBURBNAME='{suburb_upper}'",
                "returnCountOnly": "true",
                "f": "json",
            },
            timeout=30,
        )
        r_count.raise_for_status()
        count_data = r_count.json()
        total = count_data.get("count", 0)

        if total == 0:
            return {"suburb": suburb, "coverage": "no_data"}

        # Fetch most significant projects — filter by integer fields only
        # (COST_OF_DEVELOPMENT is a string field and cannot be used in numeric comparisons)
        r = httpx.get(
            _DA_TRACKING_URL,
            params={
                "where": (
                    f"SUBURBNAME='{suburb_upper}' AND "
                    "(DWELLINGS_TO_BE_CONSTRUCTED >= 2 OR STOREYS_PROPOSED >= 3)"
                ),
                "outFields": _FIELDS,
                "orderByFields": "DWELLINGS_TO_BE_CONSTRUCTED DESC",
                "resultRecordCount": 100,
                "f": "json",
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()

    except Exception as e:
        return {"suburb": suburb, "coverage": "error", "error": str(e)}

    if "error" in data:
        return {"suburb": suburb, "coverage": "no_data", "error": str(data["error"])}

    features = data.get("features", [])

    multi_dwelling_count = 0
    large_project_count = 0
    notable: list[dict] = []
    seen_addresses: set[str] = set()

    for f in features:
        a = f.get("attributes", {})
        dev_type = (a.get("TYPE_OF_DEVELOPMENT") or "").strip()
        cost_raw = a.get("COST_OF_DEVELOPMENT")
        dwellings = int(a.get("DWELLINGS_TO_BE_CONSTRUCTED") or 0)
        storeys = int(a.get("STOREYS_PROPOSED") or 0)
        address = (a.get("PRIMARY_ADDRESS") or "").strip().upper()

        try:
            cost_num = float(cost_raw) if cost_raw else 0
        except (ValueError, TypeError):
            cost_num = 0

        # Skip clearly corrupt records (data entry errors in source dataset)
        if dwellings > 2000:
            continue
        # Skip records where cost is implausibly low for the scale (e.g. 1000 units at $500)
        if dwellings > 50 and 0 < cost_num < 10_000:
            continue

        if dwellings >= 2:
            multi_dwelling_count += 1
        if storeys >= 3 or cost_num >= 1_000_000:
            large_project_count += 1

        # Deduplicate by address; keep only the first (highest dwelling count) occurrence
        addr_key = address[:40]
        if len(notable) < 5 and (dwellings >= 4 or storeys >= 3) and addr_key not in seen_addresses:
            seen_addresses.add(addr_key)
            notable.append({
                "address":   address,
                "type":      dev_type[:60] if dev_type else "Development",
                "dwellings": dwellings or None,
                "storeys":   storeys or None,
                "cost":      _fmt_cost(cost_raw),
            })

    return {
        "suburb":               suburb,
        "coverage":             "available",
        "total_records":        total,
        "multi_dwelling_count": multi_dwelling_count,
        "large_project_count":  large_project_count,
        "notable_projects":     notable,
        "data_period":          "2018–2023",
    }
