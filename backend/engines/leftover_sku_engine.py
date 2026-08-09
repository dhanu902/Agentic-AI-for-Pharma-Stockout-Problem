# backend/engines/leftover_sku_engine.py --> 🧮 Leftover (non-focus) SKU logic
#
# Everything related to SKUs OUTSIDE the AI model's focus list lives here:
#   - the trend baseline forecast (simple rolling-average rule, for budgeted
#     SKUs the champion models don't cover)
#   - the lightweight KPI/dashboard builder used as a fallback when a SKU
#     has no row in the preprocessed focus data
#
# Pure logic only — no file I/O. Inputs/outputs are DataFrames and dicts;
# loading/saving stays in services/forecast_service.py, and the orchestrator
# is the only place that wires file loads to these functions.

from datetime import datetime
from typing import Iterable, Optional, Set

import pandas as pd


# ============================================================
# BASIC HELPERS
# ============================================================
def normalize_itemcode(v) -> str:
    return str(v).strip().replace(".0", "")


def _safe_num(v, default=0.0) -> float:
    try:
        if pd.isna(v):
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _month_label(year, month_num) -> str:
    return f"{int(year):04d}-{int(month_num):02d}"


def _next_month_label(period_label: str) -> str:
    y, m = map(int, period_label.split("-"))
    m += 1
    if m == 13:
        y += 1
        m = 1
    return f"{y:04d}-{m:02d}"


def _next_year_month(year, month_number):
    year = int(year)
    month_number = int(month_number)
    if month_number == 12:
        return year + 1, 1
    return year, month_number + 1


# ============================================================
# TREND BASELINE — 📈 simple algorithmic forecast for budgeted SKUs
# that are NOT in the model (focus) SKU list.
# ============================================================
# Business context
# ----------------
# The champion models cover only the focus SKU list. The budget
# ("All Budget 26 27 FY") contains additional SKUs with little/no sales
# history — forcing them into the model list would degrade model accuracy.
# For those SKUs this section produces a simple trend baseline (last-month /
# rolling averages from fact_monthly_closed), so downstream pages can show
# a FULL budgeted-SKU analysis.
#
# Output rows are tagged Forecast_Source = "TREND_BASELINE" so the UI can
# distinguish model predictions from simple background analysis.
#
# Kept SEPARATE from forecast_latest.csv (own output file, written by
# forecast_orchestrator.export_trend_forecast_now) so model accuracy
# tracking, horizon forecasting and the risk pipeline are untouched.

TREND_WINDOW_SHORT = 3   # L3M rolling average
TREND_WINDOW_LONG  = 6   # L6M rolling average (stability anchor)

FORECAST_SOURCE_TAG = "TREND_BASELINE"

# BUDGET-ONLY routing (business change 3.1):
#   - Products with NO standard product code (synthetic "SYN-..." codes)
#     have no mapping into fact_monthly_closed, so a sales-based forecast
#     is impossible — the UI can only show their BUDGET.
#   - Products WITH a real code but NO sales history at all likewise have
#     nothing to trend from — budget only.
# These rows are tagged Forecast_Source = BUDGET_ONLY (not TREND_BASELINE)
# so every downstream page can hide the forecast and show budget instead.
# NOTE: this status is re-evaluated on every run — the moment such a
# product gets a standard code and/or starts selling, it automatically
# moves onto the trend (or model) path.
BUDGET_ONLY_SOURCE_TAG = "BUDGET_ONLY"
SYNTHETIC_CODE_PREFIX = "SYN-"


def _safe_mean(x) -> float:
    x = pd.to_numeric(pd.Series(x), errors="coerce").dropna()
    return float(x.mean()) if len(x) else 0.0


def _trend_forecast_for_sku(sales: pd.Series) -> tuple[float, str, str]:
    """
    sales: chronological Secondary_Sales_Qty for one SKU (closed months only).

    Returns (forecast_qty, used_model, routing_reason)

    Rules (deliberately simple — business-explainable):
        no history           → 0                      TREND_NO_HISTORY
        1-2 months           → mean(all)              TREND_SHORT_AVG
        3+ months            → 0.7·L3M + 0.3·L6M      TREND_ROLLING_AVG
    L6M blending dampens a single spiky/zero month in the L3M window.
    """
    sales = pd.to_numeric(sales, errors="coerce").fillna(0).clip(lower=0)
    n = len(sales)

    if n == 0:
        return 0.0, "TREND_NO_HISTORY", "NO_SALES_HISTORY"

    if n < TREND_WINDOW_SHORT:
        return _safe_mean(sales), "TREND_SHORT_AVG", "LT_3M_HISTORY"

    l3m = _safe_mean(sales.tail(TREND_WINDOW_SHORT))
    l6m = _safe_mean(sales.tail(TREND_WINDOW_LONG))
    qty = 0.7 * l3m + 0.3 * l6m
    return max(qty, 0.0), "TREND_ROLLING_AVG", "NOT_IN_MODEL_SKU_LIST"


