# backend/engines/insights_engine.py

import os
import pandas as pd
import numpy as np
import joblib

# Trend baseline + ALL-SKU actuals come from the forecast side:
#   forecast_trend_latest.csv / forecast_trend_history.csv are built by
#   engines/trend_forecast_engine.py (triggered in forecast_orchestrator)
#   for budgeted SKUs that are NOT in the model SKU list.
# Insights only CONSUMES those files — no forecast logic lives here.
from services.forecast_service import (
    load_fact_history_all_skus,
    load_trend_forecast_latest,
    load_trend_forecast_history,
)

# Single source of truth for ItemCode -> Agency/ItemName resolution, and for
# the canonical synthetic ("SYN-...") codes assigned to budgeted products
# that have no real ItemCode. Built off Budget.xlsx + Agency map.xlsx.
# Using this everywhere (instead of reading Agency map.xlsx directly, and
# instead of inventing a second synthetic-code scheme locally) keeps every
# table in Insights — actuals, forecast, budget — resolving the SAME
# ItemCode for the SAME product as the rest of the pipeline (e.g.
# engines/master_forecast_engine.py). It is also the definition of "the
# master SKU universe" used to scope every KPI total in this file.
from services.sku_master_service import load_sku_master_full

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.dirname(BACKEND_DIR)

FORECAST_FILE         = os.path.join(BACKEND_DIR, "data", "outputs", "forecast_latest.csv")
FORECAST_HISTORY_FILE = os.path.join(BACKEND_DIR, "data", "logs", "forecast_horizon_history.csv")
PROCESSED_DATA_FILE   = os.path.join(BACKEND_DIR, "data", "processed", "processed_data_actual.csv")
CHAMPION_LONG_FILE    = os.path.join(BACKEND_DIR, "models", "registry", "champion_long_map_df.pkl")
CHAMPION_MEDIUM_FILE  = os.path.join(BACKEND_DIR, "models", "registry", "champion_medium_map_df.pkl")
BUDGET_FILE           = os.path.join(PROJECT_DIR, "data", "Master Data", "Budget.xlsx")

# ALL budgeted items live here (some have budget but no sales / no forecast).
# "Focus Budget 26 27 FY" only contains forecasted items — do NOT use it for totals.
BUDGET_SHEET_NAME = "All Budget 26 27 FY"

# ─────────────────────────────────────────────────────────────
# Distributor (RD) unit price — Inventory.xlsx "DB" sheet
# ─────────────────────────────────────────────────────────────
# PRICING RULE (do not mix these two):
#   Budget qty/value  -> PRIMARY movement -> valued at Budget_Price
#                        (Budget.xlsx, a planning price)
#   Actual sales qty  -> SECONDARY movement (distributor sell-through)
#   Forecast qty      -> also a SECONDARY-movement prediction
#                        -> BOTH valued at Distributor_Unit_Price
#                        (Inventory.xlsx DB sheet, the real RD price)
# Using Budget_Price for actual/forecast values (or vice versa) produces a
# value ratio that doesn't track the qty ratio — see the FYTD sanity check:
# with only 2 of 12 fiscal months elapsed, qty reach was a normal ~13%, but
# value reach came out near 49% under budget-price valuation because a few
# high-volume SKUs had a stale/mismatched Budget_Price. Distributor price
# fixes that because it's the price those units actually move at.
INVENTORY_FILE = (
    "/Users/dhanujiamanda/Documents/Projects/Agentic AI /Pipeline/"
    "Agentic-AI-for-Pharma-Stockout-Problem/data/Inventory.xlsx"
)
INVENTORY_DB_SHEET = "DB"

# ─────────────────────────────────────────────────────────────
# Forecast comparison (Forecast tab) — third-party forecasts vs our model vs actual
# ─────────────────────────────────────────────────────────────
# External forecasts supplied by another system, keyed by ProductId/ForecastDate.
# Compared against OUR model's historically-issued forecast AND actual sales,
# all for the SAME month — specifically the CURRENTLY DISPLAYED month on the
# Insights page (the latest CLOSED month, e.g. May), not the next/future
# forecast month (e.g. June). Actuals don't exist yet for a future month, so
# a three-way comparison ("mine vs theirs vs what happened") only makes
# sense once the month has closed. Sheet has one row per
# PlantId/DataMeasure/ForecastDate/ProductId combo; DataMeasure identifies
# which forecasting method produced the row.
FORECAST_COMPARISON_FILE = (
    "/Users/dhanujiamanda/Documents/Projects/Agentic AI /Pipeline/"
    "Agentic-AI-for-Pharma-Stockout-Problem/data/Forecast.xlsx"
)
FORECAST_COMPARISON_SHEET = "Sheet1"

# DataMeasure (as it appears in the sheet) → output column name.
# Renamed to be self-explanatory in the UI table.
FORECAST_MEASURE_COLUMN_MAP = {
    "Approved Consensus Forecast": "Approved_Consensus_Forecast_Qty",
    "Best Fit With MI":            "Best_Fit_With_MI_Forecast_Qty",
    "Consensus Forecast":          "Consensus_Forecast_Qty",
    "Final Forecast":              "Final_Forecast_Qty",
    "3MA Deviation":               "Three_MA_Deviation_Forecast_Qty",
}

