# backend/services/sku_master_service.py ---> 📋 Master SKU list builder (mapper source only)
#
# Responsibility: build and read sku_master_full.csv — the full budgeted SKU
# universe (real ItemCodes from Budget.xlsx + synthetic codes generated for
# products that have no ItemCode there).
#
# NO forecast-joining logic here. Merging the master list with model/trend/
# horizon forecasts lives in engines/master_forecast_engine.py (pure pandas,
# no file I/O), wired together by engines/forecast_orchestrator.py. This file
# only ever produces or reads sku_master_full.csv and looks up display fields
# (product name / agency) for a given code.

import os
import re
from typing import Optional

import pandas as pd

from services.forecast_service import RAW_DATA_DIR, OUTPUT_DIR

# ============================================================
# FILE PATHS
# ============================================================
BUDGET_XLSX_PATH = os.path.join(RAW_DATA_DIR, "Master Data", "Budget.xlsx")
BUDGET_SHEET_NAME = "All Budget 26 27 FY"

AGENCY_MAP_XLSX_PATH = os.path.join(RAW_DATA_DIR, "Master Data", "Agency map.xlsx")
AGENCY_MAP_SHEET_NAME = "Sheet1"

SKU_MASTER_OUTPUT_PATH = os.path.join(OUTPUT_DIR, "sku_master_full.csv")