def build_trend_forecast_table(
    budget_skus: Iterable[str],
    model_skus: Set[str],
    fact_history_df: pd.DataFrame,
    forecast_month_label: Optional[str] = None,
    run_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    One forecast row per budgeted SKU that is NOT in the model SKU list.

    Args:
        budget_skus:          ALL ItemCodes from "All Budget 26 27 FY"
        model_skus:           ItemCodes present in forecast_latest.csv
        fact_history_df:      ItemCode | Year | Month_Number | Secondary_Sales_Qty
                              (ALL SKUs, closed months only)
        forecast_month_label: "YYYY-MM" target month; if None, derived as
                              latest closed month in fact history + 1
        run_date:             stamp for Run_Date; defaults to utcnow

    Output columns (superset of forecast_latest.csv schema):
        Run_Date, Forecast_Month, ItemCode, Forecast_Qty,
        Segment, Used_Model, Fallback_Used, Target_Mode, Routing_Reason,
        Forecast_Source, Last_Month_Qty, L3M_Avg, L6M_Avg, History_Months
    """
    out_cols = [
        "Run_Date", "Forecast_Month", "ItemCode", "Forecast_Qty",
        "Segment", "Used_Model", "Fallback_Used", "Target_Mode",
        "Routing_Reason", "Forecast_Source",
        "Last_Month_Qty", "L3M_Avg", "L6M_Avg", "History_Months",
    ]

    budget_set = {normalize_itemcode(s) for s in budget_skus if str(s).strip()}
    model_set  = {normalize_itemcode(s) for s in (model_skus or set())}
    target_skus = sorted(budget_set - model_set)

    if not target_skus:
        return pd.DataFrame(columns=out_cols)

    hist = pd.DataFrame()
    if fact_history_df is not None and not fact_history_df.empty:
        hist = fact_history_df.copy()
        hist["ItemCode"] = (
            hist["ItemCode"].astype(str).str.strip()
            .str.replace(r"\.0$", "", regex=True)
        )
        hist = hist.sort_values(["ItemCode", "Year", "Month_Number"])

    if forecast_month_label is None:
        if not hist.empty:
            latest = hist.sort_values(["Year", "Month_Number"]).iloc[-1]
            ny, nm = _next_year_month(latest["Year"], latest["Month_Number"])
        else:
            now = datetime.utcnow()
            ny, nm = _next_year_month(now.year, now.month)
        forecast_month_label = f"{ny:04d}-{nm:02d}"

    run_date = run_date or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    grouped = dict(tuple(hist.groupby("ItemCode"))) if not hist.empty else {}

    rows = []
    for sku in target_skus:
        sku_hist = grouped.get(sku)
        sales = (
            sku_hist["Secondary_Sales_Qty"]
            if sku_hist is not None
            else pd.Series(dtype=float)
        )
        sales_num = pd.to_numeric(sales, errors="coerce").fillna(0).clip(lower=0)

        # ---- BUDGET-ONLY routing (change 3.1) ----------------------
        # No standard code -> no fact-sales mapping -> budget only.
        # Real code but zero sales history -> nothing to trend -> budget only.
        is_synthetic = sku.startswith(SYNTHETIC_CODE_PREFIX)
        has_history = len(sales_num) > 0

        if is_synthetic:
            qty, used_model, reason = 0.0, BUDGET_ONLY_SOURCE_TAG, "NO_STANDARD_CODE"
            source_tag = BUDGET_ONLY_SOURCE_TAG
            segment = "BUDGET_ONLY"
        elif not has_history:
            qty, used_model, reason = 0.0, BUDGET_ONLY_SOURCE_TAG, "NO_SALES_MAPPING"
            source_tag = BUDGET_ONLY_SOURCE_TAG
            segment = "BUDGET_ONLY"
        else:
            # In master SKU list, not in focus list, HAS sales history
            # -> simple trend baseline forecast (separate from the model).
            qty, used_model, reason = _trend_forecast_for_sku(sales)
            source_tag = FORECAST_SOURCE_TAG
            segment = "TREND"

        rows.append({
            "Run_Date":        run_date,
            "Forecast_Month":  forecast_month_label,
            "ItemCode":        sku,
            "Forecast_Qty":    round(float(qty), 2),
            "Segment":         segment,
            "Used_Model":      used_model,
            "Fallback_Used":   1,
            "Target_Mode":     "rule",
            "Routing_Reason":  reason,
            "Forecast_Source": source_tag,
            "Last_Month_Qty":  round(float(sales_num.iloc[-1]), 2) if len(sales_num) else 0.0,
            "L3M_Avg":         round(_safe_mean(sales_num.tail(TREND_WINDOW_SHORT)), 2),
            "L6M_Avg":         round(_safe_mean(sales_num.tail(TREND_WINDOW_LONG)), 2),
            "History_Months":  int(len(sales_num)),
        })

    return pd.DataFrame(rows, columns=out_cols)


# ============================================================
# LIGHTWEIGHT DASHBOARD — pure computation for the leftover-SKU
# fallback view on the Forecast page.
#
# These SKUs never go through preprocess_engine, so none of the
# engineered features (lags, rolling stats, stock cover, ABC class,
# bonus/supply shock flags) exist for them. This builds actuals/trend
# from raw sales history and the forecast number from the master-mapped
# file, returning null for anything that genuinely requires the full
# feature pipeline. "data_completeness": "LIGHTWEIGHT" tells the UI to
# show a limited-data indicator and hide those sections.
# ============================================================
def _get_demand_status_from_sales(sales: pd.Series) -> str:
    """
    Lightweight mirror of the demand-status classification used for focus
    SKUs, working off raw Secondary_Sales_Qty instead of Clean_Demand —
    Clean_Demand is an engineered feature from preprocess_engine that
    leftover SKUs never get.
    """
    if sales is None or sales.empty:
        return "Unknown"

    sales = sales.clip(lower=0)
    avg = float(sales.mean())

    tail6 = sales.tail(6)
    zero_rate = float((tail6 == 0).mean()) if len(tail6) > 0 else 0.0

    if avg == 0 or zero_rate >= 0.5:
        return "Inactive / Near-zero demand"

    if zero_rate >= 0.3 or avg < 50:
        return "Low demand"

    if len(sales) >= 2:
        recent = float(sales.tail(3).mean())
        prev = float(sales.tail(6).head(3).mean())
        mom_pct = ((recent - prev) / (prev + 1e-6)) * 100
        if mom_pct <= -30:
            return "Declining"
        if mom_pct >= 30:
            return "Growing"

    return "Active"


def build_lightweight_dashboard(
    item_code: str,
    sku_hist: pd.DataFrame,
    master_row: Optional[dict],
    sku_info: Optional[dict] = None,
    budget_series: Optional[dict] = None,
) -> Optional[dict]:
    """
    Build the Forecast dashboard response for a leftover (non-focus) SKU.

    Parameters
    ----------
    item_code : the SKU code being requested
    sku_hist  : raw per-month history for this SKU only (columns:
                ItemCode, Year, Month_Number, Secondary_Sales_Qty, ...),
                as produced by forecast_service.load_raw_actual_history_all_skus()
                sliced to one SKU. May be empty.
    master_row: one row (as a dict) from forecast_master_mapped.csv for
                this SKU, or None if the SKU isn't in the master list yet.
    sku_info  : {"product_name", "agency", "agency_code"} from
                sku_master_service.get_sku_display_info(), or None.
    budget_series: {"YYYY-MM": qty} from forecast_service.get_budget_series_for_sku(),
                or None. Only real numeric ItemCodes have one — synthetic
                codes get budget: None on every point.

    Returns None only when there is truly nothing to show (no history AND
    no master forecast row) — i.e. an unknown SKU.
    """
    item_key = normalize_itemcode(item_code)
    sku_info = sku_info or {}
    budget_series = budget_series or {}

    if (sku_hist is None or sku_hist.empty) and master_row is None:
        return None

    # ---- KPIs from raw actuals (no engineered features) ----
    current_actual = last_month_actual = avg_sales = 0.0
    mom = None
    current_label = last_label = None
    sales_trend = []

    if sku_hist is not None and not sku_hist.empty:
        sku_hist = sku_hist.sort_values(["Year", "Month_Number"]).reset_index(drop=True)
        cur_row  = sku_hist.iloc[-1]
        prev_row = sku_hist.iloc[-2] if len(sku_hist) > 1 else cur_row

        current_actual    = _safe_num(cur_row.get("Secondary_Sales_Qty", 0))
        last_month_actual = _safe_num(prev_row.get("Secondary_Sales_Qty", 0))

        if last_month_actual > 0:
            mom = ((current_actual - last_month_actual) / (last_month_actual + 1e-6)) * 100

        avg_sales = _safe_num(sku_hist["Secondary_Sales_Qty"].mean())

        current_label = _month_label(cur_row["Year"], cur_row["Month_Number"])
        last_label     = _month_label(prev_row["Year"], prev_row["Month_Number"])

        for _, r in sku_hist.tail(12).iterrows():
            label = _month_label(r["Year"], r["Month_Number"])
            sales_trend.append({
                "period": label,
                "label": label,
                "actual": _safe_num(r.get("Secondary_Sales_Qty", 0)),
                "pastForecast": None,
                "futureForecast": None,
                "predicted": None,
                "isForecast": False,
                "budget": budget_series.get(label),
            })

    # ---- Forecast from forecast_master_mapped.csv ----
    forecast_qty     = 0.0
    forecast_source  = "NO_FORECAST"
    next_label       = None
    horizon_forecast = []

    if master_row is not None:
        forecast_qty    = _safe_num(master_row.get("Forecast_Qty", 0))
        forecast_source = str(master_row.get("Forecast_Source", "NO_FORECAST"))

        # BUDGET-ONLY products (no standard code / no fact-sales mapping):
        # there is NO forecast to show — only budget. Skip the horizon and
        # the future-forecast trend point; still extend the trend one month
        # so the budget overlay has somewhere to render.
        if forecast_source == BUDGET_ONLY_SOURCE_TAG:
            if sales_trend:
                next_label = _next_month_label(sales_trend[-1]["period"])
            elif budget_series:
                next_label = sorted(budget_series.keys())[0]
            if next_label is not None:
                sales_trend.append({
                    "period": next_label,
                    "label": next_label,
                    "actual": None,
                    "pastForecast": None,
                    "futureForecast": None,
                    "predicted": None,
                    "isForecast": False,
                    "budget": budget_series.get(next_label),
                })
        else:
            # NOTE: forecast_master_mapped.csv only carries Horizon_M1..M6
            # quantities, not calendar month labels (those live only in
            # forecast_horizon_latest.csv, keyed by focus-SKU ItemCode).
            for h in range(1, 7):
                col = f"Horizon_M{h}"
                if col in master_row and pd.notna(master_row[col]):
                    horizon_forecast.append({
                        "Horizon": f"M+{h}",
                        "Forecast_Month": None,
                        "Forecast_Qty": _safe_num(master_row[col]),
                        "Forecast_Source": forecast_source,
                    })

            if sales_trend:
                next_label = _next_month_label(sales_trend[-1]["period"])
                sales_trend.append({
                    "period": next_label,
                    "label": next_label,
                    "actual": None,
                    "pastForecast": None,
                    "futureForecast": forecast_qty,
                    "predicted": forecast_qty,
                    "isForecast": True,
                    "budget": budget_series.get(next_label),
                })

    demand_status = "Unknown"
    if sku_hist is not None and not sku_hist.empty:
        demand_status = _get_demand_status_from_sales(sku_hist["Secondary_Sales_Qty"])

    return {
        "item_code": item_key,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

        "data_completeness": "LIGHTWEIGHT",

        "product_name": sku_info.get("product_name"),
        "agency": sku_info.get("agency"),

        "abc_category": None,
        "demand_status": demand_status,

        "forecast_source": forecast_source,
        "segment": "TREND" if forecast_source == "TREND_BASELINE" else forecast_source,
        "used_model": forecast_source,
        "fallback_used": 1,
        "target_mode": "rule" if forecast_source != "AI_MODEL" else "residual",
        "baseline_used": 0.0,
        "routing_reason": (
            "BUDGET_ONLY_NO_FORECAST"
            if forecast_source == BUDGET_ONLY_SOURCE_TAG
            else "LEFTOVER_SKU_LIGHTWEIGHT_PATH"
        ),

        # BUDGET_ONLY products have NO forecast — budget is the only number
        "next_month_forecast": (
            None if forecast_source == BUDGET_ONLY_SOURCE_TAG
            else round(forecast_qty, 2)
        ),
        "next_month_label": next_label,
        "current_month_actual": round(current_actual, 2),
        "current_month_label": current_label,
        "last_month_actual": round(last_month_actual, 2),
        "last_month_label": last_label,
        "mom_change": round(float(mom), 2) if mom is not None else None,
        "avg_monthly_sales": round(float(avg_sales), 2),

        # not computable without full preprocessing — left null on purpose
        "current_l3m_avg": None,
        "last_month_l3m_avg": None,
        "current_db_stock": None,
        "current_wh_stock": None,
        "last_db_stock": None,
        "last_wh_stock": None,
        "current_db_shp": None,
        "current_wh_shp": None,
        "last_db_shp": None,
        "last_wh_shp": None,
        "bonus_qty_current_month": None,
        "bonus_qty_last_month": None,
        "bonus_shock_current_month": None,
        "bonus_shock_last_month": None,
        "supply_shock_current_month": None,
        "supply_shock_last_month": None,

        "sales_trend": sales_trend,
        "inventory_trend": [],
        "shock_trend": [],
        "horizon_forecast": horizon_forecast,
    }