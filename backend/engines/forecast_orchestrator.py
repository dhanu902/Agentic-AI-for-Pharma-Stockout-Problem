# backend/engines/forecast_orchestrator.py ---> ⚙️ Pipeline runner

import os
import threading
from datetime import datetime
from typing import Optional
import pandas as pd

from services.forecast_service import (
    load_raw_data,
    load_processed_data,
    save_processed_data,
    save_forecast_latest,
    append_forecast_run_log,
    get_file_status_summary,
    processed_is_fresh,
    load_forecast_latest,
    load_focus_item_codes,
    load_budget_item_codes,
    load_fact_history_all_skus,
    save_trend_forecast_latest,
    append_trend_forecast_history,
    load_trend_forecast_latest,
    save_forecast_all_skus_latest,
    save_master_forecast_mapped,
    load_master_forecast_mapped,
    load_raw_actual_history_all_skus,
    get_budget_series_for_sku,
)

from services.horizon_service import (
    run_horizon_forecast_pipeline,
    load_forecast_horizon_latest,
    load_forecast_horizon_history,
)

from services.sku_master_service import load_sku_master_full, get_sku_display_info

from engines.preprocess_engine import (
    build_processed_data_from_raw,
    normalize_itemcode,
)

from engines.master_forecast_engine import (
    build_combined_forecast_table,
    build_master_forecast_table,
)

from services.artifact_service import artifact_service
from engines.demand_forecast_engine import forecast_one_sku
from engines.leftover_sku_engine import build_trend_forecast_table, build_lightweight_dashboard

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(BASE_DIR)

LOCK = threading.Lock()

# Runtime stores
actual_data = pd.DataFrame()
live_data   = pd.DataFrame()
data        = pd.DataFrame()


# ============================================================
# DATA LOADING HELPERS
# ============================================================
def _month_label(year, month_num):
    return f"{int(year):04d}-{int(month_num):02d}"


