"""
Last-sold price lookup for a subject property.

Priority:
  1. Domain API (authoritative) — requires DOMAIN_CLIENT_ID + DOMAIN_CLIENT_SECRET
  2. property.com.au scrape — no credentials needed, best-effort HTML parse
All failures are soft — returns None so the caller falls back to Claude web search.
"""

import json
import os
import re
import time

import requests

_TOKEN_URL = "https://auth.domain.com.au/v1/connect/token"
_API_BASE  = "https://api.domain.com.au/v1"
_TIMEOUT   = 10

_cached: dict = {}

_SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}


def _get_token(client_id: str, client_secret: str) -> str | None:
    if _cached.get("token") and time.time() < _cached.get("expires_at", 0) - 30:
        return _cached["token"]
    try:
        r = requests.post(
            _TOKEN_URL,
            data={
                "grant_type":    "client_credentials",
                "scope":         "api_properties_read",
                "client_id":     client_id,
                "client_secret": client_secret,
            },
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        _cached["token"]      = body["access_token"]
        _cached["expires_at"] = time.time() + int(body.get("expires_in", 3600))
        return _cached["token"]
    except Exception as exc:
        print(f"  ⚠️  [Domain API] Token error: {exc}")
        return None


def _iso_to_month_year(iso: str) -> str | None:
    """Convert '2023-06-15' or '2023-06' → 'June 2023'."""
    m = re.match(r'(\d{4})-(\d{2})', str(iso))
    if not m:
        return str(iso)
    months = ["January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]
    year, month = int(m.group(1)), int(m.group(2))
    return f"{months[month - 1]} {year}" if 1 <= month <= 12 else str(iso)


_STREET_TYPE_RE = re.compile(
    r'\b(Street|Road|Avenue|Drive|Court|Place|Crescent|Close|Boulevard|'
    r'Terrace|Lane|Way|Grove|Highway|Parade|Esplanade|Circuit|Square|'
    r'Mews|Rise|Quay|Mall|Walk|Track|Ridge|'
    r'St|Rd|Ave|Dr|Ct|Pl|Cres|Cl|Bvd|Tce|Ln|Wy|Gr|Hwy|Pde|Esp|Cct)\b',
    re.IGNORECASE,
)

# Map full street type → URL abbreviation (property.com.au slug style)
_STREET_ABBREV = {
    'street': 'st', 'road': 'rd', 'avenue': 'ave', 'drive': 'dr',
    'court': 'ct', 'place': 'pl', 'crescent': 'cres', 'close': 'cl',
    'boulevard': 'bvd', 'terrace': 'tce', 'lane': 'ln', 'way': 'wy',
    'grove': 'gr', 'highway': 'hwy', 'parade': 'pde', 'circuit': 'cct',
    'esplanade': 'esp', 'square': 'sq', 'mews': 'mews', 'rise': 'rise',
    'promenade': 'prom', 'track': 'trk', 'trail': 'trl', 'ridge': 'rdge',
    'quay': 'qy', 'walk': 'wlk', 'loop': 'loop', 'link': 'lnk',
}


def _street_to_slug(street: str) -> str:
    """'Chapel Street' → 'chapel-st', 'Main Road' → 'main-rd'."""
    parts = street.lower().split()
    if parts and parts[-1] in _STREET_ABBREV:
        parts[-1] = _STREET_ABBREV[parts[-1]]
    return '-'.join(parts)


def _parse_address_parts(address: str) -> dict | None:
    """
    Parse a Google Maps formatted_address into components.
    Handles both:
      "45 Chapel Street, Windsor VIC 3181, Australia"
      "45 Chapel St Windsor VIC 3181"
    """
    # Strip trailing ", Australia" or ", AUS"
    address = re.sub(r',?\s*Australia\s*$', '', address.strip(), flags=re.IGNORECASE).strip()

    # Extract state + postcode from the end
    tail = re.search(
        r',?\s*(VIC|NSW|QLD|SA|WA|TAS|ACT|NT)\s+(\d{4})\s*$',
        address, re.IGNORECASE,
    )
    if not tail:
        return None

    state    = tail.group(1).upper()
    postcode = tail.group(2)
    body     = address[:tail.start()].strip().rstrip(',').strip()

    # body is now e.g. "45 Chapel Street, Windsor" or "45 Chapel St Windsor"
    # Split on comma if present: "45 Chapel Street" / "Windsor"
    if ',' in body:
        street_part, suburb_part = body.split(',', 1)
        street_part = street_part.strip()
        suburb_part = suburb_part.strip()

        num_m = re.match(r'^(\d+[A-Za-z]?)\s+', street_part)
        if not num_m:
            return None
        number = num_m.group(1)
        street = street_part[num_m.end():].strip()
        suburb = suburb_part
    else:
        # No comma — parse by finding the street-type boundary
        num_m = re.match(r'^(\d+[A-Za-z]?)\s+', body)
        if not num_m:
            return None
        number = num_m.group(1)
        rest   = body[num_m.end():]  # e.g. "Chapel St Windsor"

        # First street-type word NOT at position 0
        first_valid = None
        for m in _STREET_TYPE_RE.finditer(rest):
            if m.start() > 0:
                first_valid = m
                break
        if not first_valid:
            return None

        street = rest[:first_valid.end()].strip()
        suburb = rest[first_valid.end():].strip()

    if not street or not suburb:
        return None

    return {
        "number":   number,
        "street":   street,
        "suburb":   suburb,
        "state":    state,
        "postcode": postcode,
    }


def _scrape_property_com_au(address: str) -> dict | None:
    """
    Fetch last sold price from property.com.au.
    Returns {"price": <int AUD>, "date": <str or None>} or None.
    """
    parts = _parse_address_parts(address)
    if not parts:
        print("  ⚠️  [property.com.au] Could not parse address components")
        return None

    state       = parts["state"].lower()
    suburb_slug = parts["suburb"].lower().replace(" ", "-").replace("'", "")
    postcode    = parts["postcode"]
    street_slug = _street_to_slug(parts["street"])  # e.g. "berwick-springs-prom"
    number      = parts["number"]

    # Correct property.com.au URL format:
    # /state/suburb-postcode/street-slug/number[-pid-ID]/
    # PID is unknown so we try without it — site may redirect or serve the page.
    candidate_urls = [
        f"https://www.property.com.au/{state}/{suburb_slug}-{postcode}/{street_slug}/{number}/",
        f"https://www.property.com.au/{state}/{suburb_slug}/{street_slug}/{number}/",
    ]

    for url in candidate_urls:
        try:
            r = requests.get(url, headers=_SCRAPE_HEADERS, timeout=15, allow_redirects=True)
            if r.status_code != 200:
                continue
            html = r.text

            # 1 — JSON-LD structured data (most reliable when present)
            for ld_raw in re.findall(
                r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html, re.DOTALL,
            ):
                try:
                    data = json.loads(ld_raw)
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        price = item.get("price") or (item.get("offers") or {}).get("price")
                        if price:
                            p = int(float(str(price).replace(",", "")))
                            if p > 50_000:
                                date_str = item.get("dateSold") or item.get("datePublished")
                                if date_str:
                                    date_str = _iso_to_month_year(date_str)
                                print(f"  ✅ [property.com.au] Last sale (JSON-LD): "
                                      f"${p:,}{' (' + date_str + ')' if date_str else ''}")
                                return {"price": p, "date": date_str}
                except Exception:
                    pass

            # 2 — Regex: "Sold $XXX,XXX" anywhere in page text
            sold_m = re.search(r'[Ss]old[^$\n]{0,80}\$([\d,]+)', html)
            if sold_m:
                price_str = sold_m.group(1).replace(",", "")
                try:
                    price = int(price_str)
                    if price > 50_000:
                        date_m = re.search(
                            r'[Ss]old[^$\n]{0,120}'
                            r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*'
                            r'\s+\d{4}|\d{1,2}\s+\w+\s+\d{4})',
                            html,
                        )
                        date_str = date_m.group(1) if date_m else None
                        print(f"  ✅ [property.com.au] Last sale (regex): "
                              f"${price:,}{' (' + date_str + ')' if date_str else ''}")
                        return {"price": price, "date": date_str}
                except ValueError:
                    pass

        except Exception as exc:
            print(f"  ⚠️  [property.com.au] Fetch error for {url}: {exc}")

    print("  ℹ️  [property.com.au] No sold price found")
    return None


def get_last_sale(address: str) -> dict | None:
    """
    Return {"price": <int AUD>, "date": <str e.g. 'June 2023'>} for the
    subject property, or None if unavailable.

    Tries in order:
      1. Domain API (when DOMAIN_CLIENT_ID/SECRET are set)
      2. property.com.au scrape
    """
    # ── Domain API ──────────────────────────────────────────────────────────
    client_id     = os.getenv("DOMAIN_CLIENT_ID", "")
    client_secret = os.getenv("DOMAIN_CLIENT_SECRET", "")

    if client_id and client_secret:
        token = _get_token(client_id, client_secret)
        if token:
            hdrs = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

            # Step 1: resolve address → property ID
            prop_id = None
            try:
                r = requests.get(
                    f"{_API_BASE}/properties/_suggest",
                    params={"terms": address, "pageSize": 1},
                    headers=hdrs,
                    timeout=_TIMEOUT,
                )
                if r.status_code == 200:
                    suggestions = r.json()
                    if suggestions:
                        prop_id      = suggestions[0].get("id", "")
                        display_addr = suggestions[0].get("displayAddress", prop_id)
                        print(f"  🏠 [Domain API] Matched: {display_addr}")
                else:
                    print(f"  ⚠️  [Domain API] Suggest {r.status_code}: {r.text[:120]}")
            except Exception as exc:
                print(f"  ⚠️  [Domain API] Suggest error: {exc}")

            if prop_id:
                price    = None
                date_raw = None

                # Step 2: property record
                try:
                    r = requests.get(
                        f"{_API_BASE}/properties/{prop_id}",
                        headers=hdrs,
                        timeout=_TIMEOUT,
                    )
                    if r.status_code == 200:
                        prop     = r.json()
                        price    = prop.get("lastSalePrice")
                        date_raw = prop.get("lastSaleDate") or prop.get("lastSoldDate")
                    else:
                        print(f"  ⚠️  [Domain API] Property fetch {r.status_code}")
                except Exception as exc:
                    print(f"  ⚠️  [Domain API] Property fetch error: {exc}")

                # Step 3: sales history fallback
                if not price:
                    try:
                        r2 = requests.get(
                            f"{_API_BASE}/properties/{prop_id}/salesHistory",
                            headers=hdrs,
                            timeout=_TIMEOUT,
                        )
                        if r2.status_code == 200:
                            history = r2.json()
                            if history and isinstance(history, list):
                                latest   = history[0]
                                price    = latest.get("price")
                                date_raw = latest.get("date") or latest.get("soldDate")
                    except Exception:
                        pass

                if price:
                    date_str = _iso_to_month_year(date_raw) if date_raw else None
                    print(f"  ✅ [Domain API] Last sale: "
                          f"${int(price):,}{' (' + date_str + ')' if date_str else ''}")
                    return {"price": int(price), "date": date_str}

                print("  ℹ️  [Domain API] No last sale price on record — trying property.com.au")
            else:
                print("  ℹ️  [Domain API] No address match — trying property.com.au")
    else:
        print("  ℹ️  [Domain API] Credentials not set — trying property.com.au")

    # ── property.com.au scrape ───────────────────────────────────────────────
    return _scrape_property_com_au(address)