_MONTH_MAP = { "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,"jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _force_itemcode_str(df):
    df = df.copy()
    if "ItemCode" in df.columns:
        df["ItemCode"] = (
            pd.to_numeric(df["ItemCode"], errors="coerce")
            .astype("Int64")
            .astype(str)
            .replace("<NA>", np.nan)
        )
    return df


def _safe_wmape_to_accuracy(wmape):
    if wmape is None or pd.isna(wmape):
        return np.nan
    return float(np.clip(100.0 - float(wmape), 0.0, 100.0))


def _parse_forecast_month_dt(df):
    """
    Robustly build a Forecast_Month_dt column from whatever month column exists.
    Handles YYYY-MM-DD, YYYY-MM, and separate Year+Month_Num columns.
    """
    if "Forecast_Month" in df.columns:
        df["Forecast_Month_dt"] = pd.to_datetime(df["Forecast_Month"], errors="coerce")
        mask = df["Forecast_Month_dt"].isna()
        if mask.any():
            df.loc[mask, "Forecast_Month_dt"] = pd.to_datetime(
                df.loc[mask, "Forecast_Month"].astype(str).str.slice(0, 7) + "-01",
                errors="coerce",
            )
    elif {"Forecast_Year", "Forecast_Month_Num"}.issubset(df.columns):
        df["Forecast_Month_dt"] = pd.to_datetime(
            df["Forecast_Year"].astype(int).astype(str)
            + "-"
            + df["Forecast_Month_Num"].astype(int).astype(str).str.zfill(2)
            + "-01",
            errors="coerce",
        )
    else:
        raise ValueError(
            f"No recognisable forecast month column. Columns: {list(df.columns)}"
        )
    return df


def _coerce_price_column(series: pd.Series) -> pd.Series:
    """
    UnitPrice in Inventory.xlsx can come through as a Timestamp instead of
    a number — the source cell was date-formatted in Excel at some point,
    so pandas/openpyxl parses the numeric price as a date (e.g. a price of
    ~2142 reads back as "1905-11-10"). Reverse that: for any value that
    parsed as a datetime, convert it back to its Excel serial-day number,
    which recovers the original numeric price. Genuinely numeric cells
    pass through pd.to_numeric unchanged.
    """
    EPOCH = pd.Timestamp("1899-12-30")

    if pd.api.types.is_datetime64_any_dtype(series):
        return (series - EPOCH).dt.days.astype(float)

    def _one(v):
        if isinstance(v, pd.Timestamp):
            return float((v - EPOCH).days)
        return pd.to_numeric(v, errors="coerce")

    return series.apply(_one)


# ─────────────────────────────────────────────────────────────────────────────
# Loaders
# ─────────────────────────────────────────────────────────────────────────────
def load_champion_wmape_lookup():
    frames = []

    if os.path.exists(CHAMPION_LONG_FILE):
        try:
            cl = joblib.load(CHAMPION_LONG_FILE)
            cl = _force_itemcode_str(cl)
            if {"ItemCode", "Final_Model", "Best_Model_Recent4B_WMAPE"}.issubset(cl.columns):
                tmp = cl[["ItemCode", "Final_Model", "Best_Model_Recent4B_WMAPE"]].copy()
                tmp = tmp.rename(columns={"Final_Model": "Model_Used",
                                          "Best_Model_Recent4B_WMAPE": "Model_WMAPE"})
                tmp["Segment"] = "LONG"
                frames.append(tmp)
        except Exception as e:
            print(f"[INSIGHTS] Warning loading LONG champion map: {e}")

    if os.path.exists(CHAMPION_MEDIUM_FILE):
        try:
            cm = joblib.load(CHAMPION_MEDIUM_FILE)
            cm = _force_itemcode_str(cm)
            if {"ItemCode", "Final_Model", "Best_Model_Recent4_WMAPE"}.issubset(cm.columns):
                tmp = cm[["ItemCode", "Final_Model", "Best_Model_Recent4_WMAPE"]].copy()
                tmp = tmp.rename(columns={"Final_Model": "Model_Used",
                                          "Best_Model_Recent4_WMAPE": "Model_WMAPE"})
                tmp["Segment"] = "MEDIUM"
                frames.append(tmp)
        except Exception as e:
            print(f"[INSIGHTS] Warning loading MEDIUM champion map: {e}")

    if not frames:
        return pd.DataFrame(columns=["ItemCode", "Model_Used", "Model_WMAPE", "Model_Accuracy_%"])

    lookup = pd.concat(frames, ignore_index=True)
    lookup = _force_itemcode_str(lookup)
    lookup = (
        lookup.sort_values("Segment", ascending=True)
        .drop_duplicates(subset=["ItemCode"], keep="first")
    )
    lookup["Model_WMAPE"]      = pd.to_numeric(lookup["Model_WMAPE"], errors="coerce")
    lookup["Model_Accuracy_%"] = lookup["Model_WMAPE"].apply(_safe_wmape_to_accuracy)
    return lookup[["ItemCode", "Model_Used", "Model_WMAPE", "Model_Accuracy_%"]]


def load_actual_sales():
    if not os.path.exists(PROCESSED_DATA_FILE):
        raise FileNotFoundError(f"Processed data not found: {PROCESSED_DATA_FILE}")

    df = pd.read_csv(PROCESSED_DATA_FILE, low_memory=False)
    df = _force_itemcode_str(df)

    required = ["ItemCode", "Year", "Month_Number", "Secondary_Sales_Qty"]
    missing  = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Processed data missing columns: {missing}")

    df["Year"]         = pd.to_numeric(df["Year"],         errors="coerce")
    df["Month_Number"] = pd.to_numeric(df["Month_Number"], errors="coerce")
    df = df.dropna(subset=["Year", "Month_Number"])

    df["Month_dt"] = pd.to_datetime(
        df["Year"].astype(int).astype(str)
        + "-"
        + df["Month_Number"].astype(int).astype(str).str.zfill(2)
        + "-01"
    )

    df["Secondary_Sales_Qty"] = (
        pd.to_numeric(df["Secondary_Sales_Qty"], errors="coerce")
        .fillna(0).clip(lower=0)
    )
    return df


def load_all_sku_sales():
    """
    ALL-SKU closed-month actuals from fact_monthly_closed (via forecast_service,
    focus filter NOT applied). Budgeted items outside the focus list have sales
    ONLY here — processed_data_actual.csv covers focus SKUs only.

    Returns: ItemCode | Month_dt | Sales_Qty
    """
    empty = pd.DataFrame(columns=["ItemCode", "Month_dt", "Sales_Qty"])
    try:
        df = load_fact_history_all_skus()
        if df is None or df.empty:
            return empty

        df = _force_itemcode_str(df)
        df = df.dropna(subset=["ItemCode"])
        df["Month_dt"] = pd.to_datetime(
            df["Year"].astype(int).astype(str)
            + "-"
            + df["Month_Number"].astype(int).astype(str).str.zfill(2)
            + "-01"
        )
        df = df.rename(columns={"Secondary_Sales_Qty": "Sales_Qty"})
        out = df[["ItemCode", "Month_dt", "Sales_Qty"]].copy()

        print(f"[INSIGHTS] All-SKU actuals loaded: {out['ItemCode'].nunique()} SKUs, "
              f"{out['Month_dt'].min():%b %Y} → {out['Month_dt'].max():%b %Y}")
        return out

    except Exception as e:
        print(f"[INSIGHTS] Warning loading all-SKU actuals: {e}")
        return empty


def load_trend_forecast_lookup(current_month_dt):
    """
    Trend-baseline forecasts (built by trend_forecast_engine on the forecast
    side) for budgeted SKUs outside the model list.

    Preference order:
      1. forecast_trend_history.csv rows whose Forecast_Month == the current
         (latest closed) month → the trend that was actually issued for it
      2. fallback: forecast_trend_latest.csv (rolling-avg values — acceptable
         month-agnostic approximation until history accumulates)

    Returns: ItemCode | Trend_Forecast | Trend_Model
    """
    empty = pd.DataFrame(columns=["ItemCode", "Trend_Forecast", "Trend_Model"])

    def _normalize(df):
        df = _force_itemcode_str(df)
        df = df.dropna(subset=["ItemCode"])
        df["Forecast_Qty"] = pd.to_numeric(df.get("Forecast_Qty"), errors="coerce")
        df = df.dropna(subset=["Forecast_Qty"])
        if "Used_Model" not in df.columns:
            df["Used_Model"] = "TREND"
        return df

    try:
        current_label = pd.Timestamp(current_month_dt).strftime("%Y-%m")

        hist = load_trend_forecast_history()
        if hist is not None and not hist.empty and "Forecast_Month" in hist.columns:
            hist = _normalize(hist)
            hist = hist[hist["Forecast_Month"].astype(str).str.startswith(current_label)]
            if not hist.empty:
                if "Run_Date" in hist.columns:
                    hist = hist.sort_values("Run_Date")
                hist = hist.drop_duplicates(subset=["ItemCode"], keep="last")
                print(f"[INSIGHTS] Trend lookup: {len(hist)} SKUs from history for {current_label}.")
                return (
                    hist[["ItemCode", "Forecast_Qty", "Used_Model"]]
                    .rename(columns={"Forecast_Qty": "Trend_Forecast",
                                     "Used_Model":   "Trend_Model"})
                )

        latest = load_trend_forecast_latest()
        if latest is not None and not latest.empty:
            latest = _normalize(latest)
            latest = latest.drop_duplicates(subset=["ItemCode"], keep="last")
            print(f"[INSIGHTS] Trend lookup: {len(latest)} SKUs from latest file "
                  f"(no history rows for {current_label}).")
            return (
                latest[["ItemCode", "Forecast_Qty", "Used_Model"]]
                .rename(columns={"Forecast_Qty": "Trend_Forecast",
                                 "Used_Model":   "Trend_Model"})
            )

        print("[INSIGHTS] Trend lookup: no trend forecast files found. "
              "Run forecast export to generate forecast_trend_latest.csv.")
        return empty

    except Exception as e:
        print(f"[INSIGHTS] Warning loading trend forecasts: {e}")
        return empty


def load_forecast_latest():
    if not os.path.exists(FORECAST_FILE):
        raise FileNotFoundError(f"Forecast file not found: {FORECAST_FILE}")

    df = pd.read_csv(FORECAST_FILE, low_memory=False)
    df = _force_itemcode_str(df)

    for col in ["Forecast_Prediction", "Forecast_Qty", "Predicted_Qty"]:
        if col in df.columns:
            df = df.rename(columns={col: "Forecast_Qty"})
            break

    if "Forecast_Qty" not in df.columns:
        raise ValueError(f"Forecast file has no forecast qty column. Columns: {list(df.columns)}")

    df["Forecast_Qty"] = pd.to_numeric(df["Forecast_Qty"], errors="coerce").fillna(0).clip(lower=0)
    df = _parse_forecast_month_dt(df)
    return df


def load_agency_mapping():
    """
    Canonical ItemCode -> Agency/ItemName resolver, sourced from
    sku_master_full.csv (services/sku_master_service.py — built off
    Budget.xlsx + Agency map.xlsx).

    This is the single source of truth for display fields across every
    Insights table (actuals, forecast, budget). It covers BOTH real-coded
    products AND synthetic-coded ("SYN-...") budgeted products that have no
    ItemCode in SAP/Budget yet — so a budget row and a forecast row for the
    same product always resolve to the same ItemCode/Agency/ItemName.
    """
    master = load_sku_master_full()  # ProductCode, ProductName, Agency, AgencyCode, Is_Synthetic_Code
    if master is None or master.empty:
        return pd.DataFrame(columns=["ItemCode", "ItemName", "Agency"])

    out = master.rename(columns={
        "ProductCode": "ItemCode",
        "ProductName": "ItemName",
    })[["ItemCode", "ItemName", "Agency"]].copy()

    out["ItemCode"] = out["ItemCode"].astype(str)
    return out.drop_duplicates("ItemCode")


def load_master_sku_codes() -> set:
    """
    The full budgeted-SKU universe (real + synthetic codes), used to scope
    every KPI total in this file. Sales/forecast/loss rows for an ItemCode
    outside this set (e.g. a focus SKU the model forecasts but which has no
    budget entry at all) still appear in the per-SKU tables — just excluded
    from summed totals, so numbers stay internally consistent instead of
    mixing two different SKU universes silently.
    """
    master = load_sku_master_full()
    if master is None or master.empty or "ProductCode" not in master.columns:
        return set()
    return set(master["ProductCode"].astype(str))


def load_distributor_price_lookup():
    """
    Reads Inventory.xlsx -> "DB" sheet: the RD (distributor-to-customer)
    unit price. Secondary_Sales_Qty and Forecast_Qty are both distributor
    sell-through (secondary movement), so THIS is the correct price to
    value them at. Budget_Price (from Budget.xlsx) values PRIMARY movement
    (budget qty) and must never be applied to sales/forecast qty, or vice
    versa — mixing the two produces a value ratio that doesn't track the
    qty ratio (see the FYTD sanity check in build_agency_performance_table).

    Per SKU: takes the price from its most recent available month
    (averaged across distributors/batches in that month, since price can
    vary slightly by batch/distributor).

    Returns: ItemCode | Distributor_Unit_Price
    """
    empty = pd.DataFrame(columns=["ItemCode", "Distributor_Unit_Price"])
    if not os.path.exists(INVENTORY_FILE):
        print(f"[INSIGHTS] Inventory file not found: {INVENTORY_FILE}")
        return empty

    try:
        df = pd.read_excel(INVENTORY_FILE, sheet_name=INVENTORY_DB_SHEET)
        df.columns = df.columns.astype(str).str.strip()

        required = ["Month", "ItemCode", "UnitPrice"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            print(f"[INSIGHTS] Inventory DB sheet missing columns: {missing}. "
                  f"Found: {list(df.columns)}")
            return empty

        df["ItemCode"] = (
            pd.to_numeric(df["ItemCode"], errors="coerce")
            .astype("Int64").astype(str).replace("<NA>", np.nan)
        )
        df = df.dropna(subset=["ItemCode"])

        df["Month_dt"] = pd.to_datetime(df["Month"], errors="coerce")
        df = df.dropna(subset=["Month_dt"])

        df["UnitPrice"] = _coerce_price_column(df["UnitPrice"])
        df = df.dropna(subset=["UnitPrice"])
        df = df[df["UnitPrice"] > 0]

        if df.empty:
            print("[INSIGHTS] Inventory DB sheet: no usable UnitPrice rows after cleaning.")
            return empty

        latest_month = df["Month_dt"].max()
        df = df.sort_values(["ItemCode", "Month_dt"])
        latest_per_sku = df.groupby("ItemCode").tail(1)[["ItemCode", "UnitPrice"]]

        out = (
            latest_per_sku.groupby("ItemCode", as_index=False)["UnitPrice"]
            .mean()
            .rename(columns={"UnitPrice": "Distributor_Unit_Price"})
        )
        out["Distributor_Unit_Price"] = out["Distributor_Unit_Price"].round(2)

        print(f"[INSIGHTS] Distributor price loaded for {len(out)} SKUs "
              f"(latest inventory data through {latest_month:%b %Y}).")
        return out

    except Exception as e:
        print(f"[INSIGHTS] Warning loading distributor price: {e}")
        import traceback; traceback.print_exc()
        return empty


def load_shp_lookup():
    empty = pd.DataFrame(columns=[
        "ItemCode", "L3M_Moving_Avg",
        "WH_Stock", "DB_Stock", "WH_SHP", "DB_SHP", "Current_SHP",
    ])

    if not os.path.exists(PROCESSED_DATA_FILE):
        return empty

    try:
        df = pd.read_csv(PROCESSED_DATA_FILE, low_memory=False)
        df = _force_itemcode_str(df)

        required = [
            "ItemCode", "Year", "Month_Number", "Secondary_Sales_Qty",
            "Available_Primary_Inventory_Qty", "Distributor_Inventory_Qty",
        ]
        if any(c not in df.columns for c in required):
            return empty

        for c in ["Secondary_Sales_Qty", "Available_Primary_Inventory_Qty", "Distributor_Inventory_Qty"]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

        df = df.sort_values(["ItemCode", "Year", "Month_Number"])
        df["L3M_Moving_Avg"] = (
            df.groupby("ItemCode")["Secondary_Sales_Qty"]
            .transform(lambda x: x.rolling(3, min_periods=1).mean())
        )

        latest = df.groupby("ItemCode").tail(1).copy()
        latest["WH_Stock"] = latest["Available_Primary_Inventory_Qty"]
        latest["DB_Stock"]  = latest["Distributor_Inventory_Qty"]

        latest["WH_SHP"] = np.where(
            latest["L3M_Moving_Avg"] > 0,
            latest["WH_Stock"] / latest["L3M_Moving_Avg"], np.nan,
        )
        latest["DB_SHP"] = np.where(
            latest["L3M_Moving_Avg"] > 0,
            latest["DB_Stock"]  / latest["L3M_Moving_Avg"], np.nan,
        )
        latest["Current_SHP"] = np.where(
            latest["L3M_Moving_Avg"] > 0,
            (latest["WH_Stock"] + latest["DB_Stock"]) / latest["L3M_Moving_Avg"], np.nan,
        )

        for c in ["L3M_Moving_Avg", "WH_Stock", "DB_Stock", "WH_SHP", "DB_SHP", "Current_SHP"]:
            latest[c] = latest[c].round(2)

        return latest[["ItemCode", "L3M_Moving_Avg", "WH_Stock", "DB_Stock",
                        "WH_SHP", "DB_SHP", "Current_SHP"]].copy()

    except Exception as e:
        print(f"[INSIGHTS] Warning computing SHP: {e}")
        return empty


# ─────────────────────────────────────────────────────────────────────────────
# Loss analysis loader
# ─────────────────────────────────────────────────────────────────────────────
def load_current_month_forecast_loss():
    """
    Compute per-SKU loss for the latest CLOSED month.

    Loss decomposition
    ──────────────────
    Raw_Loss_Qty     = max(Forecast - Actual_Sales, 0)

    Trade_Stock_Qty  = Available_Primary_Inventory_Qty (current month)
                     + Distributor_Inventory_Qty        (current month)

    Other_Loss_Qty   = min(Raw_Loss, Trade_Stock)
        → loss that trade stock COULD have absorbed
        → cause: promo miss / demand drop / execution issue

    Stockout_Loss_Qty = max(Raw_Loss - Trade_Stock, 0)
        → loss BEYOND what stock could cover
        → cause: genuine stockout / supply failure

    Loss_Reason:
        Raw_Loss <= 0          → "None"
        Stockout_Loss_Qty > 0  → "Stockout"
        else                   → "Other"

    Stock snapshot used is the CURRENT month's own row (not prior month),
    because that is what was physically on hand to fulfil the month's demand.
    """
    empty = pd.DataFrame(columns=[
        "ItemCode", "Current_Month_Label",
        "Current_Month_Forecast",
        "Current_Month_Sales",       # kept for join convenience
        "WH_Stock_Current",
        "DB_Stock_Current",
        "Trade_Stock_Qty",
        "Raw_Loss_Qty",
        "Other_Loss_Qty",
        "Stockout_Loss_Qty",
        "Current_Month_Loss_Qty",    # alias = Raw_Loss_Qty for KPI strip
        "Stockout_Flag",
        "Loss_Reason",
    ])

    if not (os.path.exists(PROCESSED_DATA_FILE) and os.path.exists(FORECAST_HISTORY_FILE)):
        print("[INSIGHTS] Loss lookup skipped — missing processed data or forecast history file.")
        return empty

    try:
        # ── actuals + stock from CURRENT month ────────────────────────────────
        df = pd.read_csv(PROCESSED_DATA_FILE, low_memory=False)
        df = _force_itemcode_str(df)

        required = [
            "ItemCode", "Year", "Month_Number",
            "Secondary_Sales_Qty",
            "Available_Primary_Inventory_Qty",
            "Distributor_Inventory_Qty",
        ]
        if any(c not in df.columns for c in required):
            print(f"[INSIGHTS] Loss lookup: missing columns in processed data.")
            return empty

        df["Year"]         = pd.to_numeric(df["Year"],         errors="coerce")
        df["Month_Number"] = pd.to_numeric(df["Month_Number"], errors="coerce")
        df = df.dropna(subset=["Year", "Month_Number"])

        df["Month_dt"] = pd.to_datetime(
            df["Year"].astype(int).astype(str) + "-"
            + df["Month_Number"].astype(int).astype(str).str.zfill(2) + "-01"
        )

        for c in ["Secondary_Sales_Qty",
                  "Available_Primary_Inventory_Qty",
                  "Distributor_Inventory_Qty"]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).clip(lower=0)

        df = df.sort_values(["ItemCode", "Month_dt"])

        all_months = sorted(df["Month_dt"].unique())
        if not all_months:
            return empty

        current_month_dt = all_months[-1]
        current_label    = pd.Timestamp(current_month_dt).strftime("%b %Y")

        # Aggregate current-month rows per SKU
        current_rows = df[df["Month_dt"] == current_month_dt]

        actual_agg = (
            current_rows
            .groupby("ItemCode")
            .agg(
                Current_Month_Sales  =("Secondary_Sales_Qty",             "sum"),
                WH_Stock_Current     =("Available_Primary_Inventory_Qty", "sum"),
                DB_Stock_Current     =("Distributor_Inventory_Qty",       "sum"),
            )
            .reset_index()
        )

        actual_agg["Trade_Stock_Qty"] = (
            actual_agg["WH_Stock_Current"].fillna(0)
            + actual_agg["DB_Stock_Current"].fillna(0)
        )

        # ── forecast for current month from history file ───────────────────────
        fdf = pd.read_csv(FORECAST_HISTORY_FILE, low_memory=False)
        fdf = _force_itemcode_str(fdf)

        for col in ["Forecast_Prediction", "Forecast_Qty", "Predicted_Qty"]:
            if col in fdf.columns:
                fdf = fdf.rename(columns={col: "Forecast_Qty"})
                break

        if "Forecast_Qty" not in fdf.columns:
            print(f"[INSIGHTS] Loss lookup: no Forecast_Qty column in history. Cols: {list(fdf.columns)}")
            return empty

        fdf["Forecast_Qty"] = pd.to_numeric(fdf["Forecast_Qty"], errors="coerce")
        fdf = _parse_forecast_month_dt(fdf)

        # Keep only rows targeting current month
        fdf = fdf[fdf["Forecast_Month_dt"] == current_month_dt].copy()
        print(f"[INSIGHTS] History rows for {current_label}: {len(fdf)}")

        # Optional horizon filter (soft — skip if no matching values)
        if "Horizon" in fdf.columns:
            m1 = fdf["Horizon"].astype(str).isin(["M+1", "1", "m+1", "M1"])
            if m1.any():
                fdf = fdf[m1]
            else:
                print("[INSIGHTS] Horizon filter: no M+1 rows — skipping filter.")

        # Optional source filter (soft)
        if "Forecast_Source" in fdf.columns:
            ai = fdf["Forecast_Source"].astype(str).isin([
                "AI_CHAMPION_MODEL", "AI", "CHAMPION", "champion_model",
            ])
            if ai.any():
                fdf = fdf[ai]
            else:
                print("[INSIGHTS] Source filter: no AI rows — skipping filter.")

        if fdf.empty:
            print("[INSIGHTS] Loss lookup: no forecast history rows after filtering.")
            return empty

        # Take the latest run per SKU
        sort_col = next(
            (c for c in ["Run_Date", "Run_ID", "run_date", "run_id"] if c in fdf.columns),
            None,
        )
        if sort_col:
            fdf = fdf.sort_values(["ItemCode", sort_col])

        current_forecast = (
            fdf.drop_duplicates(subset=["ItemCode"], keep="last")
            [["ItemCode", "Forecast_Qty"]]
            .rename(columns={"Forecast_Qty": "Current_Month_Forecast"})
        )

        # ── Merge and compute loss decomposition ──────────────────────────────
        result = actual_agg.merge(current_forecast, on="ItemCode", how="left")

        result["Current_Month_Forecast"] = (
            pd.to_numeric(result["Current_Month_Forecast"], errors="coerce")
        )
        result["Current_Month_Sales"]  = result["Current_Month_Sales"].fillna(0)
        result["Trade_Stock_Qty"]      = result["Trade_Stock_Qty"].fillna(0)

        # Raw loss = Forecast - Sales  (clamped ≥ 0)
        result["Raw_Loss_Qty"] = np.where(
            result["Current_Month_Forecast"].notna(),
            np.maximum(result["Current_Month_Forecast"] - result["Current_Month_Sales"], 0),
            0,
        )

        # Other loss = portion that trade stock COULD have absorbed
        result["Other_Loss_Qty"] = np.minimum(
            result["Raw_Loss_Qty"],
            result["Trade_Stock_Qty"],
        )

        # Stockout loss = portion BEYOND what stock can cover
        result["Stockout_Loss_Qty"] = np.maximum(
            result["Raw_Loss_Qty"] - result["Trade_Stock_Qty"],
            0,
        )

        result["Current_Month_Loss_Qty"] = result["Raw_Loss_Qty"]

        result["Stockout_Flag"] = result["Stockout_Loss_Qty"] > 0

        result["Loss_Reason"] = np.select(
            [
                result["Raw_Loss_Qty"] <= 0,
                result["Stockout_Loss_Qty"] > 0,
            ],
            ["None", "Stockout"],
            default="Other",
        )

        result["Current_Month_Label"] = current_label

        # Round numeric columns
        for c in [
            "Current_Month_Forecast", "Current_Month_Sales",
            "WH_Stock_Current", "DB_Stock_Current", "Trade_Stock_Qty",
            "Raw_Loss_Qty", "Other_Loss_Qty", "Stockout_Loss_Qty",
            "Current_Month_Loss_Qty",
        ]:
            result[c] = pd.to_numeric(result[c], errors="coerce").fillna(0).round(2)

        keep = [c for c in empty.columns if c in result.columns]
        return result[keep].copy()

    except Exception as e:
        print(f"[INSIGHTS] Warning computing current-month loss: {e}")
        import traceback; traceback.print_exc()
        return empty


# ─────────────────────────────────────────────────────────────────────────────
# Budget loader — "All Budget 26 27 FY" sheet (ALL budgeted items)
# ─────────────────────────────────────────────────────────────────────────────
def _parse_budget_month_col(col):
    """
    Handles month headers in either format:
      • "Apr-26", "Jan-27", "Apr 2026"   → month-year text
      • 2026-04-01 (real Excel dates)    → generic datetime
    Month-year formats are tried FIRST: the generic parser would read
    "Jan-27" as January 27th of the current year (wrong fiscal year).
    """
    s = str(col).strip()

    known_non_month = {
        "agency", "itemcode", "itemname", "budgetprice", "totalqty",
        "pid", "product", "code", "name",
    }
    if s.lower() in known_non_month:
        return None

    # Explicit month-year formats first
    for fmt in ("%b-%y", "%b-%Y", "%b %y", "%b %Y",
                "%B-%y", "%B-%Y", "%B %y", "%B %Y"):
        ts = pd.to_datetime(s, format=fmt, errors="coerce")
        if not pd.isna(ts):
            return pd.Timestamp(ts.year, ts.month, 1)

    # Generic fallback for real date headers (needs a digit to avoid
    # accidental parses of plain words)
    if not any(ch.isdigit() for ch in s):
        return None

    ts = pd.to_datetime(s, errors="coerce")
    if pd.isna(ts):
        return None

    return pd.Timestamp(ts.year, ts.month, 1)


def load_budget_lookup(current_month_dt):
    """
    Reads the "All Budget 26 27 FY" sheet — every budgeted SKU, including
    items that have a budget but no sales/forecast. Totals MUST be computed
    from this full set, never from focus items only.

    Returns (per_sku_df, budget_meta)

    per_sku_df columns:
        ItemCode, Agency_Budget, Budget_Qty (current month), Annual_Budget_Qty

    ItemCode resolution:
        Every row is matched to services/sku_master_service.py's
        sku_master_full.csv by (Agency, ItemName) — the SAME canonical
        source used everywhere else in Insights. Real ItemCodes and
        synthetic "SYN-..." codes both come from there, so budget rows join
        cleanly against anything else keyed by that master (agency map,
        forecast pipeline joins, etc.) instead of using a locally-invented key.

    budget_meta keys:
        fiscal_start (Timestamp | None), fiscal_end (Timestamp | None),
        month_labels (list[str]), current_month_found (bool)
    """
    empty_sku  = pd.DataFrame(columns=["ItemCode", "Agency_Budget", "ItemName_Budget", "Budget_Qty", "Annual_Budget_Qty", "Budget_Price", "Is_Unmapped"])
    empty_meta = {"fiscal_start": None, "fiscal_end": None, "month_labels": [], "current_month_found": False}

    if not os.path.exists(BUDGET_FILE):
        print(f"[INSIGHTS] Budget file not found: {BUDGET_FILE}")
        return empty_sku, empty_meta

    try:
        df = pd.read_excel(BUDGET_FILE, sheet_name=BUDGET_SHEET_NAME, header=0)

        df.columns = df.columns.astype(str).str.strip()
        cols = list(df.columns)

        # ── Column detection: the two budget sheets use different schemas ────
        #   Focus sheet:  Agency | ItemCode | ... | 2026-04-01 | 2026-05-01 | ...
        #   All sheet:    Agency | PID | Product | BudgetPrice | AprQty | ... | MarQty | TotalQty
        agency_col   = "Agency"
        itemcode_col = next((c for c in ["ItemCode", "PID", "Code"] if c in df.columns), None)
        itemname_col = next((c for c in ["Product", "ItemName", "Name"] if c in df.columns), None)

        if agency_col not in df.columns or itemcode_col is None:
            raise ValueError(f"Budget columns missing (need Agency + ItemCode/PID). Found: {cols}")

        # Forward-fill merged agency cells, then null-safe fallback
        df[agency_col] = df[agency_col].ffill()
        df[agency_col] = df[agency_col].astype(str).str.strip()
        df.loc[df[agency_col].isin(["", "nan", "None", "NaN"]), agency_col] = np.nan
        df[agency_col] = df[agency_col].fillna("Unknown Agency")

        # ── Identify monthly budget columns ──────────────────────────────────
        # Format A: real date headers (2026-04-01, "Apr-26", ...)
        month_col_map = {}
        for col in cols:
            ts = _parse_budget_month_col(col)
            if ts is not None:
                month_col_map[col] = ts

        # Format B: "AprQty" ... "MarQty" (April-start fiscal year).
        # Anchor the FY to the current month: Apr–Dec belong to fy_start_year,
        # Jan–Mar to fy_start_year + 1.
        if not month_col_map:
            fy_start_year = (
                current_month_dt.year
                if current_month_dt.month >= 4
                else current_month_dt.year - 1
            )
            for col in cols:
                key = col.lower().replace("qty", "").strip()
                if key in _MONTH_MAP:
                    m    = _MONTH_MAP[key]
                    year = fy_start_year if m >= 4 else fy_start_year + 1
                    month_col_map[col] = pd.Timestamp(year, m, 1)

        if not month_col_map:
            print(f"[INSIGHTS] Budget: no monthly columns found. Columns: {cols}")
            return empty_sku, empty_meta

        # ── ItemCode resolution via sku_master_full.csv (canonical) ──────────
        # Real numeric codes are kept as-is (canonicalised to int-string).
        # Rows with no numeric code (new/not-yet-coded products) are matched
        # to the master's synthetic "SYN-{AgencyCode}-{slug}" code by
        # (Agency, ItemName) — the same key sku_master_service used to
        # generate them, so this table's ItemCodes are identical to the ones
        # used everywhere else in the pipeline.
        master_df = load_sku_master_full()  # ProductCode, ProductName, Agency, AgencyCode, Is_Synthetic_Code
        master_df = master_df.copy()
        master_df["_key"] = (
            master_df["Agency"].astype(str).str.strip().str.upper()
            + "::" + master_df["ProductName"].astype(str).str.strip().str.upper()
        )
        code_lookup = (
            master_df.drop_duplicates("_key")
            .set_index("_key")[["ProductCode", "Is_Synthetic_Code"]]
            .to_dict("index")
        )

        name_series = (
            df[itemname_col].astype(str).str.strip().replace({"nan": "", "None": ""})
            if itemname_col else pd.Series("", index=df.index)
        )
        match_key = (
            df[agency_col].astype(str).str.strip().str.upper()
            + "::" + name_series.str.upper()
        )

        df["ItemCode"]  = match_key.map(lambda k: code_lookup.get(k, {}).get("ProductCode"))
        df["_unmapped"] = match_key.map(lambda k: code_lookup.get(k, {}).get("Is_Synthetic_Code"))
        df["_unmapped"] = pd.to_numeric(df["_unmapped"], errors="coerce").fillna(0).astype(bool)

        # Fallback for the rare row the master didn't match (shouldn't
        # normally happen — both are built from the same sheet) — use the
        # sheet's own numeric code directly rather than dropping the row.
        raw_code = df[itemcode_col].astype(str).str.strip()
        num_code = pd.to_numeric(df[itemcode_col], errors="coerce")
        fallback_code = pd.Series(
            np.where(num_code.notna(), num_code.astype("Int64").astype(str), np.nan),
            index=df.index,
        )
        df["ItemCode"] = df["ItemCode"].fillna(fallback_code)

        unmatched = df["ItemCode"].isna()
        blank_name = raw_code.isin(["", "nan", "None", "NaN", "<NA>"]) & name_series.isin(["", "nan", "None"])
        drop_mask = unmatched & blank_name  # true blank/subtotal rows
        if drop_mask.any():
            print(f"[INSIGHTS] Budget: dropping {int(drop_mask.sum())} blank/subtotal rows.")
        df = df[~drop_mask].copy()

        still_unmatched = df["ItemCode"].isna()
        if still_unmatched.any():
            print(f"[INSIGHTS] Budget: {int(still_unmatched.sum())} rows had no SKU-master match "
                  f"and no numeric ItemCode — dropping. Run sku_master_service.build_sku_master() "
                  f"to refresh the master if this is unexpected.")
        df = df[~still_unmatched].copy()

        itemcode_col = "ItemCode"  # everything downstream keys off this now

        n_unmapped = int(df["_unmapped"].sum())
        if n_unmapped:
            print(f"[INSIGHTS] Budget: {n_unmapped} unmapped/new-product rows kept "
                  f"(synthetic ItemCode from sku_master_full.csv, Forecast_Source=NONE).")

        # Null-safe monthly values
        for col in month_col_map:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).clip(lower=0)

        # ── Budgeted unit price (BudgetPrice column) — PRIMARY movement price.
        # Used ONLY to value Budget_Qty / Annual_Budget_Qty. Never applied to
        # actual sales or forecast qty (those are secondary movement and use
        # Distributor_Unit_Price from Inventory.xlsx instead — see
        # load_distributor_price_lookup and build_budget_analysis_table).
        price_col = next(
            (c for c in ["BudgetPrice", "Budget_Price", "UnitPrice", "Unit_Price", "Price"]
             if c in df.columns),
            None,
        )
        df["_price"] = (
            pd.to_numeric(df[price_col], errors="coerce").fillna(0).clip(lower=0)
            if price_col else 0.0
        )
        if not price_col:
            print(f"[INSIGHTS] Budget: no BudgetPrice column found — values will be 0. Columns: {cols}")

        all_month_cols = list(month_col_map.keys())
        df["_annual"] = df[all_month_cols].sum(axis=1)

        cur_col = next(
            (col for col, ts in month_col_map.items() if ts == current_month_dt),
            None,
        )
        df["_current"] = df[cur_col] if cur_col else 0.0
        if not cur_col:
            available = [ts.strftime("%b %Y") for ts in month_col_map.values()]
            print(f"[INSIGHTS] Budget: no col for {current_month_dt.strftime('%b %Y')}. Available: {available}")

        # Duplicate ItemCodes (e.g. split rows) → sum budgets, keep first agency/name
        df["_itemname"] = (
            df[itemname_col].astype(str).str.strip().replace({"nan": "", "None": ""})
            if itemname_col else ""
        )
        per_sku = (
            df.groupby(itemcode_col, as_index=False)
            .agg(
                Agency_Budget    =(agency_col,  "first"),
                ItemName_Budget  =("_itemname", "first"),
                Budget_Qty       =("_current",  "sum"),
                Annual_Budget_Qty=("_annual",   "sum"),
                Budget_Price     =("_price",    "first"),
                Is_Unmapped      =("_unmapped", "max"),
            )
            .rename(columns={itemcode_col: "ItemCode"})
        )
        per_sku["Is_Unmapped"] = per_sku["Is_Unmapped"].astype(bool)

        month_ts_sorted = sorted(month_col_map.values())
        budget_meta = {
            "fiscal_start":        month_ts_sorted[0],
            "fiscal_end":          month_ts_sorted[-1],
            "month_labels":        [ts.strftime("%b %Y") for ts in month_ts_sorted],
            "current_month_found": bool(cur_col),
        }

        print(f"[INSIGHTS] Budget loaded from '{BUDGET_SHEET_NAME}': "
              f"{len(per_sku)} SKUs, FY {budget_meta['month_labels'][0]} → {budget_meta['month_labels'][-1]}.")
        return per_sku, budget_meta

    except Exception as e:
        print(f"[INSIGHTS] Warning loading budget: {e}")
        import traceback; traceback.print_exc()
        return empty_sku, empty_meta