def _num(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return float(default)


def _normalize_runtime_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    if "ItemCode" in df.columns:
        df["ItemCode"]     = normalize_itemcode(df["ItemCode"])
        df["ItemCode_key"] = df["ItemCode"]
    return df


def _load_processed_safe(mode: str) -> pd.DataFrame:
    try:
        df = load_processed_data(mode=mode)
        return _normalize_runtime_df(df)
    except FileNotFoundError:
        return pd.DataFrame()


def _merge_actual_and_live(actual_df: pd.DataFrame, live_df: pd.DataFrame) -> pd.DataFrame:
    actual_df = _normalize_runtime_df(actual_df)
    live_df   = _normalize_runtime_df(live_df)

    if actual_df.empty and live_df.empty:
        return pd.DataFrame()

    if actual_df.empty:
        merged = live_df.copy()
        merged["Runtime_Source"] = "SNAPSHOT"
        return merged.sort_values(["ItemCode", "Year", "Month_Number"]).reset_index(drop=True)

    if live_df.empty:
        merged = actual_df.copy()
        merged["Runtime_Source"] = "ACTUAL"
        return merged.sort_values(["ItemCode", "Year", "Month_Number"]).reset_index(drop=True)

    actual_df = actual_df.copy()
    live_df   = live_df.copy()
    actual_df["Runtime_Source"] = "ACTUAL"
    live_df["Runtime_Source"]   = "SNAPSHOT"

    actual_keys = set(
        zip(
            actual_df["ItemCode"].astype(str),
            actual_df["Year"].astype(int),
            actual_df["Month_Number"].astype(int),
        )
    )

    live_df = live_df[
        ~live_df.apply(
            lambda r: (str(r["ItemCode"]), int(r["Year"]), int(r["Month_Number"])) in actual_keys,
            axis=1
        )
    ].copy()

    merged = pd.concat([actual_df, live_df], ignore_index=True, sort=False)
    if {"Year", "Month_Number"}.issubset(merged.columns):
        merged = merged.sort_values(["ItemCode", "Year", "Month_Number"]).reset_index(drop=True)
    return merged


def _reload_data_into_memory():
    global actual_data, live_data, data
    actual_data = _load_processed_safe(mode="actual")
    live_data   = _load_processed_safe(mode="live")
    data        = actual_data.copy()

    return {
        "rows":        int(len(data)),
        "unique_skus": int(data["ItemCode"].nunique()) if "ItemCode" in data.columns and len(data) else 0,
        "actual_rows": int(len(actual_data)),
        "live_rows":   int(len(live_data)),
        "actual_skus": int(actual_data["ItemCode"].nunique()) if "ItemCode" in actual_data.columns and len(actual_data) else 0,
        "live_skus":   int(live_data["ItemCode"].nunique())   if "ItemCode" in live_data.columns   and len(live_data)   else 0,
        "min_year":    int(data["Year"].min()) if "Year" in data.columns and len(data) else None,
        "max_year":    int(data["Year"].max()) if "Year" in data.columns and len(data) else None,
    }


def _get_sku_df(item_code: str) -> pd.DataFrame:
    if data.empty or "ItemCode_key" not in data.columns:
        return pd.DataFrame()
    item_key = str(item_code).strip().replace(".0", "")
    sku_df   = data[data["ItemCode_key"] == item_key].copy()
    if sku_df.empty:
        return sku_df
    if {"Year", "Month_Number"}.issubset(sku_df.columns):
        sku_df = sku_df.sort_values(["Year", "Month_Number"])
    return sku_df.reset_index(drop=True)


def _get_leftover_sku_history(item_code: str) -> pd.DataFrame:
    """
    Raw (non-preprocessed) sales/inventory history for a single SKU,
    used only when the SKU has no row in the preprocessed focus data.
    """
    try:
        hist = load_raw_actual_history_all_skus()
    except FileNotFoundError:
        return pd.DataFrame()

    if hist.empty:
        return hist

    item_key = str(item_code).strip().replace(".0", "")
    sku_hist = hist[hist["ItemCode"] == item_key].copy()
    return sku_hist.sort_values(["Year", "Month_Number"]).reset_index(drop=True)


# ============================================================
# ABC CATEGORY HELPER
# FIXED: derive "A" / "B" / "C" string from artifact abc_map so
# Forecast.jsx SkuInfoStrip can colour-code the SKU badge.
# abc_map stores {item_code_str: 0|1|2} where 0=A, 1=B, 2=C.
# ============================================================
def _get_abc_category(item_code: str) -> Optional[str]:
    """Return 'A', 'B', or 'C' by looking up the champion abc_map."""
    _ABC_LABEL = {0: "A", 1: "B", 2: "C"}
    item_key = str(item_code).strip().replace(".0", "")

    # Try LONG map first, then MEDIUM
    for attr in ("long_artifacts", "medium_artifacts"):
        artifacts = getattr(artifact_service, attr, {})
        for art in artifacts.values():
            if not isinstance(art, dict):
                continue
            abc_map = art.get("abc_map", {})
            if item_key in abc_map:
                return _ABC_LABEL.get(int(abc_map[item_key]), "C")

    # Also check GRU bundle
    gru_bundle = artifact_service.gru_bundle
    if gru_bundle and isinstance(gru_bundle, dict):
        abc_map = gru_bundle.get("abc_map", {})
        if item_key in abc_map:
            return _ABC_LABEL.get(int(abc_map[item_key]), "C")

    return None


# ============================================================
# DEMAND STATUS HELPER
# FIXED: compute a human-readable demand status string that
# Forecast.jsx demandStatusColor() can interpret.
# Rules mirror common pharma demand classification:
#   - zero_rate >= 0.5 or avg_sales == 0  -> "Inactive / Near-zero demand"
#   - avg_sales < 50 or zero_rate >= 0.3  -> "Low demand"
#   - mom_change <= -30                   -> "Declining"
#   - mom_change >= 30                    -> "Growing"
#   - otherwise                           -> "Active"
# ============================================================
def _get_demand_status(sku_df: pd.DataFrame) -> str:
    if sku_df.empty or "Clean_Demand" not in sku_df.columns:
        return "Unknown"

    sales = sku_df["Clean_Demand"].clip(lower=0)
    avg   = float(sales.mean())

    tail6  = sales.tail(6)
    zero_rate = float((tail6 == 0).mean()) if len(tail6) > 0 else 0.0

    if avg == 0 or zero_rate >= 0.5:
        return "Inactive / Near-zero demand"

    if zero_rate >= 0.3 or avg < 50:
        return "Low demand"

    if len(sales) >= 2:
        recent = float(sales.tail(3).mean())
        prev   = float(sales.tail(6).head(3).mean())
        mom_pct = ((recent - prev) / (prev + 1e-6)) * 100
        if mom_pct <= -30:
            return "Declining"
        if mom_pct >= 30:
            return "Growing"

    return "Active"


# ============================================================
# ARTIFACT BOOTSTRAP
# ============================================================
def initialize_artifacts():
    artifact_service.load_all()
    return artifact_service.summary()


# ============================================================
# PUBLIC FORECAST FUNCTIONS
# ============================================================
def forecast_sku_next_month(item_code: str) -> Optional[dict]:
    if data.empty:
        return None
    sku_df = _get_sku_df(item_code)
    if sku_df.empty:
        return None
    return forecast_one_sku(item_code, data.copy())


def export_forecast_all_skus():
    if data.empty or "ItemCode" not in data.columns:
        return pd.DataFrame(columns=[
            "Run_Date", "Forecast_Month", "ItemCode", "Forecast_Qty",
            "Segment", "Used_Model", "Fallback_Used", "Target_Mode", "Routing_Reason",
        ]), None

    created_at       = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    rows             = []
    next_month_label = None

    for sku in sorted(data["ItemCode"].astype(str).unique().tolist()):
        try:
            result = forecast_one_sku(sku, data.copy())
            if result is None:
                continue
            row_month_label = _month_label(result["Forecast_Year"], result["Forecast_Month"])
            if next_month_label is None:
                next_month_label = row_month_label
            rows.append({
                "Run_Date":       created_at,
                "Forecast_Month": row_month_label,
                "ItemCode":       str(result["ItemCode"]),
                "Forecast_Qty":   round(float(result["Forecast_Prediction"]), 2),
                "Segment":        result.get("Segment", ""),
                "Used_Model":     result.get("Used_Model", ""),
                "Fallback_Used":  int(result.get("Fallback_Used", 0)),
                "Target_Mode":    result.get("Target_Mode", ""),
                "Routing_Reason": result.get("Routing_Reason", ""),
            })
        except Exception:
            continue

    return pd.DataFrame(rows), next_month_label


# ============================================================
# DASHBOARD
# ============================================================
def _get_forecast_file_row(item_code: str) -> Optional[dict]:
    try:
        df = load_forecast_latest().copy()
    except FileNotFoundError:
        return None

    if df.empty or "ItemCode" not in df.columns:
        return None

    item_key = str(item_code).strip().replace(".0", "")
    df["ItemCode"] = df["ItemCode"].astype(str).str.replace(r"\.0$", "", regex=True)

    row = df[df["ItemCode"] == item_key]
    if row.empty:
        return None
    return row.iloc[0].to_dict()


def _build_dashboard_response(item_code: str):
    sku_df = _get_sku_df(item_code)
    if sku_df.empty:
        return None

    item_key = str(item_code).strip().replace(".0", "")

    forecast_row = _get_forecast_file_row(item_code)

    if forecast_row is None:
        result = forecast_sku_next_month(item_code)
        if result is None:
            return None

        forecast_prediction = round(float(result["Forecast_Prediction"]), 2)
        forecast_year       = int(result["Forecast_Year"])
        forecast_month      = int(result["Forecast_Month"])

        segment        = result.get("Segment", "")
        used_model     = result.get("Used_Model", "")
        fallback_used  = int(result.get("Fallback_Used", 0))
        target_mode    = result.get("Target_Mode", "")
        baseline_used  = float(result.get("Baseline_Used", 0))
        routing_reason = result.get("Routing_Reason", "")
        forecast_source = "LIVE_RUNTIME"

    else:
        forecast_prediction = round(float(forecast_row.get("Forecast_Qty", 0)), 2)
        forecast_month_label = str(forecast_row.get("Forecast_Month", ""))

        if "-" not in forecast_month_label:
            return None

        yy, mm = forecast_month_label.split("-")
        forecast_year  = int(yy)
        forecast_month = int(mm)

        segment        = forecast_row.get("Segment", "")
        used_model     = forecast_row.get("Used_Model", "")
        fallback_used  = int(float(forecast_row.get("Fallback_Used", 0) or 0))
        target_mode    = forecast_row.get("Target_Mode", "")
        baseline_used  = 0.0
        routing_reason = forecast_row.get("Routing_Reason", "")
        forecast_source = "FORECAST_FILE"

    cur_row  = sku_df.iloc[-1]
    prev_row = sku_df.iloc[-2] if len(sku_df) > 1 else cur_row

    BUSINESS_ACTUAL_COL = "Secondary_Sales_Qty"

    current_actual = _num(cur_row.get(BUSINESS_ACTUAL_COL, 0))
    last_month_actual = _num(prev_row.get(BUSINESS_ACTUAL_COL, 0))

    if last_month_actual > 0:
        mom = ((current_actual - last_month_actual) / (last_month_actual + 1e-6)) * 100
    else:
        mom = None

    avg_sales = (
        _num(sku_df[BUSINESS_ACTUAL_COL].mean())
        if BUSINESS_ACTUAL_COL in sku_df.columns
        else 0.0
    )

    # ============================================================
    # BUSINESS L3M AVG - Uses PREVIOUS 3 COMPLETED MONTHS
    # ============================================================
    sales_hist = sku_df[BUSINESS_ACTUAL_COL].astype(float).tolist()

    current_l3m_avg = None
    last_month_l3m_avg = None

    if len(sales_hist) >= 4:
        current_l3m_avg = (
            sales_hist[-4] +
            sales_hist[-3] +
            sales_hist[-2]
        ) / 3

    if len(sales_hist) >= 5:
        last_month_l3m_avg = (
            sales_hist[-5] +
            sales_hist[-4] +
            sales_hist[-3]
        ) / 3

    # ============================================================
    # SHP
    # ============================================================
    current_db_stock = _num(cur_row.get("Distributor_Inventory_Qty", 0))
    last_db_stock = _num(prev_row.get("Distributor_Inventory_Qty", 0))

    current_wh_stock = _num(
        cur_row.get(
            "Net_Available_Stock",
            cur_row.get("Available_Primary_Inventory_Qty", 0)
        )
    )

    last_wh_stock = _num(
        prev_row.get(
            "Net_Available_Stock",
            prev_row.get("Available_Primary_Inventory_Qty", 0)
        )
    )

    current_db_shp = (
        current_db_stock / current_l3m_avg
        if current_l3m_avg and current_l3m_avg > 0
        else None
    )

    current_wh_shp = (
        current_wh_stock / current_l3m_avg
        if current_l3m_avg and current_l3m_avg > 0
        else None
    )

    last_db_shp = (
        last_db_stock / last_month_l3m_avg
        if last_month_l3m_avg and last_month_l3m_avg > 0
        else None
    )

    last_wh_shp = (
        last_wh_stock / last_month_l3m_avg
        if last_month_l3m_avg and last_month_l3m_avg > 0
        else None
    )

    cur_year  = int(cur_row["Year"])
    cur_month = int(cur_row["Month_Number"])

    current_label = _month_label(cur_year, cur_month)
    last_label    = _month_label(int(prev_row["Year"]), int(prev_row["Month_Number"]))
    next_label    = _month_label(forecast_year, forecast_month)

    latest_closed_label = current_label

    # ============================================================
    # SALES TREND
    # Actual + past M+1 model forecast + future horizon forecast
    # ============================================================
    tail = sku_df.tail(12).copy()
    trend_map = {}

    # 1. Actual last 12 months
    for _, r in tail.iterrows():
        label = _month_label(r["Year"], r["Month_Number"])

        trend_map[label] = {
            "period": label,
            "label": label,
            "actual": _num(r.get(BUSINESS_ACTUAL_COL, 0)),
            "pastForecast": None,
            "futureForecast": None,
            "predicted": None,  # backward compatibility
            "isForecast": False,
        }

    # 2. Past M+1 model forecast from history
    try:
        history_df = load_forecast_horizon_history()

        if history_df is not None and not history_df.empty:
            history_df = history_df.copy()
            history_df["ItemCode"] = (
                history_df["ItemCode"]
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
            )

            past_df = history_df[
                (history_df["ItemCode"] == item_key) &
                (history_df["Horizon"].astype(str) == "M+1") &
                (history_df["Forecast_Source"].astype(str) == "AI_CHAMPION_MODEL")
            ].copy()

            if not past_df.empty:
                # If multiple runs predicted same month, keep latest run.
                if "Run_Date" in past_df.columns:
                    past_df["Run_Date_sort"] = pd.to_datetime(
                        past_df["Run_Date"],
                        errors="coerce"
                    )
                    past_df = past_df.sort_values(["Forecast_Month", "Run_Date_sort"])
                elif "Run_ID" in past_df.columns:
                    past_df = past_df.sort_values(["Forecast_Month", "Run_ID"])

                past_df = past_df.drop_duplicates(
                    subset=["ItemCode", "Forecast_Month"],
                    keep="last"
                )

                for _, r in past_df.iterrows():
                    month_label = str(r.get("Forecast_Month"))

                    # past forecast means forecast month is already actual/closed
                    if month_label <= latest_closed_label:
                        if month_label not in trend_map:
                            trend_map[month_label] = {
                                "period": month_label,
                                "label": month_label,
                                "actual": None,
                                "pastForecast": None,
                                "futureForecast": None,
                                "predicted": None,
                                "isForecast": False,
                            }

                        trend_map[month_label]["pastForecast"] = _num(
                            r.get("Forecast_Qty", 0)
                        )

    except Exception:
        pass

    # 3. Future M+1 to M+6 forecast from latest horizon file
    horizon_forecast = []

    try:
        future_df = load_forecast_horizon_latest()

        if future_df is not None and not future_df.empty:
            future_df = future_df.copy()
            future_df["ItemCode"] = (
                future_df["ItemCode"]
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
            )

            future_df = future_df[future_df["ItemCode"] == item_key].copy()

            if not future_df.empty:
                def _horizon_num(v):
                    try:
                        return int(str(v).replace("M+", ""))
                    except Exception:
                        return 999

                future_df["Horizon_Num"] = future_df["Horizon"].apply(_horizon_num)
                future_df = future_df.sort_values("Horizon_Num")

                for _, r in future_df.iterrows():
                    month_label = str(r.get("Forecast_Month"))
                    qty = _num(r.get("Forecast_Qty", 0))

                    horizon_forecast.append({
                        "Horizon": str(r.get("Horizon", "")),
                        "Forecast_Month": month_label,
                        "Forecast_Qty": qty,
                        "Forecast_Source": str(r.get("Forecast_Source", "")),
                    })

                    # future forecast means month is after latest closed actual month
                    if month_label > latest_closed_label:
                        if month_label not in trend_map:
                            trend_map[month_label] = {
                                "period": month_label,
                                "label": month_label,
                                "actual": None,
                                "pastForecast": None,
                                "futureForecast": None,
                                "predicted": None,
                                "isForecast": True,
                            }

                        trend_map[month_label]["futureForecast"] = qty
                        trend_map[month_label]["predicted"] = qty
                        trend_map[month_label]["isForecast"] = True

    except Exception:
        # fallback if horizon file not generated yet
        trend_map[next_label] = {
            "period": next_label,
            "label": next_label,
            "actual": None,
            "pastForecast": None,
            "futureForecast": forecast_prediction,
            "predicted": forecast_prediction,
            "isForecast": True,
        }

    # ============================================================
    # BUDGET OVERLAY — merge monthly budgeted qty into the sales trend
    # so the chart can show actual / past forecast / future forecast /
    # budget together. Missing months (e.g. SKU not in this year's
    # budget) just get budget: None, which the chart skips.
    # ============================================================
    budget_series = get_budget_series_for_sku(item_key)
    for point in trend_map.values():
        point["budget"] = budget_series.get(point["period"])

    sales_trend = sorted(
        trend_map.values(),
        key=lambda x: x["period"]
    )

    # ============================================================
    # INVENTORY TREND
    # ============================================================
    inventory_trend = []
    for _, r in tail.iterrows():
        label = _month_label(r["Year"], r["Month_Number"])
        inventory_trend.append({
            "label": label,
            "primaryInventory": _num(r.get("Available_Primary_Inventory_Qty", 0)),
            "distInventory": _num(r.get("Distributor_Inventory_Qty", 0)),
        })

    # ============================================================
    # SHOCK TREND
    # ============================================================
    shock_trend = []
    for _, r in tail.iterrows():
        label = _month_label(r["Year"], r["Month_Number"])
        shock_trend.append({
            "label": label,
            "bonusQty": _num(r.get("Free_Qty", 0)),
            "bonusFlag": int(_num(r.get("Bonus_Flag", 0))),
            "supplyFlag": int(_num(r.get("Supply_Shock", r.get("Supply_Constraint_Flag", 0)))),
        })

    bonus_qty_current = _num(cur_row.get("Free_Qty", 0))
    bonus_qty_last = _num(prev_row.get("Free_Qty", 0))

    bonus_shock_current = int(_num(cur_row.get("Bonus_Shock", 0)))
    bonus_shock_last = int(_num(prev_row.get("Bonus_Shock", 0)))

    supply_shock_current = int(
        _num(cur_row.get("Supply_Shock", cur_row.get("Supply_Constraint_Flag", 0)))
    )

    supply_shock_last = int(
        _num(prev_row.get("Supply_Shock", prev_row.get("Supply_Constraint_Flag", 0)))
    )

    abc_category = _get_abc_category(item_code)
    demand_status = _get_demand_status(sku_df)

    return {
        # identity
        "item_code": str(item_code),
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

        "abc_category": abc_category,
        "demand_status": demand_status,

        # routing metadata
        "forecast_source": forecast_source,
        "segment": segment,
        "used_model": used_model,
        "fallback_used": fallback_used,
        "target_mode": target_mode,
        "baseline_used": baseline_used,
        "routing_reason": routing_reason,

        # headline numbers
        "next_month_forecast": forecast_prediction,
        "next_month_label": next_label,
        "current_month_actual": round(current_actual, 2),
        "current_month_label": current_label,
        "last_month_actual": round(last_month_actual, 2),
        "last_month_label": last_label,
        "mom_change": round(float(mom), 2) if mom is not None else None,
        "avg_monthly_sales": round(float(avg_sales), 2),

        # Business L3M averages
        "current_l3m_avg": (
            round(current_l3m_avg, 2)
            if current_l3m_avg is not None
            else None
        ),
        "last_month_l3m_avg": (
            round(last_month_l3m_avg, 2)
            if last_month_l3m_avg is not None
            else None
        ),

        # Stock values
        "current_db_stock": round(current_db_stock, 2),
        "current_wh_stock": round(current_wh_stock, 2),
        "last_db_stock": round(last_db_stock, 2),
        "last_wh_stock": round(last_wh_stock, 2),

        # SHP
        "current_db_shp": (
            round(current_db_shp, 2)
            if current_db_shp is not None
            else None
        ),
        "current_wh_shp": (
            round(current_wh_shp, 2)
            if current_wh_shp is not None
            else None
        ),
        "last_db_shp": (
            round(last_db_shp, 2)
            if last_db_shp is not None
            else None
        ),
        "last_wh_shp": (
            round(last_wh_shp, 2)
            if last_wh_shp is not None
            else None
        ),

        # shock snapshot
        "bonus_qty_current_month": round(float(bonus_qty_current), 2),
        "bonus_qty_last_month": round(float(bonus_qty_last), 2),
        "bonus_shock_current_month": bonus_shock_current,
        "bonus_shock_last_month": bonus_shock_last,
        "supply_shock_current_month": supply_shock_current,
        "supply_shock_last_month": supply_shock_last,

        # time-series arrays
        "sales_trend": sales_trend,
        "inventory_trend": inventory_trend,
        "shock_trend": shock_trend,
        "horizon_forecast": horizon_forecast,
    }


# ============================================================
# LIGHTWEIGHT DASHBOARD — leftover (non-focus) SKUs
#
# This function is I/O only: load the raw history, the master-mapped
# forecast row, and the display info, then hand everything to
# engines/leftover_sku_engine.build_lightweight_dashboard for the actual
# computation. No business logic lives here.
# ============================================================
def _build_lightweight_dashboard_response(item_code: str) -> Optional[dict]:
    item_key = str(item_code).strip().replace(".0", "")

    sku_hist = _get_leftover_sku_history(item_key)

    master_row = None
    try:
        master_df = load_master_forecast_mapped()
        if not master_df.empty and "ProductCode" in master_df.columns:
            master_df = master_df.copy()
            master_df["ProductCode"] = master_df["ProductCode"].astype(str)
            match = master_df[master_df["ProductCode"] == item_key]
            if not match.empty:
                master_row = match.iloc[0].to_dict()
    except Exception:
        master_row = None

    sku_info = get_sku_display_info(item_key)
    budget_series = get_budget_series_for_sku(item_key)

    return build_lightweight_dashboard(item_key, sku_hist, master_row, sku_info, budget_series)

# ============================================================
# RAW PROCESSING
# ============================================================
def process_actual_raw_now():
    with LOCK:
        try:
            raw_df = load_raw_data(mode="actual", apply_focus_filter=False)
        except FileNotFoundError as e:
            return {"ok": False, "error": str(e)}, 400

        focus_codes = load_focus_item_codes()
        processed   = build_processed_data_from_raw(
            raw_df,
            mode="actual",
            focus_codes=focus_codes,
            apply_period_filter=True,   # auto-detects and strips any still-open month
        )
        save_processed_data(processed, mode="actual")
        info = _reload_data_into_memory()

    return {
        "ok":                  True,
        "message":             "Actual raw data processed → processed_data_actual.csv generated + runtime refreshed",
        "processed_rows":      int(len(processed)),
        "unique_skus":         int(processed["ItemCode"].nunique()) if "ItemCode" in processed.columns else 0,
        "focus_codes_applied": len(focus_codes),
        **info
    }, 200


def process_live_raw_now():
    with LOCK:
        try:
            raw_df = load_raw_data(mode="live", apply_focus_filter=False)
        except FileNotFoundError as e:
            return {"ok": False, "error": str(e)}, 400

        focus_codes = load_focus_item_codes()
        processed   = build_processed_data_from_raw(
            raw_df,
            mode="live",
            focus_codes=focus_codes,
            apply_period_filter=False,  # snapshot IS the open month — never strip it
        )
        save_processed_data(processed, mode="live")
        info = _reload_data_into_memory()

    return {
        "ok":                  True,
        "message":             "Live snapshot raw data processed → processed_data_live.csv generated + runtime refreshed",
        "processed_rows":      int(len(processed)),
        "unique_skus":         int(processed["ItemCode"].nunique()) if "ItemCode" in processed.columns else 0,
        "focus_codes_applied": len(focus_codes),
        **info
    }, 200


# ============================================================
# PUBLIC API
# ============================================================
def get_dashboard(item_code: str):
    """
    Full dashboard for focus SKUs (preprocessed data + engineered features).
    Falls back to the lightweight raw-history path for leftover (non-focus)
    budgeted SKUs, which never go through preprocess_engine.
    """
    result = _build_dashboard_response(item_code)
    if result is not None:
        return result
    return _build_lightweight_dashboard_response(item_code)


def get_skus():
    """Focus SKUs only — same behavior as before, unchanged for existing callers."""
    if data.empty or "ItemCode" not in data.columns:
        return []
    return sorted(data["ItemCode"].astype(str).unique().tolist())


def get_skus_full() -> list:
    """
    Full SKU list including leftover (non-focus) budgeted SKUs, each tagged
    is_focus so the UI can flag which ones only get the lightweight dashboard.
    Focus SKUs come from in-memory `data`; the rest come from the master
    SKU list (sku_master_full.csv).
    """
    focus_codes = set(get_skus())
    out = [{"item_code": c, "is_focus": True} for c in sorted(focus_codes)]

    try:
        master_df = load_sku_master_full()
        if not master_df.empty and "ProductCode" in master_df.columns:
            for code in sorted(master_df["ProductCode"].astype(str).unique().tolist()):
                if code not in focus_codes:
                    out.append({"item_code": code, "is_focus": False})
    except Exception:
        pass

    return out


def reload_data_now():
    with LOCK:
        info = _reload_data_into_memory()
    return {"ok": True, "message": "Runtime demand data reloaded from processed actual only", **info}, 200


def reload_model_artifacts():
    try:
        summary = initialize_artifacts()
        return {"ok": True, "message": "Model artifacts reloaded successfully", "artifact_summary": summary}, 200
    except Exception as e:
        return {"ok": False, "message": f"Failed to reload model artifacts: {str(e)}"}, 500


def refresh_model_now():
    return reload_model_artifacts()


def retune_model_now():
    return {"ok": False, "message": "Model retuning is not supported in backend. Use notebook pipeline."}, 400


def retrain_model_now():
    return retune_model_now()


def train_model_now():
    return retune_model_now()


def get_health():
    artifact_summary = artifact_service.summary()

    health_info = {
        "rows":        int(len(data)),
        "unique_skus": int(data["ItemCode"].nunique()) if "ItemCode" in data.columns and len(data) else 0,
        "actual_rows": int(len(actual_data)),
        "live_rows":   int(len(live_data)),
        "actual_skus": int(actual_data["ItemCode"].nunique()) if "ItemCode" in actual_data.columns and len(actual_data) else 0,
        "live_skus":   int(live_data["ItemCode"].nunique())   if "ItemCode" in live_data.columns   and len(live_data)   else 0,
        "artifact_summary":           artifact_summary,
        "model_loaded": (
            len(artifact_summary.get("long_models_loaded", [])) > 0 or
            len(artifact_summary.get("medium_models_loaded", [])) > 0 or
            len(artifact_summary.get("short_rules_loaded", [])) > 0
        ),
        "gru_loaded":                 artifact_summary.get("gru_long_loaded", False),
        "champion_map_loaded": (
            artifact_summary.get("champion_long_loaded", False) or
            artifact_summary.get("champion_medium_loaded", False)
        ),
        "file_status":                get_file_status_summary(),
        "processed_actual_fresh":     processed_is_fresh("actual"),
        "processed_live_fresh":       processed_is_fresh("live"),
    }

    if data.empty or "ItemCode" not in data.columns:
        health_info["prediction_status"] = "FAILED: merged runtime data not loaded"
        health_info["status"]            = "UNHEALTHY"
        return health_info, 500

    try:
        any_sku   = str(data["ItemCode"].iloc[-1])
        test_pred = _build_dashboard_response(any_sku)
        health_info["sample_prediction"] = test_pred
        health_info["prediction_status"] = "OK"
        health_info["status"]            = "HEALTHY"
        return health_info, 200
    except Exception as e:
        health_info["prediction_status"] = f"FAILED: {str(e)}"
        health_info["status"]            = "UNHEALTHY"
        return health_info, 500


# ============================================================
# TREND FORECAST — budgeted SKUs NOT in the model SKU list
# ============================================================
def export_trend_forecast_now(forecast_df: Optional[pd.DataFrame] = None,
                              forecast_month_label: Optional[str] = None):
    """
    Simple algorithmic (trend baseline) forecast for budgeted SKUs that are
    NOT covered by the champion models.

    Business context: the budget ("All Budget 26 27 FY") contains SKUs with
    little/no sales history. Adding them to the model SKU list would degrade
    model accuracy, but business needs a full budgeted-SKU analysis. So these
    SKUs get a rule-based trend baseline built from fact_monthly_closed.

    Output: forecast_trend_latest.csv (+ forecast_trend_history.csv log),
    tagged Forecast_Source = TREND_BASELINE. Kept OUT of forecast_latest.csv
    so model accuracy tracking / horizon / risk pipelines are untouched.
    """
    try:
        if forecast_df is None:
            try:
                forecast_df = load_forecast_latest()
            except FileNotFoundError:
                forecast_df = pd.DataFrame()

        model_skus = set()
        if forecast_df is not None and not forecast_df.empty and "ItemCode" in forecast_df.columns:
            model_skus = set(
                forecast_df["ItemCode"].astype(str).str.replace(r"\.0$", "", regex=True)
            )
            if forecast_month_label is None and "Forecast_Month" in forecast_df.columns:
                vals = forecast_df["Forecast_Month"].dropna().astype(str)
                forecast_month_label = vals.iloc[0] if len(vals) else None

        # FIXED: the SKU universe for trend forecasting must come from
        # sku_master_full.csv (same source used to build the master-mapped
        # file), NOT from load_budget_item_codes()'s own composite-key
        # generator. Those two produced DIFFERENT synthetic IDs for the
        # same placeholder products (e.g. "New::Bayer::Jadelle" vs
        # "SYN-BAYER-JADELLE"), so trend rows for those SKUs could never
        # match back onto the master list. Using one shared ID source
        # guarantees every trend-forecasted SKU lands correctly in
        # forecast_master_mapped.csv.
        try:
            master_df = load_sku_master_full()
            budget_skus = (
                sorted(master_df["ProductCode"].astype(str).unique().tolist())
                if not master_df.empty else []
            )
        except Exception:
            budget_skus = []

        if not budget_skus:
            return {
                "ok": False,
                "error": "No budget SKUs found (sku_master_full.csv missing or empty).",
            }, 400

        fact_history_df = load_fact_history_all_skus()

        trend_df = build_trend_forecast_table(
            budget_skus=budget_skus,
            model_skus=model_skus,
            fact_history_df=fact_history_df,
            forecast_month_label=forecast_month_label,
        )

        out_path = save_trend_forecast_latest(trend_df)
        if not trend_df.empty:
            append_trend_forecast_history(trend_df)

        no_history = (
            int((trend_df["Routing_Reason"] == "NO_SALES_HISTORY").sum())
            if len(trend_df) else 0
        )

        return {
            "ok": True,
            "message": "Trend baseline exported for budgeted SKUs outside the model list",
            "rows": int(len(trend_df)),
            "budget_skus": len(budget_skus),
            "model_skus": len(model_skus),
            "skus_without_history": no_history,
            "forecast_month": forecast_month_label,
            "path": out_path,
        }, 200

    except Exception as e:
        return {"ok": False, "error": str(e)}, 500


def export_forecast_latest_now():
    """
    Full export pipeline:
        1. Run the AI model for every SKU  -> forecast_latest.csv
        2. Run the trend baseline for budgeted SKUs outside the model list
           -> forecast_trend_latest.csv
        3. Merge (1) + (2) into one deduped M+1 table
           -> forecast_all_skus_latest.csv
        4. Map that onto the full master SKU list (sku_master_full.csv),
           attaching the M+2..M+6 horizon forecast where available
           -> forecast_master_mapped.csv

    Steps 3-4 are non-fatal: if the master list or horizon file isn't ready
    yet, the model/trend exports (which everything else depends on) still
    complete and are logged.
    """
    df_forecast, label = export_forecast_all_skus()
    df_forecast = df_forecast.copy()
    if not df_forecast.empty:
        df_forecast["Forecast_Source"] = "AI_MODEL"

    out_path = save_forecast_latest(df_forecast)

    history_base_month = None
    if len(actual_data) and "Year" in actual_data.columns and "Month_Number" in actual_data.columns:
        latest_actual      = actual_data.sort_values(["Year", "Month_Number"]).iloc[-1]
        history_base_month = _month_label(latest_actual["Year"], latest_actual["Month_Number"])

    run_type = "ACTUAL_ONLY_INFERENCE"

    run_row = {
        "Run_ID":             datetime.utcnow().strftime("%Y%m%d%H%M%S"),
        "Run_Date":           datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "Forecast_Month":     label,
        "History_Base_Month": history_base_month,
        "Run_Type":           run_type,
        "Rows_Exported":      int(len(df_forecast)),
        "Output_Path":        out_path,
    }
    append_forecast_run_log(run_row)

    # Trend baseline for budgeted SKUs outside the model list (non-fatal:
    # a trend failure must never block the model forecast export).
    trend_result, _ = export_trend_forecast_now(
        forecast_df=df_forecast,
        forecast_month_label=label,
    )
    if not trend_result.get("ok"):
        print(f"[TREND] Warning: {trend_result.get('error')}")

    # Merge model + trend into the ONE file downstream code should read
    # (Insights, budget analysis, reporting — no more joining two files).
    combined_path = None
    combined_rows = 0
    try:
        trend_df = load_trend_forecast_latest()
        combined_df = build_combined_forecast_table(df_forecast, trend_df)
        combined_path = save_forecast_all_skus_latest(combined_df)
        combined_rows = int(len(combined_df))
    except Exception as e:
        print(f"[COMBINED FORECAST] Warning: {e}")
        combined_df = pd.DataFrame()

    # Map the combined forecast + horizon onto the full master SKU list
    # (real codes + synthetic codes for products with no ItemCode).
    master_mapped_path = None
    master_mapped_rows = 0
    try:
        master_df = load_sku_master_full()
        try:
            horizon_df = load_forecast_horizon_latest()
        except FileNotFoundError:
            horizon_df = pd.DataFrame()

        master_mapped_df = build_master_forecast_table(master_df, combined_df, horizon_df)
        master_mapped_path = save_master_forecast_mapped(master_mapped_df)
        master_mapped_rows = int(len(master_mapped_df))
    except Exception as e:
        print(f"[MASTER FORECAST MAP] Warning: {e}")

    return {
        "ok":      True,
        "message": "Forecast exported — model + trend merged and mapped onto master SKU list",
        "next_month": label,
        "rows":    int(len(df_forecast)),
        "path":    out_path,
        "trend":   trend_result,
        "combined_path": combined_path,
        "combined_rows": combined_rows,
        "master_mapped_path": master_mapped_path,
        "master_mapped_rows": master_mapped_rows,
    }, 200


def export_forecast_horizon_latest_now():
    """
    Connect forecast route/UI to horizon forecast service.

    M+1:
        comes from forecast_latest.csv / demand_forecast_engine

    M+2 to M+6:
        built inside horizon_service using horizon_forecast_engine

    Output:
        forecast_horizon_latest.csv
        forecast_horizon_history.csv
    """

    with LOCK:
        try:
            if data.empty:
                _reload_data_into_memory()

            try:
                forecast_df = load_forecast_latest()
            except FileNotFoundError:
                forecast_df, _ = export_forecast_all_skus()
                save_forecast_latest(forecast_df)

            if forecast_df is None or forecast_df.empty:
                return {
                    "ok": False,
                    "error": "forecast_latest.csv is empty. Cannot build horizon forecast."
                }, 400

            history_df = actual_data.copy() if not actual_data.empty else data.copy()

            return run_horizon_forecast_pipeline(
                forecast_df=forecast_df,
                history_df=history_df,
                horizon_months=6,
            )

        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
            }, 500


# ============================================================
# INIT ON IMPORT
# ============================================================
try:
    initialize_artifacts()
except Exception as e:
    print(f"[Artifact Init Warning] {e}")

try:
    _reload_data_into_memory()
except Exception:
    actual_data = pd.DataFrame()
    live_data   = pd.DataFrame()
    data        = pd.DataFrame()