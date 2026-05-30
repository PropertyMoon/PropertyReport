"""
Domain API client — looks up last-sold price for a subject property.
Requires DOMAIN_CLIENT_ID and DOMAIN_CLIENT_SECRET env vars (from developer.domain.com.au).
All failures are soft — returns None so the caller falls back to Claude web search.
"""

import os
import re
import time

import requests

_TOKEN_URL = "https://auth.domain.com.au/v1/connect/token"
_API_BASE  = "https://api.domain.com.au/v1"
_TIMEOUT   = 10

_cached: dict = {}


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


def get_last_sale(address: str) -> dict | None:
    """
    Return {"price": <int AUD>, "date": <str e.g. 'June 2023'>} for the
    subject property, or None if unavailable / credentials not set.
    """
    client_id     = os.getenv("DOMAIN_CLIENT_ID", "")
    client_secret = os.getenv("DOMAIN_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return None

    token = _get_token(client_id, client_secret)
    if not token:
        return None

    hdrs = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    # Step 1: resolve address → property ID
    try:
        r = requests.get(
            f"{_API_BASE}/properties/_suggest",
            params={"terms": address, "pageSize": 1},
            headers=hdrs,
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            print(f"  ⚠️  [Domain API] Suggest {r.status_code}: {r.text[:120]}")
            return None
        suggestions = r.json()
    except Exception as exc:
        print(f"  ⚠️  [Domain API] Suggest error: {exc}")
        return None

    if not suggestions:
        print("  ℹ️  [Domain API] No match for address")
        return None

    prop_id      = suggestions[0].get("id", "")
    display_addr = suggestions[0].get("displayAddress", prop_id)
    print(f"  🏠 [Domain API] Matched: {display_addr}")

    if not prop_id:
        return None

    # Step 2: fetch property record for lastSalePrice / lastSaleDate
    price    = None
    date_raw = None
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

    # Step 3: fallback to sales history endpoint if property record had no price
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

    if not price:
        print("  ℹ️  [Domain API] No last sale price on record")
        return None

    date_str = _iso_to_month_year(date_raw) if date_raw else None
    print(f"  ✅ [Domain API] Last sale: ${int(price):,}{' (' + date_str + ')' if date_str else ''}")
    return {"price": int(price), "date": date_str}