# ─────────────────────────────────────────────────────────────────────────────
# Forecast comparison loader — third-party forecasts (Forecast.xlsx)
# ─────────────────────────────────────────────────────────────────────────────
def load_external_forecast_comparison(target_month_dt):
    """
    Reads Forecast.xlsx (Sheet1) — third-party forecasts per ProductId,
    filtered to `target_month_dt`. Called with the CURRENTLY DISPLAYED
    Insights month (the latest closed month) so the result can be compared
    against both our own model's forecast for that month AND actual sales.

    Pivots the known DataMeasure labels into separate, clearly-named columns
    (see FORECAST_MEASURE_COLUMN_MAP). Any DataMeasure value outside that
    map is ignored. Where a SKU has more than one row for the same
    DataMeasure/month (re-runs), the most recently updated row wins
    (LastUpdate, then CreationDate).

    Returns: ItemCode | Approved_Consensus_Forecast_Qty
             | Best_Fit_With_MI_Forecast_Qty | Consensus_Forecast_Qty
             | Final_Forecast_Qty | Three_MA_Deviation_Forecast_Qty
    """
    out_cols = ["ItemCode"] + list(FORECAST_MEASURE_COLUMN_MAP.values())
    empty = pd.DataFrame(columns=out_cols)

    if not os.path.exists(FORECAST_COMPARISON_FILE):
        print(f"[INSIGHTS] Forecast comparison file not found: {FORECAST_COMPARISON_FILE}")
        return empty

    try:
        df = pd.read_excel(FORECAST_COMPARISON_FILE, sheet_name=FORECAST_COMPARISON_SHEET)
        df.columns = df.columns.astype(str).str.strip()

        required = ["ProductId", "DataMeasure", "ForecastDate", "Quantity"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            print(f"[INSIGHTS] Forecast comparison file missing columns: {missing}. "
                  f"Found: {list(df.columns)}")
            return empty

        df["ForecastDate"] = pd.to_datetime(df["ForecastDate"], errors="coerce")
        df = df.dropna(subset=["ForecastDate"])

        target_month = pd.Timestamp(
            pd.Timestamp(target_month_dt).year, pd.Timestamp(target_month_dt).month, 1
        )
        # Normalise ForecastDate to month-start before comparing, so any
        # day-of-month value in the sheet still matches the target month.
        df["_fc_month"] = df["ForecastDate"].values.astype("datetime64[M]")
        df = df[df["_fc_month"] == target_month].copy()

        if df.empty:
            print(f"[INSIGHTS] Forecast comparison: no rows for {target_month:%b %Y}.")
            return empty

        df["ItemCode"] = (
            pd.to_numeric(df["ProductId"], errors="coerce")
            .astype("Int64").astype(str).replace("<NA>", np.nan)
        )
        df = df.dropna(subset=["ItemCode"])

        df["DataMeasure"] = df["DataMeasure"].astype(str).str.strip()
        df = df[df["DataMeasure"].isin(FORECAST_MEASURE_COLUMN_MAP.keys())]
        if df.empty:
            print("[INSIGHTS] Forecast comparison: no rows matching known DataMeasure "
                  f"labels ({list(FORECAST_MEASURE_COLUMN_MAP.keys())}).")
            return empty

        df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0)

        # Keep the most recently updated row per ItemCode + DataMeasure
        sort_cols = [c for c in ["CreationDate", "LastUpdate"] if c in df.columns]
        for c in sort_cols:
            df[c] = pd.to_datetime(df[c], errors="coerce")
        if sort_cols:
            df = df.sort_values(["ItemCode", "DataMeasure"] + sort_cols)
        df = df.drop_duplicates(subset=["ItemCode", "DataMeasure"], keep="last")

        pivot = df.pivot_table(
            index="ItemCode", columns="DataMeasure", values="Quantity", aggfunc="last"
        ).reset_index()

        pivot = pivot.rename(columns=FORECAST_MEASURE_COLUMN_MAP)
        for col in FORECAST_MEASURE_COLUMN_MAP.values():
            if col not in pivot.columns:
                pivot[col] = np.nan
            pivot[col] = pd.to_numeric(pivot[col], errors="coerce").round(2)

        print(f"[INSIGHTS] Forecast comparison loaded: {len(pivot)} SKUs for {target_month:%b %Y}.")
        return pivot[out_cols].copy()

    except Exception as e:
        print(f"[INSIGHTS] Warning loading forecast comparison file: {e}")
        import traceback; traceback.print_exc()
        return empty


