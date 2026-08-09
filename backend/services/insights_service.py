# backend/services/insights_service.py
#
# v8 — Budget vs Actual(secondary) vs Forecast, all priced via
# DistributorPrice.xlsx (see engines/insights_engine.py header). Forecast
# is back (Current_Forecast_Qty/Value, Forecast_Vs_Actual_Loss_*) after
# having been removed in v6; build_agency_performance_table() still
# returns 3 values (df, budget_df, meta) — forecast is columns on the
# existing tables now, not a separate 4th comparison table.
#
# v9 — this is also where the EXTERNAL (business-supplied) forecast and
# our own past-forecast history get loaded from disk (Forecast.xlsx /
# forecast_horizon_history.csv — see load_external_forecast() and
# load_model_forecast_history() below). All file handling for this
# pipeline lives in this one service module; engines/insights_engine.py
# stays pure computation and receives these as already-loaded DataFrames.

import json
import os
import re

import numpy as np
import pandas as pd

from engines.insights_engine import build_agency_performance_table, PROCESSED_DATA_FILE

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "outputs")

AGENCY_PERFORMANCE_LATEST_PATH = os.path.join(
    OUTPUT_DIR, "agency_performance_latest.csv"
)
AGENCY_PERFORMANCE_META_PATH = os.path.join(
    OUTPUT_DIR, "agency_performance_meta.json"
)
BUDGET_ANALYSIS_LATEST_PATH = os.path.join(
    OUTPUT_DIR, "budget_analysis_latest.csv"
)
# v9 — forecast analysis is its own table now (our forecast vs the external
# business-supplied forecast, scored against budget + actual).
FORECAST_ANALYSIS_LATEST_PATH = os.path.join(
    OUTPUT_DIR, "forecast_analysis_latest.csv"
)
# v12 — per-SKU-per-month FYTD series behind the Agency Performance
# trend chart (Actual vs Budget). Served by its own /trend endpoint
# rather than bundled into /results, so the main payload stays small.
TREND_ANALYSIS_LATEST_PATH = os.path.join(
    OUTPUT_DIR, "trend_analysis_latest.csv"
)

# Raw (non-backend) data directory — the same one Forecast.xlsx and
# Inventory.xlsx live in. BASE_DIR is <project>/backend, so its parent is
# the project root and <project>/data is the raw data folder (matches
# services/risk_service.py's RAW_DATA_DIR).
RAW_DATA_DIR = os.path.join(os.path.dirname(BASE_DIR), "data")

# Inventory workbook — batch-level stock, sheets "DB" (distributor) and
# "WH" (warehouse/primary). Columns used here: Month, ItemCode, UnitQty,
# ItemExpiryDate.
#
# IMPORTANT (business rule): a row's Month is that month's OPENING stock,
# i.e. Inventory.xlsx Month=2026-06-01 IS the 2026-05 month-end stock,
# saved forward as June's opening position. So the stock available to
# serve June's demand is the Month=2026-06-01 rows — the same month we
# report on, no offset needed at read time.
INVENTORY_XLSX_PATH = os.path.join(RAW_DATA_DIR, "Inventory.xlsx")


# EXTERNAL (business-supplied) forecast workbook — ONE SHEET PER "SavedOn"
# MONTH, named "YYYY_MM" (e.g. "2026_06"). Each sheet:
#
#   PlantId | SavedOn | DataMeasure | ForecastDate | ProductId | RegionId |
#   Quantity | Note | ForecastedProductId | ForecastedRegionId |
#   SplitFactor | Remark | LastUpdate | LastUpdatedUser | NumUpdates
#
# The columns that matter:
#   ForecastDate -> the month being forecast (the row's target month)
#   ProductId    -> ItemCode
#   Quantity     -> the externally-forecast qty for that product/month
#
# Everything else (PlantId, RegionId, SplitFactor, audit columns) is
# passed over — Insights is national/agency-level, not plant/region-level,
# so rows are summed per ProductId x ForecastDate month.
EXTERNAL_FORECAST_FILE = (
    "/Users/dhanujiamanda/Documents/Projects/Agentic AI /Pipeline/"
    "Agentic-AI-for-Pharma-Stockout-Problem/data/Forecast.xlsx"
)

