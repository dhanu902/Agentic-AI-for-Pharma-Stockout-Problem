# backend/engines/insights_engine.py
#
# ─────────────────────────────────────────────────────────────
# v8 — BUDGET vs ACTUAL vs FORECAST, all priced via DistributorPrice.xlsx
# ─────────────────────────────────────────────────────────────
# Business change (confirmed): the qty in Budget.xlsx IS "primary budgeted
# qty", and primary qty is ~= the secondary (RD/distributor sell-through)
# sales TARGET qty. That means we no longer need a separate Primary_Sales_Qty
# stream at all (PrimarySales.xlsx is no longer read anywhere in this file):
# Budget can be compared DIRECTLY against SECONDARY sales, and Forecast is
# back in scope as a third comparison lens.
#
#   BUDGET     -> Budget_Qty per SKU per month, from Budget.xlsx
#                 ("All Budget 26 27 FY"). Still the planning target.
#   ACTUAL     -> Secondary_Sales_Qty (distributor sell-through), from
#                 processed_data_actual.csv (focus∩master SKUs) with
#                 fact_monthly_closed as the leftover-SKU fallback. THIS is
#                 now the headline number compared to budget everywhere
#                 (Achievement %, Annual Reach %, Raw/Stockout/Other Loss).
#   FORECAST   -> Current_Forecast_Qty, from Forecast.xlsx (the EXTERNAL,
#                 business-supplied forecast — see v10 note below), for the
#                 current reporting month. Restored as a third KPI:
#                 "Current Forecast" + "Loss — Forecast vs Actual"
#                 (Forecast_Qty vs the same month's Secondary actual).
#
# v9 — FORECAST ANALYSIS is now its own table (it used to be a few extra
# columns bolted onto the budget analysis table). See
# build_forecast_analysis_table(): our model's forecast alongside the
# EXTERNAL business-supplied forecast (Forecast.xlsx), each scored against
# the same month's budget and actual, plus a per-SKU accuracy score.
# Deviations are computed on QTY only, never value. External-file I/O
# lives in services/insights_service.py, not in this engine.
#
# v10 — "Current Forecast" (this table's KPI + the SKU-wise Performance
# table's "Current Forecast (units)" column) now reads the SAME EXTERNAL
# forecast (Forecast.xlsx, current reporting month) as the Forecast
# Analysis tab's "External Forecast", instead of our own model's live
# forecast (forecast_master_mapped.csv). Previously these showed different
# numbers under similar labels — one was our model's forward-looking
# snapshot (whichever month it happened to target), the other was scoped
# to the current closed month — which looked like a bug even though each
# was internally correct for what it measured. Making both read one
# source removes the ambiguity.
#
# v11 — REMOVED the "LIVE_FORWARD" fallback from build_forecast_analysis_
# table()'s "My Forecast" column (load_current_forecast() /
# forecast_master_mapped.csv is no longer read by this engine at all).
# Business rule (confirmed): every number on Insights must describe the
# SAME reporting month — the latest CLOSED month (`latest_month_dt`). The
# forecasting pipeline always runs ONE STEP AHEAD of that: once a month
# closes, the NEXT month's forecast is generated from that just-closed
# month's data (plus next month's inventory projection) — so
# forecast_master_mapped.csv's live snapshot is, by design, never a
# forecast FOR the reporting month; it's a forecast for the month AFTER
# it. Silently substituting that next-month number into a column labelled
# for the reporting month (as the old LIVE_FORWARD fallback did) reported
# the wrong month's forecast as if it were the right one — a real bug, not
# just a labelling caveat. Now: a SKU either has a genuine same-month
# entry in forecast_horizon_history.csv (Horizon == "M+1" — i.e. predicted
# one month ahead, for the reporting month specifically) and gets
# Model_Forecast_Basis = "HISTORY_M+1", or it doesn't and gets
# Model_Forecast_Basis = "NONE" (qty 0, no deviation/accuracy shown) —
# exactly like a SKU with no forecast at all. Nothing is backfilled from a
# different month.
#
# PRICING (v8 — the actual point of this rework): ONE shared price source
# for everything above — DistributorPrice.xlsx (Id | ItemCode | UnitPrice |
# CreationDate | LastModifiedUserId | LastModifiedDate | MonthId). The
# EFFECTIVE month for a price is the month of its CreationDate (per business
# instruction, NOT MonthId). Budget.xlsx's own BudgetPrice column and the
# old Inventory.xlsx "DB"-sheet snapshot price are NO LONGER used to value
# anything — both Budget_Qty and Secondary_Sales_Qty are valued at THIS one
# distributor price, so a value ratio always tracks its qty ratio.
#
#   budget (month)   = Budget_Qty(month)      × DistributorPrice(SKU, month)
#   actual (month)   = Secondary_Sales_Qty(month) × DistributorPrice(SKU, month)
#   forecast (month) = Forecast_Qty(month)    × DistributorPrice(SKU, month)
#
# Multi-month KPIs (FYTD, Annual) sum this SAME per-month qty×price
# calculation across every month in range — never a flat annual qty times
# one single price — since the distributor price can change month to
# month. See _price_asof() / _monthly_value_sum() below. When a SKU has no
# price entry for the exact target month, the most recent PRIOR month's
# price is carried forward (a SKU's price doesn't necessarily get
# re-entered every single month).
#
# SKU universe: the full SKU master list (sku_master_full.csv, built off
# Budget.xlsx). Every table in this file is scoped to that list ONLY —
# FocusItemCodes.xlsx and preprocessing are forecasting-model concerns and
# never expand or restrict the Insights output.
import os
import pandas as pd
import numpy as np

from services.forecast_service import load_fact_history_all_skus, load_master_forecast_mapped

# NOTE: load_external_forecast() / load_model_forecast_history() (file I/O
# for Forecast.xlsx / forecast_horizon_history.csv) live in
# services/insights_service.py alongside the rest of this pipeline's file
# handling. They're imported lazily inside build_agency_performance_table()
# below, not at module level here — insights_service.py imports THIS module
# at its own top level (to call build_agency_performance_table()), so a
# top-level import back from here would be circular.

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

PROCESSED_DATA_FILE   = os.path.join(BACKEND_DIR, "data", "processed", "processed_data_actual.csv")
BUDGET_FILE           = os.path.join(PROJECT_DIR, "data", "Master Data", "Budget.xlsx")

# ALL budgeted items live here (some have budget but no sales history yet).
# "Focus Budget 26 27 FY" only contains focus items — do NOT use it for totals.
BUDGET_SHEET_NAME = "All Budget 26 27 FY"

# ─────────────────────────────────────────────────────────────
# Distributor price — DistributorPrice.xlsx (v8, THE sole pricing source)
# ─────────────────────────────────────────────────────────────
# One row per price entry: Id | ItemCode | UnitPrice | CreationDate |
# LastModifiedUserId | LastModifiedDate | MonthId. The month a price is
# EFFECTIVE for is derived from CreationDate (business-confirmed rule),
# not MonthId. See load_distributor_price_history() / _price_asof() below.
DISTRIBUTOR_PRICE_FILE = (
    "/Users/dhanujiamanda/Documents/Projects/Agentic AI /Pipeline/"
    "Agentic-AI-for-Pharma-Stockout-Problem/data/DistributorPrice.xlsx"
)
DISTRIBUTOR_PRICE_SHEET = "DB price"

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


