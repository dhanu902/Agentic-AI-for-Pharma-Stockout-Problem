# backend/services/risk_service.py ---> 📂 File I/O for M+1 risk
#
# Responsibility:
#   - Define risk/inventory file paths
#   - Load Inventory.xlsx DB/WH sheets
#   - Load saved inventory/risk outputs
#   - Append risk run logs
#   - Provide risk_latest.csv results to Forecast page and Inventory page
#
# NO orchestration here.
# Pipeline sequencing lives in engines/risk_orchestrator.py.
# Risk calculations live in engines/risk_engine.py.

import os
import pandas as pd

from services.forecast_service import (
    OUTPUT_DIR,
    LOG_DIR,
    BACKEND_DATA_DIR,
)

# ============================================================
# PATHS
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(BACKEND_DATA_DIR))
RAW_DATA_DIR = os.path.join(PROJECT_ROOT, "data")

INVENTORY_XLSX_PATH = os.path.join(RAW_DATA_DIR, "Inventory.xlsx")

PROCESSED_DIR = os.path.join(BACKEND_DATA_DIR, "processed")
os.makedirs(PROCESSED_DIR, exist_ok=True)

INVENTORY_SNAPSHOT_PATH = os.path.join(PROCESSED_DIR, "inventory_risk_snapshot.csv")
BASE_SNAPSHOT_PATH = os.path.join(OUTPUT_DIR, "risk_base_snapshot.csv")
RISK_LATEST_PATH = os.path.join(OUTPUT_DIR, "risk_latest.csv")
RISK_RUN_LOG_PATH = os.path.join(LOG_DIR, "risk_run_log.csv")