# ─────────────────────────────────────────────────────────────────────────────
# Budget analysis table builder — ALL budgeted items
# ─────────────────────────────────────────────────────────────────────────────
def build_budget_analysis_table(budget_sku_lookup, budget_meta,
                                actuals_df, agency_map, loss_lookup,
                                latest_month_dt, price_lookup,
                                all_sales_df=None, trend_lookup=None):
    """
    One row per budgeted SKU (from "All Budget 26 27 FY"), even when the SKU
    has no sales and no forecast.

    Actuals (current month + FYTD) come from fact_monthly_closed (ALL SKUs);
    processed_data_actual.csv (focus SKUs only) is the fallback.

    Forecast per SKU:
        model forecast when available          → Forecast_Source = "MODEL"
        else trend baseline (trend engine)     → Forecast_Source = "TREND"
        else null (budgeted, no history)       → Forecast_Source = "NONE"

    PRICING (do not mix these two — see module-level note):
        Budget_Qty / Annual_Budget_Qty are PRIMARY movement -> valued at
            Budget_Price (Budget.xlsx planning price).
        Current_Month_Sales / FYTD_Sales_Qty are SECONDARY movement
            (distributor sell-through) -> valued at Distributor_Unit_Price
            (Inventory.xlsx DB sheet), falling back to Budget_Price only
            when a SKU has no inventory-price history yet.

    Columns:
        Agency, ItemCode, ItemName,
        Budget_Qty              (current month budget)
        Current_Month_Sales     (actual, 0 when no sales)
        Achievement_%           (Actual / Budget, null when Budget = 0)
        Current_Month_Forecast  (model or trend; null when neither exists)
        Forecast_Source         ("MODEL" | "TREND" | "NONE")
        Trend_Model             (trend rule used, only for TREND rows)
        Possible_Achievement_%  (Forecast / Budget, null when Budget = 0 or no forecast)
        Annual_Budget_Qty
        FYTD_Sales_Qty          (fiscal-year-to-date actual sales)
        Annual_Reach_%          (FYTD / Annual, null when Annual = 0)
        Annual_Remaining_Qty    (max(Annual − FYTD, 0))
        Budget_Price            (primary/planning price — values Budget_*)
        Distributor_Unit_Price  (secondary/RD price — values sales/forecast)
        Price_Source            ("DISTRIBUTOR" | "BUDGET_FALLBACK" | "NONE")
    """
    empty = pd.DataFrame(columns=[
        "Agency", "ItemCode", "ItemName",
        "Budget_Qty", "Current_Month_Sales", "Achievement_%",
        "Current_Month_Forecast", "Forecast_Source", "Trend_Model",
        "Possible_Achievement_%",
        "Annual_Budget_Qty", "FYTD_Sales_Qty",
        "Annual_Reach_%", "Annual_Remaining_Qty",
        "Budget_Price", "Budget_Value", "Annual_Budget_Value",
        "Distributor_Unit_Price", "Price_Source",
        "Current_Month_Sales_Value", "Current_Month_Forecast_Value",
        "FYTD_Sales_Value",
        "Is_Unmapped",
    ])

    if budget_sku_lookup is None or budget_sku_lookup.empty:
        return empty

    bt = budget_sku_lookup.copy()
    if "Is_Unmapped" not in bt.columns:
        bt["Is_Unmapped"] = False
    bt["Is_Unmapped"] = bt["Is_Unmapped"].fillna(False).astype(bool)

    use_fact = all_sales_df is not None and not all_sales_df.empty

    # ── Agency / ItemName from master mapping; budget sheet values as fallback ─
    bt = bt.merge(agency_map, on="ItemCode", how="left")
    bt["Agency"]   = bt["Agency"].fillna(bt["Agency_Budget"]).fillna("Unknown Agency")
    if "ItemName_Budget" in bt.columns:
        bt["ItemName"] = bt["ItemName"].fillna(
            bt["ItemName_Budget"].replace("", np.nan)
        )
    bt["ItemName"] = bt["ItemName"].fillna("")
    bt = bt.drop(columns=[c for c in ["Agency_Budget", "ItemName_Budget"] if c in bt.columns])

    # ── Current-month actual sales — fact file covers ALL SKUs ───────────────
    if use_fact:
        cur_sales = (
            all_sales_df[all_sales_df["Month_dt"] == latest_month_dt]
            .groupby("ItemCode")["Sales_Qty"]
            .sum().reset_index()
            .rename(columns={"Sales_Qty": "Current_Month_Sales"})
        )
    else:
        cur_sales = (
            actuals_df[actuals_df["Month_dt"] == latest_month_dt]
            .groupby("ItemCode")["Secondary_Sales_Qty"]
            .sum().reset_index()
            .rename(columns={"Secondary_Sales_Qty": "Current_Month_Sales"})
        )
    bt = bt.merge(cur_sales, on="ItemCode", how="left")
    bt["Current_Month_Sales"] = pd.to_numeric(bt["Current_Month_Sales"], errors="coerce").fillna(0)

    # ── Current-month forecast: model first ──────────────────────────────────
    if loss_lookup is not None and not loss_lookup.empty and "Current_Month_Forecast" in loss_lookup.columns:
        fcst = loss_lookup[["ItemCode", "Current_Month_Forecast"]].drop_duplicates("ItemCode")
        bt = bt.merge(fcst, on="ItemCode", how="left")
    else:
        bt["Current_Month_Forecast"] = np.nan
    bt["Current_Month_Forecast"] = pd.to_numeric(bt["Current_Month_Forecast"], errors="coerce")

    # ── Trend baseline fill for SKUs without a model forecast ────────────────
    bt["Forecast_Source"] = np.where(bt["Current_Month_Forecast"].notna(), "MODEL", None)
    bt["Trend_Model"] = None
    if trend_lookup is not None and not trend_lookup.empty:
        bt = bt.merge(trend_lookup, on="ItemCode", how="left", suffixes=("", "_trend"))
        need = bt["Current_Month_Forecast"].isna() & bt["Trend_Forecast"].notna()
        bt.loc[need, "Current_Month_Forecast"] = bt.loc[need, "Trend_Forecast"]
        bt.loc[need, "Forecast_Source"] = "TREND"
        bt.loc[need, "Trend_Model"] = bt.loc[need, "Trend_Model_trend"] \
            if "Trend_Model_trend" in bt.columns else bt.loc[need, "Trend_Model"]
        bt = bt.drop(columns=[c for c in ["Trend_Forecast", "Trend_Model_trend"] if c in bt.columns])
    bt["Forecast_Source"] = bt["Forecast_Source"].fillna("NONE")

    # ── FYTD actual sales (fiscal start → latest closed month) ───────────────
    fy_start = budget_meta.get("fiscal_start")
    if use_fact:
        src_df, qty_col = all_sales_df, "Sales_Qty"
    else:
        src_df, qty_col = actuals_df, "Secondary_Sales_Qty"

    if fy_start is not None:
        fytd_mask = (src_df["Month_dt"] >= fy_start) & (src_df["Month_dt"] <= latest_month_dt)
    else:
        fytd_mask = src_df["Month_dt"] == latest_month_dt

    fytd_sales = (
        src_df[fytd_mask]
        .groupby("ItemCode")[qty_col]
        .sum().reset_index()
        .rename(columns={qty_col: "FYTD_Sales_Qty"})
    )
    bt = bt.merge(fytd_sales, on="ItemCode", how="left")
    bt["FYTD_Sales_Qty"] = pd.to_numeric(bt["FYTD_Sales_Qty"], errors="coerce").fillna(0)

    bt["Budget_Qty"]        = pd.to_numeric(bt["Budget_Qty"],        errors="coerce").fillna(0)
    bt["Annual_Budget_Qty"] = pd.to_numeric(bt["Annual_Budget_Qty"], errors="coerce").fillna(0)

    # ── Pricing: Budget_Price (primary) vs Distributor_Unit_Price (secondary) ─
    bt["Budget_Price"] = pd.to_numeric(bt.get("Budget_Price"), errors="coerce").fillna(0)

    if price_lookup is not None and not price_lookup.empty:
        bt = bt.merge(price_lookup, on="ItemCode", how="left")
    else:
        bt["Distributor_Unit_Price"] = np.nan

    bt["Price_Source"] = np.where(
        bt["Distributor_Unit_Price"].notna(), "DISTRIBUTOR",
        np.where(bt["Budget_Price"] > 0, "BUDGET_FALLBACK", "NONE"),
    )
    # Sales/forecast (secondary movement) use distributor price; fall back to
    # Budget_Price only when a SKU has no inventory-price history at all.
    bt["_sales_price"] = bt["Distributor_Unit_Price"].fillna(bt["Budget_Price"]).fillna(0)

    # Budget (primary movement) ALWAYS uses Budget_Price, never distributor price.
    bt["Budget_Value"]              = bt["Budget_Qty"]          * bt["Budget_Price"]
    bt["Annual_Budget_Value"]       = bt["Annual_Budget_Qty"]   * bt["Budget_Price"]
    bt["Current_Month_Sales_Value"] = bt["Current_Month_Sales"] * bt["_sales_price"]
    bt["Current_Month_Forecast_Value"] = bt["Current_Month_Forecast"].fillna(0) * bt["_sales_price"]
    bt["FYTD_Sales_Value"]          = bt["FYTD_Sales_Qty"]      * bt["_sales_price"]

    # ── Ratios (null-safe: never divide by 0) ────────────────────────────────
    bt["Achievement_%"] = np.where(
        bt["Budget_Qty"] > 0,
        (bt["Current_Month_Sales"] / bt["Budget_Qty"] * 100).clip(0, 200),
        np.nan,
    )
    bt["Possible_Achievement_%"] = np.where(
        (bt["Budget_Qty"] > 0) & bt["Current_Month_Forecast"].notna(),
        (bt["Current_Month_Forecast"] / bt["Budget_Qty"] * 100).clip(0, 200),
        np.nan,
    )
    bt["Annual_Reach_%"] = np.where(
        bt["Annual_Budget_Qty"] > 0,
        (bt["FYTD_Sales_Qty"] / bt["Annual_Budget_Qty"] * 100).clip(0, 200),
        np.nan,
    )
    bt["Annual_Remaining_Qty"] = np.maximum(
        bt["Annual_Budget_Qty"] - bt["FYTD_Sales_Qty"], 0
    )

    bt = bt.drop(columns=["_sales_price"], errors="ignore")

    for c in ["Budget_Qty", "Current_Month_Sales", "Current_Month_Forecast",
              "Achievement_%", "Possible_Achievement_%",
              "Annual_Budget_Qty", "FYTD_Sales_Qty",
              "Annual_Reach_%", "Annual_Remaining_Qty",
              "Budget_Price", "Budget_Value", "Annual_Budget_Value",
              "Distributor_Unit_Price",
              "Current_Month_Sales_Value", "Current_Month_Forecast_Value",
              "FYTD_Sales_Value"]:
        bt[c] = pd.to_numeric(bt[c], errors="coerce").round(2)

    bt = bt[list(empty.columns)].copy()
    bt = bt.sort_values("Budget_Qty", ascending=False).reset_index(drop=True)
    return bt


