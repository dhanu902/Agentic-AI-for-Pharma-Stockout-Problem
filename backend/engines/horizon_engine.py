# backend/engines/horizon_engine.py

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional
import pandas as pd


def now_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def normalize_itemcode(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )


def safe_float(x) -> float:
    try:
        if pd.isna(x):
            return 0.0
        return float(x)
    except Exception:
        return 0.0


def month_add(month_label: str, n: int) -> str:
    y, m = map(int, str(month_label).split("-"))
    m = m + n
    y = y + (m - 1) // 12
    m = ((m - 1) % 12) + 1
    return f"{y:04d}-{m:02d}"


def allocate(need: float, available: float):
    used = min(max(need, 0.0), max(available, 0.0))
    remaining_need = max(need - used, 0.0)
    remaining_available = max(available - used, 0.0)
    return used, remaining_need, remaining_available


def classify_month_risk(
    demand: float,
    db_no_risk_used: float,
    db_short_used: float,
    wh_trade_used: float,
    wh_insp_used: float,
    wh_block_used: float,
    unmet: float,
) -> str:
    if unmet > 0:
        return "CRITICAL_STOCKOUT"

    if wh_trade_used > 0 or wh_insp_used > 0 or wh_block_used > 0:
        return "USABLE_STOCK_REQUIRED"

    if db_short_used > 0:
        return "SHORT_EXPIRY_REQUIRED"

    return "NO_RISK"


def build_default_horizon_forecast(
    forecast_df: pd.DataFrame,
    horizon_months: int = 6,
) -> pd.DataFrame:
    """
    Temporary fallback:
    If only M+1 forecast exists, repeat that forecast for M+1..M+6.
    Later replace this with real M+2..M+6 model output.
    """
    rows = []

    forecast_df = forecast_df.copy()
    forecast_df["ItemCode"] = normalize_itemcode(forecast_df["ItemCode"])

    for _, r in forecast_df.iterrows():
        base_forecast_month = str(r["Forecast_Month"])
        item = str(r["ItemCode"])
        qty = safe_float(r["Forecast_Qty"])

        for h in range(1, horizon_months + 1):
            rows.append({
                "ItemCode": item,
                "Horizon": f"M+{h}",
                "Forecast_Month": month_add(base_forecast_month, h - 1),
                "Forecast_Qty": qty,
                "Forecast_Source": "M1_REPEATED_TEMP",
            })

    return pd.DataFrame(rows)


def prepare_supply_df(supply_df: Optional[pd.DataFrame]) -> pd.DataFrame:
    if supply_df is None or supply_df.empty:
        return pd.DataFrame(columns=[
            "ItemCode",
            "Expected_Arrival_Month",
            "Incoming_Qty",
            "Status",
        ])

    df = supply_df.copy()

    required = ["ItemCode", "Expected_Arrival_Month", "Incoming_Qty"]
    for c in required:
        if c not in df.columns:
            df[c] = 0 if c == "Incoming_Qty" else ""

    df["ItemCode"] = normalize_itemcode(df["ItemCode"])
    df["Expected_Arrival_Month"] = df["Expected_Arrival_Month"].astype(str).str[:7]
    df["Incoming_Qty"] = pd.to_numeric(df["Incoming_Qty"], errors="coerce").fillna(0).clip(lower=0)

    if "Status" not in df.columns:
        df["Status"] = "UNKNOWN"

    return df


def get_incoming_qty(supply_df: pd.DataFrame, item_code: str, forecast_month: str) -> float:
    if supply_df.empty:
        return 0.0

    g = supply_df[
        (supply_df["ItemCode"].astype(str) == str(item_code)) &
        (supply_df["Expected_Arrival_Month"].astype(str) == str(forecast_month))
    ]

    return float(g["Incoming_Qty"].sum()) if len(g) else 0.0


