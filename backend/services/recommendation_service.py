"""
recommendation_service.py
=========================
AI Planner orchestrator: consumes the outputs of the upstream engines
(Forecast, Risk/Horizon, Budget) plus License.xlsx and business rules,
calls recommendation_engine (the decision layer), and shapes a complete
planning summary for the frontend.

Dependency direction: route -> THIS -> engines/services (leaf-ward only).

DESIGN RULE: nothing is recalculated here or in the engine — every number
comes from the same artifacts the other pages use:

    forecast_latest.csv          M+1 champion forecast     (Forecast page)
    forecast_trend_latest.csv    trend forecast, non-model budgeted SKUs
    risk_base_snapshot.csv       M+1 physical stock basis  (Risk/Horizon)
    fact_monthly_closed          actual sales history      (all pages)
    forecast_horizon_history.csv past M+1 forecasts        (accuracy)
    License.xlsx                 import/registration expiry
    BusinessRules.xlsx           MOQ / multiples (no data collected yet)

Budget.xlsx ("All Budget 26 27 FY") defines the PLANNER UNIVERSE only —
budget is NOT a risk factor. Item expiry is out of scope until batch-level
data exists (one SKU = many import batches with different expiry dates).

Column names in saved CSVs can drift between runs, so loaders resolve
columns from candidate lists (same pattern as load_budget_item_codes) and
fail with the actual columns listed.
"""

import os
from typing import Optional

import numpy as np
import pandas as pd

from engines.recommendation_engine import (
    build_recommendations,
    COL_ITEM, COL_MONTH, COL_FORECAST, COL_ACTUAL,
    COL_TRADE_STOCK, COL_NORISK_STOCK, COL_CLASS,
    COL_IMPORT_EXPIRY, COL_REG_EXPIRY,
)

from services.forecast_service import (
    RAW_DATA_DIR,
    load_forecast_latest,
    load_trend_forecast_latest,
    load_fact_history_all_skus,
    load_budget_item_codes,
)
from services.risk_service import (
    load_risk_base_snapshot,
    _normalize_itemcode,
)
from services.horizon_service import load_forecast_horizon_history

# ============================================================
# PATHS — licence + business rules live with the other masters
# ============================================================
LICENSE_XLSX_PATH = os.path.join(RAW_DATA_DIR, "License.xlsx")
LICENSE_SHEET = "License"
RULES_XLSX_PATH = os.path.join(RAW_DATA_DIR, "Master Data", "BusinessRules.xlsx")
RULES_SHEET = "Rules"

# Candidate column names (saved artifacts differ between engines/runs)
FORECAST_QTY_CANDIDATES = [
    "Forecast_Qty", "Forecast", "Forecast_Quantity", "Predicted_Qty",
    "Prediction", "Champion_Forecast_Qty", "Final_Forecast_Qty", "yhat",
]
# risk_base_snapshot.csv buckets stock by expiry risk:
#   *_Trade_Qty = *_NoRisk_Qty + *_ShortExp_Qty   (excludes Expired only;
#   WH Inspection/Blocked stock sits outside trade entirely).
# COVER USES NO-RISK STOCK ONLY (WH + DB NoRisk): short-expiry stock may
# lapse before it sells, so it is not dependable cover. Trade is still
# loaded for display, matching the Inventory page cards.
WH_CANDIDATES = ["Primary_Trade_Qty", "WH_Stock", "WH_Qty",
                 "Available_Primary_Qty", "Warehouse_Qty", "Primary_Qty", "WH"]
DB_CANDIDATES = ["Distributor_Trade_Qty", "DB_Stock", "DB_Qty",
                 "Distributor_Qty", "DB"]
WH_NORISK_CANDIDATES = ["Primary_NoRisk_Qty", "WH_NoRisk_Qty", "WH_NoRisk"]
DB_NORISK_CANDIDATES = ["Distributor_NoRisk_Qty", "DB_NoRisk_Qty", "DB_NoRisk"]
TOTAL_STOCK_CANDIDATES = ["TradeStock", "Trade_Stock", "Trade_Stock_Qty",
                          "Total_Stock", "Total_Qty", "Physical_Stock",
                          "Opening_Stock", "Closing_Stock"]
