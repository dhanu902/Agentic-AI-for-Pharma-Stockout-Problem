# backend/engines/master_forecast_engine.py --> 🧩 Pure merge logic: master SKU list + all forecast sources
#
# Responsibility: take already-loaded DataFrames (master SKU list, model
# forecast, trend baseline forecast, horizon forecast) and merge them into
# consolidated tables. NO file I/O here — reading/writing CSVs stays in
# services/forecast_service.py and services/sku_master_service.py, exactly
# the same split already used for horizon_service.py / horizon_forecast_engine.py.

import pandas as pd

UNIFIED_FORECAST_COLUMNS = [
    "Run_Date", "Forecast_Month", "ItemCode", "Forecast_Qty",
    "Segment", "Used_Model", "Fallback_Used", "Target_Mode",
    "Routing_Reason", "Forecast_Source",
]


# ============================================================
# STEP 1 — combine model (forecast_latest) + trend (forecast_trend_latest)
# ============================================================
def build_combined_forecast_table(model_df: pd.DataFrame, trend_df: pd.DataFrame) -> pd.DataFrame:
    """
    One row per budgeted SKU that has EITHER an AI model forecast or a trend
    baseline forecast. Model rows are tagged Forecast_Source = AI_MODEL,
    trend rows arrive already tagged TREND_BASELINE (see demand_forecast_engine
    .build_trend_forecast_table). This is the single, deduped M+1 forecast
    table downstream code should read instead of joining two files itself.
    """
    frames = []

    if model_df is not None and not model_df.empty:
        m = model_df.copy()
        if "Forecast_Source" not in m.columns:
            m["Forecast_Source"] = "AI_MODEL"
        frames.append(m)

    if trend_df is not None and not trend_df.empty:
        frames.append(trend_df.copy())

    if not frames:
        return pd.DataFrame(columns=UNIFIED_FORECAST_COLUMNS)

    combined = pd.concat(frames, ignore_index=True, sort=False)

    for c in UNIFIED_FORECAST_COLUMNS:
        if c not in combined.columns:
            combined[c] = None

    combined["ItemCode"] = (
        combined["ItemCode"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    )

    # AI_MODEL wins on the rare chance a SKU appears in both — shouldn't
    # normally happen, since trend generation already excludes model SKUs.
    # BUDGET_ONLY (no standard code / no sales mapping — budget is the only
    # number shown) ranks below both forecast sources.
    source_rank = {"AI_MODEL": 0, "TREND_BASELINE": 1, "BUDGET_ONLY": 2}
    combined = combined.sort_values(
        by="Forecast_Source", key=lambda s: s.map(source_rank).fillna(2)
    )
    combined = combined.drop_duplicates(subset=["ItemCode"], keep="first")

    return combined.sort_values("ItemCode").reset_index(drop=True)


# ============================================================
# STEP 2 — pivot horizon forecast (long -> wide)
# ============================================================
def pivot_horizon_forecast(horizon_df: pd.DataFrame) -> pd.DataFrame:
    """
    Long format (ItemCode, Horizon, Forecast_Qty) from forecast_horizon_latest.csv
    -> wide format (ItemCode, Horizon_M1 ... Horizon_M6).
    Only SKUs routed through the AI model have horizon rows — trend-only
    SKUs simply won't appear here, and callers should treat their absence
    as "no M+2..M+6 projection available", not an error.
    """
    if horizon_df is None or horizon_df.empty:
        return pd.DataFrame(columns=["ItemCode"])

    df = horizon_df.copy()
    df["ItemCode"] = df["ItemCode"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)

    pivot = df.pivot_table(index="ItemCode", columns="Horizon", values="Forecast_Qty", aggfunc="first")
    pivot = pivot.reindex(columns=[f"M+{i}" for i in range(1, 7)])
    pivot.columns = [f"Horizon_{c.replace('+', '')}" for c in pivot.columns]  # Horizon_M1 ... Horizon_M6
    return pivot.reset_index()


# ============================================================
# STEP 3 — map everything onto the master SKU list
# ============================================================
def build_master_forecast_table(
    master_df: pd.DataFrame,
    combined_forecast_df: pd.DataFrame,
    horizon_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    sku_master_full.csv (ProductCode, ProductName, Agency, AgencyCode,
    Is_Synthetic_Code) as the primary anchor — every budgeted SKU appears.

    Uses an OUTER join against the combined M+1 forecast so that any
    forecasted SKU (model or trend) that ISN'T in this year's budget is
    still included rather than silently dropped — it's flagged
    Is_Unbudgeted=1 instead. Those rows have no budget-sheet metadata to
    pull from, so ProductName/Agency/AgencyCode come back blank for them.

        - combined_forecast_df -> Forecast_Qty (M+1), Forecast_Source
                                  (AI_MODEL / TREND_BASELINE)
        - horizon_df (pivoted)  -> Horizon_M1..Horizon_M6
                                  (only populated for model-routed SKUs)

    SKUs with neither get Forecast_Source = "NO_FORECAST" and
    Forecast_Qty = 0. SKUs covered only by the trend baseline get
    Horizon_M1 mirrored from Forecast_Qty; M2-M6 stay blank since the
    trend baseline doesn't project a 6-month horizon.
    """
    master = master_df.rename(columns={"ProductCode": "ItemCode"}).copy()
    master["ItemCode"] = master["ItemCode"].astype(str).str.strip()
    budgeted_codes = set(master["ItemCode"])

    # Guard: combined_forecast_df may be a genuinely empty, columnless
    # DataFrame() (e.g. forecast_all_skus_latest.csv hasn't been generated
    # yet — the very first run before any /export). Treat that the same
    # as "no forecast for anyone" instead of crashing on the merge.
    if combined_forecast_df is None or combined_forecast_df.empty or "ItemCode" not in combined_forecast_df.columns:
        combined_forecast_df = pd.DataFrame(columns=["ItemCode", "Forecast_Qty", "Forecast_Source"])
    else:
        combined_forecast_df = combined_forecast_df.copy()
        combined_forecast_df["ItemCode"] = (
            combined_forecast_df["ItemCode"].astype(str).str.strip()
        )

    horizon_pivot = pivot_horizon_forecast(horizon_df)

    # OUTER merge: keep every budgeted SKU AND every forecasted SKU, even
    # if a forecasted SKU has no matching budget row at all.
    merged = master.merge(combined_forecast_df, on="ItemCode", how="outer")
    merged = merged.merge(horizon_pivot, on="ItemCode", how="left")

    merged["Is_Unbudgeted"] = (~merged["ItemCode"].isin(budgeted_codes)).astype(int)

    merged["Forecast_Source"] = merged["Forecast_Source"].fillna("NO_FORECAST")
    merged["Forecast_Qty"] = pd.to_numeric(merged["Forecast_Qty"], errors="coerce").fillna(0)

    # Unbudgeted rows have no master-list row to pull these from
    for col in ["ProductName", "Agency", "AgencyCode"]:
        if col in merged.columns:
            merged[col] = merged[col].fillna("")

    if "Is_Synthetic_Code" in merged.columns:
        merged["Is_Synthetic_Code"] = (
            pd.to_numeric(merged["Is_Synthetic_Code"], errors="coerce").fillna(0).astype(int)
        )

    if "Horizon_M1" in merged.columns:
        # BUDGET_ONLY rows have no forecast at all — never mirror a
        # quantity into the horizon for them (the UI shows budget only).
        needs_h1 = (
            merged["Horizon_M1"].isna()
            & (~merged["Forecast_Source"].isin(["NO_FORECAST", "BUDGET_ONLY"]))
        )
        merged.loc[needs_h1, "Horizon_M1"] = merged.loc[needs_h1, "Forecast_Qty"]

    merged = merged.rename(columns={"ItemCode": "ProductCode"})

    return merged