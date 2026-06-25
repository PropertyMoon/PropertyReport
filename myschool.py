"""
myschool NAPLAN fetcher — uses Playwright headless Chromium to retrieve
authoritative NAPLAN scores from myschool.edu.au.

myschool is protected by Cloudflare Turnstile (auto-passes with their test
sitekey). After the challenge, a React component fetches NAPLAN data from an
AEM Sling endpoint. We intercept that network call or call it in-page.

The resource path is stable across all schools; only `sml_id` (ACARA ID) differs.

Data returned per school:
  naplan_performance  — 'Above Average', 'Average', 'Below Average', or None
                         Computed by averaging Year 3 + Year 5 Reading and Numeracy
                         SchoolAvg vs NationalAvg (±10 point threshold).
  year                — calendar year of the data (e.g. 2025)

In-process 24 h cache prevents re-scraping the same school within a report batch.
"""

import asyncio
import json
import time
import urllib.parse

try:
    from playwright.async_api import async_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

_RESOURCE_PATH = (
    "/content/acara-myschool/au/en/school/naplan/results"
    "/jcr:content/root/container/globalconfig_1719137697.result_naplan"
)
_SEARCH_API = "/bin/acara/principals/searchschools.json?searchKey="
_BASE_URL   = "https://myschool.edu.au"

# In-process cache: school_name (lower) → naplan dict | None
_cache:    dict  = {}
_cache_ts: float = 0.0
_CACHE_TTL = 86_400  # 24 h


def _parse_naplan(data: dict) -> dict | None:
    """Extract Above/Average/Below label from the API JSON response."""
    resp    = data.get("response", {})
    numbers = resp.get("Numbers", [])
    if not numbers:
        return None

    # Use the most recent year available
    latest_year = max((n["CAL_YR"] for n in numbers), default=None)
    if not latest_year:
        return None
    latest = [n for n in numbers if n["CAL_YR"] == latest_year]

    def _avg(entries, field):
        vals = [e[field] for e in entries if e.get(field) is not None]
        return sum(vals) / len(vals) if vals else None

    reading_entries  = [n for n in latest if n.get("Domain_Desc") == "Reading"]
    numeracy_entries = [n for n in latest if n.get("Domain_Desc") == "Numeracy"]

    school_reading  = _avg(reading_entries,  "SchoolAvg")
    school_numeracy = _avg(numeracy_entries, "SchoolAvg")
    natl_reading    = _avg(reading_entries,  "NationalAvg")
    natl_numeracy   = _avg(numeracy_entries, "NationalAvg")

    def _compare(school, natl, threshold=10):
        if school is None or natl is None:
            return None
        if school > natl + threshold:
            return "Above Average"
        if school < natl - threshold:
            return "Below Average"
        return "Average"

    r_label = _compare(school_reading,  natl_reading)
    n_label = _compare(school_numeracy, natl_numeracy)

    # Combined label: both must agree for Above/Below; any disagreement → Average
    if r_label and n_label:
        if r_label == n_label:
            combined = r_label
        elif "Below Average" in (r_label, n_label) and "Above Average" in (r_label, n_label):
            combined = "Average"   # one up one down
        elif r_label == "Above Average" or n_label == "Above Average":
            combined = "Above Average"  # one above, other average
        elif r_label == "Below Average" or n_label == "Below Average":
            combined = "Below Average"  # one below, other average
        else:
            combined = "Average"
    else:
        combined = r_label or n_label  # use whichever is available

    info = resp.get("SchoolInfo", {})
    return {
        "acara_id":          info.get("SML_ID"),
        "naplan_year":       latest_year,
        "naplan_performance": combined,
    }