ABC_CANDIDATES = ["ABC_Class", "Classification", "Class", "ABC"]
AGENCY_CANDIDATES = ["AgencyName", "Agency"]
HORIZON_IDX_CANDIDATES = ["Horizon", "Months_Ahead", "Horizon_Index",
                          "Horizon_Month_Index", "M_Plus"]
MONTH_LABEL_CANDIDATES = ["Forecast_Month", "Target_Month", "Month"]


def _pick(df: pd.DataFrame, candidates) -> Optional[str]:
    return next((c for c in candidates if c in df.columns), None)


def _parse_month_label(s: pd.Series) -> pd.Series:
    """'2026-02' / 'Feb-26' / datetime-ish -> month-start Timestamp."""
    out = pd.to_datetime(s.astype(str), format="%Y-%m", errors="coerce")
    for fmt in ("%b-%y", "%b-%Y"):
        mask = out.isna()
        if mask.any():
            out[mask] = pd.to_datetime(s.astype(str)[mask], format=fmt, errors="coerce")
    mask = out.isna()
    if mask.any():
        out[mask] = pd.to_datetime(s.astype(str)[mask], errors="coerce")
    return out.dt.to_period("M").dt.to_timestamp()


# ============================================================
# LOADERS
# ============================================================

def _load_forecast() -> dict:
    """Forecast Engine outputs.

    Returns {"history": df, "next": df, "forecast_month": str|None}
        next    : ItemCode, Forecast (M+1). forecast_latest.csv (champion
                  model) + forecast_trend_latest.csv for budgeted SKUs the
                  model doesn't cover (model wins on overlap).
        history : ItemCode, Month, Actual, Forecast — actuals from
                  fact_monthly_closed; past M+1 forecasts joined from
                  forecast_horizon_history.csv where recoverable (drives
                  the forecast_trust factor; NaN Forecast -> factor
                  degrades gracefully to unavailable).
    """
    nxt_raw = load_forecast_latest()
    nxt_raw[COL_ITEM] = _normalize_itemcode(nxt_raw[COL_ITEM])
    qty_col = _pick(nxt_raw, FORECAST_QTY_CANDIDATES)
    if qty_col is None:
        raise ValueError(
            f"forecast_latest.csv: no forecast qty column among "
            f"{FORECAST_QTY_CANDIDATES}. Found: {list(nxt_raw.columns)}"
        )
    forecast_month = None
    if "Forecast_Month" in nxt_raw.columns:
        vals = nxt_raw["Forecast_Month"].dropna().astype(str).unique()
        forecast_month = vals[0] if len(vals) else None

    nxt = nxt_raw[[COL_ITEM, qty_col]].rename(columns={qty_col: COL_FORECAST})
    nxt[COL_FORECAST] = pd.to_numeric(nxt[COL_FORECAST], errors="coerce").fillna(0.0)

    # Trend forecast for budgeted SKUs without a model forecast
    try:
        trend = load_trend_forecast_latest()
        if trend is not None and not trend.empty and COL_ITEM in trend.columns:
            trend = trend.copy()
            trend[COL_ITEM] = _normalize_itemcode(trend[COL_ITEM])
            tq = _pick(trend, FORECAST_QTY_CANDIDATES + ["Trend_Forecast_Qty"])
            if tq:
                t = trend[[COL_ITEM, tq]].rename(columns={tq: COL_FORECAST})
                t[COL_FORECAST] = pd.to_numeric(t[COL_FORECAST], errors="coerce").fillna(0.0)
                t = t[~t[COL_ITEM].isin(set(nxt[COL_ITEM]))]
                nxt = pd.concat([nxt, t], ignore_index=True)
    except Exception as e:
        print(f"[RECO] trend forecast skipped: {e}")

    nxt = nxt.groupby(COL_ITEM, as_index=False)[COL_FORECAST].sum()

    # ---- history: actuals + past M+1 forecasts --------------------------
    acts = load_fact_history_all_skus()
    acts[COL_MONTH] = pd.to_datetime(dict(
        year=acts["Year"].astype(int), month=acts["Month_Number"].astype(int), day=1
    ))
    history = acts.rename(columns={"Secondary_Sales_Qty": COL_ACTUAL})[
        [COL_ITEM, COL_MONTH, COL_ACTUAL]
    ].copy()
    history[COL_FORECAST] = np.nan

    try:
        fh = load_forecast_horizon_history()
        fh = fh.copy()
        fh[COL_ITEM] = _normalize_itemcode(fh[COL_ITEM])
        hz_col = _pick(fh, HORIZON_IDX_CANDIDATES)
        mon_col = _pick(fh, MONTH_LABEL_CANDIDATES)
        fq_col = _pick(fh, FORECAST_QTY_CANDIDATES)
        if mon_col and fq_col:
            if hz_col is not None:
                hz = fh[hz_col].astype(str).str.replace(" ", "", regex=False)
                fh = fh[hz.isin(["1", "M+1", "M1", "1.0"])]
            fh["__month__"] = _parse_month_label(fh[mon_col])
            fh["__fcst__"] = pd.to_numeric(fh[fq_col], errors="coerce")
            fh = fh.dropna(subset=["__month__", "__fcst__"])
            # keep the LAST forecast made for each SKU-month
            if "Run_ID" in fh.columns:
                fh = fh.sort_values("Run_ID")
            fh = fh.drop_duplicates(subset=[COL_ITEM, "__month__"], keep="last")
            history = history.merge(
                fh[[COL_ITEM, "__month__", "__fcst__"]],
                left_on=[COL_ITEM, COL_MONTH], right_on=[COL_ITEM, "__month__"],
                how="left",
            )
            history[COL_FORECAST] = history["__fcst__"]
            history = history.drop(columns=["__month__", "__fcst__"])
    except FileNotFoundError:
        print("[RECO] forecast_horizon_history.csv not found — "
              "forecast_trust factor will report unavailable until history accrues")
    except Exception as e:
        print(f"[RECO] forecast history join skipped: {e}")

    return {"history": history, "next": nxt, "forecast_month": forecast_month}


