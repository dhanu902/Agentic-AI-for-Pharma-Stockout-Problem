# backend/engines/inventory_engine.py
# ------------------------------------------------------------
# Build SKU-level inventory risk snapshot from Inventory.xlsx
#
# Source workbook:
#   project_root/data/Inventory.xlsx
#
# Sheets:
#   - DB : distributor batch stock
#   - WH : warehouse / primary batch stock
#
# Output:
#   backend/data/processed/inventory_risk_snapshot.csv
# ------------------------------------------------------------

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

import pandas as pd
from pandas.tseries.offsets import DateOffset, MonthEnd


# ============================================================
# PATHS
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # backend/
PROJECT_ROOT = os.path.dirname(BASE_DIR)                                 # project root

RAW_DATA_DIR = os.path.join(PROJECT_ROOT, "data")
BACKEND_DATA_DIR = os.path.join(BASE_DIR, "data")
PROCESSED_DIR = os.path.join(BACKEND_DATA_DIR, "processed")

os.makedirs(PROCESSED_DIR, exist_ok=True)

INVENTORY_XLSX_PATH = os.path.join(RAW_DATA_DIR, "Inventory.xlsx")
INVENTORY_SNAPSHOT_PATH = os.path.join(PROCESSED_DIR, "inventory_risk_snapshot.csv")


# ============================================================
# HELPERS
# ============================================================
def normalize_itemcode(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )


def safe_numeric(series, default=0.0) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(default)