async def _fetch_naplan_batch(school_names: list[str]) -> dict[str, dict | None]:
    """
    Open ONE headless browser, solve Turnstile once, then for each school:
    1. Navigate to the school search page — the React app fires its own XHR to
       the search API, which we intercept via expect_response().
    2. Navigate to the school's NAPLAN results page — the React component fires
       its own XHR, which we intercept the same way.

    Using browser navigation + response interception avoids the Cloudflare WAF
    blocks that occur when calling these endpoints via in-page fetch() directly.
    """
    results: dict[str, dict | None] = {n: None for n in school_names}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-AU",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        # Warmup on the NAPLAN landing page to solve the Turnstile challenge once.
        try:
            await page.goto(
                f"{_BASE_URL}/school/naplan/results",
                wait_until="load",
                timeout=45_000,
            )
            await page.wait_for_timeout(6_000)
        except Exception as e:
            print(f"  ⚠️  myschool warmup failed: {e}")
            await browser.close()
            return results

        for name in school_names:
            try:
                # Step 1 — navigate to search results page; capture the school's
                # ACARA ID from the React app's own XHR call to the search API.
                acara_id = None
                search_url = (
                    f"{_BASE_URL}/school/search"
                    f"?SchoolSearchQuery={urllib.parse.quote_plus(name)}"
                )
                try:
                    async with page.expect_response(
                        lambda r: "searchschools" in r.url and r.ok,
                        timeout=20_000,
                    ) as search_ctx:
                        await page.goto(
                            search_url, wait_until="domcontentloaded", timeout=30_000
                        )
                    search_resp = await search_ctx.value
                    search_data = await search_resp.json()
                    data_list = search_data.get("data") or []
                    if data_list:
                        acara_id = data_list[0].get("schoolId")
                except Exception:
                    pass

                if not acara_id:
                    print(f"  ⚠️  myschool: no ACARA ID for '{name}'")
                    continue

                # Step 2 — navigate to the NAPLAN results page; capture the JSON
                # that the React NAPLAN component fetches on page load.
                naplan_data = None
                naplan_url = f"{_BASE_URL}/school/naplan/results?sml_id={acara_id}"
                try:
                    async with page.expect_response(
                        lambda r: "result_naplan" in r.url and r.ok,
                        timeout=25_000,
                    ) as naplan_ctx:
                        await page.goto(
                            naplan_url, wait_until="domcontentloaded", timeout=45_000
                        )
                    naplan_resp = await naplan_ctx.value
                    naplan_data = await naplan_resp.json()
                except Exception:
                    pass

                if naplan_data:
                    parsed = _parse_naplan(naplan_data)
                    results[name] = parsed
                    perf = (parsed or {}).get("naplan_performance", "—")
                    year = (parsed or {}).get("naplan_year", "?")
                    print(f"  ✅ myschool NAPLAN: {name} → {perf} ({year})")
                else:
                    print(f"  ⚠️  myschool: no NAPLAN data for '{name}' (ACARA {acara_id})")

            except Exception as e:
                print(f"  ⚠️  myschool NAPLAN fetch failed for '{name}': {e}")

        await browser.close()

    return results


def get_naplan_for_schools(school_names: list[str]) -> dict[str, dict | None]:
    """
    Synchronous entry point. Fetch NAPLAN performance labels for a list of
    school names. Results are cached for 24 h.

    Returns a dict: school_name → {naplan_performance, naplan_year, acara_id} | None
    """
    if not _PLAYWRIGHT_AVAILABLE:
        print("  ⚠️  myschool: playwright not installed — NAPLAN unavailable")
        return {n: None for n in school_names}

    global _cache, _cache_ts
    now = time.time()

    # Invalidate cache if older than TTL
    if (now - _cache_ts) > _CACHE_TTL:
        _cache.clear()
        _cache_ts = now

    to_fetch = [n for n in school_names if n not in _cache]
    if to_fetch:
        try:
            new_data = asyncio.run(_fetch_naplan_batch(to_fetch))
            _cache.update(new_data)
        except Exception as e:
            print(f"  ⚠️  myschool batch fetch error: {e}")
            for n in to_fetch:
                _cache.setdefault(n, None)

    return {n: _cache.get(n) for n in school_names}
