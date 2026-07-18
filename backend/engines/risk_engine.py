# backend/engines/risk_engine.py ---> 🧠 M+1 physical-stock risk core logic
#
# Responsibility: EVERYTHING needed for the M+1 (Inventory page) view —
#   - Expiry bucket classification from Inventory.xlsx (DB + WH sheets)
#   - DB (distributor) and WH (warehouse/primary) stock aggregation
#   - Scenario A/B/C stockout classification against M+1 forecast
#
# Physically available stock ONLY (DB + WH). No pending PO/GRN here —
# that belongs to horizon_inventory_engine.py (M+1..M+6).
#
# NO FILE I/O HERE. Inputs are DataFrames (raw DB/WH sheets + forecast_df)
# passed in by risk_service; outputs are DataFrames returned to the caller.

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import pandas as pd
from pandas.tseries.offsets import DateOffset, MonthEnd


# ============================================================
# BASIC HELPERS
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


def safe_float(x) -> float:
    try:
        if pd.isna(x):
            return 0.0
        return float(x)
    except Exception:
        return 0.0


def safe_value(x):
    return None if pd.isna(x) else x


def month_label(year: int, month: int) -> str:
    return f"{int(year):04d}-{int(month):02d}"


def next_year_month(year: int, month: int) -> tuple[int, int]:
    year = int(year)
    month = int(month)
    if month == 12:
        return year + 1, 1
    return year, month + 1


def now_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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
# EXPIRY CLASSIFICATION
# ============================================================
def risk_cutoff_date(base_month_start: pd.Timestamp) -> pd.Timestamp:
    """
    Cutoff = first day of base month + 3 months.
    Example: Base month 2026-02-01 -> Cutoff 2026-05-01
    """
    return base_month_start + DateOffset(months=3)


def classify_expiry_bucket(
    expiry_date: pd.Series,
    base_month_start: pd.Timestamp,
    cutoff_date: pd.Timestamp,
) -> pd.Series:
    """
    ShortExp_Date = ExpiryDate - 3 months.

    EXPIRED:
        ExpiryDate < Base_Month_Start

    SHORT_EXP:
        ShortExp_Date is between Base_Month_Start and Cutoff_Date

    NO_RISK:
        Not expired and not short-exp risk
    """
    expiry_date = safe_datetime(expiry_date)
    short_exp_date = expiry_date - DateOffset(months=3)

    out = pd.Series("NO_RISK", index=expiry_date.index, dtype="object")
    out.loc[expiry_date.isna()] = "UNKNOWN"
    out.loc[expiry_date < base_month_start] = "EXPIRED"

    out.loc[
        (expiry_date.notna()) &
        (expiry_date >= base_month_start) &
        (short_exp_date >= base_month_start) &
        (short_exp_date <= cutoff_date)
    ] = "SHORT_EXP"

    return out


def get_inventory_base_month(db_df: pd.DataFrame, wh_df: pd.DataFrame) -> str:
    """
    Base month = latest (Year-Month) found in the 'Month' column shared by
    the DB and WH sheets of Inventory.xlsx, capped at today + 7 days to
    filter out impossible future dates (e.g. data-entry errors like 2051).
    """
    today = pd.Timestamp(datetime.now().date())
    max_allowed = today + pd.DateOffset(days=7)

    dates = []
    for df in (db_df, wh_df):
        if "Month" in df.columns:
            dates.append(pd.to_datetime(df["Month"], errors="coerce"))

    if not dates:
        raise ValueError("Inventory.xlsx DB/WH sheets must contain a 'Month' column.")

    all_dates = pd.concat(dates).dropna()
    all_dates = all_dates[all_dates <= max_allowed]

    if all_dates.empty:
        raise ValueError("No valid inventory dates after filtering future values.")

    latest_date = all_dates.max()
    return f"{latest_date.year:04d}-{latest_date.month:02d}"