def _load_inventory() -> pd.DataFrame:
    """Current stock position — SAME basis as Risk/Horizon pages
    (risk_base_snapshot.csv; built on demand if absent).

    Returns: ItemCode, TradeStock, NoRiskStock
             [, WH_Stock, DB_Stock, WH_NoRisk, DB_NoRisk,
                ABC_Class, AgencyName]
    """
    try:
        snap = load_risk_base_snapshot()
    except FileNotFoundError:
        from engines.risk_orchestrator import build_inventory_snapshot
        build_inventory_snapshot()
        snap = load_risk_base_snapshot()

    snap = snap.copy()
    snap[COL_ITEM] = _normalize_itemcode(snap[COL_ITEM])

    wh_col = _pick(snap, WH_CANDIDATES)
    db_col = _pick(snap, DB_CANDIDATES)
    wh_nr_col = _pick(snap, WH_NORISK_CANDIDATES)
    db_nr_col = _pick(snap, DB_NORISK_CANDIDATES)
    tot_col = _pick(snap, TOTAL_STOCK_CANDIDATES)
    if tot_col is None and wh_col is None and db_col is None:
        raise ValueError(
            f"risk_base_snapshot.csv: no stock column among "
            f"{TOTAL_STOCK_CANDIDATES + WH_CANDIDATES + DB_CANDIDATES}. "
            f"Found: {list(snap.columns)}"
        )

    for c in (wh_col, db_col, wh_nr_col, db_nr_col, tot_col):
        if c:
            snap[c] = pd.to_numeric(snap[c], errors="coerce").fillna(0.0)

    agg = {}
    if wh_col:
        agg["WH_Stock"] = (wh_col, "sum")
    if db_col:
        agg["DB_Stock"] = (db_col, "sum")
    if wh_nr_col:
        agg["WH_NoRisk"] = (wh_nr_col, "sum")
    if db_nr_col:
        agg["DB_NoRisk"] = (db_nr_col, "sum")
    if tot_col:
        agg[COL_TRADE_STOCK] = (tot_col, "sum")
    abc_col = _pick(snap, ABC_CANDIDATES)
    if abc_col:
        agg[COL_CLASS] = (abc_col, "first")
    agency_col = _pick(snap, AGENCY_CANDIDATES)
    if agency_col:
        agg["AgencyName"] = (agency_col, "first")

    out = snap.groupby(COL_ITEM, as_index=False).agg(**agg)
    if COL_TRADE_STOCK not in out.columns:
        out[COL_TRADE_STOCK] = out.get("WH_Stock", 0.0) + out.get("DB_Stock", 0.0)

    # NO-RISK stock basis for cover; fall back to trade if NoRisk missing
    if "WH_NoRisk" in out.columns or "DB_NoRisk" in out.columns:
        out[COL_NORISK_STOCK] = (
            out.get("WH_NoRisk", 0.0) + out.get("DB_NoRisk", 0.0))
    else:
        print("[RECO] WARNING: no NoRisk columns in snapshot — "
              "cover falls back to trade stock")
        out[COL_NORISK_STOCK] = out[COL_TRADE_STOCK]
    return out