def safe_datetime(series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def month_label(year: int, month: int) -> str:
    return f"{int(year):04d}-{int(month):02d}"


def next_year_month(year: int, month: int) -> tuple[int, int]:
    year = int(year)
    month = int(month)
    if month == 12:
        return year + 1, 1
    return year, month + 1


def forecast_cutoff_date(forecast_year: int, forecast_month: int) -> pd.Timestamp:
    """
    Business rule:
    Cutoff = end of (forecast month + 2 months)

    Example:
    forecast month = 2026-02
    cutoff = 2026-04-30
    """
    start = pd.Timestamp(year=int(forecast_year), month=int(forecast_month), day=1)
    return start + DateOffset(months=2) + MonthEnd(0)


def get_inventory_base_month(db_df: pd.DataFrame, wh_df: pd.DataFrame) -> str:
    today = pd.Timestamp(datetime.now().date())
    max_allowed = today + pd.DateOffset(days=7)

    dates = []

    if "Date" in db_df.columns:
        dates.append(pd.to_datetime(db_df["Date"], errors="coerce"))

    if "CreationDate" in wh_df.columns:
        dates.append(pd.to_datetime(wh_df["CreationDate"], errors="coerce"))

    if not dates:
        raise ValueError("Inventory.xlsx must contain DB.Date or WH.CreationDate.")

    all_dates = pd.concat(dates).dropna()

    # remove impossible future dates like 2051 / 2036
    all_dates = all_dates[all_dates <= max_allowed]

    if all_dates.empty:
        raise ValueError("No valid inventory dates after filtering future values.")

    latest_date = all_dates.max()
    return f"{latest_date.year:04d}-{latest_date.month:02d}"


def classify_expiry_bucket(
    expiry_date: pd.Series,
    run_date: pd.Timestamp,
    cutoff_date: pd.Timestamp,
) -> pd.Series:
    """
    Classification:
    - EXPIRED    : expiry < run_date
    - SHORT_EXP  : run_date <= expiry <= cutoff_date
    - NO_RISK    : expiry > cutoff_date
    """
    expiry_date = safe_datetime(expiry_date)

    out = pd.Series("NO_RISK", index=expiry_date.index, dtype="object")
    out.loc[expiry_date.isna()] = "UNKNOWN"
    out.loc[expiry_date < run_date] = "EXPIRED"
    out.loc[(expiry_date >= run_date) & (expiry_date <= cutoff_date)] = "SHORT_EXP"
    out.loc[expiry_date > cutoff_date] = "NO_RISK"

    return out


def _pick_quantity_column(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _pick_date_column(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    for col in candidates:
        if col in df.columns:
            return col
    return None


# ============================================================
# LOADERS
# ============================================================
def load_inventory_sheet(sheet_name: str) -> pd.DataFrame:
    if not os.path.exists(INVENTORY_XLSX_PATH):
        raise FileNotFoundError(f"Inventory workbook not found: {INVENTORY_XLSX_PATH}")

    return pd.read_excel(INVENTORY_XLSX_PATH, sheet_name=sheet_name)


def get_forecast_context_from_runtime(
    runtime_df: Optional[pd.DataFrame] = None,
) -> tuple[pd.Timestamp, int, int, str]:
    """
    Decide forecast month based on runtime merged/actual/live data.

    If latest runtime month is 2026-01,
    forecast month becomes 2026-02.
    """
    run_date = pd.Timestamp(datetime.now().date())

    if runtime_df is None or runtime_df.empty:
        raise ValueError("runtime_df is required to determine forecast month context.")

    needed = {"Year", "Month_Number"}
    if not needed.issubset(runtime_df.columns):
        raise ValueError("runtime_df must contain Year and Month_Number.")

    latest = (
        runtime_df[["Year", "Month_Number"]]
        .dropna()
        .sort_values(["Year", "Month_Number"])
        .iloc[-1]
    )

    latest_year = int(latest["Year"])
    latest_month = int(latest["Month_Number"])

    forecast_year, forecast_month = next_year_month(latest_year, latest_month)
    forecast_month_label = month_label(forecast_year, forecast_month)

    return run_date, forecast_year, forecast_month, forecast_month_label


# ============================================================
# DISTRIBUTOR (DB SHEET)
# ============================================================
def build_distributor_inventory_snapshot(
    db_df: pd.DataFrame,
    run_date: pd.Timestamp,
    cutoff_date: pd.Timestamp,
) -> pd.DataFrame:
    """
    DB sheet expected columns:
    - ProductCode
    - UnitQty
    - ItemExpiryDate

    Output per SKU:
    - Distributor_Total_Qty
    - Distributor_Expired_Qty
    - Distributor_ShortExp_Qty
    - Distributor_NoRisk_Qty
    - Distributor_Trade_Qty
    """
    df = db_df.copy()

    if "ProductCode" not in df.columns:
        raise KeyError("DB sheet must contain 'ProductCode'.")

    qty_col = _pick_quantity_column(df, ["UnitQty", "Qty", "Quantity", "StockQty"])
    if qty_col is None:
        raise KeyError("DB sheet must contain a usable quantity column like 'UnitQty'.")

    expiry_col = _pick_date_column(df, ["ItemExpiryDate", "ExpiryDate", "ExpDate"])
    if expiry_col is None:
        raise KeyError("DB sheet must contain 'ItemExpiryDate' or equivalent.")

    df["ItemCode"] = normalize_itemcode(df["ProductCode"])
    df["UnitQty"] = safe_numeric(df[qty_col], 0.0).clip(lower=0)
    df["ItemExpiryDate"] = safe_datetime(df[expiry_col])
    df["Expiry_Bucket"] = classify_expiry_bucket(df["ItemExpiryDate"], run_date, cutoff_date)

    grouped = []
    for item_code, g in df.groupby("ItemCode", dropna=False):
        total_qty = float(g["UnitQty"].sum())
        expired_qty = float(g.loc[g["Expiry_Bucket"] == "EXPIRED", "UnitQty"].sum())
        short_qty = float(g.loc[g["Expiry_Bucket"] == "SHORT_EXP", "UnitQty"].sum())
        no_risk_qty = float(g.loc[g["Expiry_Bucket"] == "NO_RISK", "UnitQty"].sum())
        trade_qty = no_risk_qty + short_qty

        grouped.append({
            "ItemCode": str(item_code),
            "Distributor_Total_Qty": total_qty,
            "Distributor_Expired_Qty": expired_qty,
            "Distributor_ShortExp_Qty": short_qty,
            "Distributor_NoRisk_Qty": no_risk_qty,
            "Distributor_Trade_Qty": trade_qty,
        })

    out = pd.DataFrame(grouped)
    if out.empty:
        out = pd.DataFrame(columns=[
            "ItemCode",
            "Distributor_Total_Qty",
            "Distributor_Expired_Qty",
            "Distributor_ShortExp_Qty",
            "Distributor_NoRisk_Qty",
            "Distributor_Trade_Qty",
        ])

    return out


# ============================================================
# WAREHOUSE (WH SHEET)
# ============================================================
def build_warehouse_inventory_snapshot(
    wh_df: pd.DataFrame,
    run_date: pd.Timestamp,
    cutoff_date: pd.Timestamp,
) -> pd.DataFrame:
    """
    WH sheet expected columns:
    - ItemCode
    - ItemExpiryDate
    - Trade Qty
    - Blocked
    - Insp

    Trade Qty = primary trade stock.
    Primary_Trade_Qty = Primary_NoRisk_Qty + Primary_ShortExp_Qty
    """

    df = wh_df.copy()

    if "ItemCode" not in df.columns:
        raise KeyError("WH sheet must contain 'ItemCode'.")

    if "Trade Qty" not in df.columns:
        raise KeyError("WH sheet must contain 'Trade Qty'.")

    expiry_col = _pick_date_column(df, ["ItemExpiryDate", "ExpiryDate", "ExpDate"])
    if expiry_col is None:
        raise KeyError("WH sheet must contain 'ItemExpiryDate' or equivalent.")

    df["ItemCode"] = normalize_itemcode(df["ItemCode"])
    df["ItemExpiryDate"] = safe_datetime(df[expiry_col])

    df["Trade_Qty"] = safe_numeric(df["Trade Qty"], 0.0).clip(lower=0)
    df["Blocked_Qty"] = safe_numeric(df.get("Blocked", 0), 0.0).clip(lower=0)
    df["Insp_Qty"] = safe_numeric(df.get("Insp", 0), 0.0).clip(lower=0)

    df["Expiry_Bucket"] = classify_expiry_bucket(
        df["ItemExpiryDate"],
        run_date,
        cutoff_date
    )

    df["Primary_NoRisk_Qty_Row"] = 0.0
    df["Primary_ShortExp_Qty_Row"] = 0.0
    df["Primary_Expired_Qty_Row"] = 0.0

    df.loc[df["Expiry_Bucket"] == "NO_RISK", "Primary_NoRisk_Qty_Row"] = df["Trade_Qty"]
    df.loc[df["Expiry_Bucket"] == "SHORT_EXP", "Primary_ShortExp_Qty_Row"] = df["Trade_Qty"]
    df.loc[df["Expiry_Bucket"] == "EXPIRED", "Primary_Expired_Qty_Row"] = df["Trade_Qty"]

    grouped = []

    for item_code, g in df.groupby("ItemCode", dropna=False):
        primary_no_risk = float(g["Primary_NoRisk_Qty_Row"].sum())
        primary_short = float(g["Primary_ShortExp_Qty_Row"].sum())
        primary_expired = float(g["Primary_Expired_Qty_Row"].sum())
        primary_trade = primary_no_risk + primary_short

        blocked_qty = float(g["Blocked_Qty"].sum())
        insp_qty = float(g["Insp_Qty"].sum())

        total_qty = primary_trade + primary_expired + blocked_qty + insp_qty

        grouped.append({
            "ItemCode": str(item_code),
            "Primary_Total_Qty": total_qty,
            "Primary_Expired_Qty": primary_expired,
            "Primary_ShortExp_Qty": primary_short,
            "Primary_NoRisk_Qty": primary_no_risk,
            "Primary_Trade_Qty": primary_trade,
            "Blocked_Stock_Qty": blocked_qty,
            "Inspection_Stock_Qty": insp_qty,
        })

    return pd.DataFrame(grouped)


# ============================================================
# MAIN BUILD
# ============================================================
def build_inventory_risk_snapshot(
    runtime_df: Optional[pd.DataFrame] = None,
    forecast_year: Optional[int] = None,
    forecast_month: Optional[int] = None,
    save: bool = True,
) -> pd.DataFrame:
    """
    Build merged SKU-level inventory risk snapshot from Inventory.xlsx.

    Returns DataFrame with:
    - forecast context
    - distributor stock buckets
    - warehouse stock buckets
    """
    run_date = pd.Timestamp(datetime.now().date())

    if forecast_year is not None and forecast_month is not None:
        forecast_year = int(forecast_year)
        forecast_month = int(forecast_month)
        forecast_month_label = month_label(forecast_year, forecast_month)
    else:
        run_date, forecast_year, forecast_month, forecast_month_label = get_forecast_context_from_runtime(runtime_df)

    cutoff_date = forecast_cutoff_date(forecast_year, forecast_month)

    db_df = load_inventory_sheet("DB")
    wh_df = load_inventory_sheet("WH")

    base_month_label = get_inventory_base_month(db_df, wh_df)

    dist_df = build_distributor_inventory_snapshot(
        db_df=db_df,
        run_date=run_date,
        cutoff_date=cutoff_date,
    )

    wh_out_df = build_warehouse_inventory_snapshot(
        wh_df=wh_df,
        run_date=run_date,
        cutoff_date=cutoff_date,
    )

    merged = pd.merge(
        dist_df,
        wh_out_df,
        on="ItemCode",
        how="outer",
    )

    if merged.empty:
        merged = pd.DataFrame(columns=["ItemCode"])

    merged["Run_Date"] = str(run_date.date())
    merged["Base_Month"] = base_month_label
    merged["Forecast_Year"] = int(forecast_year)
    merged["Forecast_Month_Number"] = int(forecast_month)
    merged["Forecast_Month"] = forecast_month_label
    merged["Cutoff_Date"] = str(cutoff_date.date())

    numeric_cols = [c for c in merged.columns if c not in ["ItemCode", "Run_Date", "Base_Month", "Forecast_Month", "Cutoff_Date"]]
    merged[numeric_cols] = merged[numeric_cols].apply(pd.to_numeric, errors="coerce").fillna(0)

    # Recalculate trade buckets after merge to guarantee correctness
    merged["Distributor_Trade_Qty"] = (merged["Distributor_NoRisk_Qty"] + merged["Distributor_ShortExp_Qty"])
    merged["Primary_Trade_Qty"] = (merged["Primary_NoRisk_Qty"] + merged["Primary_ShortExp_Qty"])

    ordered_cols = [
        "Run_Date",
        "Base_Month",
        "Forecast_Year",
        "Forecast_Month_Number",
        "Forecast_Month",
        "Cutoff_Date",
        "ItemCode",

        "Distributor_Total_Qty",
        "Distributor_NoRisk_Qty",
        "Distributor_ShortExp_Qty",
        "Distributor_Expired_Qty",
        "Distributor_Trade_Qty",

        "Primary_Total_Qty",
        "Primary_NoRisk_Qty",
        "Primary_ShortExp_Qty",
        "Primary_Expired_Qty",
        "Primary_Trade_Qty",

        "Inspection_Stock_Qty",
        "Blocked_Stock_Qty",
    ]

    for col in ordered_cols:
        if col not in merged.columns:
            merged[col] = 0

    merged = merged[ordered_cols].sort_values(["ItemCode"]).reset_index(drop=True)

    if save:
        merged.to_csv(INVENTORY_SNAPSHOT_PATH, index=False)

    return merged


def load_inventory_risk_snapshot() -> pd.DataFrame:
    if not os.path.exists(INVENTORY_SNAPSHOT_PATH):
        raise FileNotFoundError(f"Inventory risk snapshot not found: {INVENTORY_SNAPSHOT_PATH}")
    return pd.read_csv(INVENTORY_SNAPSHOT_PATH)


def get_inventory_snapshot_path() -> str:
    return INVENTORY_SNAPSHOT_PATH