def build_horizon_projection_table(
    inventory_df: pd.DataFrame,
    forecast_horizon_df: pd.DataFrame,
    supply_df: Optional[pd.DataFrame] = None,
    horizon_months: int = 6,
) -> pd.DataFrame:
    """
    Builds cumulative 6-month risk projection.

    Stock consumption order:
    1. DB No Risk
    2. DB Short Expiry
    3. WH Trade
    4. WH Inspection
    5. WH Blocked
    6. Unmet / stockout

    Upcoming_Qty is added into WH Trade for that month.
    """

    inv = inventory_df.copy()
    fc = forecast_horizon_df.copy()
    supply = prepare_supply_df(supply_df)

    inv["ItemCode"] = normalize_itemcode(inv["ItemCode"])
    fc["ItemCode"] = normalize_itemcode(fc["ItemCode"])

    required_inv = [
        "ItemCode",
        "Base_Month",
        "Distributor_NoRisk_Qty",
        "Distributor_ShortExp_Qty",
        "Primary_Trade_Qty",
        "Inspection_Stock_Qty",
        "Blocked_Stock_Qty",
    ]

    required_fc = [
        "ItemCode",
        "Horizon",
        "Forecast_Month",
        "Forecast_Qty",
    ]

    missing_inv = [c for c in required_inv if c not in inv.columns]
    missing_fc = [c for c in required_fc if c not in fc.columns]

    if missing_inv:
        raise KeyError(f"inventory_df missing columns: {missing_inv}")

    if missing_fc:
        raise KeyError(f"forecast_horizon_df missing columns: {missing_fc}")

    run_id = now_run_id()
    out_rows = []

    for _, inv_row in inv.iterrows():
        item = str(inv_row["ItemCode"])

        sku_fc = fc[fc["ItemCode"] == item].copy()
        if sku_fc.empty:
            continue

        sku_fc["Horizon_Num"] = (
            sku_fc["Horizon"]
            .astype(str)
            .str.replace("M+", "", regex=False)
            .astype(int)
        )
        sku_fc = sku_fc.sort_values("Horizon_Num").head(horizon_months)

        db_no_risk = safe_float(inv_row.get("Distributor_NoRisk_Qty", 0))
        db_short = safe_float(inv_row.get("Distributor_ShortExp_Qty", 0))
        wh_trade = safe_float(inv_row.get("Primary_Trade_Qty", 0))
        wh_insp = safe_float(inv_row.get("Inspection_Stock_Qty", 0))
        wh_block = safe_float(inv_row.get("Blocked_Stock_Qty", 0))

        for _, f in sku_fc.iterrows():
            forecast_month = str(f["Forecast_Month"])
            forecast_qty = safe_float(f["Forecast_Qty"])
            incoming_qty = get_incoming_qty(supply, item, forecast_month)

            # Incoming supply enters usable WH trade bucket
            wh_trade += incoming_qty

            opening_total = db_no_risk + db_short + wh_trade + wh_insp + wh_block

            need = forecast_qty

            used_db_nr, need, db_no_risk = allocate(need, db_no_risk)
            used_db_short, need, db_short = allocate(need, db_short)
            used_wh_trade, need, wh_trade = allocate(need, wh_trade)
            used_wh_insp, need, wh_insp = allocate(need, wh_insp)
            used_wh_block, need, wh_block = allocate(need, wh_block)

            closing_total = db_no_risk + db_short + wh_trade + wh_insp + wh_block

            risk_level = classify_month_risk(
                demand=forecast_qty,
                db_no_risk_used=used_db_nr,
                db_short_used=used_db_short,
                wh_trade_used=used_wh_trade,
                wh_insp_used=used_wh_insp,
                wh_block_used=used_wh_block,
                unmet=need,
            )

            reasoning = [
                f"Horizon {f['Horizon']} projection for {forecast_month}",
                f"Opening stock={opening_total}",
                f"Incoming qty={incoming_qty}",
                f"Forecast demand={forecast_qty}",
                f"Used DB no-risk={used_db_nr}",
                f"Used DB short-expiry={used_db_short}",
                f"Used WH trade={used_wh_trade}",
                f"Used WH inspection={used_wh_insp}",
                f"Used WH blocked={used_wh_block}",
                f"Unmet demand={need}",
                f"Closing stock={closing_total}",
            ]

            out_rows.append({
                "run_id": run_id,
                "ItemCode": item,
                "Base_Month": inv_row.get("Base_Month"),
                "Horizon": f["Horizon"],
                "Forecast_Month": forecast_month,
                "Forecast_Qty": forecast_qty,
                "Incoming_Qty": incoming_qty,

                "Opening_Total_Stock": opening_total,
                "Opening_DB_NoRisk": opening_total if False else None,
                "Closing_Total_Stock": closing_total,

                "Remaining_DB_NoRisk": db_no_risk,
                "Remaining_DB_ShortExp": db_short,
                "Remaining_WH_Trade": wh_trade,
                "Remaining_WH_Insp": wh_insp,
                "Remaining_WH_Blocked": wh_block,

                "Used_DB_NoRisk": used_db_nr,
                "Used_DB_ShortExp": used_db_short,
                "Used_WH_Trade": used_wh_trade,
                "Used_WH_Insp": used_wh_insp,
                "Used_WH_Blocked": used_wh_block,

                "Unmet_Qty": need,
                "Risk_Level": risk_level,
                "Reasoning": json.dumps(reasoning),
                "Forecast_Source": f.get("Forecast_Source", ""),
            })

    return pd.DataFrame(out_rows)