def _load_license() -> Optional[pd.DataFrame]:
    """License.xlsx (Master Data), sheet 'License'.
    Expected: ItemCode, Import_License_Expiry, Registration_Expiry.
    Status columns are ignored — status derives from dates so it can
    never disagree with them. Missing file -> None (factor unavailable).

    License.xlsx may cover ALL SKUs; the planner only cares about
    BUDGETED SKUs, so rows are mapped against the 'All Budget 26 27 FY'
    item list and everything else is dropped. This keeps the licence
    factor, gating actions and the summary expiry counts consistent
    with the budgeted universe (a non-budgeted SKU's expiring licence
    must not raise a STOP_PROCUREMENT or inflate the constraint card).
    """
    if not os.path.exists(LICENSE_XLSX_PATH):
        print(f"[RECO] License.xlsx not found at {LICENSE_XLSX_PATH} — "
              f"licence factor unavailable")
        return None
    df = pd.read_excel(LICENSE_XLSX_PATH, sheet_name=LICENSE_SHEET)
    df.columns = df.columns.astype(str).str.strip()

    # Actual sheet format:
    #   Id | Code | Name | AgencyId | Status | Agency | Plant
    #      | RegLicense | ImportLicense
    # -> map to the engine's canonical names
    code_col = _pick(df, ["Code", COL_ITEM, "PID"])
    imp_col = _pick(df, ["ImportLicense", COL_IMPORT_EXPIRY, "Import_License_Expire_Date"])
    reg_col = _pick(df, ["RegLicense", COL_REG_EXPIRY, "Registration_Expiry_Date"])
    if code_col is None:
        print(f"[RECO] License.xlsx: no item code column. Found: {list(df.columns)}")
        return None
    df = df.rename(columns={code_col: COL_ITEM})
    df[COL_ITEM] = _normalize_itemcode(df[COL_ITEM])
    df[COL_IMPORT_EXPIRY] = (
        pd.to_datetime(df[imp_col], errors="coerce") if imp_col else pd.NaT)
    df[COL_REG_EXPIRY] = (
        pd.to_datetime(df[reg_col], errors="coerce") if reg_col else pd.NaT)
    df = df[[COL_ITEM, COL_IMPORT_EXPIRY, COL_REG_EXPIRY]]

    # map to budgeted SKUs only (placeholder composite keys like
    # "New::agency::product" can never match a licence ItemCode — fine)
    try:
        budget_codes = set(load_budget_item_codes())
    except Exception as e:
        print(f"[RECO] budget item list unavailable ({e}) — "
              f"licence data NOT filtered to budgeted SKUs")
        budget_codes = set()
    if budget_codes:
        before = len(df)
        df = df[df[COL_ITEM].isin(budget_codes)].copy()
        print(f"[RECO] licence rows mapped to budgeted SKUs: "
              f"{len(df)}/{before} kept")

    # one row per SKU: if duplicated, keep the EARLIEST expiry per column
    # (the binding constraint is always the soonest-lapsing licence)
    if df.duplicated(COL_ITEM).any():
        df = df.groupby(COL_ITEM, as_index=False).agg({
            COL_IMPORT_EXPIRY: "min", COL_REG_EXPIRY: "min",
        })
    return df