def get_forecast_context_from_runtime(
    runtime_df: Optional[pd.DataFrame] = None,
) -> tuple[pd.Timestamp, int, int, str]:
    """
    Decide forecast month based on runtime_df.

    Important:
        In the risk pipeline, runtime_df already contains the forecast month,
        not the training/base month.

    Example:
        forecast_latest.csv Forecast_Month = 2026-04
        runtime_df Year=2026, Month_Number=4
        output Forecast_Month = 2026-04
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

    forecast_year = int(latest["Year"])
    forecast_month = int(latest["Month_Number"])
    forecast_month_label = month_label(forecast_year, forecast_month)

    return run_date, forecast_year, forecast_month, forecast_month_label


# ============================================================
# DISTRIBUTOR (DB SHEET)
#
# DB sheet columns: Month, ItemCode, DistributorCode, UnitPrice,
#                    UnitQty, BatchCode, ItemExpiryDate
# ============================================================
def build_distributor_inventory_snapshot(
    db_df: pd.DataFrame,
    base_month_start: pd.Timestamp,
    cutoff_date: pd.Timestamp,
) -> pd.DataFrame:
    """
    Output per SKU:
        - Distributor_Total_Qty
        - Distributor_Expired_Qty
        - Distributor_ShortExp_Qty
        - Distributor_NoRisk_Qty
        - Distributor_Trade_Qty   (= NoRisk + ShortExp)
    """
    df = db_df.copy()

    if "ItemCode" not in df.columns:
        raise KeyError("DB sheet must contain 'ItemCode'.")

    qty_col = _pick_quantity_column(df, ["UnitQty", "Qty", "Quantity", "StockQty"])
    if qty_col is None:
        raise KeyError("DB sheet must contain a usable quantity column like 'UnitQty'.")

    expiry_col = _pick_date_column(df, ["ItemExpiryDate", "ExpiryDate", "ExpDate"])
    if expiry_col is None:
        raise KeyError("DB sheet must contain 'ItemExpiryDate' or equivalent.")

    df["ItemCode"] = normalize_itemcode(df["ItemCode"])
    df["UnitQty"] = safe_numeric(df[qty_col], 0.0).clip(lower=0)
    df["ItemExpiryDate"] = safe_datetime(df[expiry_col])
    df["Expiry_Bucket"] = classify_expiry_bucket(df["ItemExpiryDate"],base_month_start,cutoff_date,)
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
#
# WH sheet columns: Month, ItemCode, BatchCode, ExpiryDate,
#                    Blocked, Insp, Trade Qty
# ============================================================
def build_warehouse_inventory_snapshot(
    wh_df: pd.DataFrame,
    base_month_start: pd.Timestamp,
    cutoff_date: pd.Timestamp,
) -> pd.DataFrame:
    """
    Trade Qty = primary trade stock.
    Primary_Trade_Qty = Primary_NoRisk_Qty + Primary_ShortExp_Qty
    """
    df = wh_df.copy()

    if "ItemCode" not in df.columns:
        raise KeyError("WH sheet must contain 'ItemCode'.")
    if "Trade Qty" not in df.columns:
        raise KeyError("WH sheet must contain 'Trade Qty'.")

    expiry_col = _pick_date_column(df, ["ExpiryDate", "ItemExpiryDate", "ExpDate"])
    if expiry_col is None:
        raise KeyError("WH sheet must contain 'ExpiryDate' or equivalent.")

    df["ItemCode"] = normalize_itemcode(df["ItemCode"])
    df["ItemExpiryDate"] = safe_datetime(df[expiry_col])
    df["Trade_Qty"] = safe_numeric(df["Trade Qty"], 0.0).clip(lower=0)
    df["Blocked_Qty"] = safe_numeric(df.get("Blocked", 0), 0.0).clip(lower=0)
    df["Insp_Qty"] = safe_numeric(df.get("Insp", 0), 0.0).clip(lower=0)
    df["Expiry_Bucket"] = classify_expiry_bucket(df["ItemExpiryDate"], base_month_start, cutoff_date,)

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
# M+1 SNAPSHOT BUILD (physical stock only)
# ============================================================
def build_inventory_risk_snapshot(
    db_df: pd.DataFrame,
    wh_df: pd.DataFrame,
    runtime_df: Optional[pd.DataFrame] = None,
    forecast_year: Optional[int] = None,
    forecast_month: Optional[int] = None,
) -> pd.DataFrame:
    """
    Build merged SKU-level inventory risk snapshot/base for the forecast month.

    Important:
        Inventory.xlsx Month represents opening stock for that same month.
        So if Forecast_Month = 2026-04, this function uses only
        Inventory.xlsx rows where Month = 2026-04-01 00:00.

    No FEFO. Only expiry bucket classification:
        EXPIRED / SHORT_EXP / NO_RISK.
    """
    run_date = pd.Timestamp(datetime.now().date())

    # ------------------------------------------------------------
    # 1) Determine forecast month
    # ------------------------------------------------------------
    if forecast_year is not None and forecast_month is not None:
        forecast_year = int(forecast_year)
        forecast_month = int(forecast_month)
        forecast_month_label = month_label(forecast_year, forecast_month)
    else:
        run_date, forecast_year, forecast_month, forecast_month_label = (
            get_forecast_context_from_runtime(runtime_df)
        )

    # ------------------------------------------------------------
    # 2) Select matching inventory month only
    #    Example: Forecast_Month = 2026-04
    #             use Inventory Month = 2026-04-01 00:00
    # ------------------------------------------------------------
    target_inventory_month = forecast_month_label

    db_df = db_df.copy()
    wh_df = wh_df.copy()

    if "Month" not in db_df.columns:
        raise KeyError("DB sheet must contain 'Month'.")
    if "Month" not in wh_df.columns:
        raise KeyError("WH sheet must contain 'Month'.")

    db_df["Month_Label"] = pd.to_datetime(
        db_df["Month"],
        errors="coerce"
    ).dt.strftime("%Y-%m")

    wh_df["Month_Label"] = pd.to_datetime(
        wh_df["Month"],
        errors="coerce"
    ).dt.strftime("%Y-%m")

    db_df = db_df[db_df["Month_Label"] == target_inventory_month].copy()
    wh_df = wh_df[wh_df["Month_Label"] == target_inventory_month].copy()

    if db_df.empty and wh_df.empty:
        raise ValueError(
            f"No inventory rows found for forecast month {target_inventory_month}. "
            f"Inventory.xlsx must contain Month={target_inventory_month}-01 00:00 "
            f"in DB or WH sheet."
        )

    # ------------------------------------------------------------
    # 3) Base month and short-expiry cutoff
    # ------------------------------------------------------------
    base_month_label = target_inventory_month
    base_month_start = pd.to_datetime(base_month_label + "-01")

    # Cutoff = base month first day + 3 months
    # Example: 2026-04-01 -> 2026-07-01
    cutoff_date = risk_cutoff_date(base_month_start)

    # ------------------------------------------------------------
    # 4) Build DB / WH bucketed inventory
    # ------------------------------------------------------------
    dist_df = build_distributor_inventory_snapshot(
        db_df=db_df,
        base_month_start=base_month_start,
        cutoff_date=cutoff_date,
    )

    wh_out_df = build_warehouse_inventory_snapshot(
        wh_df=wh_df,
        base_month_start=base_month_start,
        cutoff_date=cutoff_date,
    )

    merged = pd.merge(dist_df, wh_out_df, on="ItemCode", how="outer")

    if merged.empty:
        merged = pd.DataFrame(columns=["ItemCode"])

    # ------------------------------------------------------------
    # 5) Add context columns
    # ------------------------------------------------------------
    merged["Run_Date"] = str(run_date.date())
    merged["Base_Month"] = base_month_label
    merged["Forecast_Year"] = int(forecast_year)
    merged["Forecast_Month_Number"] = int(forecast_month)
    merged["Forecast_Month"] = forecast_month_label
    merged["Cutoff_Date"] = str(cutoff_date.date())

    numeric_cols = [
        c for c in merged.columns
        if c not in [
            "ItemCode",
            "Run_Date",
            "Base_Month",
            "Forecast_Month",
            "Cutoff_Date",
        ]
    ]

    merged[numeric_cols] = (
        merged[numeric_cols]
        .apply(pd.to_numeric, errors="coerce")
        .fillna(0)
    )

    # ------------------------------------------------------------
    # 6) Recalculate trade buckets after merge
    # ------------------------------------------------------------
    merged["Distributor_Trade_Qty"] = (
        merged["Distributor_NoRisk_Qty"] +
        merged["Distributor_ShortExp_Qty"]
    )

    merged["Primary_Trade_Qty"] = (
        merged["Primary_NoRisk_Qty"] +
        merged["Primary_ShortExp_Qty"]
    )

    # ------------------------------------------------------------
    # 7) Final column order
    # ------------------------------------------------------------
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

    merged = (
        merged[ordered_cols]
        .sort_values(["ItemCode"])
        .reset_index(drop=True)
    )

    return merged


# ============================================================
# SCENARIO RESULT SHAPE
# ============================================================
@dataclass
class ScenarioResult:
    scenario: str
    met_demand: bool
    unmet: float
    used_db_no_risk: float
    used_db_short_exp: float
    used_wh_trade: float
    used_wh_inspection: float
    used_wh_blocked: float
    flags: List[str]
    reasoning: List[str]


def allocate_step(need: float, available: float) -> Tuple[float, float]:
    used = min(max(need, 0.0), max(available, 0.0))
    remaining = max(need - used, 0.0)
    return used, remaining


def ensure_cols(df: pd.DataFrame, required: List[str], where: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"[{where}] Missing required columns: {missing}")


# ============================================================
# SCENARIOS
# All scenarios operate on raw M+1 Forecast_Qty (F) against physical
# stock buckets only. No pending PO/GRN supply deduction.
# ============================================================
def scenario_A_no_risk_only(F: float, db_no_risk: float) -> ScenarioResult:
    """Scenario A: use only distributor no-risk stock (X)."""
    need = max(F, 0.0)
    used_x, need = allocate_step(need, db_no_risk)

    flags = []
    if need > 0:
        flags.append("DB_TRADE_REQUIRED")

    reasoning = [
        f"Scenario A (DB No-Risk only): demand={F}",
        f"Step 1 Distributor No-Risk: used={used_x} / available={db_no_risk}, remaining={need}",
        "Only distributor no-risk stock allowed.",
    ]

    return ScenarioResult(
        scenario="A_NO_RISK_ONLY",
        met_demand=(need <= 0),
        unmet=need,
        used_db_no_risk=used_x,
        used_db_short_exp=0.0,
        used_wh_trade=0.0,
        used_wh_inspection=0.0,
        used_wh_blocked=0.0,
        flags=flags,
        reasoning=reasoning,
    )


def scenario_B_trade_allowed(F: float, db_no_risk: float, db_short_exp: float) -> ScenarioResult:
    """Scenario B: use distributor trade stock = X (no-risk) + Y (short-expiry)."""
    need = max(F, 0.0)
    used_x, need = allocate_step(need, db_no_risk)
    used_y, need = allocate_step(need, db_short_exp)

    flags = []
    if used_y > 0:
        flags.append("DB_SHORT_EXP_REQUIRED")
    if need > 0:
        flags.append("WH_STOCK_REQUIRED")
    if not flags:
        flags.append("NO_RISK_COVERED")

    reasoning = [
        f"Scenario B (DB Trade = No-Risk + Short-Expiry): demand={F}",
        f"Step 1 Distributor No-Risk: used={used_x} / available={db_no_risk}, remaining={F - used_x}",
        f"Step 2 Distributor Short-Expiry: used={used_y} / available={db_short_exp}, remaining={need}",
        "Distributor trade stock includes no-risk and short-expiry stock.",
    ]

    return ScenarioResult(
        scenario="B_TRADE_ALLOWED",
        met_demand=(need <= 0),
        unmet=need,
        used_db_no_risk=used_x,
        used_db_short_exp=used_y,
        used_wh_trade=0.0,
        used_wh_inspection=0.0,
        used_wh_blocked=0.0,
        flags=flags,
        reasoning=reasoning,
    )


def scenario_C_total_usable(
    F: float,
    db_no_risk: float,
    db_short_exp: float,
    wh_no_risk: float,
    wh_short_exp: float,
    wh_insp: float,
    wh_blocked: float,
) -> ScenarioResult:
    """
    Scenario C: use X + Y + WH (FEFO order)
        X  = distributor no-risk
        Y  = distributor short-expiry
        WH = primary no-risk -> primary short-expiry -> inspection -> blocked
    """
    need = max(F, 0.0)
    used_x, need = allocate_step(need, db_no_risk)
    used_y, need = allocate_step(need, db_short_exp)
    used_wh_nr, need = allocate_step(need, wh_no_risk)
    used_wh_se, need = allocate_step(need, wh_short_exp)
    used_wh_i, need = allocate_step(need, wh_insp)
    used_wh_b, need = allocate_step(need, wh_blocked)

    flags = []
    if used_y > 0:
        flags.append("DB_SHORT_EXP_REQUIRED")
    if used_wh_nr > 0:
        flags.append("WH_NO_RISK_REQUIRED")
    if used_wh_se > 0:
        flags.append("WH_SHORT_EXP_REQUIRED")
    if used_wh_i > 0:
        flags.append("WH_INSPECTION_REQUIRED")
    if used_wh_b > 0:
        flags.append("WH_BLOCKED_REQUIRED")
    if need > 0:
        flags.append("CRITICAL_STOCKOUT")

    used_x_acc = used_x
    used_y_acc = used_x_acc + used_y
    used_nr_acc = used_y_acc + used_wh_nr
    used_se_acc = used_nr_acc + used_wh_se
    used_i_acc = used_se_acc + used_wh_i

    reasoning = [
        f"Scenario C (DB Trade + Warehouse usable): demand={F}",
        f"Step 1 Distributor No-Risk: used={used_x} / available={db_no_risk}, remaining={F - used_x_acc}",
        f"Step 2 Distributor Short-Expiry: used={used_y} / available={db_short_exp}, remaining={F - used_y_acc}",
        f"Step 3 Warehouse No-Risk: used={used_wh_nr} / available={wh_no_risk}, remaining={F - used_nr_acc}",
        f"Step 4 Warehouse Short-Expiry: used={used_wh_se} / available={wh_short_exp}, remaining={F - used_se_acc}",
        f"Step 5 Inspection: used={used_wh_i} / available={wh_insp}, remaining={F - used_i_acc}",
        f"Step 6 Blocked: used={used_wh_b} / available={wh_blocked}, remaining={need}",
        "Scenario C includes DB no-risk + DB short-expiry + WH no-risk + WH short-expiry + inspection + blocked.",
    ]

    return ScenarioResult(
        scenario="C_TOTAL_USABLE",
        met_demand=(need <= 0),
        unmet=need,
        used_db_no_risk=used_x,
        used_db_short_exp=used_y,
        used_wh_trade=used_wh_nr + used_wh_se,
        used_wh_inspection=used_wh_i,
        used_wh_blocked=used_wh_b,
        flags=flags,
        reasoning=reasoning,
    )


def classify_risk(A: ScenarioResult, B: ScenarioResult, C: ScenarioResult) -> str:
    if A.met_demand:
        return "SAFE"
    if B.met_demand:
        return "UNDER_RISK"
    if C.met_demand:
        if C.used_wh_blocked > 0:
            return "WH_BLOCKED_REQUIRED"
        if C.used_wh_inspection > 0:
            return "WH_INSPECTION_REQUIRED"
        if C.used_wh_trade > 0:
            return "WH_TRADE_REQUIRED"
        return "CRITICAL_STOCKOUT"
    return "CRITICAL_STOCKOUT"


# ============================================================
# MAIN BUILD — M+1 risk table (physical stock only)
# ============================================================
def build_risk_table(
    base_df: pd.DataFrame,
    forecast_df: pd.DataFrame,
    base_month_col: str = "Base_Month",
    forecast_month_col: str = "Forecast_Month",
    item_col: str = "ItemCode",
    forecast_col: str = "Forecast_Qty",
) -> pd.DataFrame:
    """
    Build M+1 stockout risk table from physical stock (base_df, output of
    build_inventory_risk_snapshot) + M+1 forecast (forecast_df).

    No pending PO/GRN supply deduction here — see horizon_inventory_engine
    for the M+1..M+6 path that includes pending supply.
    """
    required_base = [
        item_col,
        "Distributor_NoRisk_Qty",
        "Distributor_ShortExp_Qty",
        "Distributor_Expired_Qty",
        "Distributor_Trade_Qty",
        "Primary_NoRisk_Qty",
        "Primary_ShortExp_Qty",
        "Primary_Trade_Qty",
        "Inspection_Stock_Qty",
        "Blocked_Stock_Qty",
    ]
    ensure_cols(base_df, required_base, "base_data")
    ensure_cols(forecast_df, [item_col, forecast_col], "forecast_data")

    base_df = base_df.copy()
    forecast_df = forecast_df.copy()

    base_df[item_col] = normalize_itemcode(base_df[item_col])
    forecast_df[item_col] = normalize_itemcode(forecast_df[item_col])

    keep_forecast_cols = [item_col, forecast_col]

    merged = base_df.merge(
        forecast_df[keep_forecast_cols],
        on=item_col,
        how="inner",
    )

    run_id = now_run_id()
    out_rows = []

    for _, r in merged.iterrows():
        item = str(r[item_col])
        base_month = safe_value(r.get(base_month_col))
        forecast_month = safe_value(r.get(forecast_month_col))

        F = safe_float(r[forecast_col])

        # Distributor buckets
        db_no_risk = safe_float(r.get("Distributor_NoRisk_Qty", 0))
        db_short_exp = safe_float(r.get("Distributor_ShortExp_Qty", 0))
        db_expired = safe_float(r.get("Distributor_Expired_Qty", 0))
        db_trade = db_no_risk + db_short_exp

        # Primary / warehouse buckets
        primary_no_risk = safe_float(r.get("Primary_NoRisk_Qty", 0))
        primary_short_exp = safe_float(r.get("Primary_ShortExp_Qty", 0))
        primary_expired = safe_float(r.get("Primary_Expired_Qty", 0))
        primary_trade = primary_no_risk + primary_short_exp

        inspection_qty = safe_float(r.get("Inspection_Stock_Qty", 0))
        blocked_qty = safe_float(r.get("Blocked_Stock_Qty", 0))

        A = scenario_A_no_risk_only(F, db_no_risk)
        B = scenario_B_trade_allowed(F, db_no_risk, db_short_exp)
        C = scenario_C_total_usable(
            F,
            db_no_risk,
            db_short_exp,
            primary_no_risk,
            primary_short_exp,
            inspection_qty,
            blocked_qty,
        )

        risk_level = classify_risk(A, B, C)

        out_rows.append({
            "run_id": run_id,
            "Base_Month": base_month,
            "Forecast_Month": forecast_month,
            "ItemCode": item,

            "Forecast_Qty": F,

            # ── stock buckets ──────────────────────────────────
            "Distributor_NoRisk_Qty": db_no_risk,
            "Distributor_ShortExp_Qty": db_short_exp,
            "Distributor_Expired_Qty": db_expired,
            "Distributor_Trade_Qty": db_trade,
            "Primary_NoRisk_Qty": primary_no_risk,
            "Primary_ShortExp_Qty": primary_short_exp,
            "Primary_Expired_Qty": primary_expired,
            "Primary_Trade_Qty": primary_trade,
            "Inspection_Stock_Qty": inspection_qty,
            "Blocked_Stock_Qty": blocked_qty,

            # ── Scenario A ─────────────────────────────────────
            "A_met": A.met_demand,
            "A_unmet": A.unmet,
            "A_used_db_no_risk": A.used_db_no_risk,
            "A_used_db_short_exp": A.used_db_short_exp,
            "A_used_wh_trade": A.used_wh_trade,
            "A_used_wh_insp": A.used_wh_inspection,
            "A_used_wh_blocked": A.used_wh_blocked,
            "A_flags": json.dumps(A.flags),
            "A_reasoning": json.dumps(A.reasoning),

            # ── Scenario B ─────────────────────────────────────
            "B_met": B.met_demand,
            "B_unmet": B.unmet,
            "B_used_db_no_risk": B.used_db_no_risk,
            "B_used_db_short_exp": B.used_db_short_exp,
            "B_used_wh_trade": B.used_wh_trade,
            "B_used_wh_insp": B.used_wh_inspection,
            "B_used_wh_blocked": B.used_wh_blocked,
            "B_flags": json.dumps(B.flags),
            "B_reasoning": json.dumps(B.reasoning),

            # ── Scenario C ─────────────────────────────────────
            "C_met": C.met_demand,
            "C_unmet": C.unmet,
            "C_used_db_no_risk": C.used_db_no_risk,
            "C_used_db_short_exp": C.used_db_short_exp,
            "C_used_wh_trade": C.used_wh_trade,
            "C_used_wh_insp": C.used_wh_inspection,
            "C_used_wh_blocked": C.used_wh_blocked,
            "C_flags": json.dumps(C.flags),
            "C_reasoning": json.dumps(C.reasoning),

            # ── Final classification ───────────────────────────
            "Risk_Level": risk_level,
        })

    return pd.DataFrame(out_rows)