# Sheets are named for the month the forecast was SAVED in, e.g. "2026_06".
_EXTERNAL_FORECAST_SHEET_RE = re.compile(r"^\s*(\d{4})[_\-](\d{1,2})\s*$")

_EXTERNAL_FORECAST_OUT_COLS = ["ItemCode", "Forecast_Month_dt", "External_Forecast_Qty"]


# ─────────────────────────────────────────────────────────────
# Inventory.xlsx — month-opening batch stock (file I/O only)
#
# Returns the RAW DB/WH sheet rows, untouched apart from the month
# filter. Expiry-bucket classification and aggregation are computation
# and belong to engines/risk_engine.py, which already owns that logic
# for the Inventory/Risk page — Insights reuses those same functions
# rather than keeping a second, drifting copy.
# ─────────────────────────────────────────────────────────────
def load_inventory_sheets(month_dt=None):
    """
    Raw DB and WH sheets from Inventory.xlsx for ONE month's OPENING
    position, in the shape risk_engine expects.

    `month_dt` is the month being reported on. Because Inventory.xlsx
    stores each month-end snapshot forward as the NEXT month's opening
    stock, the rows for that same month (Month == month_dt) are exactly
    the stock that was on hand to serve that month — no offset here.
    This matches build_inventory_risk_snapshot()'s own convention.

    If the workbook has no rows for that month, EMPTY frames are returned
    (with the available months printed) rather than silently falling back
    to another month — SHP for June must be June's stock or nothing.

    Returns (db_df, wh_df) with their original columns preserved:
        DB: Month | ItemCode | UnitQty | ItemExpiryDate | ...
        WH: Month | ItemCode | ExpiryDate | Blocked | Insp | Trade Qty
    """
    empty = pd.DataFrame()

    if not os.path.exists(INVENTORY_XLSX_PATH):
        print(f"[INSIGHTS] Inventory workbook not found: {INVENTORY_XLSX_PATH} "
              f"— SHP will be blank.")
        return empty, empty

    out = {}
    for sheet in ("DB", "WH"):
        try:
            df = pd.read_excel(INVENTORY_XLSX_PATH, sheet_name=sheet)
        except Exception as e:
            print(f"[INSIGHTS] Could not read Inventory.xlsx sheet '{sheet}': {e}")
            out[sheet] = empty
            continue

        df.columns = df.columns.astype(str).str.strip()

        if month_dt is not None and "Month" in df.columns:
            target = pd.Timestamp(month_dt).to_period("M").to_timestamp()
            months = pd.to_datetime(df["Month"], errors="coerce") \
                       .dt.to_period("M").dt.to_timestamp()
            rows = df[months == target]
            if rows.empty:
                avail = sorted(months.dropna().unique())
                lbl = [pd.Timestamp(m).strftime("%b %Y") for m in avail]
                print(f"[INSIGHTS] Inventory.xlsx '{sheet}' has no opening stock "
                      f"for {target:%b %Y}. Months present: {lbl}")
            df = rows

        print(f"[INSIGHTS] Inventory.xlsx '{sheet}': {len(df)} batch rows "
              f"for the reporting month.")
        out[sheet] = df.reset_index(drop=True)

    return out.get("DB", empty), out.get("WH", empty)


# ─────────────────────────────────────────────────────────────
# External forecast (Forecast.xlsx) + our own past-forecast history
# (forecast_horizon_history.csv) — file I/O for the Forecast Analysis
# table. Pure computation on top of these lives in
# engines/insights_engine.py (build_forecast_analysis_table()).
# ─────────────────────────────────────────────────────────────
def _external_forecast_sheet_label(month_dt) -> str:
    """Timestamp -> the sheet-name convention used in Forecast.xlsx."""
    ts = pd.Timestamp(month_dt)
    return f"{ts.year:04d}_{ts.month:02d}"