def _load_business_rules() -> Optional[pd.DataFrame]:
    """BusinessRules.xlsx (Master Data), sheet 'Rules'.
    Expected: ItemCode, MOQ, OrderMultiple, MaxInventoryDays.
    Missing file -> None (suggested_qty falls back to raw gap_qty)."""
    if not os.path.exists(RULES_XLSX_PATH):
        return None
    df = pd.read_excel(RULES_XLSX_PATH, sheet_name=RULES_SHEET)
    df[COL_ITEM] = _normalize_itemcode(df[COL_ITEM])
    return df


# ============================================================
# PLANNER SUMMARY
# ============================================================

def _build_summary(history: pd.DataFrame, current: pd.DataFrame,
                   licence: Optional[pd.DataFrame], result: dict,
                   as_of: pd.Timestamp, forecast_month: Optional[str]) -> dict:
    total_forecast = float(current[COL_FORECAST].sum())

    growth_pct = None
    if history is not None and not history.empty:
        cutoff = history[COL_MONTH].max() - pd.DateOffset(months=2)
        trailing = history[history[COL_MONTH] >= cutoff]
        monthly_avg = trailing.groupby(COL_MONTH)[COL_ACTUAL].sum().mean()
        if monthly_avg and monthly_avg > 0:
            growth_pct = round((total_forecast / monthly_avg - 1) * 100, 1)

    wh_nr = float(current["WH_NoRisk"].sum()) if "WH_NoRisk" in current.columns else None
    db_nr = float(current["DB_NoRisk"].sum()) if "DB_NoRisk" in current.columns else None
    no_risk = (float(current[COL_NORISK_STOCK].sum())
               if COL_NORISK_STOCK in current.columns else None)

    covers = [r["cover_months"] for r in result["all_items"]
              if r["cover_months"] is not None]
    median_cover = round(float(np.median(covers)), 2) if covers else None
    inv_status = ("LOW" if median_cover is not None and median_cover < 1.0
                  else "WATCH" if median_cover is not None and median_cover < 1.5
                  else "OK")

    # Licence bands: current date vs expiry — expired | <1yr RISK |
    # 1-1.5yr ALERT | >=1.5yr safe (matches engine LICENCE_RISK/ALERT_DAYS).
    #
    # FIX: bands are counted against the FULL planner universe (current),
    # not just the rows present in License.xlsx. Two leak paths existed:
    #   1. budgeted SKUs with NO ROW in License.xlsx fell into no band;
    #   2. rows with a BLANK/unparseable date -> NaT -> days = NaN, which
    #      fails every band comparison and silently vanished.
    # Both now land in an explicit "no_data" bucket so every column sums
    # to the universe size. NO DATA is invisible risk, not zero risk —
    # surfacing the count is the point.
    lic_summary = {"available": licence is not None}
    if licence is not None and not licence.empty:
        n_universe = int(current[COL_ITEM].nunique())
        imp_days = (licence[COL_IMPORT_EXPIRY] - as_of).dt.days
        reg_days = (licence[COL_REG_EXPIRY] - as_of).dt.days

        def _bands(days):
            b = {
                "expired": int((days < 0).sum()),
                "risk_1y": int(((days >= 0) & (days < 365)).sum()),
                "alert_18m": int(((days >= 365) & (days < 548)).sum()),
                "safe": int((days >= 548).sum()),
            }
            # universe minus everything banded: covers SKUs absent from
            # License.xlsx AND NaT dates in one number. max(0,...) guards
            # the fallback where licence data wasn't budget-filtered.
            b["no_data"] = max(0, n_universe - sum(b.values()))
            return b

        lic_summary["import"] = _bands(imp_days)
        lic_summary["registration"] = _bands(reg_days)

    if forecast_month is None:
        forecast_month = (as_of + pd.DateOffset(months=1)).strftime("%Y-%m")

    return {
        # note: no "confidence" here — that value is FACTOR COVERAGE
        # (planner-wide), not forecast accuracy; it lives on its own card
        "forecast": {
            "month": forecast_month,
            "total_forecast_qty": round(total_forecast),
            "growth_pct": growth_pct,
        },
        "inventory": {
            # NO-RISK stock only (Trade minus short-expiry) — the cover basis
            "wh_no_risk": None if wh_nr is None else round(wh_nr),
            "db_no_risk": None if db_nr is None else round(db_nr),
            "no_risk_stock": None if no_risk is None else round(no_risk),
            "median_cover_months": median_cover,   # NO-RISK stock basis
            "status": inv_status,
        },
        "licences": lic_summary,
    }