def _coerce_price_column(series: pd.Series) -> pd.Series:
    """
    A price column can come through as a Timestamp instead of a number —
    the source cell was date-formatted in Excel at some point, so
    pandas/openpyxl parses the numeric price as a date (e.g. a price of
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


def _codes_of(frame, col="ItemCode"):
    """
    Set of non-null ItemCodes (as str) from a DataFrame; empty/None-safe.
    Used by the mapping diagnostics to intersect the SKU universes of the
    different sources (master / budget / forecast / price / focus).
    """
    if frame is None:
        return set()
    if getattr(frame, "empty", True) or col not in getattr(frame, "columns", []):
        return set()
    return set(frame[col].dropna().astype(str))


def _qty_with_fallback(focus_df, all_sku_df, month_mask_fn, qty_col, out_col):
    """
    Unified sales-qty resolver for Secondary_Sales_Qty, at any month
    granularity (a single month, or an FYTD range):

      - focus∩master SKUs -> value from `focus_df` (processed_data_actual.csv,
        the preprocessing pipeline's output) — PREFERRED.
      - leftover SKUs (in the SKU master, NOT in the focus list) -> `focus_df`
        has no rows for them at all, so the value comes from `all_sku_df`
        (fact_monthly_closed, focus filter NOT applied) instead.

    `month_mask_fn(df)` must return a boolean mask (selecting one month, or
    a date range) against that frame's own Month_dt column — applied
    separately to `focus_df` and `all_sku_df` since they're different frames.

    Returns: ItemCode | out_col
    """
    primary = pd.Series(dtype=float)
    if focus_df is not None and not focus_df.empty and qty_col in focus_df.columns:
        primary = focus_df[month_mask_fn(focus_df)].groupby("ItemCode")[qty_col].sum()

    if all_sku_df is not None and not all_sku_df.empty and qty_col in all_sku_df.columns:
        fallback = all_sku_df[month_mask_fn(all_sku_df)].groupby("ItemCode")[qty_col].sum()
        combined = primary.combine_first(fallback)
    else:
        combined = primary

    out = combined.reset_index()
    out.columns = ["ItemCode", out_col]
    return out


def _price_asof(item_month_df, price_history_df, month_col="Month_dt"):
    """
    Attach the distributor UnitPrice EFFECTIVE in each row's month: the
    most recent price at or before that month for that SKU (carry-forward
    when the exact month has no price update — prices aren't necessarily
    re-entered every single month). A SKU with no price history at or
    before its target month gets NaN (callers fillna(0)).

    `item_month_df` needs ItemCode + `month_col` (any other columns pass
    through untouched). `price_history_df` is load_distributor_price_history()'s
    output (ItemCode | Price_Month | UnitPrice). Rows whose `month_col` is
    null (e.g. a SKU with no forecast, so no Forecast_Month to price) get
    UnitPrice = NaN rather than raising — pd.merge_asof can't join on a
    null key.

    Returns item_month_df with a new "UnitPrice" column, original row order
    preserved.
    """
    out_cols = list(item_month_df.columns) + ["UnitPrice"]

    if item_month_df.empty:
        empty = item_month_df.copy()
        empty["UnitPrice"] = pd.Series(dtype=float)
        return empty[out_cols]

    if price_history_df is None or price_history_df.empty:
        out = item_month_df.copy()
        out["UnitPrice"] = np.nan
        return out[out_cols]

    left = item_month_df.copy()
    left["_orig_order"] = np.arange(len(left))

    # Normalise both sides to the SAME datetime64 resolution before the
    # asof join. Different construction paths (e.g. `.values.astype(
    # "datetime64[M]")` vs `pd.to_datetime(...)`) can land on different
    # units (ns vs s) depending on the pandas version, and merge_asof
    # refuses to join columns of mismatched datetime64 units even though
    # both are genuinely datetimes — force ns on both to avoid that.
    left[month_col] = pd.to_datetime(left[month_col]).astype("datetime64[ns]")

    valid_mask = left[month_col].notna()
    valid   = left[valid_mask].sort_values(month_col)
    invalid = left[~valid_mask].copy()
    invalid["UnitPrice"] = np.nan

    right = (
        price_history_df[["ItemCode", "Price_Month", "UnitPrice"]]
        .rename(columns={"Price_Month": month_col})
        .sort_values(month_col)
    )
    right[month_col] = pd.to_datetime(right[month_col]).astype("datetime64[ns]")

    if not valid.empty:
        merged_valid = pd.merge_asof(valid, right, on=month_col, by="ItemCode", direction="backward")
    else:
        merged_valid = valid.copy()
        merged_valid["UnitPrice"] = np.nan

    merged = pd.concat([merged_valid, invalid], ignore_index=True)
    merged = merged.sort_values("_orig_order").drop(columns=["_orig_order"]).reset_index(drop=True)
    return merged


def _monthly_value_sum(monthly_qty_df, qty_col, price_history_df, month_mask_fn, out_col):
    """
    Sum qty×price across every (ItemCode, month) row in `monthly_qty_df`
    that passes `month_mask_fn` — pricing EACH month independently via
    _price_asof() (the v8 rule: budget/actual/forecast are all valued at
    the distributor price effective in THAT SAME month, never one flat
    price applied across a multi-month range).

    Returns: ItemCode | out_col
    """
    empty = pd.DataFrame(columns=["ItemCode", out_col])
    if monthly_qty_df is None or monthly_qty_df.empty:
        return empty

    sub = monthly_qty_df[month_mask_fn(monthly_qty_df)].copy()
    if sub.empty:
        return empty

    priced = _price_asof(sub[["ItemCode", "Month_dt", qty_col]], price_history_df)
    priced["UnitPrice"] = priced["UnitPrice"].fillna(0)
    priced["_value"] = pd.to_numeric(priced[qty_col], errors="coerce").fillna(0) * priced["UnitPrice"]

    out = priced.groupby("ItemCode", as_index=False)["_value"].sum()
    out.columns = ["ItemCode", out_col]
    return out


def _value_with_fallback(focus_df, all_sku_df, qty_col, price_history_df, month_mask_fn, out_col):
    """
    Value-side counterpart to _qty_with_fallback(): sums qty×month-price
    (via _monthly_value_sum) separately over `focus_df` and `all_sku_df`,
    then combines with the SAME focus-preferred / leftover-fallback rule
    (per-ItemCode, not per-row, since a SKU is never split across both
    sources for the same month in practice).
    """
    primary = _monthly_value_sum(focus_df, qty_col, price_history_df, month_mask_fn, out_col)
    primary_s = primary.set_index("ItemCode")[out_col] if not primary.empty else pd.Series(dtype=float)

    if all_sku_df is not None and not all_sku_df.empty:
        fb = _monthly_value_sum(all_sku_df, qty_col, price_history_df, month_mask_fn, out_col)
        fb_s = fb.set_index("ItemCode")[out_col] if not fb.empty else pd.Series(dtype=float)
        combined = primary_s.combine_first(fb_s)
    else:
        combined = primary_s

    out = combined.reset_index()
    out.columns = ["ItemCode", out_col]
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Loaders
# ─────────────────────────────────────────────────────────────────────────────
def load_actual_sales():
    """
    Focus-SKU actuals from processed_data_actual.csv (the preprocessing
    pipeline's output — focus∩master SKUs only). Secondary (RD/distributor
    sell-through) sales — this is the headline ACTUAL compared to budget
    everywhere on this page (see module docstring: budget qty ≈ secondary
    sales target qty, so Primary_Sales_Qty / PrimarySales.xlsx is no longer
    used anywhere in this file).
    """
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
    ALL-SKU closed-month SECONDARY actuals from fact_monthly_closed (via
    forecast_service, focus filter NOT applied). Budgeted items outside the
    focus list have secondary sales ONLY here — processed_data_actual.csv
    covers focus SKUs only. This is the leftover-SKU fallback for the
    headline ACTUAL comparison.

    Returns: ItemCode | Month_dt | Secondary_Sales_Qty
    """
    empty = pd.DataFrame(columns=["ItemCode", "Month_dt", "Secondary_Sales_Qty"])
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
        out = df[["ItemCode", "Month_dt", "Secondary_Sales_Qty"]].copy()

        print(f"[INSIGHTS] All-SKU secondary actuals loaded: {out['ItemCode'].nunique()} SKUs, "
              f"{out['Month_dt'].min():%b %Y} → {out['Month_dt'].max():%b %Y}")
        return out

    except Exception as e:
        print(f"[INSIGHTS] Warning loading all-SKU actuals: {e}")
        return empty


def load_distributor_price_history():
    """
    Reads DistributorPrice.xlsx -> "DB price" sheet (ItemCode | UnitPrice |
    CreationDate | LastModifiedUserId | LastModifiedDate | MonthId — an
    "Id" column may or may not be present, it isn't required).

    v8: the SOLE price source for every value calculation in this file —
    Budget_Qty, Secondary_Sales_Qty AND Forecast_Qty are all valued at the
    distributor price effective for that SKU in that month, taken from
    here. Budget.xlsx's own BudgetPrice column and the old Inventory.xlsx
    "DB"-sheet snapshot price are no longer used for valuation at all.

    The EFFECTIVE month for a price row is the month of its CreationDate
    (business-confirmed — NOT MonthId): "to get May month's price, look up
    creation date -> 2026-05-xx -> get unit price for that item code."
    When a SKU has more than one price row created within the same month,
    the row with the latest CreationDate wins (most recent entry that
    month).

    Returns a LONG history table — ItemCode | Price_Month | UnitPrice, one
    row per SKU per month it has a price for (NOT collapsed to a single
    "latest" price) — so every KPI (current month, FYTD, annual) can be
    valued at the price that was actually in effect for EACH month it
    spans, via _price_asof() / _monthly_value_sum() above.
    """
    empty = pd.DataFrame(columns=["ItemCode", "Price_Month", "UnitPrice"])
    if not os.path.exists(DISTRIBUTOR_PRICE_FILE):
        print(f"[INSIGHTS] Distributor price file not found: {DISTRIBUTOR_PRICE_FILE}")
        return empty

    try:
        try:
            df = pd.read_excel(DISTRIBUTOR_PRICE_FILE, sheet_name=DISTRIBUTOR_PRICE_SHEET)
        except ValueError:
            # Sheet isn't literally named "DB price" in this workbook (e.g. it
            # was exported/renamed differently) — fall back to whichever
            # sheet is first rather than failing the whole load.
            print(f"[INSIGHTS] DistributorPrice.xlsx: sheet '{DISTRIBUTOR_PRICE_SHEET}' "
                  f"not found — falling back to the first sheet in the workbook.")
            df = pd.read_excel(DISTRIBUTOR_PRICE_FILE, sheet_name=0)
        df.columns = df.columns.astype(str).str.strip()

        required = ["ItemCode", "UnitPrice", "CreationDate"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            print(f"[INSIGHTS] DistributorPrice.xlsx missing columns: {missing}. "
                  f"Found: {list(df.columns)}")
            return empty

        df = _force_itemcode_str(df)
        df = df.dropna(subset=["ItemCode"])

        df["CreationDate"] = pd.to_datetime(df["CreationDate"], errors="coerce")
        df = df.dropna(subset=["CreationDate"])
        # Effective month = month of CreationDate (per business rule above).
        df["Price_Month"] = df["CreationDate"].values.astype("datetime64[M]")

        df["UnitPrice"] = _coerce_price_column(df["UnitPrice"])
        df = df.dropna(subset=["UnitPrice"])
        df = df[df["UnitPrice"] > 0]

        if df.empty:
            print("[INSIGHTS] DistributorPrice.xlsx: no usable UnitPrice rows after cleaning.")
            return empty

        # Multiple price entries within the same SKU+month -> the one with
        # the latest CreationDate wins (most recent entry that month).
        df = df.sort_values("CreationDate")
        out = (
            df.groupby(["ItemCode", "Price_Month"], as_index=False)
            .last()[["ItemCode", "Price_Month", "UnitPrice"]]
        )
        out["UnitPrice"] = out["UnitPrice"].round(2)
        out = out.sort_values(["ItemCode", "Price_Month"]).reset_index(drop=True)

        print(f"[INSIGHTS] Distributor price history loaded from DistributorPrice.xlsx: "
              f"{out['ItemCode'].nunique()} SKUs, "
              f"{out['Price_Month'].min():%b %Y} → {out['Price_Month'].max():%b %Y}, "
              f"{len(out)} SKU-month price points.")
        return out

    except Exception as e:
        print(f"[INSIGHTS] Warning loading DistributorPrice.xlsx: {e}")
        import traceback; traceback.print_exc()
        return empty


def load_current_forecast():
    """
    OUR OWN model's current forecast per SKU, from forecast_master_mapped.csv
    (built by engines/master_forecast_engine.py off the AI-model +
    trend-baseline combined forecast, already mapped onto the FULL master
    SKU list — every budgeted SKU, whether AI-modeled, trend-baseline, or
    has no forecast at all).

    This is the LIVE current forecast snapshot — whichever month the last
    forecast run targeted (Forecast_Month). By design that is always the
    month AFTER the last closed month (the pipeline generates next month's
    forecast FROM this month's just-closed data), never a same-month
    historical backtest.

    v10/v11: NOT called anywhere in this pipeline any more. "Current
    Forecast" reads the EXTERNAL forecast instead (see
    build_agency_performance_table()), and "My Forecast" in
    build_forecast_analysis_table() uses ONLY same-month history
    (forecast_horizon_history.csv) with no fallback to this — because this
    function's output never describes the reporting month, only the one
    after it, so it can't correctly stand in for either. Left defined here
    in case another Insights feature legitimately needs "what does the
    model currently forecast going forward," which this still answers
    correctly.

    Returns: ItemCode | Forecast_Month | Forecast_Qty | Forecast_Source
    """
    empty = pd.DataFrame(columns=["ItemCode", "Forecast_Month", "Forecast_Qty", "Forecast_Source"])
    try:
        df = load_master_forecast_mapped()
        if df is None or df.empty:
            return empty

        df = df.copy()
        if "ItemCode" not in df.columns and "ProductCode" in df.columns:
            df = df.rename(columns={"ProductCode": "ItemCode"})
        if "ItemCode" not in df.columns:
            print("[INSIGHTS] forecast_master_mapped.csv has no ItemCode/ProductCode column.")
            return empty

        df = _force_itemcode_str(df)
        df = df.dropna(subset=["ItemCode"])

        for c, default in [("Forecast_Month", ""), ("Forecast_Qty", 0), ("Forecast_Source", "NO_FORECAST")]:
            if c not in df.columns:
                df[c] = default

        df["Forecast_Qty"] = pd.to_numeric(df["Forecast_Qty"], errors="coerce").fillna(0).clip(lower=0)
        df["Forecast_Source"] = df["Forecast_Source"].fillna("NO_FORECAST")

        out = (
            df[["ItemCode", "Forecast_Month", "Forecast_Qty", "Forecast_Source"]]
            .drop_duplicates("ItemCode")
            .reset_index(drop=True)
        )
        n_forecasted = int((out["Forecast_Source"] != "NO_FORECAST").sum())
        print(f"[INSIGHTS] Current forecast loaded from forecast_master_mapped.csv: "
              f"{len(out)} SKUs mapped, {n_forecasted} with an actual forecast.")
        return out

    except Exception as e:
        print(f"[INSIGHTS] Warning loading forecast_master_mapped.csv: {e}")
        import traceback; traceback.print_exc()
        return empty


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


def compute_no_risk_stock(db_df, wh_df, month_start):
    """
    Per-SKU NO-RISK stock for one month, from the raw Inventory.xlsx DB and
    WH sheets (loaded by services/insights_service.load_inventory_sheets()).

    The expiry classification is NOT reimplemented here. Insights delegates
    to engines/risk_engine.py — the same
    build_distributor_inventory_snapshot() / build_warehouse_inventory_
    snapshot() the Inventory/Risk page uses — so "no-risk stock" has ONE
    definition in the codebase and the two pages cannot drift apart. That
    also means Insights automatically picks up the sheets' differing
    schemas (DB uses "UnitQty", WH uses "Trade Qty" plus Blocked/Insp)
    without duplicating that knowledge.

    "No risk" = not expired, and not already/soon short-dated:
        EXPIRED   : expiry < the REPORTING MONTH start
        SHORT_EXP : (expiry - 3 months) <= month_start + 3 months
        NO_RISK   : everything else with a known expiry

    EXPIRED is measured against the reporting month, NOT today: Insights
    describes a month that has already closed, so a batch that expired
    after that month ended was still perfectly sellable during it.
    Measuring against today would both misstate the month and change the
    same report every day it is re-run. The Risk page keeps today as its
    reference, since it is a live forward-looking view — that is the only
    difference between the two, and it is passed in explicitly rather
    than forked in the logic.

    Returns: ItemCode | WH_NoRisk_Qty | DB_NoRisk_Qty | NoRisk_Stock_Qty
    """
    cols = ["ItemCode", "WH_NoRisk_Qty", "DB_NoRisk_Qty", "NoRisk_Stock_Qty"]
    empty = pd.DataFrame(columns=cols)

    db_empty = db_df is None or db_df.empty
    wh_empty = wh_df is None or wh_df.empty
    if db_empty and wh_empty:
        return empty

    # Lazy import: a broken/absent risk pipeline should degrade SHP to
    # blank, not take down the whole Insights run.
    try:
        from engines.risk_engine import (
            risk_cutoff_date,
            build_distributor_inventory_snapshot,
            build_warehouse_inventory_snapshot,
        )
    except Exception as e:
        print(f"[INSIGHTS] risk_engine unavailable ({e}) — SHP will be blank.")
        return empty

    base   = pd.Timestamp(month_start).to_period("M").to_timestamp()
    cutoff = risk_cutoff_date(base)

    parts = []
    if not db_empty:
        try:
            db_snap = build_distributor_inventory_snapshot(
                db_df, base, cutoff, expired_asof=base)
            if not db_snap.empty:
                parts.append(
                    db_snap[["ItemCode", "Distributor_NoRisk_Qty"]]
                    .rename(columns={"Distributor_NoRisk_Qty": "DB_NoRisk_Qty"})
                )
        except Exception as e:
            print(f"[INSIGHTS] Could not build DB inventory snapshot: {e}")

    if not wh_empty:
        try:
            wh_snap = build_warehouse_inventory_snapshot(
                wh_df, base, cutoff, expired_asof=base)
            if not wh_snap.empty:
                parts.append(
                    wh_snap[["ItemCode", "Primary_NoRisk_Qty"]]
                    .rename(columns={"Primary_NoRisk_Qty": "WH_NoRisk_Qty"})
                )
        except Exception as e:
            print(f"[INSIGHTS] Could not build WH inventory snapshot: {e}")

    if not parts:
        return empty

    out = parts[0]
    for p in parts[1:]:
        out = out.merge(p, on="ItemCode", how="outer")

    out["ItemCode"] = out["ItemCode"].astype(str)
    for c in ["WH_NoRisk_Qty", "DB_NoRisk_Qty"]:
        if c not in out.columns:
            out[c] = 0.0
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0).round(2)
    out["NoRisk_Stock_Qty"] = (out["WH_NoRisk_Qty"] + out["DB_NoRisk_Qty"]).round(2)

    print(f"[INSIGHTS] No-risk stock for {base:%b %Y} via risk_engine "
          f"(short-exp cutoff {cutoff:%Y-%m-%d}): {len(out)} SKUs, "
          f"WH {out['WH_NoRisk_Qty'].sum():,.0f} + DB {out['DB_NoRisk_Qty'].sum():,.0f} units.")
    return out[cols].reset_index(drop=True)


def build_l3m_and_shp(actuals_df, all_sales_df, latest_month_dt, no_risk_df=None):
    """
    L3M moving average and stock-holding-period (SHP) for the reporting
    month.

    L3M AVG *EXCLUDES* the reporting month itself — it is the average of
    the THREE MONTHS BEFORE it, always divided by 3:

        L3M(Jun) = (May + Apr + Mar actual) / 3

    That's the business definition: L3M is the demand baseline the month
    was entered WITH, so including the month's own sales would let the
    outcome contaminate the baseline used to judge it. A month with no
    sales row counts as 0 rather than shrinking the divisor, so the
    average always reflects a true 3-month window.

    SHP = that month's OPENING no-risk stock / L3M avg, i.e. how many
    months of typical demand the stock on hand could cover:

        WH_SHP  = WH no-risk  / L3M
        DB_SHP  = DB no-risk  / L3M
        Current_SHP = (WH + DB no-risk) / L3M

    Stock comes from Inventory.xlsx (via `no_risk_df`), NOT from the
    processed sales file's inventory columns — the workbook is the
    authoritative batch-level source and the only one that knows expiry.
    SHP is null when L3M is 0 (dividing a stock figure by no demand is
    meaningless, not "infinite cover").

    Returns: ItemCode | L3M_Moving_Avg | WH_Stock | DB_Stock |
             WH_SHP | DB_SHP | Current_SHP
    """
    cols = ["ItemCode", "L3M_Moving_Avg", "WH_Stock", "DB_Stock",
            "WH_SHP", "DB_SHP", "Current_SHP"]

    latest = pd.Timestamp(latest_month_dt).to_period("M").to_timestamp()
    prior_months = [latest - pd.DateOffset(months=k) for k in (1, 2, 3)]

    # Sum the three PRIOR months' actuals per SKU (same focus/leftover
    # resolution the rest of the page uses), then divide by 3.
    total = None
    for i, m in enumerate(prior_months):
        part = _qty_with_fallback(
            actuals_df, all_sales_df,
            (lambda mm: (lambda d: d["Month_dt"] == mm))(m),
            "Secondary_Sales_Qty", f"_m{i}",
        )
        total = part if total is None else total.merge(part, on="ItemCode", how="outer")

    if total is None or total.empty:
        return pd.DataFrame(columns=cols)

    qty_cols = [c for c in total.columns if c.startswith("_m")]
    for c in qty_cols:
        total[c] = pd.to_numeric(total[c], errors="coerce").fillna(0)
    total["L3M_Moving_Avg"] = (total[qty_cols].sum(axis=1) / 3.0).round(2)
    total = total[["ItemCode", "L3M_Moving_Avg"]]

    if no_risk_df is not None and not no_risk_df.empty:
        total = total.merge(
            no_risk_df.rename(columns={
                "WH_NoRisk_Qty": "WH_Stock",
                "DB_NoRisk_Qty": "DB_Stock",
            })[["ItemCode", "WH_Stock", "DB_Stock"]],
            on="ItemCode", how="outer",
        )
        # We DO have inventory for this month, so a SKU missing from it
        # genuinely holds no usable stock — 0 is the right answer here.
        total["WH_Stock"] = total["WH_Stock"].fillna(0)
        total["DB_Stock"] = total["DB_Stock"].fillna(0)
    else:
        # No inventory data for the month at all. Leave stock UNKNOWN
        # rather than 0: filling zeros would report "no cover anywhere"
        # across every SKU, which looks like a catastrophic stock position
        # instead of what it is — missing data.
        total["WH_Stock"] = np.nan
        total["DB_Stock"] = np.nan

    for c in ["L3M_Moving_Avg", "WH_Stock", "DB_Stock"]:
        total[c] = pd.to_numeric(total.get(c), errors="coerce")

    l3m = total["L3M_Moving_Avg"]
    scorable = l3m > 0
    total["WH_SHP"] = np.where(scorable, total["WH_Stock"] / l3m, np.nan)
    total["DB_SHP"] = np.where(scorable, total["DB_Stock"] / l3m, np.nan)
    total["Current_SHP"] = np.where(
        scorable, (total["WH_Stock"].fillna(0) + total["DB_Stock"].fillna(0)) / l3m, np.nan
    )

    for c in ["L3M_Moving_Avg", "WH_Stock", "DB_Stock", "WH_SHP", "DB_SHP", "Current_SHP"]:
        total[c] = pd.to_numeric(total[c], errors="coerce").round(2)

    lbl = ", ".join(pd.Timestamp(m).strftime("%b") for m in reversed(prior_months))
    print(f"[INSIGHTS] L3M avg for {latest:%b %Y} built from {lbl} "
          f"(reporting month excluded); SHP from that month's opening no-risk stock.")

    return total[cols].copy()


# ─────────────────────────────────────────────────────────────────────────────
# Current-month trade stock (Stockout vs Other loss split)
# ─────────────────────────────────────────────────────────────────────────────
def load_current_month_stock():
    """
    Per-SKU trade stock (WH + DB) for the latest CLOSED month — the stock
    that was physically on hand to cover that month's demand. Used by the
    loss lens (Budget vs Actual, computed in build_agency_performance_table)
    to split the Budget-vs-Actual gap into Stockout (stock genuinely
    insufficient) vs Other (stock existed — execution/other reason).

    Sourced from processed_data_actual.csv (focus SKUs; leftover SKUs get
    Trade_Stock_Qty = 0, since inventory snapshots are only tracked for
    SKUs with a real, focus-mapped ItemCode).

    Returns: ItemCode | Current_Month_Label | WH_Stock_Current |
             DB_Stock_Current | Trade_Stock_Qty
    """
    empty = pd.DataFrame(columns=[
        "ItemCode", "Current_Month_Label",
        "WH_Stock_Current", "DB_Stock_Current", "Trade_Stock_Qty",
    ])

    if not os.path.exists(PROCESSED_DATA_FILE):
        print("[INSIGHTS] Stock lookup skipped — processed data file missing.")
        return empty

    try:
        df = pd.read_csv(PROCESSED_DATA_FILE, low_memory=False)
        df = _force_itemcode_str(df)

        required = [
            "ItemCode", "Year", "Month_Number",
            "Available_Primary_Inventory_Qty",
            "Distributor_Inventory_Qty",
        ]
        if any(c not in df.columns for c in required):
            print(f"[INSIGHTS] Stock lookup: missing columns in processed data.")
            return empty

        df["Year"]         = pd.to_numeric(df["Year"],         errors="coerce")
        df["Month_Number"] = pd.to_numeric(df["Month_Number"], errors="coerce")
        df = df.dropna(subset=["Year", "Month_Number"])

        df["Month_dt"] = pd.to_datetime(
            df["Year"].astype(int).astype(str) + "-"
            + df["Month_Number"].astype(int).astype(str).str.zfill(2) + "-01"
        )

        for c in ["Available_Primary_Inventory_Qty", "Distributor_Inventory_Qty"]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).clip(lower=0)

        df = df.sort_values(["ItemCode", "Month_dt"])

        all_months = sorted(df["Month_dt"].unique())
        if not all_months:
            return empty

        current_month_dt = all_months[-1]
        current_label    = pd.Timestamp(current_month_dt).strftime("%b %Y")

        current_rows = df[df["Month_dt"] == current_month_dt]

        result = (
            current_rows
            .groupby("ItemCode")
            .agg(
                WH_Stock_Current = ("Available_Primary_Inventory_Qty", "sum"),
                DB_Stock_Current = ("Distributor_Inventory_Qty",       "sum"),
            )
            .reset_index()
        )
        result["Trade_Stock_Qty"] = (
            result["WH_Stock_Current"].fillna(0) + result["DB_Stock_Current"].fillna(0)
        )
        result["Current_Month_Label"] = current_label

        for c in ["WH_Stock_Current", "DB_Stock_Current", "Trade_Stock_Qty"]:
            result[c] = pd.to_numeric(result[c], errors="coerce").fillna(0).round(2)

        return result[list(empty.columns)].copy()

    except Exception as e:
        print(f"[INSIGHTS] Warning computing current-month stock: {e}")
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
        ItemCode, Agency_Budget, Budget_Qty (current month), Annual_Budget_Qty,
        Budget_Price (reference only — the sheet's own BudgetPrice column;
        NOT used for valuation any more, see module docstring)

    ItemCode resolution:
        Every row is matched to services/sku_master_service.py's
        sku_master_full.csv by (Agency, ItemName) — the SAME canonical
        source used everywhere else in Insights. Real ItemCodes and
        synthetic "SYN-..." codes both come from there, so budget rows join
        cleanly against anything else keyed by that master (agency map,
        forecast pipeline joins, etc.) instead of using a locally-invented key.

    budget_meta keys:
        fiscal_start (Timestamp | None), fiscal_end (Timestamp | None),
        month_labels (list[str]), current_month_found (bool),
        monthly_qty (DataFrame: ItemCode | Month_dt | Budget_Qty, EVERY
        budgeted month, long format — used to value budget at the
        distributor price effective in EACH month, see _monthly_value_sum()).
    """
    empty_sku  = pd.DataFrame(columns=["ItemCode", "Agency_Budget", "ItemName_Budget", "Budget_Qty", "Annual_Budget_Qty", "Budget_Price", "Is_Unmapped"])
    empty_meta = {
        "fiscal_start": None, "fiscal_end": None, "month_labels": [], "current_month_found": False,
        "monthly_qty": pd.DataFrame(columns=["ItemCode", "Month_dt", "Budget_Qty"]),
    }

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

        # ── Long-format monthly qty (v8): ItemCode | Month_dt | Budget_Qty
        # for EVERY budgeted month — used to value budget at the
        # distributor price effective in EACH month it spans (not one flat
        # annual/current price), via _monthly_value_sum() above.
        monthly_qty = df.melt(
            id_vars=["ItemCode"], value_vars=list(month_col_map.keys()),
            var_name="_month_col", value_name="Budget_Qty",
        )
        monthly_qty["Month_dt"] = monthly_qty["_month_col"].map(month_col_map)
        monthly_qty = (
            monthly_qty.groupby(["ItemCode", "Month_dt"], as_index=False)["Budget_Qty"]
            .sum()
        )

        # ── Budgeted unit price (BudgetPrice column) — REFERENCE ONLY (v8).
        # No longer used to value anything — Budget_Qty and Secondary sales
        # are both valued at DistributorPrice.xlsx instead (see module
        # docstring). Kept in the output purely for visibility/comparison.
        price_col = next(
            (c for c in ["BudgetPrice", "Budget_Price", "UnitPrice", "Unit_Price", "Price"]
             if c in df.columns),
            None,
        )
        df["_price"] = (
            pd.to_numeric(df[price_col], errors="coerce").fillna(0).clip(lower=0)
            if price_col else 0.0
        )

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
            "monthly_qty":         monthly_qty,
        }

        print(f"[INSIGHTS] Budget loaded from '{BUDGET_SHEET_NAME}': "
              f"{len(per_sku)} SKUs, FY {budget_meta['month_labels'][0]} → {budget_meta['month_labels'][-1]}.")
        return per_sku, budget_meta

    except Exception as e:
        print(f"[INSIGHTS] Warning loading budget: {e}")
        import traceback; traceback.print_exc()
        return empty_sku, empty_meta


# ─────────────────────────────────────────────────────────────────────────────
# Budget analysis table builder — ALL budgeted items, ACTUAL (secondary) vs
# budget vs forecast
# ─────────────────────────────────────────────────────────────────────────────
def build_budget_analysis_table(budget_sku_lookup, budget_meta,
                                actuals_df, agency_map,
                                latest_month_dt, price_history,
                                all_sales_df=None):
    """
    One row per budgeted SKU (from "All Budget 26 27 FY"), even when the SKU
    has no sales at all.

    SKU universe = the full SKU master list.

    ACTUAL (v8): Secondary_Sales_Qty is now the headline comparison basis
    (budget qty ≈ secondary sales target qty — business confirmed), combined
    via _qty_with_fallback() / _value_with_fallback() (focus-side value
    always preferred where it exists):
        - processed_data_actual.csv (`actuals_df`)       -> focus∩master SKUs
        - fact_monthly_closed, all SKUs (`all_sales_df`) -> the ONLY source
          for leftover SKUs (in the master, not in the focus list), which
          never go through preprocessing at all.

    PRICING (v8 — see module docstring): Budget_Qty and Secondary_Sales_Qty
    are BOTH valued at DistributorPrice.xlsx, priced PER MONTH (never one
    flat annual/current price) via _monthly_value_sum()/_value_with_fallback().
    Budget.xlsx's own BudgetPrice column is kept for reference only.

    Columns:
        Agency, ItemCode, ItemName,
        Budget_Qty, Budget_Price (reference only), Budget_Value,
        Current_Month_Secondary_Sales, Current_Month_Secondary_Sales_Value,
        Achievement_%                 (Actual / Budget, null when Budget = 0)
        Distributor_Unit_Price, Price_Source,
        Annual_Budget_Qty, Annual_Budget_Value,
        FYTD_Secondary_Sales_Qty, FYTD_Secondary_Sales_Value,
        Annual_Reach_%                (FYTD Actual / Annual, null when Annual = 0)
        Annual_Remaining_Qty          (max(Annual − FYTD Actual, 0))
        Is_Unmapped

    NOTE: forecast columns used to live here too. They now have their own
    table — see build_forecast_analysis_table() — so this table stays
    purely Budget vs Actual.
    """
    empty = pd.DataFrame(columns=[
        "Agency", "ItemCode", "ItemName",
        "Budget_Qty", "Budget_Price", "Budget_Value",
        "Current_Month_Secondary_Sales", "Distributor_Unit_Price", "Price_Source",
        "Current_Month_Secondary_Sales_Value", "Achievement_%",
        "Annual_Budget_Qty", "Annual_Budget_Value",
        "FYTD_Secondary_Sales_Qty", "FYTD_Secondary_Sales_Value",
        "Annual_Reach_%", "Annual_Remaining_Qty",
        "Is_Unmapped",
    ])

    if budget_sku_lookup is None or budget_sku_lookup.empty:
        return empty

    bt = budget_sku_lookup.copy()
    if "Is_Unmapped" not in bt.columns:
        bt["Is_Unmapped"] = False
    bt["Is_Unmapped"] = bt["Is_Unmapped"].fillna(False).astype(bool)

    # ── Agency / ItemName from master mapping; budget sheet values as fallback ─
    bt = bt.merge(agency_map, on="ItemCode", how="left")
    bt["Agency"]   = bt["Agency"].fillna(bt["Agency_Budget"]).fillna("Unknown Agency")
    if "ItemName_Budget" in bt.columns:
        bt["ItemName"] = bt["ItemName"].fillna(
            bt["ItemName_Budget"].replace("", np.nan)
        )
    bt["ItemName"] = bt["ItemName"].fillna("")
    bt = bt.drop(columns=[c for c in ["Agency_Budget", "ItemName_Budget"] if c in bt.columns])

    monthly_budget_qty = budget_meta.get("monthly_qty")
    if monthly_budget_qty is None:
        monthly_budget_qty = pd.DataFrame(columns=["ItemCode", "Month_dt", "Budget_Qty"])

    # ── Current-month ACTUAL (secondary, focus-preferred/leftover-fallback) ──
    cur_mask = lambda d: d["Month_dt"] == latest_month_dt
    cur_secondary = _qty_with_fallback(actuals_df, all_sales_df, cur_mask,
                                        "Secondary_Sales_Qty", "Current_Month_Secondary_Sales")
    bt = bt.merge(cur_secondary, on="ItemCode", how="left")
    bt["Current_Month_Secondary_Sales"] = pd.to_numeric(bt["Current_Month_Secondary_Sales"], errors="coerce").fillna(0)

    # ── FYTD ACTUAL (fiscal start → latest closed month) ─────────────────────
    fy_start = budget_meta.get("fiscal_start")
    fytd_mask = (
        (lambda d: (d["Month_dt"] >= fy_start) & (d["Month_dt"] <= latest_month_dt))
        if fy_start is not None else cur_mask
    )
    fytd_secondary = _qty_with_fallback(actuals_df, all_sales_df, fytd_mask,
                                         "Secondary_Sales_Qty", "FYTD_Secondary_Sales_Qty")
    bt = bt.merge(fytd_secondary, on="ItemCode", how="left")
    bt["FYTD_Secondary_Sales_Qty"] = pd.to_numeric(bt["FYTD_Secondary_Sales_Qty"], errors="coerce").fillna(0)

    bt["Budget_Qty"]        = pd.to_numeric(bt["Budget_Qty"],        errors="coerce").fillna(0)
    bt["Annual_Budget_Qty"] = pd.to_numeric(bt["Annual_Budget_Qty"], errors="coerce").fillna(0)
    bt["Budget_Price"]      = pd.to_numeric(bt.get("Budget_Price"),  errors="coerce").fillna(0)  # reference only

    # ── PRICING (v8): everything valued at DistributorPrice.xlsx, per month ──
    full_fy_mask = (
        (lambda d: (d["Month_dt"] >= fy_start) & (d["Month_dt"] <= budget_meta.get("fiscal_end")))
        if fy_start is not None else (lambda d: pd.Series(True, index=d.index))
    )

    budget_value_cur    = _monthly_value_sum(monthly_budget_qty, "Budget_Qty", price_history, cur_mask,      "Budget_Value")
    budget_value_annual  = _monthly_value_sum(monthly_budget_qty, "Budget_Qty", price_history, full_fy_mask,  "Annual_Budget_Value")
    secondary_value_cur  = _value_with_fallback(actuals_df, all_sales_df, "Secondary_Sales_Qty", price_history, cur_mask,  "Current_Month_Secondary_Sales_Value")
    secondary_value_fytd = _value_with_fallback(actuals_df, all_sales_df, "Secondary_Sales_Qty", price_history, fytd_mask, "FYTD_Secondary_Sales_Value")

    for frame in (budget_value_cur, budget_value_annual, secondary_value_cur, secondary_value_fytd):
        bt = bt.merge(frame, on="ItemCode", how="left")
    for c in ["Budget_Value", "Annual_Budget_Value",
              "Current_Month_Secondary_Sales_Value", "FYTD_Secondary_Sales_Value"]:
        bt[c] = pd.to_numeric(bt[c], errors="coerce").fillna(0)

    # Current-month distributor price per SKU — shown for reference/QA
    # (the value columns above are already correctly priced per-month
    # internally; this is just "what price is being used this month").
    cur_price_lookup = _price_asof(
        pd.DataFrame({"ItemCode": bt["ItemCode"], "Month_dt": latest_month_dt}),
        price_history,
    )[["ItemCode", "UnitPrice"]].rename(columns={"UnitPrice": "Distributor_Unit_Price"})
    bt = bt.merge(cur_price_lookup, on="ItemCode", how="left")
    bt["Price_Source"] = np.where(bt["Distributor_Unit_Price"].notna(), "DISTRIBUTOR", "NONE")

    # ── Ratios (null-safe: never divide by 0) — ACTUAL vs budget ─────────────
    # v12: the old `.clip(0, 200)` upper bound is GONE. Capping at 200 made
    # every strong over-performer read exactly "200%", which is simply the
    # wrong number — a SKU that sold 5x its budget did 500%, and the
    # business needs to see that. Over-100% values are now reported as-is
    # (the UI colours them black to mark them as over-achievement).
    #
    # A SKU with NO budget still yields null, NOT a percentage: achievement
    # against a budget of zero is undefined, not infinite and certainly not
    # 200%. The UI renders those as "No budget" rather than a number.
    bt["Achievement_%"] = np.where(
        bt["Budget_Qty"] > 0,
        (bt["Current_Month_Secondary_Sales"] / bt["Budget_Qty"] * 100).clip(lower=0),
        np.nan,
    )
    bt["Annual_Reach_%"] = np.where(
        bt["Annual_Budget_Qty"] > 0,
        (bt["FYTD_Secondary_Sales_Qty"] / bt["Annual_Budget_Qty"] * 100).clip(lower=0),
        np.nan,
    )
    bt["Annual_Remaining_Qty"] = np.maximum(
        bt["Annual_Budget_Qty"] - bt["FYTD_Secondary_Sales_Qty"], 0
    )

    for c in ["Budget_Qty", "Budget_Price", "Budget_Value",
              "Current_Month_Secondary_Sales", "Distributor_Unit_Price",
              "Current_Month_Secondary_Sales_Value", "Achievement_%",
              "Annual_Budget_Qty", "Annual_Budget_Value",
              "FYTD_Secondary_Sales_Qty", "FYTD_Secondary_Sales_Value",
              "Annual_Reach_%", "Annual_Remaining_Qty"]:
        bt[c] = pd.to_numeric(bt[c], errors="coerce").round(2)

    bt = bt[list(empty.columns)].copy()
    bt = bt.sort_values("Budget_Qty", ascending=False).reset_index(drop=True)
    return bt


# ─────────────────────────────────────────────────────────────────────────────
# Forecast analysis table builder — OUR forecast vs EXTERNAL forecast,
# each scored against budget and actual
# ─────────────────────────────────────────────────────────────────────────────
def build_forecast_analysis_table(budget_sku_lookup, agency_map,
                                  latest_month_dt, price_history,
                                  cur_secondary, model_hist_df=None,
                                  external_df=None):
    """
    One row per master SKU: our model's forecast next to the external
    (business-supplied) forecast, both scored against the same month's
    budget and actual.

    EVALUATION MONTH = `latest_month_dt` (the latest CLOSED month, the same
    month the rest of the page reports on). That choice is deliberate: it
    is the only month where all four numbers exist at once — budget,
    actual, our forecast and the external forecast — so deviations and
    accuracy are all computable. Scoring against a future month would
    leave every accuracy cell blank.

    OUR forecast for that month comes ONLY from `model_hist_df`
    (forecast_horizon_history.csv, Horizon == "M+1" — what the model
    predicted FOR that month back when the month was still ahead). If a
    SKU has no row there, Model_Forecast_Qty is 0 and Model_Forecast_Basis
    is "NONE" — same as a SKU with no forecast at all.

    v11: there is deliberately NO fallback to the current live forecast
    run (forecast_master_mapped.csv) here any more. That file always
    targets the month AFTER the last closed month (it's generated FROM
    that month's just-closed data, plus next month's inventory
    projection) — so for a page scoped to the last closed month, it is
    never a valid stand-in for "this month's forecast," only for next
    month's. Backfilling it under a "LIVE_FORWARD" tag used to report the
    wrong month's number as if it were this month's — a real bug, not
    just a caveat — so that fallback was removed entirely.

    DEVIATIONS are computed on QTY only (never value), per the business
    rule — a value deviation would just re-express the same qty gap
    through a price both sides share.

        Model_Dev_Vs_Budget_Qty  = Model_Forecast_Qty − Budget_Qty
        Model_Dev_Vs_Actual_Qty  = Model_Forecast_Qty − Actual_Qty
        External_Dev_Vs_*        likewise
        *_Dev_Vs_*_%             the same gap as a % of the baseline
                                 (null when the baseline is 0)

    ACCURACY (per SKU, vs that month's actual):
        100 − |forecast − actual| / actual × 100, floored at 0, null when
        actual is 0 (nothing meaningful to score against). Computed for
        BOTH forecasts so they can be compared head to head.

    Returns one row per master SKU with:
        Agency, ItemCode, ItemName, Forecast_Month,
        Distributor_Unit_Price, Price_Source,
        Budget_Qty, Actual_Qty,
        Model_Forecast_Qty, Model_Forecast_Value, Model_Forecast_Basis,
        External_Forecast_Qty, External_Forecast_Value,
        Model_Dev_Vs_Budget_Qty, Model_Dev_Vs_Budget_%,
        Model_Dev_Vs_Actual_Qty, Model_Dev_Vs_Actual_%,
        External_Dev_Vs_Budget_Qty, External_Dev_Vs_Budget_%,
        External_Dev_Vs_Actual_Qty, External_Dev_Vs_Actual_%,
        Model_Accuracy_%, External_Accuracy_%
    """
    out_cols = [
        "Agency", "ItemCode", "ItemName", "Forecast_Month",
        "Distributor_Unit_Price", "Price_Source",
        "Budget_Qty", "Actual_Qty",
        "Model_Forecast_Qty", "Model_Forecast_Value", "Model_Forecast_Basis",
        "External_Forecast_Qty", "External_Forecast_Value",
        "Model_Dev_Vs_Budget_Qty", "Model_Dev_Vs_Budget_%",
        "Model_Dev_Vs_Actual_Qty", "Model_Dev_Vs_Actual_%",
        "External_Dev_Vs_Budget_Qty", "External_Dev_Vs_Budget_%",
        "External_Dev_Vs_Actual_Qty", "External_Dev_Vs_Actual_%",
        "Model_Accuracy_%", "External_Accuracy_%",
    ]
    empty = pd.DataFrame(columns=out_cols)

    if budget_sku_lookup is None or budget_sku_lookup.empty:
        return empty

    month_label = pd.Timestamp(latest_month_dt).strftime("%Y-%m")

    ft = budget_sku_lookup[["ItemCode", "Agency_Budget", "ItemName_Budget", "Budget_Qty"]].copy()
    ft = ft.merge(agency_map, on="ItemCode", how="left")
    ft["Agency"] = ft["Agency"].fillna(ft["Agency_Budget"]).fillna("Unknown Agency")
    ft["ItemName"] = ft["ItemName"].fillna(
        ft["ItemName_Budget"].replace("", np.nan)
    ).fillna("")
    ft = ft.drop(columns=[c for c in ["Agency_Budget", "ItemName_Budget"] if c in ft.columns])
    ft["Budget_Qty"] = pd.to_numeric(ft["Budget_Qty"], errors="coerce").fillna(0)
    ft["Forecast_Month"] = month_label

    # ── Actuals for the evaluation month (same figures the other tables use) ─
    if cur_secondary is not None and not cur_secondary.empty:
        ft = ft.merge(
            cur_secondary.rename(columns={"Current_Month_Secondary_Sales": "Actual_Qty"}),
            on="ItemCode", how="left",
        )
    else:
        ft["Actual_Qty"] = 0.0
    ft["Actual_Qty"] = pd.to_numeric(ft["Actual_Qty"], errors="coerce").fillna(0)

    # ── OUR forecast for that month — SAME-MONTH history ONLY. No fallback
    # to the current live forecast run: that file targets next month, not
    # this one (see v11 note above), so a SKU with no same-month history
    # simply gets no forecast here (Basis="NONE"), exactly like a SKU that
    # was never forecast at all — never a different month's number.
    if model_hist_df is not None and not model_hist_df.empty:
        hist = model_hist_df[["ItemCode", "Model_Forecast_Qty"]].drop_duplicates("ItemCode")
        ft = ft.merge(hist, on="ItemCode", how="left")
    else:
        ft["Model_Forecast_Qty"] = np.nan

    ft["Model_Forecast_Basis"] = np.where(
        ft["Model_Forecast_Qty"].notna(), "HISTORY_M+1", "NONE"
    )

    ft["Model_Forecast_Qty"] = pd.to_numeric(ft["Model_Forecast_Qty"], errors="coerce").fillna(0)

    # ── EXTERNAL forecast for that month ─────────────────────────────────────
    if external_df is not None and not external_df.empty:
        ext = (
            external_df.groupby("ItemCode", as_index=False)["External_Forecast_Qty"].sum()
        )
        ft = ft.merge(ext, on="ItemCode", how="left")
        ft["Has_External"] = ft["External_Forecast_Qty"].notna()
    else:
        ft["External_Forecast_Qty"] = np.nan
        ft["Has_External"] = False
    ft["External_Forecast_Qty"] = pd.to_numeric(ft["External_Forecast_Qty"], errors="coerce").fillna(0)

    # ── Pricing: same distributor price the rest of the page uses ────────────
    price_lookup = _price_asof(
        pd.DataFrame({"ItemCode": ft["ItemCode"], "Month_dt": pd.Timestamp(latest_month_dt)}),
        price_history,
    )[["ItemCode", "UnitPrice"]].rename(columns={"UnitPrice": "Distributor_Unit_Price"})
    ft = ft.merge(price_lookup, on="ItemCode", how="left")
    ft["Price_Source"] = np.where(ft["Distributor_Unit_Price"].notna(), "DISTRIBUTOR", "NONE")
    _price = ft["Distributor_Unit_Price"].fillna(0)

    ft["Model_Forecast_Value"]    = (ft["Model_Forecast_Qty"]    * _price).round(2)
    ft["External_Forecast_Value"] = (ft["External_Forecast_Qty"] * _price).round(2)

    # ── Deviations (QTY basis only — business rule) ──────────────────────────
    def _dev(fc_col, base_col, out_qty, out_pct):
        gap = ft[fc_col] - ft[base_col]
        ft[out_qty] = gap.round(2)
        ft[out_pct] = np.where(
            ft[base_col] > 0, (gap / ft[base_col] * 100).round(2), np.nan
        )

    _dev("Model_Forecast_Qty",    "Budget_Qty", "Model_Dev_Vs_Budget_Qty",    "Model_Dev_Vs_Budget_%")
    _dev("Model_Forecast_Qty",    "Actual_Qty", "Model_Dev_Vs_Actual_Qty",    "Model_Dev_Vs_Actual_%")
    _dev("External_Forecast_Qty", "Budget_Qty", "External_Dev_Vs_Budget_Qty", "External_Dev_Vs_Budget_%")
    _dev("External_Forecast_Qty", "Actual_Qty", "External_Dev_Vs_Actual_Qty", "External_Dev_Vs_Actual_%")

    # A SKU with NO forecast at all must not be reported as "-100% deviation"
    # — that reads like a forecast that missed badly, when in fact nothing
    # was forecast. Blank the deviation cells instead, so the UI shows "—".
    no_model = ft["Model_Forecast_Basis"] == "NONE"
    for c in ["Model_Dev_Vs_Budget_Qty", "Model_Dev_Vs_Budget_%",
              "Model_Dev_Vs_Actual_Qty", "Model_Dev_Vs_Actual_%"]:
        ft.loc[no_model, c] = np.nan

    no_ext = ~ft["Has_External"]
    for c in ["External_Dev_Vs_Budget_Qty", "External_Dev_Vs_Budget_%",
              "External_Dev_Vs_Actual_Qty", "External_Dev_Vs_Actual_%"]:
        ft.loc[no_ext, c] = np.nan

    # ── Accuracy vs the month's actual (0-100, null when actual is 0) ────────
    def _accuracy(fc_col, out_col, only_when=None):
        acc = 100.0 - (ft[fc_col] - ft["Actual_Qty"]).abs() / ft["Actual_Qty"] * 100.0
        acc = acc.clip(lower=0, upper=100).round(2)
        mask = ft["Actual_Qty"] > 0
        if only_when is not None:
            mask = mask & only_when
        ft[out_col] = np.where(mask, acc, np.nan)

    # A live forward forecast targets a DIFFERENT month than the actual it
    # would be scored against — scoring it would be meaningless, so those
    # rows get no accuracy figure.
    _accuracy("Model_Forecast_Qty", "Model_Accuracy_%",
              only_when=(ft["Model_Forecast_Basis"] == "HISTORY_M+1"))
    _accuracy("External_Forecast_Qty", "External_Accuracy_%",
              only_when=ft["Has_External"])

    for c in ["Budget_Qty", "Actual_Qty", "Distributor_Unit_Price",
              "Model_Forecast_Qty", "Model_Forecast_Value",
              "External_Forecast_Qty", "External_Forecast_Value"]:
        ft[c] = pd.to_numeric(ft[c], errors="coerce").round(2)

    ft = ft[out_cols].copy()
    ft = ft.sort_values("Budget_Qty", ascending=False).reset_index(drop=True)
    return ft


# ─────────────────────────────────────────────────────────────────────────────
# Trend table — per SKU per month, FY-to-date Actual vs Budget
# ─────────────────────────────────────────────────────────────────────────────
def build_trend_table(master_codes, agency_map, monthly_budget_qty,
                      actuals_df, all_sales_df, price_history,
                      latest_month_dt, fy_start=None):
    """
    Monthly Actual-vs-Budget series for the Agency Performance trend chart:
    one row per SKU per month, from the fiscal-year start through the
    reporting month.

    Every month is priced at ITS OWN distributor price (not the reporting
    month's), so the value lines track what each month was actually worth —
    the same per-month rule the FYTD/Annual roll-ups use.

    Kept as a long, per-SKU table rather than pre-aggregated totals so the
    chart can show either the agency/overall total or a single SKU without
    a second engine run; the service aggregates on read.

    Returns: Agency | ItemCode | ItemName | Month | Budget_Qty | Actual_Qty
             | Budget_Value | Actual_Value
    """
    cols = ["Agency", "ItemCode", "ItemName", "Month",
            "Budget_Qty", "Actual_Qty", "Budget_Value", "Actual_Value"]
    empty = pd.DataFrame(columns=cols)

    latest = pd.Timestamp(latest_month_dt).to_period("M").to_timestamp()

    # Fiscal year starts in April. Fall back to deriving it from the
    # reporting month when the budget sheet didn't report one.
    if fy_start is None:
        fy_year = latest.year if latest.month >= 4 else latest.year - 1
        fy_start = pd.Timestamp(fy_year, 4, 1)
    fy_start = pd.Timestamp(fy_start).to_period("M").to_timestamp()

    if fy_start > latest:
        return empty

    months = pd.date_range(fy_start, latest, freq="MS")
    if len(months) == 0:
        return empty

    base = pd.DataFrame({"ItemCode": sorted(set(master_codes))})
    base = base.merge(agency_map, on="ItemCode", how="left")
    base["Agency"]   = base["Agency"].fillna("Unknown Agency")
    base["ItemName"] = base["ItemName"].fillna("")

    budget_long = monthly_budget_qty if monthly_budget_qty is not None else pd.DataFrame()

    frames = []
    for m in months:
        part = base.copy()
        part["Month"] = m.strftime("%Y-%m")

        actual = _qty_with_fallback(
            actuals_df, all_sales_df,
            (lambda mm: (lambda d: d["Month_dt"] == mm))(m),
            "Secondary_Sales_Qty", "Actual_Qty",
        )
        part = part.merge(actual, on="ItemCode", how="left")

        if not budget_long.empty and "Month_dt" in budget_long.columns:
            bq = (
                budget_long[budget_long["Month_dt"] == m]
                .groupby("ItemCode", as_index=False)["Budget_Qty"].sum()
            )
            part = part.merge(bq, on="ItemCode", how="left")
        else:
            part["Budget_Qty"] = np.nan

        for c in ["Actual_Qty", "Budget_Qty"]:
            part[c] = pd.to_numeric(part[c], errors="coerce").fillna(0)

        price = _price_asof(
            pd.DataFrame({"ItemCode": part["ItemCode"], "Month_dt": m}),
            price_history,
        )[["ItemCode", "UnitPrice"]]
        part = part.merge(price, on="ItemCode", how="left")
        p = part["UnitPrice"].fillna(0)

        part["Actual_Value"] = (part["Actual_Qty"] * p).round(2)
        part["Budget_Value"] = (part["Budget_Qty"] * p).round(2)

        # Drop rows that are entirely empty for the month — a SKU with no
        # budget AND no sale contributes nothing to any line and would
        # otherwise multiply the payload by the full master list every month.
        part = part[(part["Actual_Qty"] > 0) | (part["Budget_Qty"] > 0)]
        frames.append(part[cols])

    if not frames:
        return empty

    out = pd.concat(frames, ignore_index=True)
    print(f"[INSIGHTS] Trend series built: {len(months)} months "
          f"({months[0]:%b %Y} → {months[-1]:%b %Y}), {len(out)} SKU-month rows.")
    return out.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# Main builder
# ─────────────────────────────────────────────────────────────────────────────

def build_agency_performance_table():
    actuals_df   = load_actual_sales()
    agency_map   = load_agency_mapping()
    stock_lookup = load_current_month_stock()   # Trade_Stock_Qty (WH+DB)
    price_history = load_distributor_price_history()  # v8 — sole price source
    # v11: forecast_master_mapped.csv (load_current_forecast()) is NOT read
    # here any more — it always targets the month AFTER the last closed
    # month, never the reporting month itself, so it can't correctly feed
    # either "Current Forecast" (now external — see below) or "My Forecast"
    # (now same-month history only — see build_forecast_analysis_table()).
    master_codes = load_master_sku_codes()

    latest_month_dt = actuals_df["Month_dt"].max()
    data_upto_label  = latest_month_dt.strftime("%b %Y")

    # v10 — "Current Forecast" (this table + the SKU-wise Performance table)
    # and "External Forecast" (Forecast Analysis tab) must be the SAME
    # number, since they're the same concept shown in two places. Both now
    # read the EXTERNAL (business-supplied) forecast — Forecast.xlsx, via
    # services/insights_service.py — for the current reporting month,
    # loaded ONCE here and reused below when building the forecast analysis
    # table too, so there is exactly one call/one source for this data.
    from services.insights_service import (
        load_external_forecast,
        load_model_forecast_history,
        load_inventory_sheets,
    )
    external_forecast_df = load_external_forecast(latest_month_dt)

    # ALL-SKU actuals (raw fact, focus filter NOT applied) — the ONLY source
    # for "leftover" SKUs (in the SKU master, not in the focus list), which
    # never go through the preprocessing pipeline at all.
    all_sales_df = load_all_sku_sales()

    # v12 — L3M avg + SHP. L3M now EXCLUDES the reporting month (prior 3
    # months / 3), and SHP divides that month's OPENING no-risk stock from
    # Inventory.xlsx by it. Both are per-month figures for the month being
    # reported on, matching every other number on the page. The expiry
    # bucketing is risk_engine's, not a local copy — see
    # compute_no_risk_stock().
    inv_db, inv_wh = load_inventory_sheets(latest_month_dt)
    no_risk_stock = compute_no_risk_stock(inv_db, inv_wh, latest_month_dt)
    shp_lookup = build_l3m_and_shp(
        actuals_df, all_sales_df, latest_month_dt, no_risk_df=no_risk_stock
    )

    if not all_sales_df.empty and all_sales_df["Month_dt"].max() < latest_month_dt:
        print(f"[INSIGHTS] WARNING: fact_monthly_closed lags processed data "
              f"({all_sales_df['Month_dt'].max():%b %Y} < {data_upto_label}). "
              f"Leftover-SKU actuals for the latest month will be 0.")

    # Budget lookup — keyed to the CURRENT (latest closed) month. Loaded
    # from "All Budget 26 27 FY" (ALL budgeted items).
    budget_sku_lookup, budget_meta = load_budget_lookup(latest_month_dt)
    monthly_budget_qty = budget_meta.get("monthly_qty")
    if monthly_budget_qty is None:
        monthly_budget_qty = pd.DataFrame(columns=["ItemCode", "Month_dt", "Budget_Qty"])

    all_months = sorted(actuals_df["Month_dt"].unique())

    # ── Current + last-month ACTUAL (secondary) sales ────────────────────────
    # focus∩master SKUs -> processed_data_actual.csv (preferred); leftover
    # SKUs (master, not focus) -> fact_monthly_closed raw (all_sales_df).
    cur_mask = lambda d: d["Month_dt"] == latest_month_dt
    cur_secondary = _qty_with_fallback(actuals_df, all_sales_df, cur_mask,
                                        "Secondary_Sales_Qty", "Current_Month_Secondary_Sales")

    if len(all_months) >= 2:
        prev_month_dt = all_months[-2]
        prev_mask = lambda d: d["Month_dt"] == prev_month_dt
        prev_secondary = _qty_with_fallback(actuals_df, all_sales_df, prev_mask,
                                             "Secondary_Sales_Qty", "Last_Month_Secondary_Sales")
    else:
        prev_secondary = pd.DataFrame(columns=["ItemCode", "Last_Month_Secondary_Sales"])

    # ── SKU universe: the SKU MASTER LIST ONLY. ──────────────────────────────
    # FocusItemCodes.xlsx (which SKUs get an AI forecast) and the raw fact
    # file (which naturally contains every SKU ever sold, budgeted or not)
    # are both forecasting/model concerns — irrelevant here. Insights is
    # scoped end-to-end to the master list, so every row in the output
    # belongs to a real master SKU (nothing extra leaks in from either
    # sales source, and nothing is ever silently dropped since the master
    # list IS the full universe by definition).
    df = pd.DataFrame({"ItemCode": sorted(set(master_codes))})

    df = df.merge(cur_secondary,  on="ItemCode", how="left")
    df = df.merge(prev_secondary, on="ItemCode", how="left")
    df = df.merge(agency_map,     on="ItemCode", how="left")
    df = df.merge(shp_lookup,     on="ItemCode", how="left")
    df = df.merge(stock_lookup,   on="ItemCode", how="left")
    # "Current Forecast" = the EXTERNAL forecast for the current reporting
    # month (same source + same month as the Forecast Analysis tab's
    # "External Forecast" — see external_forecast_df above), NOT our own
    # model's live forecast. That keeps every "Current Forecast" reading on
    # the page — this KPI, and the Current Forecast (units) column on the
    # SKU-wise Performance table — tied to one single source.
    # Guard the empty case explicitly: a missing/unreadable Forecast.xlsx
    # yields a frame with no columns, and grouping that by "ItemCode"
    # raises KeyError — which would take down the ENTIRE Insights run over
    # one optional input. Forecast is supplementary here, so degrade to
    # "no forecast" instead.
    if (external_forecast_df is not None and not external_forecast_df.empty
            and "ItemCode" in external_forecast_df.columns):
        ext_for_main = (
            external_forecast_df.groupby("ItemCode", as_index=False)["External_Forecast_Qty"].sum()
            .rename(columns={"External_Forecast_Qty": "Current_Forecast_Qty"})
        )
        df = df.merge(ext_for_main, on="ItemCode", how="left")
    else:
        print("[INSIGHTS] No external forecast for this month — "
              "Current Forecast will show 0.")
        df["Current_Forecast_Qty"] = 0.0

    for c in ["Current_Month_Secondary_Sales", "Last_Month_Secondary_Sales", "Current_Forecast_Qty"]:
        df[c] = pd.to_numeric(df.get(c), errors="coerce").fillna(0)
    df["Forecast_Source"] = np.where(df["Current_Forecast_Qty"] > 0, "EXTERNAL", "NO_FORECAST")
    df["Forecast_Month"]  = pd.Timestamp(latest_month_dt).strftime("%Y-%m")

    df["MoM_Growth_%"] = np.where(
        df["Last_Month_Secondary_Sales"] > 0,
        ((df["Current_Month_Secondary_Sales"] - df["Last_Month_Secondary_Sales"])
         / df["Last_Month_Secondary_Sales"] * 100).round(2),
        np.nan,
    )

    df["Data_Available_Upto"] = data_upto_label
    df["Current_Month_Label"] = df.get("Current_Month_Label", pd.Series(dtype=object)).fillna(data_upto_label)
    df["Agency"]   = df["Agency"].fillna("Unknown Agency")
    df["ItemName"] = df["ItemName"].fillna("")

    for c in ["Trade_Stock_Qty", "WH_Stock_Current", "DB_Stock_Current",
              "L3M_Moving_Avg"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # Stock + SHP are deliberately NOT zero-filled. build_l3m_and_shp()
    # already wrote 0 where a SKU truly holds no usable stock; anything
    # still null there means we could not compute cover at all (no
    # inventory data for the month, or no L3M demand to divide by), and
    # "—" is the honest rendering of that. A 0.00 would read as "no stock
    # cover", which is a very different — and alarming — claim.
    for c in ["WH_Stock", "DB_Stock", "WH_SHP", "DB_SHP", "Current_SHP"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # ── Budget qty (current month, reference price) ──────────────────────────
    if budget_sku_lookup is not None and not budget_sku_lookup.empty:
        df = df.merge(
            budget_sku_lookup[["ItemCode", "Budget_Qty", "Budget_Price"]], on="ItemCode", how="left"
        )
    else:
        df["Budget_Qty"] = 0.0
        df["Budget_Price"] = 0.0
    df["Budget_Qty"]   = pd.to_numeric(df["Budget_Qty"],   errors="coerce").fillna(0)
    df["Budget_Price"] = pd.to_numeric(df["Budget_Price"], errors="coerce").fillna(0)  # reference only

    # ── PRICING (v8): current-month distributor price, one per SKU — used to
    # value EVERY "current month" figure below (actual, budget, loss,
    # forecast-vs-actual loss) on a consistent, single basis. FYTD/Annual
    # roll-ups in build_budget_analysis_table price each month independently
    # instead (see _monthly_value_sum) since a flat current-month price isn't
    # representative across a whole fiscal-year range.
    cur_price = _price_asof(
        pd.DataFrame({"ItemCode": df["ItemCode"], "Month_dt": latest_month_dt}),
        price_history,
    )[["ItemCode", "UnitPrice"]].rename(columns={"UnitPrice": "Distributor_Unit_Price"})
    df = df.merge(cur_price, on="ItemCode", how="left")
    df["Price_Source"] = np.where(df["Distributor_Unit_Price"].notna(), "DISTRIBUTOR", "NONE")
    _price = df["Distributor_Unit_Price"].fillna(0)

    # ── Budget-vs-ACTUAL loss decomposition ───────────────────────────────────
    # Budget reflects what was actually planned/committed; ACTUAL (secondary)
    # sales is what's compared against it now (budget qty ≈ secondary sales
    # target qty — business confirmed) — both valued at the SAME distributor
    # price for this month, so a value ratio always tracks its qty ratio.
    df["Raw_Loss_Qty"] = np.maximum(df["Budget_Qty"] - df["Current_Month_Secondary_Sales"], 0).round(2)
    df["Other_Loss_Qty"] = np.minimum(df["Raw_Loss_Qty"], df["Trade_Stock_Qty"]).round(2)
    df["Stockout_Loss_Qty"] = np.maximum(df["Raw_Loss_Qty"] - df["Trade_Stock_Qty"], 0).round(2)
    df["Current_Month_Loss_Qty"] = df["Raw_Loss_Qty"]
    df["Stockout_Flag"] = df["Stockout_Loss_Qty"] > 0

    # Loss_Reason lists EVERY reason that applies, not just the dominant
    # one. A SKU can genuinely be both at once: if budget 157,441 vs actual
    # 81,478 leaves a 75,963 gap while 10,910 units sat in trade stock,
    # then 10,910 of that gap was lost DESPITE having stock ("Other") and
    # the remaining 65,053 to a true supply gap ("Stockout"). Reporting
    # only "Stockout" there hides a real, separately-actionable execution
    # miss on stock that was actually available to sell.
    _reasons = np.where(df["Stockout_Loss_Qty"] > 0, "Stockout", "")
    _other   = np.where(df["Other_Loss_Qty"]    > 0, "Other",    "")
    df["Loss_Reason"] = [
        " + ".join([r for r in (a, b) if r]) or "None"
        for a, b in zip(_reasons, _other)
    ]
    df.loc[df["Raw_Loss_Qty"] <= 0, "Loss_Reason"] = "None"

    # ── Forecast vs ACTUAL loss (v8 restore) — same-basis gap, current month ─
    df["Forecast_Vs_Actual_Loss_Qty"] = np.maximum(
        df["Current_Forecast_Qty"] - df["Current_Month_Secondary_Sales"], 0
    ).round(2)

    # ── Value fields — everything below priced at THIS month's distributor
    # price (current-month KPI strip; FYTD/Annual live in the budget table
    # and are priced per-month there instead — see module docstring). ───────
    df["Current_Month_Secondary_Sales_Value"] = (df["Current_Month_Secondary_Sales"] * _price).round(2)
    # Last month's actual valued at THIS month's price on purpose — the
    # SKU-wise table shows it beside the current month so the two can be
    # compared directly; re-pricing it at last month's own price would
    # make the difference a blend of volume AND price movement.
    df["Last_Month_Secondary_Sales_Value"]    = (df["Last_Month_Secondary_Sales"]   * _price).round(2)
    df["Budget_Value"]                        = (df["Budget_Qty"]                   * _price).round(2)
    df["Current_Forecast_Value"]              = (df["Current_Forecast_Qty"]         * _price).round(2)
    df["Raw_Loss_Value"]                      = (df["Raw_Loss_Qty"]                 * _price).round(2)
    df["Other_Loss_Value"]                    = (df["Other_Loss_Qty"]               * _price).round(2)
    df["Stockout_Loss_Value"]                 = (df["Stockout_Loss_Qty"]            * _price).round(2)
    df["Forecast_Vs_Actual_Loss_Value"]       = (df["Forecast_Vs_Actual_Loss_Qty"]  * _price).round(2)

    # ── Master-scope flag — kept for schema stability (older consumers may
    # still read this column), but it is now ALWAYS True: the table is
    # built directly from master_codes (see above), so nothing outside the
    # SKU master ever reaches this output any more.
    df["Is_In_Master"] = df["ItemCode"].isin(master_codes)

    output_cols = [
        "Agency", "ItemCode", "ItemName", "Is_In_Master",
        "Data_Available_Upto",
        "Last_Month_Secondary_Sales", "Current_Month_Secondary_Sales",
        "Budget_Qty", "Budget_Price",
        "MoM_Growth_%",
        "Distributor_Unit_Price", "Price_Source",
        "Last_Month_Secondary_Sales_Value",
        "Current_Month_Secondary_Sales_Value", "Budget_Value",
        "L3M_Moving_Avg", "WH_Stock", "DB_Stock",
        "WH_SHP", "DB_SHP", "Current_SHP",
        "Current_Month_Label",
        "WH_Stock_Current", "DB_Stock_Current", "Trade_Stock_Qty",
        # Budget vs Actual loss
        "Raw_Loss_Qty", "Other_Loss_Qty", "Stockout_Loss_Qty",
        "Raw_Loss_Value", "Other_Loss_Value", "Stockout_Loss_Value",
        "Current_Month_Loss_Qty",
        "Stockout_Flag", "Loss_Reason",
        # Forecast (v8 restore)
        "Current_Forecast_Qty", "Current_Forecast_Value",
        "Forecast_Month", "Forecast_Source",
        "Forecast_Vs_Actual_Loss_Qty", "Forecast_Vs_Actual_Loss_Value",
    ]
    output_cols = [c for c in output_cols if c in df.columns]
    result = df[output_cols].copy()
    result = result.where(pd.notnull(result), other=None)

    # ── Budget analysis table (ALL budgeted items — separate table) ──────────
    budget_table = build_budget_analysis_table(
        budget_sku_lookup, budget_meta,
        actuals_df, agency_map,
        latest_month_dt, price_history,
        all_sales_df=all_sales_df,
    )
    budget_result = budget_table.where(pd.notnull(budget_table), other=None)

    # ── Forecast analysis table (our forecast vs external, separate table) ───
    # Scored on the latest CLOSED month so budget/actual/both forecasts all
    # exist and deviations + accuracy are computable (see the builder's
    # docstring). Reuses the SAME external_forecast_df loaded above (one
    # call, one source — see the note by that load) rather than fetching
    # Forecast.xlsx a second time. History file I/O (load_model_forecast_
    # history) still lives in services/insights_service.py, imported
    # lazily above to avoid a circular import (insights_service imports
    # this module at its own top level). No live_forecast_df fallback any
    # more (v11) — a SKU with no same-month history in that CSV simply
    # gets no forecast here, never a different month's number.
    forecast_table = build_forecast_analysis_table(
        budget_sku_lookup, agency_map,
        latest_month_dt, price_history,
        cur_secondary,
        model_hist_df=load_model_forecast_history(latest_month_dt),
        external_df=external_forecast_df,
    )
    forecast_result = forecast_table.where(pd.notnull(forecast_table), other=None)

    # ── Trend series (FY-to-date Actual vs Budget, per SKU per month) ────────
    # Feeds the flip-side chart on the Agency Performance tab. Served by its
    # own /trend endpoint, so it never bloats the /results payload.
    trend_table = build_trend_table(
        master_codes, agency_map, monthly_budget_qty,
        actuals_df, all_sales_df, price_history,
        latest_month_dt, fy_start=budget_meta.get("fiscal_start"),
    )
    trend_result = trend_table.where(pd.notnull(trend_table), other=None)

    # ── Mapping / coverage diagnostics ───────────────────────────────────────
    # Rendered by the UI as a separate "SKU Mapping & Coverage" section below
    # the tables. All sets keyed by canonical ItemCode (resolved via
    # sku_master_full.csv wherever possible) so they intersect meaningfully.
    # NOTE: the per-SKU output table above is scoped to master_codes ONLY —
    # focus_set/all_sku_set below are informational coverage counts (how
    # many codes each raw source contributes in total), not universes the
    # output ever expands into. A SKU appearing in the fact file or
    # FocusItemCodes.xlsx but NOT in the master list is a forecasting-model
    # concern (which SKUs get an AI forecast), not an Insights data-quality
    # issue, so there is no "not in master" bucket here any more.
    master_set  = set(map(str, master_codes))     # SKU master (Budget.xlsx + Agency map)
    budget_set  = _codes_of(budget_table)         # budgeted items (All Budget sheet)
    price_set   = _codes_of(price_history)        # DistributorPrice.xlsx
    focus_set   = _codes_of(actuals_df)           # focus∩master items (processed sales data)
    all_sku_set = _codes_of(all_sales_df)         # raw fact, all SKUs (leftover-path source)

    if not budget_table.empty:
        synthetic_set   = _codes_of(budget_table[budget_table["Is_Unmapped"] == True]) \
            if "Is_Unmapped" in budget_table.columns else set()
        no_rd_price_set = _codes_of(budget_table[budget_table["Price_Source"] != "DISTRIBUTOR"]) \
            if "Price_Source" in budget_table.columns else set()
    else:
        synthetic_set = no_rd_price_set = set()

    fully_mapped = master_set & budget_set & price_set & (focus_set | all_sku_set)

    # Name/agency resolver for display — master first, then budget table,
    # then the performance table.
    _name_src = {}
    for frame in (agency_map, budget_table, result):
        if frame is not None and not getattr(frame, "empty", True) \
                and {"ItemCode", "ItemName", "Agency"}.issubset(frame.columns):
            for rec in frame.drop_duplicates("ItemCode")[["ItemCode", "ItemName", "Agency"]].itertuples(index=False):
                _name_src.setdefault(str(rec.ItemCode), {"ItemName": rec.ItemName, "Agency": rec.Agency})

    def _mk_items(codes):
        out = []
        for c in sorted(codes):
            info = _name_src.get(c, {})
            out.append({
                "ItemCode": c,
                "ItemName": str(info.get("ItemName") or ""),
                "Agency":   str(info.get("Agency") or ""),
            })
        return out

    mapping_categories = [
        {
            "key": "fully_mapped", "severity": "ok",
            "label": "No issue — fully mapped",
            "description": ("Maps end-to-end: SKU master → budget → actual (secondary) sales "
                            "(focus preprocess or leftover raw fact) → DistributorPrice.xlsx "
                            "(month-effective price). No fallbacks anywhere in the "
                            "Budget-vs-Actual KPI chain."),
            "count": len(fully_mapped), "items": _mk_items(fully_mapped),
        },
        {
            "key": "budget_no_code", "severity": "warn",
            "label": "Has budget but no product code",
            "description": ("New products with no real ItemCode yet. The SKU master "
                            "assigned a synthetic \"SYN-...\" code so they can still be "
                            "tracked; they cannot join sales/price data until a real "
                            "code exists in those systems — Actual Sales show as 0."),
            "count": len(synthetic_set), "items": _mk_items(synthetic_set),
        },
        {
            "key": "no_rd_price", "severity": "info",
            "label": "No distributor price",
            "description": ("Budgeted items with no usable price in DistributorPrice.xlsx "
                            "at or before the current month. Their value fields show as 0 "
                            "(Price_Source = NONE) until a price entry exists for that SKU."),
            "count": len(no_rd_price_set), "items": _mk_items(no_rd_price_set),
        },
    ]

    mapping_diagnostics = {
        "month": data_upto_label,
        "sources": {
            "sku_master":         len(master_set),
            "budget":             len(budget_set),
            "distributor_price":  len(price_set),
            "focus_sales":        len(focus_set),
            "all_sku_sales":      len(all_sku_set),
        },
        "categories": mapping_categories,
        "counts": {c["key"]: c["count"] for c in mapping_categories},
    }

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
    excluded_row_count = int((result["Is_In_Master"] == False).sum()) if "Is_In_Master" in result.columns else 0

    total_secondary_sales_qty   = _fsum(result_scoped, "Current_Month_Secondary_Sales")
    total_secondary_sales_value = _fsum(result_scoped, "Current_Month_Secondary_Sales_Value")
    total_current_forecast_qty   = _fsum(result_scoped, "Current_Forecast_Qty")
    total_current_forecast_value = _fsum(result_scoped, "Current_Forecast_Value")

    # Budget totals come from ALL budgeted items — budget_table is inherently
    # master-scoped already (every row originates from sku_master_full.csv
    # via load_budget_lookup), so no extra filter needed here.
    total_budget_qty        = _fsum(budget_table, "Budget_Qty")
    total_annual_budget_qty = _fsum(budget_table, "Annual_Budget_Qty")
    total_fytd_secondary_qty = _fsum(budget_table, "FYTD_Secondary_Sales_Qty")

    total_budget_value          = _fsum(budget_table, "Budget_Value")
    total_annual_budget_value   = _fsum(budget_table, "Annual_Budget_Value")
    total_fytd_secondary_value  = _fsum(budget_table, "FYTD_Secondary_Sales_Value")

    agency_budget_records = []
    if not budget_table.empty:
        agency_budget_records = (
            budget_table
            .groupby("Agency")[[
                "Budget_Qty", "Annual_Budget_Qty",
                "FYTD_Secondary_Sales_Qty",
                "Budget_Value", "Annual_Budget_Value",
                "FYTD_Secondary_Sales_Value",
            ]]
            .sum().round(2).reset_index()
            .to_dict(orient="records")
        )

    # Price coverage — how many master SKUs got a real distributor price
    # vs have none at all (useful debug/QA signal for the UI).
    price_source_counts = {}
    if "Price_Source" in result_scoped.columns and not result_scoped.empty:
        price_source_counts = result_scoped["Price_Source"].value_counts().to_dict()

    meta = {
        "data_available_upto":  data_upto_label,
        "current_month_label":  data_upto_label,
        "total_skus":           int(len(result)),
        "agencies":             sorted(result["Agency"].dropna().unique().tolist()),

        # Master-scope diagnostics
        "master_sku_count":            len(master_codes),
        "excluded_from_totals_count":  excluded_row_count,

        # SKU mapping / coverage audit — rendered by the UI as its own
        # section below the tables.
        "mapping_diagnostics": mapping_diagnostics,

        # ── Budget vs ACTUAL loss (per-SKU decomposition, master-scoped) ─────
        # Each SKU's gap is floored at 0 BEFORE summing, so an over-performing
        # SKU never nets against an under-performing one in the total.
        "total_raw_loss_qty":           _fsum(result_scoped, "Raw_Loss_Qty"),
        "total_other_loss_qty":         _fsum(result_scoped, "Other_Loss_Qty"),
        "total_stockout_loss_qty":      _fsum(result_scoped, "Stockout_Loss_Qty"),
        "total_current_month_loss_qty": _fsum(result_scoped, "Current_Month_Loss_Qty"),
        "total_raw_loss_value":         _fsum(result_scoped, "Raw_Loss_Value"),
        "total_other_loss_value":       _fsum(result_scoped, "Other_Loss_Value"),
        "total_stockout_loss_value":    _fsum(result_scoped, "Stockout_Loss_Value"),
        "stockout_sku_count":           _isum(result_scoped, "Stockout_Flag"),

        # Performance KPI strip — ACTUAL (secondary) sales is now the
        # headline (budget qty ≈ secondary sales target, business confirmed).
        "total_secondary_sales_qty":   total_secondary_sales_qty,
        "total_secondary_sales_value": total_secondary_sales_value,

        # Forecast (v8 restore)
        "total_current_forecast_qty":         total_current_forecast_qty,
        "total_current_forecast_value":       total_current_forecast_value,
        "total_forecast_vs_actual_loss_qty":  _fsum(result_scoped, "Forecast_Vs_Actual_Loss_Qty"),
        "total_forecast_vs_actual_loss_value":_fsum(result_scoped, "Forecast_Vs_Actual_Loss_Value"),

        # Budget (ALL budgeted items)
        "budget_item_count":        int(len(budget_table)),
        "unmapped_budget_item_count": (
            int(budget_table["Is_Unmapped"].sum())
            if "Is_Unmapped" in budget_table.columns and not budget_table.empty else 0
        ),
        "total_budget_qty":         total_budget_qty,
        "total_annual_budget_qty":  total_annual_budget_qty,
        "total_fytd_secondary_sales_qty": total_fytd_secondary_qty,

        "total_budget_value":               total_budget_value,
        "total_annual_budget_value":        total_annual_budget_value,
        "total_fytd_secondary_sales_value": total_fytd_secondary_value,
        "budget_fy_months":         budget_meta.get("month_labels", []),
        "budget_current_month_found": budget_meta.get("current_month_found", False),
        "agency_budget":            agency_budget_records,

        # Price coverage diagnostics
        "price_source_counts":      price_source_counts,

        "fact_data_upto": (
            all_sales_df["Month_dt"].max().strftime("%b %Y")
            if not all_sales_df.empty else None
        ),

        # ── Forecast analysis table (our forecast vs external) ───────────────
        "forecast_item_count":            int(len(forecast_table)),
        "forecast_eval_month":            latest_month_dt.strftime("%b %Y"),
        "total_model_forecast_qty":       _fsum(forecast_table, "Model_Forecast_Qty"),
        "total_model_forecast_value":     _fsum(forecast_table, "Model_Forecast_Value"),
        "total_external_forecast_qty":    _fsum(forecast_table, "External_Forecast_Qty"),
        "total_external_forecast_value":  _fsum(forecast_table, "External_Forecast_Value"),
        # Portfolio-level accuracy = the mean of the per-SKU scores that
        # could actually be computed (SKUs with no actual to score against
        # are excluded rather than counted as 0, which would drag the
        # average down for a data-availability reason, not a model one).
        "avg_model_accuracy_%": (
            float(pd.to_numeric(forecast_table["Model_Accuracy_%"], errors="coerce").dropna().mean())
            if "Model_Accuracy_%" in forecast_table.columns
            and pd.to_numeric(forecast_table["Model_Accuracy_%"], errors="coerce").notna().any()
            else None
        ),
        "avg_external_accuracy_%": (
            float(pd.to_numeric(forecast_table["External_Accuracy_%"], errors="coerce").dropna().mean())
            if "External_Accuracy_%" in forecast_table.columns
            and pd.to_numeric(forecast_table["External_Accuracy_%"], errors="coerce").notna().any()
            else None
        ),
        "scored_model_sku_count": (
            int(pd.to_numeric(forecast_table["Model_Accuracy_%"], errors="coerce").notna().sum())
            if "Model_Accuracy_%" in forecast_table.columns else 0
        ),
        "scored_external_sku_count": (
            int(pd.to_numeric(forecast_table["External_Accuracy_%"], errors="coerce").notna().sum())
            if "External_Accuracy_%" in forecast_table.columns else 0
        ),

        # Top-strip loss gap KPI — Budget vs Actual (qty), per-SKU floored
        # sum so it exactly matches the Stockout Loss section total.
        # (The UI must use THIS, not aggregate budget − aggregate actual:
        # netting at the aggregate level lets over-performing SKUs cancel
        # out under-performing ones, which is why the two KPI cards used
        # to disagree.)
        "total_budget_vs_actual_loss_qty":   _fsum(result_scoped, "Raw_Loss_Qty"),
        "total_budget_vs_actual_loss_value": _fsum(result_scoped, "Raw_Loss_Value"),
    }

    return result, budget_result, forecast_result, trend_result, meta