def list_external_forecast_sheets() -> list:
    """
    Month-named sheets present in Forecast.xlsx, oldest first, as
    (sheet_name, Timestamp) pairs. Sheets that don't match the YYYY_MM
    convention are ignored rather than treated as an error — the workbook
    may carry notes/lookup tabs alongside the monthly data.
    """
    if not os.path.exists(EXTERNAL_FORECAST_FILE):
        return []
    try:
        xls = pd.ExcelFile(EXTERNAL_FORECAST_FILE)
    except Exception as e:
        print(f"[INSIGHTS] Could not open Forecast.xlsx: {e}")
        return []

    found = []
    for name in xls.sheet_names:
        m = _EXTERNAL_FORECAST_SHEET_RE.match(str(name))
        if m:
            year, month = int(m.group(1)), int(m.group(2))
            if 1 <= month <= 12:
                found.append((name, pd.Timestamp(year, month, 1)))
    return sorted(found, key=lambda t: t[1])


def load_external_forecast(target_month_dt=None) -> pd.DataFrame:
    """
    External forecast quantities for ONE month.

    `target_month_dt` is the month we want a forecast FOR (normally the
    latest closed month, so it can be scored against that month's actuals).
    Sheet selection:
      1. The sheet named for that month ("YYYY_MM") if it exists.
      2. Otherwise the newest month-named sheet at or before it.
      3. Otherwise the newest month-named sheet in the workbook.
    Then, within the chosen sheet, only rows whose ForecastDate falls in
    `target_month_dt` are kept (a sheet can hold several target months).
    If none match that month, the sheet's own rows are returned as-is
    grouped by their ForecastDate month, so the caller can still see what
    the external source did supply.

    Passing target_month_dt=None returns EVERY month-named sheet combined.

    Returns: ItemCode | Forecast_Month_dt | External_Forecast_Qty
    """
    empty = pd.DataFrame(columns=_EXTERNAL_FORECAST_OUT_COLS)

    if not os.path.exists(EXTERNAL_FORECAST_FILE):
        print(f"[INSIGHTS] Forecast file not found: {EXTERNAL_FORECAST_FILE}")
        return empty

    sheets = list_external_forecast_sheets()
    if not sheets:
        print("[INSIGHTS] Forecast.xlsx has no month-named (YYYY_MM) sheets.")
        return empty

    if target_month_dt is None:
        chosen = sheets
    else:
        target = pd.Timestamp(target_month_dt).to_period("M").to_timestamp()
        want = _external_forecast_sheet_label(target)
        exact = [s for s in sheets if s[0].strip() == want]
        if exact:
            chosen = exact
        else:
            at_or_before = [s for s in sheets if s[1] <= target]
            chosen = [at_or_before[-1]] if at_or_before else [sheets[-1]]
            print(f"[INSIGHTS] No sheet '{want}' in Forecast.xlsx — "
                  f"using '{chosen[0][0]}' instead.")

    frames = []
    for sheet_name, _ in chosen:
        try:
            df = pd.read_excel(EXTERNAL_FORECAST_FILE, sheet_name=sheet_name)
        except Exception as e:
            print(f"[INSIGHTS] Could not read Forecast.xlsx sheet '{sheet_name}': {e}")
            continue

        df.columns = df.columns.astype(str).str.strip()

        required = ["ForecastDate", "ProductId", "Quantity"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            print(f"[INSIGHTS] Forecast.xlsx sheet '{sheet_name}' missing columns: "
                  f"{missing}. Found: {list(df.columns)}")
            continue

        out = pd.DataFrame({
            "ItemCode": (
                pd.to_numeric(df["ProductId"], errors="coerce")
                .astype("Int64").astype(str).replace("<NA>", np.nan)
            ),
            "Forecast_Month_dt": pd.to_datetime(df["ForecastDate"], errors="coerce"),
            "External_Forecast_Qty": (
                pd.to_numeric(df["Quantity"], errors="coerce").fillna(0).clip(lower=0)
            ),
        })
        out = out.dropna(subset=["ItemCode", "Forecast_Month_dt"])
        # Floor to month start so it joins cleanly against every other
        # month-keyed frame in the pipeline.
        out["Forecast_Month_dt"] = (
            out["Forecast_Month_dt"].dt.to_period("M").dt.to_timestamp()
        )
        frames.append(out)

    if not frames:
        return empty

    combined = pd.concat(frames, ignore_index=True)

    if target_month_dt is not None:
        target = pd.Timestamp(target_month_dt).to_period("M").to_timestamp()
        month_rows = combined[combined["Forecast_Month_dt"] == target]
        if not month_rows.empty:
            combined = month_rows
        else:
            avail = sorted(combined["Forecast_Month_dt"].dropna().unique())
            avail_lbl = [pd.Timestamp(m).strftime("%b %Y") for m in avail]
            print(f"[INSIGHTS] No Forecast.xlsx rows with ForecastDate in "
                  f"{target:%b %Y}. Months present: {avail_lbl}")

    result = (
        combined.groupby(["ItemCode", "Forecast_Month_dt"], as_index=False)
        ["External_Forecast_Qty"].sum()
    )

    if not result.empty:
        months = sorted(result["Forecast_Month_dt"].unique())
        lbl = ", ".join(pd.Timestamp(m).strftime("%b %Y") for m in months)
        print(f"[INSIGHTS] External forecast loaded: "
              f"{result['ItemCode'].nunique()} SKUs for {lbl}.")
    return result[_EXTERNAL_FORECAST_OUT_COLS]


def load_model_forecast_history(target_month_dt=None) -> pd.DataFrame:
    """
    OUR model's own past M+1 forecast for a given month, from
    forecast_horizon_history.csv (written by horizon_service). This is what
    makes a real accuracy score possible: the live forecast file only holds
    the NEXT month's numbers, which have no actuals to score against yet,
    whereas this history holds what the model predicted FOR a month that
    has since closed.

    Lazy import of horizon_service so a missing/failed horizon pipeline
    degrades to "no accuracy available" instead of breaking Insights.

    Returns: ItemCode | Forecast_Month_dt | Model_Forecast_Qty
    """
    cols = ["ItemCode", "Forecast_Month_dt", "Model_Forecast_Qty"]
    empty = pd.DataFrame(columns=cols)

    try:
        from services.horizon_service import load_forecast_horizon_history
        hist = load_forecast_horizon_history()
    except Exception as e:
        print(f"[INSIGHTS] Model forecast history unavailable: {e}")
        return empty

    if hist is None or hist.empty:
        return empty

    hist = hist.copy()
    hist.columns = hist.columns.astype(str).str.strip()

    need = ["ItemCode", "Forecast_Month", "Forecast_Qty"]
    if any(c not in hist.columns for c in need):
        print(f"[INSIGHTS] forecast_horizon_history.csv missing one of {need}. "
              f"Found: {list(hist.columns)}")
        return empty

    # M+1 only — that's the horizon the live forecast is measured on.
    if "Horizon" in hist.columns:
        hist = hist[hist["Horizon"].astype(str).str.strip() == "M+1"]

    hist["ItemCode"] = (
        pd.to_numeric(hist["ItemCode"], errors="coerce")
        .astype("Int64").astype(str).replace("<NA>", np.nan)
    )
    hist = hist.dropna(subset=["ItemCode"])

    hist["Forecast_Month_dt"] = pd.to_datetime(
        hist["Forecast_Month"].astype(str) + "-01", errors="coerce"
    )
    hist = hist.dropna(subset=["Forecast_Month_dt"])
    hist["Forecast_Qty"] = pd.to_numeric(hist["Forecast_Qty"], errors="coerce").fillna(0).clip(lower=0)

    if target_month_dt is not None:
        target = pd.Timestamp(target_month_dt).to_period("M").to_timestamp()
        hist = hist[hist["Forecast_Month_dt"] == target]

    if hist.empty:
        return empty

    # Latest run wins when a month was forecast by more than one run.
    sort_col = "Run_Date" if "Run_Date" in hist.columns else (
        "Run_ID" if "Run_ID" in hist.columns else None
    )
    if sort_col:
        hist = hist.sort_values(sort_col)
    hist = hist.drop_duplicates(subset=["ItemCode", "Forecast_Month_dt"], keep="last")

    out = hist[["ItemCode", "Forecast_Month_dt", "Forecast_Qty"]].rename(
        columns={"Forecast_Qty": "Model_Forecast_Qty"}
    )
    print(f"[INSIGHTS] Model forecast history: {len(out)} SKU-months "
          f"(used for accuracy scoring).")
    return out.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# Trend series (Agency Performance chart)
# ─────────────────────────────────────────────────────────────
def get_trend_series(agency: str = None, item_code: str = None) -> dict:
    """
    FY-to-date monthly Actual vs Budget series for the trend chart.

    Filters (both optional, and independent):
      - `agency`    : one agency, else all agencies combined
      - `item_code` : one SKU, else every SKU in scope summed together

    Aggregation happens here rather than in the engine so a single stored
    table can serve the "total" view and any per-SKU drill-down without
    re-running the pipeline.

    Returns {"ok", "months": [...], "series": {...}, "scope": {...}}
    """
    if not os.path.exists(TREND_ANALYSIS_LATEST_PATH):
        return {
            "ok": False,
            "months": [], "series": {},
            "error": "No trend data found. Run the engine first.",
        }

    try:
        tdf = pd.read_csv(TREND_ANALYSIS_LATEST_PATH)
        if tdf.empty:
            return {"ok": True, "months": [], "series": {}, "scope": {}}

        if "ItemCode" in tdf.columns:
            tdf["ItemCode"] = (
                pd.to_numeric(tdf["ItemCode"], errors="coerce")
                .astype("Int64").astype(str).replace("<NA>", None)
            )

        if agency:
            tdf = tdf[tdf["Agency"].astype(str).str.lower() == str(agency).lower()]
        if item_code:
            tdf = tdf[tdf["ItemCode"].astype(str) == str(item_code)]

        if tdf.empty:
            return {"ok": True, "months": [], "series": {},
                    "scope": {"agency": agency, "item_code": item_code}}

        for c in ["Budget_Qty", "Actual_Qty", "Budget_Value", "Actual_Value"]:
            if c not in tdf.columns:
                tdf[c] = 0.0
            tdf[c] = pd.to_numeric(tdf[c], errors="coerce").fillna(0)

        grouped = (
            tdf.groupby("Month", as_index=False)[
                ["Budget_Qty", "Actual_Qty", "Budget_Value", "Actual_Value"]
            ].sum().sort_values("Month")
        )

        return {
            "ok": True,
            "months": grouped["Month"].astype(str).tolist(),
            "series": {
                "budget_qty":   [round(float(v), 2) for v in grouped["Budget_Qty"]],
                "actual_qty":   [round(float(v), 2) for v in grouped["Actual_Qty"]],
                "budget_value": [round(float(v), 2) for v in grouped["Budget_Value"]],
                "actual_value": [round(float(v), 2) for v in grouped["Actual_Value"]],
            },
            "scope": {
                "agency":    agency or "All Agencies",
                "item_code": item_code,
                "sku_count": int(tdf["ItemCode"].nunique()),
            },
        }

    except Exception as e:
        print(f"[INSIGHTS] Warning loading trend series: {e}")
        return {"ok": False, "months": [], "series": {}, "error": str(e)}


# ─────────────────────────────────────────────────────────────
# Run engine
# ─────────────────────────────────────────────────────────────
def run_agency_performance_engine() -> dict:
    # The only hard requirement is the preprocessed actuals file
    # (Secondary_Sales_Qty) — budget, price and forecast are all read
    # independently inside build_agency_performance_table() and degrade
    # gracefully (0s / NONE) if any one of them is missing.
    if not os.path.exists(PROCESSED_DATA_FILE):
        return {
            "ok": False,
            "error": "processed_data_actual.csv not found. Run preprocessing first.",
        }

    try:
        df, budget_df, forecast_df, trend_df, meta = build_agency_performance_table()

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        df.to_csv(AGENCY_PERFORMANCE_LATEST_PATH, index=False)
        budget_df.to_csv(BUDGET_ANALYSIS_LATEST_PATH, index=False)
        forecast_df.to_csv(FORECAST_ANALYSIS_LATEST_PATH, index=False)
        trend_df.to_csv(TREND_ANALYSIS_LATEST_PATH, index=False)

        with open(AGENCY_PERFORMANCE_META_PATH, "w") as f:
            json.dump(meta, f, indent=2, default=str)

        return {
            "ok": True,
            "rows": int(len(df)),
            "budget_rows": int(len(budget_df)),
            "forecast_rows": int(len(forecast_df)),
            "trend_rows": int(len(trend_df)),
            "path": AGENCY_PERFORMANCE_LATEST_PATH,
            "data_available_upto": meta.get("data_available_upto"),
            "total_skus": meta.get("total_skus"),

            # Budget (ALL budgeted items)
            "budget_item_count": meta.get("budget_item_count"),
            "total_budget_qty": meta.get("total_budget_qty"),
            "total_annual_budget_qty": meta.get("total_annual_budget_qty"),
            "total_fytd_secondary_sales_qty": meta.get("total_fytd_secondary_sales_qty"),
            "total_budget_value": meta.get("total_budget_value"),
            "total_annual_budget_value": meta.get("total_annual_budget_value"),
            "total_fytd_secondary_sales_value": meta.get("total_fytd_secondary_sales_value"),

            # Actual (secondary) sales — headline vs budget (v8)
            "total_secondary_sales_qty": meta.get("total_secondary_sales_qty"),
            "total_secondary_sales_value": meta.get("total_secondary_sales_value"),

            # Forecast (v8 restore)
            "total_current_forecast_qty": meta.get("total_current_forecast_qty"),
            "total_current_forecast_value": meta.get("total_current_forecast_value"),
            "total_forecast_vs_actual_loss_qty": meta.get("total_forecast_vs_actual_loss_qty"),
            "total_forecast_vs_actual_loss_value": meta.get("total_forecast_vs_actual_loss_value"),

            # Forecast analysis table (v9 — ours vs external)
            "forecast_item_count": meta.get("forecast_item_count"),
            "forecast_eval_month": meta.get("forecast_eval_month"),
            "total_model_forecast_qty": meta.get("total_model_forecast_qty"),
            "total_external_forecast_qty": meta.get("total_external_forecast_qty"),
            "avg_model_accuracy_%": meta.get("avg_model_accuracy_%"),
            "avg_external_accuracy_%": meta.get("avg_external_accuracy_%"),

            # Loss (Budget vs Actual/secondary sales)
            "total_raw_loss_qty": meta.get("total_raw_loss_qty"),
            "total_stockout_loss_qty": meta.get("total_stockout_loss_qty"),
            "total_other_loss_qty": meta.get("total_other_loss_qty"),

            "message": (
                f"Agency performance built for {len(df)} SKUs "
                f"({len(budget_df)} budgeted items, "
                f"{len(forecast_df)} forecast rows). "
                f"Data up to {meta.get('data_available_upto')}."
            ),
        }

    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"Unexpected error: {str(e)}"}