# ============================================================
# PUBLIC API
# ============================================================

def get_recommendations(agency: Optional[str] = None,
                        min_priority: Optional[str] = None) -> dict:
    """Run the planner. Filters: agency, min_priority ('HIGH'|'MEDIUM')."""
    as_of = pd.Timestamp.today().normalize()

    forecast = _load_forecast()
    inventory = _load_inventory()
    licence = _load_license()
    rules = _load_business_rules()

    history = forecast["history"]
    nxt = forecast["next"]

    # current planner snapshot = stock position + M+1 forecast.
    # Outer-ish logic: keep every SKU that has stock OR a forecast.
    current = inventory.merge(nxt, on=COL_ITEM, how="outer")
    current[COL_TRADE_STOCK] = current[COL_TRADE_STOCK].fillna(0.0)
    current[COL_NORISK_STOCK] = current[COL_NORISK_STOCK].fillna(0.0)
    current[COL_FORECAST] = current[COL_FORECAST].fillna(0.0)

    # PLANNER UNIVERSE = the FULL budgeted item list ('All Budget 26 27 FY'),
    # exactly as the Insights page defines it — including unmapped/new
    # products carried under synthetic composite keys (label::agency::product).
    # LEFT JOIN from the budget list (not an intersection): a budgeted new
    # product with no inventory/forecast row must still appear in the
    # universe with stock 0 / forecast 0, not silently disappear.
    try:
        budget_codes = load_budget_item_codes()
    except Exception as e:
        print(f"[RECO] budget item list unavailable ({e}) — "
              f"planner NOT scoped to budgeted SKUs")
        budget_codes = []
    if budget_codes:
        before = len(current)
        universe = pd.DataFrame({COL_ITEM: pd.unique(pd.Series(budget_codes))})
        current = universe.merge(current, on=COL_ITEM, how="left")
        current[COL_TRADE_STOCK] = current[COL_TRADE_STOCK].fillna(0.0)
        current[COL_NORISK_STOCK] = current[COL_NORISK_STOCK].fillna(0.0)
        current[COL_FORECAST] = current[COL_FORECAST].fillna(0.0)
        history = history[history[COL_ITEM].isin(set(budget_codes))].copy()
        n_unmapped = int(current[COL_ITEM].str.contains("::").sum())
        print(f"[RECO] planner universe = {len(current)} budgeted SKUs "
              f"({n_unmapped} unmapped/new-product rows; inventory∪forecast "
              f"had {before})")

    if agency and "AgencyName" in current.columns:
        current = current[current["AgencyName"] == agency]

    items = current[[COL_ITEM] + ([COL_CLASS] if COL_CLASS in current.columns else [])]

    result = build_recommendations({
        "items": items,
        "current": current,
        "history": history,
        "licence": licence,
        "rules": rules,
        "as_of": as_of,
    })

    if min_priority:
        order = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
        floor = order.get(min_priority.upper(), 0)
        result["recommendations"] = [
            r for r in result["recommendations"]
            if order.get(r["priority"], 0) >= floor
        ]

    result["summary"] = _build_summary(
        history, current, licence, result, as_of, forecast["forecast_month"])

    result["run_meta"] = {
        "run_ts": pd.Timestamp.now().isoformat(),
        "agency": agency,
        "n_items_scored": len(result["all_items"]),
        "n_recommendations": len(result["recommendations"]),
        "factors_pending": [
            k for k, v in result["factor_coverage"]["availability"].items() if not v
        ],
    }

    recs = result["recommendations"]
    result["kpis"] = {
        "stop_procurement": sum(1 for r in recs if r["action"] == "STOP_PROCUREMENT"),
        "renew_licence": sum(1 for r in recs if r["action"] == "RENEW_IMPORT_LICENCE"),
        "critical": sum(1 for r in recs if r["action"] == "REORDER_URGENT"),
        "reorder_review": sum(1 for r in recs if r["action"] == "REORDER_REVIEW"),
        "monitor": sum(1 for r in recs if r["action"] == "MONITOR"),
        "confidence": result["factor_coverage"]["confidence"],
    }
    return result