# ─────────────────────────────────────────────────────────────────────────────
# Forecast comparison table builder — model vs third parties vs actual,
# for the CURRENTLY DISPLAYED (latest closed) month
# ─────────────────────────────────────────────────────────────────────────────
def build_forecast_comparison_table(budget_sku_lookup, agency_map,
                                    own_forecast_lookup, actual_sales_lookup,
                                    external_forecast_df,
                                    comparison_month_label):
    """
    One row per budgeted SKU comparing, for the SAME month:
        - My_Model_Forecast_Qty     (our model's forecast, as it was issued
                                      for this month — from forecast history)
        - {source}_Forecast_Qty     (third-party forecasts from Forecast.xlsx)
        - Actual_Sales_Qty          (what actually happened)

    The month used is the CURRENTLY DISPLAYED Insights month (the latest
    CLOSED month, e.g. May) — not the next/future forecast month (e.g.
    June) — because Actual_Sales_Qty only exists once a month has closed.
    This is what makes "mine vs theirs vs reality" possible.

    Also computes a per-source Accuracy_% vs actual (null when that source
    has no forecast value, or when the forecast is 0/not applicable).

    own_forecast_lookup:    ItemCode | My_Model_Forecast_Qty
    actual_sales_lookup:    ItemCode | Actual_Sales_Qty
    external_forecast_df:   output of load_external_forecast_comparison()
    """
    forecast_cols = ["My_Model_Forecast_Qty"] + list(FORECAST_MEASURE_COLUMN_MAP.values())
    accuracy_cols = [c.replace("_Forecast_Qty", "_Accuracy_%") for c in forecast_cols]

    empty = pd.DataFrame(columns=(
        ["Agency", "ItemCode", "ItemName", "Comparison_Month"]
        + forecast_cols + ["Actual_Sales_Qty"] + accuracy_cols
    ))

    if budget_sku_lookup is None or budget_sku_lookup.empty:
        return empty

    # Budgeted SKU list only (left join) — this IS the budgeted-item universe.
    ft = budget_sku_lookup[["ItemCode"]].drop_duplicates().copy()

    ft = ft.merge(agency_map, on="ItemCode", how="left")
    ft["Agency"]   = ft["Agency"].fillna("Unknown Agency")
    ft["ItemName"] = ft["ItemName"].fillna("")

    if own_forecast_lookup is not None and not own_forecast_lookup.empty:
        ft = ft.merge(own_forecast_lookup, on="ItemCode", how="left")
    if external_forecast_df is not None and not external_forecast_df.empty:
        ft = ft.merge(external_forecast_df, on="ItemCode", how="left")
    if actual_sales_lookup is not None and not actual_sales_lookup.empty:
        ft = ft.merge(actual_sales_lookup, on="ItemCode", how="left")

    for col in forecast_cols:
        if col not in ft.columns:
            ft[col] = np.nan
        ft[col] = pd.to_numeric(ft[col], errors="coerce").round(2)

    if "Actual_Sales_Qty" not in ft.columns:
        ft["Actual_Sales_Qty"] = np.nan
    ft["Actual_Sales_Qty"] = pd.to_numeric(ft["Actual_Sales_Qty"], errors="coerce").fillna(0).round(2)

    # Per-source accuracy vs actual — null when that source had no forecast.
    for fcol, acol in zip(forecast_cols, accuracy_cols):
        ft[acol] = np.where(
            ft[fcol].notna() & (ft[fcol] > 0),
            (1 - (ft[fcol] - ft["Actual_Sales_Qty"]).abs() / ft[fcol]).clip(0, 1) * 100,
            np.nan,
        )
        ft[acol] = pd.to_numeric(ft[acol], errors="coerce").round(2)

    ft["Comparison_Month"] = comparison_month_label
    ft = ft[
        ["Agency", "ItemCode", "ItemName", "Comparison_Month"]
        + forecast_cols + ["Actual_Sales_Qty"] + accuracy_cols
    ].copy()
    ft = ft.sort_values(["Agency", "ItemCode"]).reset_index(drop=True)
    return ft