# ============================================================
# RAW LOADERS
# ============================================================
def load_inventory_workbook() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load the raw Inventory.xlsx workbook.

    Returns (db_df, wh_df) — DB sheet (distributor batches) and WH sheet
    (warehouse/primary batches), untouched. All normalization and
    expiry-bucket classification happens in risk_engine.
    """
    if not os.path.exists(INVENTORY_XLSX_PATH):
        raise FileNotFoundError(f"Inventory workbook not found: {INVENTORY_XLSX_PATH}")
    db_df = pd.read_excel(INVENTORY_XLSX_PATH, sheet_name="DB")
    wh_df = pd.read_excel(INVENTORY_XLSX_PATH, sheet_name="WH")
    return db_df, wh_df


# ============================================================
# HELPERS
# ============================================================
def _normalize_itemcode(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )

def _next_month_label(label: str) -> str:
    year, month = map(int, label.split("-"))
    if month == 12:
        return f"{year + 1}-01"
    return f"{year}-{month + 1:02d}"

def validate_inventory_forecast_alignment(
    base_df: pd.DataFrame,
    forecast_df: pd.DataFrame,
) -> dict:
    if "Base_Month" not in base_df.columns:
        raise ValueError("Inventory snapshot missing Base_Month column.")
    if "Forecast_Month" not in forecast_df.columns:
        raise ValueError("forecast_latest.csv missing Forecast_Month column.")

    base_months = sorted(base_df["Base_Month"].dropna().astype(str).unique().tolist())
    forecast_months = sorted(forecast_df["Forecast_Month"].dropna().astype(str).unique().tolist())

    if len(base_months) != 1:
        raise ValueError(f"Inventory snapshot must contain exactly one Base_Month. Found: {base_months}")
    if len(forecast_months) != 1:
        raise ValueError(f"Forecast file must contain exactly one Forecast_Month. Found: {forecast_months}")

    base_month = base_months[0]
    forecast_month = forecast_months[0]
    expected_forecast_month = base_month

    if forecast_month != expected_forecast_month:
        raise ValueError(
            f"Month mismatch blocked: inventory Base_Month={base_month}, "
            f"forecast Forecast_Month={forecast_month}. "
            f"Expected Forecast_Month={expected_forecast_month} because inventory "
            f"Month represents opening stock for the same forecast month."
        )

    return {
        "base_month": base_month,
        "forecast_month": forecast_month,
        "expected_forecast_month": expected_forecast_month,
    }

def append_risk_run_log(row: dict) -> str:
    log_df = pd.DataFrame([row])
    if os.path.exists(RISK_RUN_LOG_PATH):
        existing = pd.read_csv(RISK_RUN_LOG_PATH)
        log_df = pd.concat([existing, log_df], ignore_index=True)
    log_df.to_csv(RISK_RUN_LOG_PATH, index=False)
    return RISK_RUN_LOG_PATH


def save_inventory_snapshot(df: pd.DataFrame) -> str:
    df.to_csv(INVENTORY_SNAPSHOT_PATH, index=False)
    return INVENTORY_SNAPSHOT_PATH

def save_risk_base_snapshot(df: pd.DataFrame) -> str:
    df.to_csv(BASE_SNAPSHOT_PATH, index=False)
    return BASE_SNAPSHOT_PATH

def save_risk_latest(df: pd.DataFrame) -> str:
    df.to_csv(RISK_LATEST_PATH, index=False)
    return RISK_LATEST_PATH


def load_inventory_snapshot() -> pd.DataFrame:
    if not os.path.exists(INVENTORY_SNAPSHOT_PATH):
        raise FileNotFoundError(f"Inventory risk snapshot not found: {INVENTORY_SNAPSHOT_PATH}")
    return pd.read_csv(INVENTORY_SNAPSHOT_PATH)

def load_risk_base_snapshot() -> pd.DataFrame:
    if not os.path.exists(BASE_SNAPSHOT_PATH):
        raise FileNotFoundError(f"Risk base snapshot not found: {BASE_SNAPSHOT_PATH}")
    return pd.read_csv(BASE_SNAPSHOT_PATH)

def load_risk_latest() -> pd.DataFrame:
    if not os.path.exists(RISK_LATEST_PATH):
        return pd.DataFrame()
    return pd.read_csv(RISK_LATEST_PATH)


def get_risk_results() -> pd.DataFrame:
    return load_risk_latest()


# ============================================================
# AGENCY-WISE INVENTORY PROJECTION (business change 5)
#
# The Inventory page moves from ITEM-wise to AGENCY-wise. The per-item
# risk logic (risk_engine scenarios A/B/C, Risk_Level classification)
# is UNCHANGED — this only aggregates the already-computed
# risk_latest.csv rows per agency:
#   quantities (forecast, stock buckets, scenario unmet/used) -> SUMMED
#   A/B/C met  -> True only when EVERY item of the agency met it
#                 (equivalently: summed unmet == 0)
#   Risk_Level -> worst case among the agency's items, with per-level
#                 counts kept so the UI can show the mix
# Agency mapping comes from the master SKU list (sku_master_full.csv).
# ============================================================
_RISK_SEVERITY = {
    "CRITICAL_STOCKOUT": 6,
    "WH_BLOCKED_REQUIRED": 5,
    "WH_INSPECTION_REQUIRED": 4,
    "WH_TRADE_REQUIRED": 3,
    "UNDER_RISK": 2,
    "SAFE": 1,
    # data-completeness states — only win when the agency has NO real verdict
    "NO_FORECAST_DATA": 0,
    "NO_INVENTORY_DATA": 0,
    "NO_DATA": 0,
    "NOT_TRACKED": 0,
}

# Quantity columns summed across the agency's items (whatever subset exists)
_AGENCY_SUM_COLS = [
    "Forecast_Qty",
    "Distributor_NoRisk_Qty", "Distributor_ShortExp_Qty",
    "Distributor_Expired_Qty", "Distributor_Trade_Qty",
    "Primary_NoRisk_Qty", "Primary_ShortExp_Qty",
    "Primary_Expired_Qty", "Primary_Trade_Qty",
    "Inspection_Stock_Qty", "Blocked_Stock_Qty",
    "A_unmet", "A_used_db_no_risk", "A_used_db_short_exp",
    "A_used_wh_trade", "A_used_wh_insp", "A_used_wh_blocked",
    "B_unmet", "B_used_db_no_risk", "B_used_db_short_exp",
    "B_used_wh_trade", "B_used_wh_insp", "B_used_wh_blocked",
    "C_unmet", "C_used_db_no_risk", "C_used_db_short_exp",
    "C_used_wh_trade", "C_used_wh_insp", "C_used_wh_blocked",
]


def _worst_risk_level(levels: pd.Series) -> str:
    vals = [str(v) for v in levels.dropna()]
    if not vals:
        return "NO_DATA"
    return max(vals, key=lambda v: (_RISK_SEVERITY.get(v, 0), vals.count(v)))


def _attach_agency_info(df: pd.DataFrame) -> pd.DataFrame:
    """Join Agency / AgencyCode / ProductName display columns from the
    master SKU list onto item-wise risk rows. Display metadata only —
    no risk numbers are touched."""
    df = df.copy()
    df["ItemCode"] = _normalize_itemcode(df["ItemCode"])

    # Lazy import: sku_master_service sits higher in the load order.
    try:
        from services.sku_master_service import load_sku_master_full
        master = load_sku_master_full()
    except Exception:
        master = pd.DataFrame()

    if master is not None and not master.empty and "ProductCode" in master.columns:
        m = master.copy()
        m["ProductCode"] = m["ProductCode"].astype(str).str.strip()
        m = m.drop_duplicates(subset=["ProductCode"])
        df = df.merge(
            m[["ProductCode", "ProductName", "Agency", "AgencyCode"]].rename(
                columns={"ProductCode": "ItemCode"}
            ),
            on="ItemCode",
            how="left",
        )
    else:
        df["ProductName"] = None
        df["Agency"] = None
        df["AgencyCode"] = None

    df["ProductName"] = df["ProductName"].fillna("").astype(str).str.strip()
    df["Agency"] = df["Agency"].fillna("UNMAPPED").astype(str).str.strip()
    df["AgencyCode"] = df["AgencyCode"].fillna("").astype(str).str.strip()
    return df


def get_risk_results_with_agency() -> pd.DataFrame:
    """Item-wise risk_latest rows enriched with Agency / AgencyCode /
    ProductName — used by the Inventory page's SKU-wise section (filter
    SKUs within a selected agency). Risk logic unchanged."""
    df = load_risk_latest()
    if df.empty or "ItemCode" not in df.columns:
        return df
    return _attach_agency_info(df)


def get_risk_results_by_agency() -> pd.DataFrame:
    df = load_risk_latest()
    if df.empty or "ItemCode" not in df.columns:
        return df

    df = _attach_agency_info(df)

    group_keys = ["Agency"] + [c for c in ["Base_Month", "Forecast_Month"] if c in df.columns]

    sum_cols = [c for c in _AGENCY_SUM_COLS if c in df.columns]
    for c in sum_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    agg_spec = {c: (c, "sum") for c in sum_cols}
    agg_spec["SKU_Count"] = ("ItemCode", "nunique")
    agg_spec["AgencyCode"] = ("AgencyCode", "first")
    if "run_id" in df.columns:
        agg_spec["run_id"] = ("run_id", "first")

    agg = df.groupby(group_keys, as_index=False).agg(**agg_spec)

    # A/B/C met: True only when every item of the agency met the scenario
    for p in ("A", "B", "C"):
        if f"{p}_unmet" in agg.columns:
            agg[f"{p}_met"] = agg[f"{p}_unmet"] <= 0

    # Data-completeness counts (how many items lack inventory/forecast/code)
    for src, out in [
        ("Has_Inventory_Data", "No_Inventory_Item_Count"),
        ("Has_Forecast_Data", "No_Forecast_Item_Count"),
    ]:
        if src in df.columns:
            counts = (
                df.assign(_gap=(pd.to_numeric(df[src], errors="coerce").fillna(1) == 0).astype(int))
                .groupby(group_keys)["_gap"].sum().reset_index(name=out)
            )
            agg = agg.merge(counts, on=group_keys, how="left")
    if "Is_Synthetic_Code" in df.columns:
        counts = (
            df.assign(_syn=(pd.to_numeric(df["Is_Synthetic_Code"], errors="coerce").fillna(0) == 1).astype(int))
            .groupby(group_keys)["_syn"].sum().reset_index(name="Synthetic_Item_Count")
        )
        agg = agg.merge(counts, on=group_keys, how="left")

    # Worst-case Risk_Level + per-level counts (mix stays visible)
    if "Risk_Level" in df.columns:
        worst = (
            df.groupby(group_keys)["Risk_Level"]
            .apply(_worst_risk_level)
            .reset_index(name="Risk_Level")
        )
        agg = agg.merge(worst, on=group_keys, how="left")

        level_counts = (
            df.groupby(group_keys)["Risk_Level"]
            .value_counts()
            .unstack(fill_value=0)
            .add_prefix("Risk_")
            .add_suffix("_Count")
            .reset_index()
        )
        agg = agg.merge(level_counts, on=group_keys, how="left")

    return agg.sort_values("Agency").reset_index(drop=True)