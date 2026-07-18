# backend/engines/horizon_inventory_engine.py ---> 🧠 M+1..M+6 horizon projection logic
#
# Responsibility: project SKU-level stock forward across M+1..M+6 using
#   - Opening stock (physically available primary inventory, M+1 basis)
#   - Pending PO / GRN (ToBeGRN) supply arriving in each horizon month
#   - M+1..M+6 forecast quantity (from horizon_forecast_engine)
#
# This is the Horizon page's engine — it differs from risk_engine (M+1,
# Inventory page) by INCLUDING pending supply and projecting 6 months
# instead of 1.
#
# Calculation details (allocation order, risk thresholds, multi-bucket
# coverage, etc.) are placeholders for now and will be refined later.
# NO FILE I/O HERE — inputs are DataFrames passed in by horizon_service.

from __future__ import annotations

from typing import Optional

import pandas as pd


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


def month_add(month_label: str, n: int) -> str:
    y, m = map(int, str(month_label).split("-"))
    m += n
    y += (m - 1) // 12
    m = ((m - 1) % 12) + 1
    return f"{y:04d}-{m:02d}"


# ============================================================
# PENDING SUPPLY (ToBeGRN / Pending POs) — NORMALIZATION
# ============================================================
def prepare_supply_df(raw_supply_df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize the raw ToBeGRN / Pending PO sheet into a standard shape:

        ItemCode | Incoming_Qty | Expected_Arrival_Month | Status

    Expected raw columns: ItemCode, OpenQty, DeliveryDate
    (status/PO metadata columns are ignored for now — placeholder for
    future PO-number-level tracking).
    """
    cols = ["ItemCode", "Incoming_Qty", "Expected_Arrival_Month", "Status"]

    if raw_supply_df is None or raw_supply_df.empty:
        return pd.DataFrame(columns=cols)

    df = raw_supply_df.copy()

    required = {"ItemCode", "OpenQty", "DeliveryDate"}
    if not required.issubset(df.columns):
        return pd.DataFrame(columns=cols)

    df["ItemCode"] = normalize_itemcode(df["ItemCode"])
    df["Incoming_Qty"] = safe_numeric(df["OpenQty"], 0.0).clip(lower=0)
    df["DeliveryDate"] = safe_datetime(df["DeliveryDate"])
    df["Expected_Arrival_Month"] = df["DeliveryDate"].dt.strftime("%Y-%m")
    df["Status"] = "TO_BE_GRN"

    return df[cols]


def get_incoming_qty(supply_df: pd.DataFrame, item_code: str, month_label: str) -> float:
    """
    Total incoming (pending PO/GRN) quantity for a SKU arriving in a
    given month (Expected_Arrival_Month == month_label).
    """
    if supply_df is None or supply_df.empty:
        return 0.0
    rows = supply_df[
        (supply_df["ItemCode"] == str(item_code))
        & (supply_df["Expected_Arrival_Month"].astype(str) == str(month_label))
    ]
    if rows.empty:
        return 0.0
    return float(rows["Incoming_Qty"].sum())


# ============================================================
# RISK CLASSIFICATION (placeholder thresholds — refine later)
# ============================================================
def allocate_supply(opening_stock: float, incoming_supply: float) -> float:
    """Opening stock + incoming supply, floored at 0."""
    return max(0.0, float(opening_stock) + float(incoming_supply))


def classify_month_risk(closing_stock: float) -> str:
    """
    Placeholder risk classification for horizon months.
        closing_stock <= 0    -> STOCKOUT
        closing_stock <= 100  -> HIGH_RISK
        closing_stock <= 500  -> MEDIUM_RISK
        else                  -> SAFE
    """
    if closing_stock <= 0:
        return "STOCKOUT"
    if closing_stock <= 100:
        return "HIGH_RISK"
    if closing_stock <= 500:
        return "MEDIUM_RISK"
    return "SAFE"


# ============================================================
# OPENING STOCK
# ============================================================
def get_opening_stock(inventory_row: pd.Series) -> float:
    """
    Opening stock for the M+1 horizon start.

    Placeholder: uses physically available primary inventory
    (Primary_NoRisk_Qty + Primary_ShortExp_Qty, i.e. Primary_Trade_Qty,
    from risk_engine's snapshot — falls back to
    Available_Primary_Inventory_Qty if present for backward compat).
    """
    if "Available_Primary_Inventory_Qty" in inventory_row.index:
        return float(inventory_row.get("Available_Primary_Inventory_Qty", 0) or 0)
    return float(inventory_row.get("Primary_Trade_Qty", 0) or 0)


# ============================================================
# MAIN BUILD — M+1..M+6 horizon projection
# ============================================================
def build_horizon_projection_table(
    inventory_df: pd.DataFrame,
    forecast_horizon_df: pd.DataFrame,
    supply_df: Optional[pd.DataFrame] = None,
    horizon_months: int = 6,
) -> pd.DataFrame:
    """
    Current simplified horizon output.

    Shows:
        - M+1 to M+6 forecast
        - M+1 stock status only using trade stock

    Does NOT project stock depletion.
    Does NOT use PO/GRN yet.
    """
    if inventory_df is None or inventory_df.empty:
        return pd.DataFrame()

    if forecast_horizon_df is None or forecast_horizon_df.empty:
        return pd.DataFrame()

    inventory_df = inventory_df.copy()
    forecast_horizon_df = forecast_horizon_df.copy()

    inventory_df["ItemCode"] = normalize_itemcode(inventory_df["ItemCode"])
    forecast_horizon_df["ItemCode"] = normalize_itemcode(forecast_horizon_df["ItemCode"])

    # Trade stock only
    inventory_df["Distributor_Trade_Qty"] = safe_numeric(
        inventory_df.get("Distributor_Trade_Qty", 0), 0.0
    )

    inventory_df["Primary_Trade_Qty"] = safe_numeric(
        inventory_df.get("Primary_Trade_Qty", 0), 0.0
    )

    inventory_df["Total_Trade_Stock"] = (
        inventory_df["Distributor_Trade_Qty"] +
        inventory_df["Primary_Trade_Qty"]
    )

    keep_inv_cols = [
        "ItemCode",
        "Base_Month",
        "Distributor_Trade_Qty",
        "Primary_Trade_Qty",
        "Total_Trade_Stock",
    ]

    merged = forecast_horizon_df.merge(
        inventory_df[keep_inv_cols],
        on="ItemCode",
        how="left",
    )

    merged["Forecast_Qty"] = safe_numeric(merged["Forecast_Qty"], 0.0)
    merged["Total_Trade_Stock"] = safe_numeric(merged["Total_Trade_Stock"], 0.0)

    def _horizon_num(v):
        try:
            return int(str(v).replace("M+", ""))
        except Exception:
            return 999

    merged["Horizon_Num"] = merged["Horizon"].apply(_horizon_num)

    # Only M+1 gets stock status
    merged["M1_Stock_Status"] = None
    m1_mask = merged["Horizon_Num"] == 1

    merged.loc[m1_mask, "M1_Stock_Status"] = merged.loc[m1_mask].apply(
        lambda r: "ENOUGH_STOCK"
        if float(r["Total_Trade_Stock"]) >= float(r["Forecast_Qty"])
        else "SHORT_STOCK",
        axis=1,
    )

    # Placeholder columns for future PO/GRN logic
    merged["PO_Qty"] = None
    merged["PO_Arrival_Date"] = None
    merged["GRN_Qty"] = None
    merged["GRN_Clearance_Date"] = None
    merged["Projected_Closing_Stock"] = None
    merged["Risk_Level"] = merged["M1_Stock_Status"]

    ordered_cols = [
        "ItemCode",
        "Horizon",
        "Forecast_Month",
        "Forecast_Qty",
        "Base_Month",
        "Distributor_Trade_Qty",
        "Primary_Trade_Qty",
        "Total_Trade_Stock",
        "M1_Stock_Status",
        "PO_Qty",
        "PO_Arrival_Date",
        "GRN_Qty",
        "GRN_Clearance_Date",
        "Projected_Closing_Stock",
        "Risk_Level",
    ]

    for col in ordered_cols:
        if col not in merged.columns:
            merged[col] = None

    return merged[ordered_cols].sort_values(["ItemCode", "Horizon"]).reset_index(drop=True)
