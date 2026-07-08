"""
One-off ABS Census ingestion for the suburb comparator feature.

Downloads the 2021 Census General Community Profile DataPack at SAL
(Suburbs and Localities) geography for all of Australia, pulls the tables
we need (G02 medians/averages, G35 household composition, G37 tenure type),
joins them against the SAL code -> suburb name/state lookup in the geography
descriptor metadata, and writes the result to data/abs_suburb_demographics.csv
— a small, committed extract that the running app bulk-loads at startup
(see suburb_db.py), so the deployed app never depends on ABS at runtime.

Run from the PropertyReport/ directory: python ingest_abs_census.py
Requires: pip install -r requirements-ingest.txt
"""

import csv
import io
import json
import os
import re
import zipfile

import httpx
import openpyxl

CENSUS_YEAR = 2021
DATAPACK_URL = (
    "https://www.abs.gov.au/census/find-census-data/datapacks/download/"
    "2021_GCP_SAL_for_AUS_short-header.zip"
)
DATAPACK_DIR = "2021 Census GCP Suburbs and Localities for AUS"

OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "abs_suburb_demographics.csv")

# SAL code first digit -> state, matching the 2021 ASGS STE structure codes
# (verified directly against the DataPack: e.g. SAL22805 = "Windsor (Vic.)").
_SAL_PREFIX_TO_STATE = {
    "1": "NSW", "2": "VIC", "3": "QLD", "4": "SA",
    "5": "WA", "6": "TAS", "7": "NT", "8": "ACT",
}

_SUFFIX_RE = re.compile(r'\s*\([^)]*\)\s*$')  # strips " (Vic.)", " (NSW)" etc.


def _num(v):
    """ABS suppresses small cells as '..' or leaves blanks; treat both as missing."""
    if v is None:
        return None
    v = str(v).strip()
    if v in ("", "..", "-", "np", "N/A"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _download_datapack() -> bytes:
    print(f"Downloading ABS 2021 Census SAL DataPack from {DATAPACK_URL} ...")
    with httpx.stream("GET", DATAPACK_URL, timeout=300, follow_redirects=True) as r:
        r.raise_for_status()
        buf = io.BytesIO()
        for chunk in r.iter_bytes(chunk_size=1 << 20):
            buf.write(chunk)
        print(f"  downloaded {buf.tell() / 1e6:.1f} MB")
        return buf.getvalue()


def _read_csv_table(zf: zipfile.ZipFile, table_code: str) -> dict[str, dict]:
    """Read a G-table CSV into {SAL_CODE_2021: {column: value}}."""
    name = f"{DATAPACK_DIR}/2021Census_{table_code}_AUST_SAL.csv"
    with zf.open(name) as f:
        text = io.TextIOWrapper(f, encoding="utf-8-sig")
        reader = csv.DictReader(text)
        return {row["SAL_CODE_2021"]: row for row in reader}


def _read_geog_names(zf: zipfile.ZipFile) -> dict[str, str]:
    """Read {SAL_CODE_2021: raw Census_Name_2021} from the geography metadata workbook."""
    with zf.open("Metadata/2021Census_geog_desc_1st_2nd_3rd_release.xlsx") as f:
        wb = openpyxl.load_workbook(io.BytesIO(f.read()), read_only=True)
        ws = wb["2021_ASGS_Non_ABS_Structures"]
        names = {}
        for row in ws.iter_rows(values_only=True):
            if row[0] == "SAL":
                names[row[1]] = row[3]
        return names


def build_records(zip_bytes: bytes) -> list[dict]:
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    g02 = _read_csv_table(zf, "G02")
    g35 = _read_csv_table(zf, "G35")
    g37 = _read_csv_table(zf, "G37")
    names = _read_geog_names(zf)

    records = []
    for sal_code, name_raw in names.items():
        prefix = sal_code.replace("SAL", "")[:1]
        state = _SAL_PREFIX_TO_STATE.get(prefix)
        if not state:
            continue  # external territories / no-usual-address codes — not in scope

        suburb = _SUFFIX_RE.sub("", name_raw).strip()

        row02 = g02.get(sal_code, {})
        row35 = g35.get(sal_code, {})
        row37 = g37.get(sal_code, {})

        household_composition = None
        total_hh = _num(row35.get("Total_Total"))
        if total_hh:
            fam_hh = _num(row35.get("Total_FamHhold")) or 0
            nonfam_hh = _num(row35.get("Total_NonFamHhold")) or 0
            lone_person = _num(row35.get("Num_Psns_UR_1_Total")) or 0
            household_composition = {
                "family_household_pct": round(fam_hh / total_hh * 100, 1),
                "non_family_household_pct": round(nonfam_hh / total_hh * 100, 1),
                "lone_person_pct": round(lone_person / total_hh * 100, 1),
            }

        owner_pct = renter_pct = None
        total_tenure = _num(row37.get("Total_Total"))
        if total_tenure:
            owned = (_num(row37.get("O_OR_Total")) or 0) + (_num(row37.get("O_MTG_Total")) or 0)
            rented = _num(row37.get("R_Tot_Total")) or 0
            owner_pct = round(owned / total_tenure * 100, 1)
            renter_pct = round(rented / total_tenure * 100, 1)

        records.append({
            "suburb": suburb,
            "state": state,
            "sal_code": sal_code,
            "census_year": CENSUS_YEAR,
            "median_age": _num(row02.get("Median_age_persons")),
            "median_household_income_weekly": _num(row02.get("Median_tot_hhd_inc_weekly")),
            "avg_household_size": _num(row02.get("Average_household_size")),
            "owner_occupied_pct": owner_pct,
            "renter_pct": renter_pct,
            "household_composition_json": json.dumps(household_composition) if household_composition else "",
        })
    return records


def main():
    zip_bytes = _download_datapack()
    records = build_records(zip_bytes)
    print(f"Built {len(records)} SAL records")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    fieldnames = [
        "suburb", "state", "sal_code", "census_year", "median_age",
        "median_household_income_weekly", "avg_household_size",
        "owner_occupied_pct", "renter_pct", "household_composition_json",
    ]
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