# ─────────────────────────────────────────────────────────────────────────────
# Main builder
# ─────────────────────────────────────────────────────────────────────────────

def build_agency_performance_table():
    actuals_df   = load_actual_sales()
    forecast_df  = load_forecast_latest()
    agency_map   = load_agency_mapping()
    wmape_lookup = load_champion_wmape_lookup()
    shp_lookup   = load_shp_lookup()
    loss_lookup  = load_current_month_forecast_loss()
    price_lookup = load_distributor_price_lookup()
    master_codes = load_master_sku_codes()

    latest_month_dt      = actuals_df["Month_dt"].max()
    data_upto_label      = latest_month_dt.strftime("%b %Y")

    # ALL-SKU actuals + trend baselines (built on the forecast side) for
    # budgeted items outside the model list
    all_sales_df = load_all_sku_sales()
    trend_lookup = load_trend_forecast_lookup(latest_month_dt)

    if not all_sales_df.empty and all_sales_df["Month_dt"].max() < latest_month_dt:
        print(f"[INSIGHTS] WARNING: fact_monthly_closed lags processed data "
              f"({all_sales_df['Month_dt'].max():%b %Y} < {data_upto_label}). "
              f"Budget actuals for the latest month will be 0.")

    # Budget lookup — keyed to the CURRENT (latest closed) month.
    # Loaded from "All Budget 26 27 FY" (ALL budgeted items).
    budget_sku_lookup, budget_meta = load_budget_lookup(latest_month_dt)

    next_forecast_month_dt = forecast_df["Forecast_Month_dt"].max()
    next_forecast_label    = next_forecast_month_dt.strftime("%b %Y")

    current_month_actuals = actuals_df[actuals_df["Month_dt"] == latest_month_dt].copy()

    all_months = sorted(actuals_df["Month_dt"].unique())
    prev_month_actuals = (
        actuals_df[actuals_df["Month_dt"] == all_months[-2]].copy()
        if len(all_months) >= 2
        else pd.DataFrame(columns=actuals_df.columns)
    )

    # ── Actual sales with ALL-SKU fallback ────────────────────────────────
    # processed_data_actual.csv (actuals_df) covers FOCUS SKUs only. A
    # model-forecasted SKU whose sales history lives only in
    # fact_monthly_closed (all_sales_df) would otherwise read 0 here even
    # though it genuinely sold — combine_first fills gaps from the
    # all-SKU source without overriding a real focus-side number.
    def _sales_with_fallback(month_dt, out_col):
        primary = (
            actuals_df[actuals_df["Month_dt"] == month_dt]
            .groupby("ItemCode")["Secondary_Sales_Qty"].sum()
        )
        if not all_sales_df.empty:
            fallback = (
                all_sales_df[all_sales_df["Month_dt"] == month_dt]
                .groupby("ItemCode")["Sales_Qty"].sum()
            )
            combined = primary.combine_first(fallback)
        else:
            combined = primary
        out = combined.reset_index()
        out.columns = ["ItemCode", out_col]
        return out

    cur_sales  = _sales_with_fallback(latest_month_dt, "Current_Month_Sales")
    prev_sales = (
        _sales_with_fallback(all_months[-2], "Last_Month_Sales")
        if len(all_months) >= 2
        else pd.DataFrame(columns=["ItemCode", "Last_Month_Sales"])
    )

    latest_forecast = (
        forecast_df.sort_values("Forecast_Month_dt")
        .groupby("ItemCode").tail(1)
        [["ItemCode", "Forecast_Qty", "Forecast_Month_dt"]]
        .rename(columns={"Forecast_Qty": "Next_Month_Forecast",
                         "Forecast_Month_dt": "Next_Forecast_Month_dt"})
        .copy()
    )

    # ── Merge all sources ────────────────────────────────────────────────────
    df = latest_forecast.copy()
    df = df.merge(cur_sales,    on="ItemCode", how="left")
    df = df.merge(prev_sales,   on="ItemCode", how="left")
    df = df.merge(loss_lookup,  on="ItemCode", how="left")   # brings Current_Month_Forecast, Current_Month_Sales too
    df = df.merge(wmape_lookup, on="ItemCode", how="left")
    df = df.merge(agency_map,   on="ItemCode", how="left")
    df = df.merge(shp_lookup,   on="ItemCode", how="left")
    df = df.merge(price_lookup, on="ItemCode", how="left")

    # ── Resolve Current_Month_Sales: loss_lookup has it; cur_sales also has it.
    # After merges, if "Current_Month_Sales_x" / "_y" exist, pick the right one.
    if "Current_Month_Sales_x" in df.columns and "Current_Month_Sales_y" in df.columns:
        df["Current_Month_Sales"] = df["Current_Month_Sales_x"].fillna(df["Current_Month_Sales_y"])
        df.drop(columns=["Current_Month_Sales_x", "Current_Month_Sales_y"], inplace=True)

    for c, fill in [
        ("Current_Month_Sales",    0),
        ("Last_Month_Sales",       0),
        ("Next_Month_Forecast",    0),
        ("Current_Month_Forecast", np.nan),
    ]:
        df[c] = pd.to_numeric(df.get(c, pd.Series(fill, index=df.index)), errors="coerce")
        if fill == 0:
            df[c] = df[c].fillna(0)

    df["MoM_Growth_%"] = np.where(
        df["Last_Month_Sales"] > 0,
        ((df["Current_Month_Sales"] - df["Last_Month_Sales"])
         / df["Last_Month_Sales"] * 100).round(2),
        np.nan,
    )

    df["Model_Accuracy_%"] = pd.to_numeric(df["Model_Accuracy_%"], errors="coerce")
    df["Model_WMAPE"]      = pd.to_numeric(df["Model_WMAPE"],       errors="coerce")
    df["Model_Used"]       = df["Model_Used"].fillna("RULE_BASED")

    df["Realised_Accuracy_%"] = np.where(
        df["Current_Month_Forecast"].notna() & (df["Current_Month_Forecast"] > 0),
        (
            1 - np.abs(df["Current_Month_Forecast"] - df["Current_Month_Sales"])
            / df["Current_Month_Forecast"]
        ).clip(lower=0, upper=1) * 100,
        np.nan,
    ).round(2)

    df["Realised_Accuracy_Available"] = df["Current_Month_Forecast"].notna()

    df["Forecast_Month"]      = next_forecast_label
    df["Data_Available_Upto"] = data_upto_label
    df["Agency"]   = df["Agency"].fillna("Unknown Agency")
    df["ItemName"] = df["ItemName"].fillna("")

    df["Stockout_Flag"] = df["Stockout_Flag"].fillna(False)
    df["Loss_Reason"]   = df["Loss_Reason"].fillna("None")

    for c in [
        "Raw_Loss_Qty", "Other_Loss_Qty", "Stockout_Loss_Qty",
        "Current_Month_Loss_Qty", "Trade_Stock_Qty",
        "WH_Stock_Current", "DB_Stock_Current",
        "L3M_Moving_Avg", "WH_Stock", "DB_Stock",
        "WH_SHP", "DB_SHP", "Current_SHP",
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # ── Value fields — SALES/FORECAST only use distributor (RD) price ────────
    # Budget's own value fields (Budget_Value/Annual_Budget_Value) live in
    # budget_table and are computed with Budget_Price there — never here.
    df["Distributor_Unit_Price"] = pd.to_numeric(df.get("Distributor_Unit_Price"), errors="coerce")
    df["Price_Source"] = np.where(df["Distributor_Unit_Price"].notna(), "DISTRIBUTOR", "NONE")
    _price = df["Distributor_Unit_Price"].fillna(0)

    df["Current_Month_Sales_Value"]    = (df["Current_Month_Sales"]    * _price).round(2)
    df["Current_Month_Forecast_Value"] = (df["Current_Month_Forecast"].fillna(0) * _price).round(2)
    df["Next_Month_Forecast_Value"]    = (df["Next_Month_Forecast"]    * _price).round(2)

    # ── Master-scope flag — every KPI total sums ONLY rows where this is True.
    # Some FOCUS SKUs (model-forecasted) have no entry in Budget.xlsx at all,
    # so they're absent from sku_master_full.csv. They still appear in the
    # per-SKU table below (nothing is silently dropped), just excluded from
    # summed KPIs so totals don't mix two different SKU universes.
    df["Is_In_Master"] = df["ItemCode"].isin(master_codes)

    output_cols = [
        "Agency", "ItemCode", "ItemName", "Is_In_Master",
        "Data_Available_Upto", "Forecast_Month",
        "Last_Month_Sales", "Current_Month_Sales",
        "Current_Month_Forecast", "Next_Month_Forecast",
        "MoM_Growth_%",
        "Distributor_Unit_Price", "Price_Source",
        "Current_Month_Sales_Value", "Current_Month_Forecast_Value",
        "Next_Month_Forecast_Value",
        "Model_Used", "Model_WMAPE", "Model_Accuracy_%",
        "Realised_Accuracy_%", "Realised_Accuracy_Available",
        "L3M_Moving_Avg", "WH_Stock", "DB_Stock",
        "WH_SHP", "DB_SHP", "Current_SHP",
        "Current_Month_Label",
        "WH_Stock_Current", "DB_Stock_Current", "Trade_Stock_Qty",
        "Raw_Loss_Qty", "Other_Loss_Qty", "Stockout_Loss_Qty",
        "Current_Month_Loss_Qty",
        "Stockout_Flag", "Loss_Reason",
    ]
    output_cols = [c for c in output_cols if c in df.columns]
    result = df[output_cols].copy()
    result = result.where(pd.notnull(result), other=None)

    # ── Budget analysis table (ALL budgeted items — separate table) ──────────
    budget_table = build_budget_analysis_table(
        budget_sku_lookup, budget_meta,
        actuals_df, agency_map, loss_lookup,
        latest_month_dt, price_lookup,
        all_sales_df=all_sales_df, trend_lookup=trend_lookup,
    )
    budget_result = budget_table.where(pd.notnull(budget_table), other=None)

    # ── Forecast comparison table (Forecast tab) ──────────────────────────────
    # Targets the CURRENTLY DISPLAYED Insights month (latest closed month,
    # e.g. May) — not the next/future forecast month (June) — because this
    # table's whole purpose is a three-way check: my forecast vs their
    # forecast vs what actually happened, and actuals only exist for a
    # closed month.
    #
    # "My_Model_Forecast_Qty" here is deliberately the HISTORICALLY ISSUED
    # forecast for the current month (Current_Month_Forecast, sourced from
    # forecast_horizon_history via loss_lookup) — not Next_Month_Forecast,
    # which targets the future month and has no actual to compare against.
    own_forecast_lookup = (
        df[["ItemCode", "Current_Month_Forecast"]]
        .rename(columns={"Current_Month_Forecast": "My_Model_Forecast_Qty"})
        .drop_duplicates("ItemCode")
        .copy()
    )
    actual_sales_lookup = (
        df[["ItemCode", "Current_Month_Sales"]]
        .rename(columns={"Current_Month_Sales": "Actual_Sales_Qty"})
        .drop_duplicates("ItemCode")
        .copy()
    )
    external_forecast_df = load_external_forecast_comparison(latest_month_dt)
    forecast_comparison_table = build_forecast_comparison_table(
        budget_sku_lookup, agency_map,
        own_forecast_lookup, actual_sales_lookup, external_forecast_df,
        data_upto_label,
    )
    forecast_comparison_result = forecast_comparison_table.where(
        pd.notnull(forecast_comparison_table), other=None
    )

    # ── Meta ─────────────────────────────────────────────────────────────────
    def _fsum(frame, col):
        if col not in frame.columns:
            return 0.0
        return float(pd.to_numeric(frame[col], errors="coerce").fillna(0).sum())

    def _isum(frame, col):
        if col not in frame.columns:
            return 0
        return int(pd.to_numeric(frame[col], errors="coerce").fillna(0).sum())

    # Performance-table totals: master-scoped only (see Is_In_Master above).
    result_scoped = (
        result[result["Is_In_Master"] == True] if "Is_In_Master" in result.columns else result
    )
    total_sales             = _fsum(result_scoped, "Current_Month_Sales")
    total_sales_value       = _fsum(result_scoped, "Current_Month_Sales_Value")
    total_cur_fcst          = _fsum(result_scoped, "Current_Month_Forecast")
    total_cur_fcst_value    = _fsum(result_scoped, "Current_Month_Forecast_Value")
    total_next_fcst         = _fsum(result_scoped, "Next_Month_Forecast")
    total_next_fcst_value   = _fsum(result_scoped, "Next_Month_Forecast_Value")

    excluded_row_count = int((result["Is_In_Master"] == False).sum()) if "Is_In_Master" in result.columns else 0

    # Budget totals come from ALL budgeted items — budget_table is inherently
    # master-scoped already (every row originates from sku_master_full.csv
    # via load_budget_lookup), so no extra filter needed here.
    total_budget_qty        = _fsum(budget_table, "Budget_Qty")
    total_annual_budget_qty = _fsum(budget_table, "Annual_Budget_Qty")
    total_fytd_sales_qty    = _fsum(budget_table, "FYTD_Sales_Qty")

    # Value totals — Budget_* stay on Budget_Price; sales/forecast on
    # Distributor_Unit_Price (see build_budget_analysis_table docstring).
    total_budget_value         = _fsum(budget_table, "Budget_Value")
    total_annual_budget_value  = _fsum(budget_table, "Annual_Budget_Value")
    total_fytd_sales_value     = _fsum(budget_table, "FYTD_Sales_Value")
    total_cur_sales_value_bt   = _fsum(budget_table, "Current_Month_Sales_Value")

    agency_budget_records = []
    if not budget_table.empty:
        agency_budget_records = (
            budget_table
            .groupby("Agency")[["Budget_Qty", "Annual_Budget_Qty", "FYTD_Sales_Qty",
                                "Budget_Value", "Annual_Budget_Value", "FYTD_Sales_Value"]]
            .sum().round(2).reset_index()
            .to_dict(orient="records")
        )

    # Forecast-source breakdown (MODEL / TREND / NONE) across budgeted items
    forecast_source_counts = {}
    if "Forecast_Source" in budget_table.columns and not budget_table.empty:
        forecast_source_counts = (
            budget_table["Forecast_Source"].value_counts().to_dict()
        )

    # Coverage of the third-party forecast comparison table
    forecast_comparison_sku_count = int(len(forecast_comparison_table))
    forecast_comparison_matched_count = 0
    if not forecast_comparison_table.empty:
        external_value_cols = [
            c for c in forecast_comparison_table.columns
            if c.endswith("_Forecast_Qty") and c != "My_Model_Forecast_Qty"
        ]
        if external_value_cols:
            any_external = forecast_comparison_table[external_value_cols].notna().any(axis=1)
            forecast_comparison_matched_count = int(any_external.sum())

    # Price coverage — how many master SKUs got a real distributor price
    # vs fell back vs have none at all (useful debug/QA signal for the UI).
    price_source_counts = {}
    if "Price_Source" in result_scoped.columns and not result_scoped.empty:
        price_source_counts = result_scoped["Price_Source"].value_counts().to_dict()

    meta = {
        "data_available_upto":         data_upto_label,
        "forecast_month":              next_forecast_label,
        "current_month_label":         data_upto_label,
        "realised_accuracy_available": (
            bool(result["Realised_Accuracy_Available"].any())
            if "Realised_Accuracy_Available" in result.columns else False
        ),
        "total_skus":                  int(len(result)),
        "agencies":                    sorted(result["Agency"].dropna().unique().tolist()),

        # Master-scope diagnostics
        "master_sku_count":            len(master_codes),
        "excluded_from_totals_count":  excluded_row_count,

        # Loss (per-SKU decomposition, forecasted items, master-scoped)
        "total_raw_loss_qty":           _fsum(result_scoped, "Raw_Loss_Qty"),
        "total_other_loss_qty":         _fsum(result_scoped, "Other_Loss_Qty"),
        "total_stockout_loss_qty":      _fsum(result_scoped, "Stockout_Loss_Qty"),
        "total_current_month_loss_qty": _fsum(result_scoped, "Current_Month_Loss_Qty"),
        "stockout_sku_count":           _isum(result_scoped, "Stockout_Flag"),

        # Performance KPI strip — qty AND value (value = the primary display)
        "total_actual_sales_qty":       total_sales,
        "total_actual_sales_value":     total_sales_value,        # distributor price
        "total_current_forecast_qty":   total_cur_fcst,
        "total_current_forecast_value": total_cur_fcst_value,     # distributor price
        "total_next_forecast_qty":      total_next_fcst,
        "total_next_forecast_value":    total_next_fcst_value,    # distributor price

        # Budget (ALL budgeted items — Budget_* always at Budget_Price)
        "budget_item_count":        int(len(budget_table)),
        "unmapped_budget_item_count": (
            int(budget_table["Is_Unmapped"].sum())
            if "Is_Unmapped" in budget_table.columns and not budget_table.empty else 0
        ),
        "total_budget_qty":         total_budget_qty,
        "total_annual_budget_qty":  total_annual_budget_qty,
        "total_fytd_sales_qty":     total_fytd_sales_qty,

        "total_budget_value":              total_budget_value,        # budget price
        "total_annual_budget_value":       total_annual_budget_value, # budget price
        "total_fytd_sales_value":          total_fytd_sales_value,    # distributor price
        "total_current_month_sales_value": total_cur_sales_value_bt,  # distributor price
        "budget_fy_months":         budget_meta.get("month_labels", []),
        "budget_current_month_found": budget_meta.get("current_month_found", False),
        "agency_budget":            agency_budget_records,

        # Price coverage diagnostics
        "price_source_counts":      price_source_counts,

        # Forecast coverage of the budgeted-SKU universe
        # (MODEL = champion model, TREND = simple background analysis,
        #  NONE = budgeted item with no history at all)
        "forecast_source_counts":   forecast_source_counts,
        "model_forecast_sku_count": int(forecast_source_counts.get("MODEL", 0)),
        "trend_forecast_sku_count": int(forecast_source_counts.get("TREND", 0)),
        "no_forecast_sku_count":    int(forecast_source_counts.get("NONE", 0)),
        "fact_data_upto": (
            all_sales_df["Month_dt"].max().strftime("%b %Y")
            if not all_sales_df.empty else None
        ),

        # Aggregate gaps for the Performance KPI strip
        "total_budget_vs_actual_loss_qty":   max(total_budget_qty - total_sales, 0.0),
        "total_forecast_vs_actual_loss_qty": max(total_cur_fcst  - total_sales, 0.0),

        # Forecast comparison tab — targets the CURRENT (closed) month, not
        # the future forecast month, so it can include actuals.
        "forecast_comparison_month":             data_upto_label,
        "forecast_comparison_sku_count":         forecast_comparison_sku_count,
        "forecast_comparison_matched_sku_count": forecast_comparison_matched_count,
    }

    return result, budget_result, forecast_comparison_result, meta