# ─────────────────────────────────────────────────────────────
# Get latest rows
# ─────────────────────────────────────────────────────────────
def _read_side_table(path: str, label: str) -> list:
    """
    Read one of the secondary tables (budget / forecast analysis) into
    JSON-safe records. Never raises — a missing or malformed side table
    degrades that one tab to empty rather than failing the whole response.
    """
    if not os.path.exists(path):
        return []
    try:
        sdf = pd.read_csv(path)
        # ItemCode may parse as float from CSV — normalise back to string
        if "ItemCode" in sdf.columns:
            sdf["ItemCode"] = (
                pd.to_numeric(sdf["ItemCode"], errors="coerce")
                .astype("Int64").astype(str).replace("<NA>", None)
            )
        sdf = sdf.replace([float("inf"), float("-inf")], None)
        sdf = sdf.astype(object).where(pd.notnull(sdf), None)
        return sdf.to_dict(orient="records")
    except Exception as e:
        print(f"[INSIGHTS] Warning loading {label} rows: {e}")
        return []


def get_agency_performance_rows() -> dict:
    if not os.path.exists(AGENCY_PERFORMANCE_LATEST_PATH):
        return {
            "ok": False,
            "rows": [],
            "budget_rows": [],
            "forecast_rows": [],
            "meta": None,
            "error": "No agency performance results found. Run the engine first.",
        }

    try:
        df = pd.read_csv(AGENCY_PERFORMANCE_LATEST_PATH)

        df = df.replace([float("inf"), float("-inf")], None)
        df = df.astype(object).where(pd.notnull(df), None)

        rows = df.to_dict(orient="records")
        meta = _load_meta(df)

        return {
            "ok": True,
            "rows": rows,
            "budget_rows":   _read_side_table(BUDGET_ANALYSIS_LATEST_PATH,   "budget analysis"),
            "forecast_rows": _read_side_table(FORECAST_ANALYSIS_LATEST_PATH, "forecast analysis"),
            "meta": meta,
        }

    except Exception as e:
        return {
            "ok": False,
            "rows": [],
            "budget_rows": [],
            "forecast_rows": [],
            "meta": None,
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────
# Meta loader
# ─────────────────────────────────────────────────────────────
def _load_meta(df: pd.DataFrame) -> dict:
    if os.path.exists(AGENCY_PERFORMANCE_META_PATH):
        try:
            with open(AGENCY_PERFORMANCE_META_PATH) as f:
                return json.load(f)
        except Exception:
            pass

    meta = {
        "data_available_upto": None,
        "total_skus": int(len(df)),
        "agencies": [],
    }

    if "Data_Available_Upto" in df.columns:
        vals = df["Data_Available_Upto"].dropna().unique()
        meta["data_available_upto"] = str(vals[0]) if len(vals) else None

    if "Agency" in df.columns:
        meta["agencies"] = sorted(df["Agency"].dropna().unique().tolist())

    # Loss metrics (Budget vs Actual/secondary sales)
    if "Raw_Loss_Qty" in df.columns:
        meta["total_raw_loss_qty"] = float(
            pd.to_numeric(df["Raw_Loss_Qty"], errors="coerce").fillna(0).sum()
        )

    if "Other_Loss_Qty" in df.columns:
        meta["total_other_loss_qty"] = float(
            pd.to_numeric(df["Other_Loss_Qty"], errors="coerce").fillna(0).sum()
        )

    if "Stockout_Loss_Qty" in df.columns:
        meta["total_stockout_loss_qty"] = float(
            pd.to_numeric(df["Stockout_Loss_Qty"], errors="coerce").fillna(0).sum()
        )

    if "Current_Month_Loss_Qty" in df.columns:
        meta["total_current_month_loss_qty"] = float(
            pd.to_numeric(df["Current_Month_Loss_Qty"], errors="coerce").fillna(0).sum()
        )

    # Forecast (v8 restore)
    if "Current_Forecast_Qty" in df.columns:
        meta["total_current_forecast_qty"] = float(
            pd.to_numeric(df["Current_Forecast_Qty"], errors="coerce").fillna(0).sum()
        )
    if "Current_Forecast_Value" in df.columns:
        meta["total_current_forecast_value"] = float(
            pd.to_numeric(df["Current_Forecast_Value"], errors="coerce").fillna(0).sum()
        )
    if "Forecast_Vs_Actual_Loss_Qty" in df.columns:
        meta["total_forecast_vs_actual_loss_qty"] = float(
            pd.to_numeric(df["Forecast_Vs_Actual_Loss_Qty"], errors="coerce").fillna(0).sum()
        )
    if "Forecast_Vs_Actual_Loss_Value" in df.columns:
        meta["total_forecast_vs_actual_loss_value"] = float(
            pd.to_numeric(df["Forecast_Vs_Actual_Loss_Value"], errors="coerce").fillna(0).sum()
        )

    if "Stockout_Flag" in df.columns:
        meta["stockout_sku_count"] = int(
            pd.to_numeric(df["Stockout_Flag"], errors="coerce").fillna(0).sum()
        )

    if "Current_Month_Label" in df.columns:
        vals = df["Current_Month_Label"].dropna().unique()
        meta["current_month_label"] = (
            str(vals[0]) if len(vals) else meta.get("data_available_upto")
        )

    # Budget metrics — fallback from the budget analysis CSV (ALL items)
    if os.path.exists(BUDGET_ANALYSIS_LATEST_PATH):
        try:
            bdf = pd.read_csv(BUDGET_ANALYSIS_LATEST_PATH)
            meta["budget_item_count"] = int(len(bdf))
            for src, key in [
                ("Budget_Qty",                  "total_budget_qty"),
                ("Annual_Budget_Qty",           "total_annual_budget_qty"),
                ("FYTD_Secondary_Sales_Qty",    "total_fytd_secondary_sales_qty"),
                ("Budget_Value",                "total_budget_value"),
                ("Annual_Budget_Value",         "total_annual_budget_value"),
                ("FYTD_Secondary_Sales_Value",  "total_fytd_secondary_sales_value"),
            ]:
                if src in bdf.columns:
                    meta[key] = float(
                        pd.to_numeric(bdf[src], errors="coerce").fillna(0).sum()
                    )
        except Exception:
            pass

    # Forecast-analysis metrics — fallback from the forecast analysis CSV
    if os.path.exists(FORECAST_ANALYSIS_LATEST_PATH):
        try:
            fdf = pd.read_csv(FORECAST_ANALYSIS_LATEST_PATH)
            meta["forecast_item_count"] = int(len(fdf))
            for src, key in [
                ("Model_Forecast_Qty",      "total_model_forecast_qty"),
                ("Model_Forecast_Value",    "total_model_forecast_value"),
                ("External_Forecast_Qty",   "total_external_forecast_qty"),
                ("External_Forecast_Value", "total_external_forecast_value"),
            ]:
                if src in fdf.columns:
                    meta[key] = float(
                        pd.to_numeric(fdf[src], errors="coerce").fillna(0).sum()
                    )
            # Accuracy averages skip un-scorable SKUs (no actual to score
            # against) rather than counting them as 0.
            for src, key, cnt_key in [
                ("Model_Accuracy_%",    "avg_model_accuracy_%",    "scored_model_sku_count"),
                ("External_Accuracy_%", "avg_external_accuracy_%", "scored_external_sku_count"),
            ]:
                if src in fdf.columns:
                    vals = pd.to_numeric(fdf[src], errors="coerce").dropna()
                    meta[key] = float(vals.mean()) if len(vals) else None
                    meta[cnt_key] = int(len(vals))
            if "Forecast_Month" in fdf.columns:
                fm = fdf["Forecast_Month"].dropna().unique()
                if len(fm):
                    meta["forecast_eval_month"] = str(fm[0])
        except Exception:
            pass

    return meta