# ============================================================
# HELPERS
# ============================================================
def _slugify(text: str) -> str:
    """Turn a product name into a short, stable, code-safe token."""
    text = str(text or "").strip().upper()
    text = re.sub(r"[^A-Z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:40] if text else "UNKNOWN_PRODUCT"


def _normalize_agency_name(name: str) -> str:
    return str(name or "").strip().upper()


# ============================================================
# AGENCY MAP (AgencyName -> AgencyCode)
#
# Agency map.xlsx is a PRODUCT-to-agency table: Code, Name, AgencyCode,
# AgencyName — "Code"/"Name" refer to a (different, already-coded) product,
# not the agency itself. Many rows share the same AgencyName/AgencyCode
# pair, so we dedupe on AgencyName and take the first AgencyCode seen.
# ============================================================
def load_agency_code_map() -> dict:
    if not os.path.exists(AGENCY_MAP_XLSX_PATH):
        return {}

    df = pd.read_excel(AGENCY_MAP_XLSX_PATH, sheet_name=AGENCY_MAP_SHEET_NAME)
    df.columns = df.columns.astype(str).str.strip()

    required = ["AgencyCode", "AgencyName"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"Agency map.xlsx missing columns: {missing}. Found: {list(df.columns)}"
        )

    df = df.dropna(subset=["AgencyName"]).copy()
    df["AgencyName_Key"] = df["AgencyName"].apply(_normalize_agency_name)
    df["AgencyCode"] = df["AgencyCode"].astype(str).str.strip()

    agency_map = (
        df.drop_duplicates(subset=["AgencyName_Key"], keep="first")
        .set_index("AgencyName_Key")["AgencyCode"]
        .to_dict()
    )
    return agency_map


# ============================================================
# BUDGET SKU ROWS (ALL rows, including ones with no ItemCode)
# ============================================================
def load_budget_sku_rows() -> pd.DataFrame:
    if not os.path.exists(BUDGET_XLSX_PATH):
        raise FileNotFoundError(f"Budget.xlsx not found at: {BUDGET_XLSX_PATH}")

    df = pd.read_excel(BUDGET_XLSX_PATH, sheet_name=BUDGET_SHEET_NAME, header=0)
    df.columns = df.columns.astype(str).str.strip()

    required = ["Agency", "ItemCode", "ItemName"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"Budget sheet '{BUDGET_SHEET_NAME}' missing columns: {missing}. "
            f"Found: {list(df.columns)}"
        )

    # Agency is often only populated on the first row of each agency's block
    df["Agency"] = df["Agency"].ffill().astype(str).str.strip()
    df["ItemName"] = df["ItemName"].astype(str).str.strip()

    df = df.dropna(subset=["ItemName"])
    df = df[df["ItemName"] != ""].copy()

    return df[["Agency", "ItemCode", "ItemName"]].copy()


# ============================================================
# BUILD FULL SKU MASTER (real codes + synthetic codes where missing)
# ============================================================
def build_sku_master(save: bool = True) -> pd.DataFrame:
    """
    Full budgeted SKU list with columns:
        ProductCode, ProductName, Agency, AgencyCode, Is_Synthetic_Code

    Rows WITH a real numeric ItemCode:
        ProductCode = canonical int-string of ItemCode
        Is_Synthetic_Code = 0

    Rows WITHOUT a numeric ItemCode:
        AgencyCode looked up from Agency map.xlsx via AgencyName.
        ProductCode = "SYN-{AgencyCode}-{slug(ItemName)}"
        If the agency isn't found in the map, the agency NAME is used
        instead of the code: "SYN-{AGENCY_NAME}-{slug(ItemName)}"
        Is_Synthetic_Code = 1
    """
    budget_df = load_budget_sku_rows()
    agency_code_map = load_agency_code_map()

    numeric_code = pd.to_numeric(budget_df["ItemCode"], errors="coerce")

    rows = []
    for agency, item_name, num_code in zip(
        budget_df["Agency"], budget_df["ItemName"], numeric_code
    ):
        agency_key = _normalize_agency_name(agency)
        agency_code = agency_code_map.get(agency_key)

        if pd.notna(num_code):
            product_code = str(int(num_code))
            is_synthetic = 0
        else:
            agency_token = agency_code if agency_code else _normalize_agency_name(agency)
            product_code = f"SYN-{agency_token}-{_slugify(item_name)}"
            is_synthetic = 1

        rows.append({
            "ProductCode": product_code,
            "ProductName": item_name,
            "Agency": agency,
            "AgencyCode": agency_code if agency_code else "",
            "Is_Synthetic_Code": is_synthetic,
        })

    sku_master_df = pd.DataFrame(rows)

    # De-dupe exact repeats (same product/agency appearing on multiple rows)
    sku_master_df = (
        sku_master_df
        .drop_duplicates(subset=["ProductCode", "ProductName", "Agency"], keep="first")
        .sort_values(["Agency", "ProductName"])
        .reset_index(drop=True)
    )

    if save:
        os.makedirs(os.path.dirname(SKU_MASTER_OUTPUT_PATH), exist_ok=True)
        sku_master_df.to_csv(SKU_MASTER_OUTPUT_PATH, index=False)

    return sku_master_df


def load_sku_master_full() -> pd.DataFrame:
    """
    Load sku_master_full.csv as a DataFrame, building it if missing.
    This is the DataFrame form consumed by engines/master_forecast_engine.py
    as the join anchor (columns kept as strings — ItemCode-style joins
    should never go through float coercion).
    """
    if not os.path.exists(SKU_MASTER_OUTPUT_PATH):
        return build_sku_master(save=True)
    return pd.read_csv(SKU_MASTER_OUTPUT_PATH, dtype=str)


# ============================================================
# DISPLAY LOOKUP (cached) — used by forecast_orchestrator to attach
# product_name / agency to dashboard responses without touching the
# forecast pipeline itself.
# ============================================================
_sku_master_cache = None


def load_sku_master_lookup(force_reload: bool = False) -> dict:
    """ProductCode -> {ProductName, Agency, AgencyCode}, cached in memory."""
    global _sku_master_cache
    if _sku_master_cache is not None and not force_reload:
        return _sku_master_cache

    if not os.path.exists(SKU_MASTER_OUTPUT_PATH) or force_reload:
        build_sku_master(save=True)

    df = pd.read_csv(SKU_MASTER_OUTPUT_PATH, dtype=str)
    _sku_master_cache = df.set_index("ProductCode")[
        ["ProductName", "Agency", "AgencyCode"]
    ].to_dict("index")
    return _sku_master_cache


def get_sku_display_info(item_code: str) -> dict:
    item_code = str(item_code).strip().replace(".0", "")
    info = load_sku_master_lookup().get(item_code)
    if info is None:
        return {"product_name": None, "agency": None, "agency_code": None}
    return {
        "product_name": info.get("ProductName"),
        "agency": info.get("Agency"),
        "agency_code": info.get("AgencyCode"),
    }


def get_sku_master_summary(sku_master_df: Optional[pd.DataFrame] = None) -> dict:
    if sku_master_df is None:
        sku_master_df = build_sku_master(save=False)

    is_synth = sku_master_df["Is_Synthetic_Code"].astype(str)
    agency_code = sku_master_df["AgencyCode"].fillna("").astype(str)

    return {
        "total_skus": int(len(sku_master_df)),
        "with_real_code": int((is_synth == "0").sum()),
        "with_synthetic_code": int((is_synth == "1").sum()),
        "agencies_unmatched": int(((is_synth == "1") & (agency_code == "")).sum()),
        "output_path": SKU_MASTER_OUTPUT_PATH,
    }


if __name__ == "__main__":
    df = build_sku_master(save=True)
    print(get_sku_master_summary(df))