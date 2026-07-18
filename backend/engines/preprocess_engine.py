# backend/engines/preprocess_engine.py

import pandas as pd
import numpy as np
from datetime import date
from typing import Optional, List

ALLOW_FUTURE_VALIDATION = False

# ── Automatic incomplete-period detection ─────────────────────────────────────
def _detect_incomplete_periods(df: pd.DataFrame) -> list[tuple[int, int]]:
    """
    Inspect raw_df and return a list of (year, month) tuples that should be
    excluded because they represent months still open on the calendar.

    Logic
    -----
    1. Find the latest (Year, Month_Number) present in df.
    2. If that month's first day >= the first day of today's calendar month,
       the month is still open → add it to the exclusion list.
    3. Otherwise every month in the file is fully closed → return [].

    Examples (run on 2026-03-05)
    ----------------------------
    latest = (2026, 2) → 2026-02-01 < 2026-03-01 → closed   → []
    latest = (2026, 3) → 2026-03-01 >= 2026-03-01 → open    → [(2026, 3)]
    latest = (2025, 10)→ 2025-10-01 < 2026-03-01 → closed   → []
    """
    if "Year" not in df.columns or "Month_Number" not in df.columns:
        return []

    years   = pd.to_numeric(df["Year"],         errors="coerce").dropna()
    months  = pd.to_numeric(df["Month_Number"], errors="coerce").dropna()

    if years.empty or months.empty:
        return []

    # Reconstruct period index to find the true latest month
    period_idx = years.astype(int) * 12 + months.astype(int)
    max_idx    = int(period_idx.max())

    latest_year  = (max_idx - 1) // 12
    latest_month = (max_idx - 1) % 12 + 1

    today             = date.today()
    current_month_start = date(today.year, today.month, 1)
    latest_month_start  = date(latest_year, latest_month, 1)

    if latest_month_start >= current_month_start:
        # Latest month in the file is still open on the calendar
        return [(latest_year, latest_month)]

    # All months in the file are fully closed
    return []


# ============================================================
# BASIC HELPERS
# ============================================================
def normalize_itemcode(series):
    return (
        series.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )


def safe_numeric(series, default=0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def force_itemcode_str(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "ItemCode" in df.columns:
        df["ItemCode"] = normalize_itemcode(df["ItemCode"])
    if "ItemCode_Original" in df.columns:
        df["ItemCode_Original"] = normalize_itemcode(df["ItemCode_Original"])
    return df


def standardize_month_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Supports both old and new raw data formats.

    Old:
        Month, Year, Month_Number

    New:
        Month, Year, MonthNo

    Output standard:
        Month = YYYY-MM
        Month_Number = numeric month
    """
    df = df.copy()

    if "MonthNo" in df.columns and "Month_Number" not in df.columns:
        df = df.rename(columns={"MonthNo": "Month_Number"})

    if "Month" in df.columns:
        month_dt = pd.to_datetime(df["Month"], errors="coerce")

        if month_dt.notna().any():
            df["Month"] = month_dt.dt.strftime("%Y-%m")

            if "Year" not in df.columns:
                df["Year"] = month_dt.dt.year

            if "Month_Number" not in df.columns:
                df["Month_Number"] = month_dt.dt.month

    if "Year" in df.columns:
        df["Year"] = pd.to_numeric(df["Year"], errors="coerce")

    if "Month_Number" in df.columns:
        df["Month_Number"] = pd.to_numeric(df["Month_Number"], errors="coerce")

    return df


# ============================================================
# FOCUS-SKU FILTER
# FIXED: added helper so preprocessing can apply the focus filter
# ============================================================
def filter_to_focus_skus(df: pd.DataFrame, focus_codes: Optional[list[str]]) -> pd.DataFrame:
    if not focus_codes:
        return df

    if df is None or df.empty or "ItemCode" not in df.columns:
        return df

    df = force_itemcode_str(df)
    return df[df["ItemCode"].isin(set(focus_codes))].copy()

# ============================================================
# BONUS PATTERN FEATURES
# ============================================================
def detect_recurring_bonus_skus(
    df: pd.DataFrame,
    min_bonus_months: int = 3,
    gap_tolerance: int = 1,
    uplift_threshold: float = 1.4,
) -> pd.DataFrame:

    output_cols = [
        "ItemCode",
        "Recurring_Bonus_SKU",
        "Bonus_Cycle_Length",
        "Avg_Bonus_Gap",
        "Bonus_Frequency_All",
        "Avg_Bonus_Uplift",
    ]

    if df is None or df.empty or "ItemCode" not in df.columns:
        return pd.DataFrame(columns=output_cols)

    df = force_itemcode_str(df)

    required = ["ItemCode", "Year", "Month_Number", "Bonus_Flag", "Uplift_vs_Baseline"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return pd.DataFrame(columns=output_cols)

    df = df.sort_values(["ItemCode", "Year", "Month_Number"]).copy()

    results = []

    for item, g in df.groupby("ItemCode"):
        g = g.copy()
        g["Time_Index"] = g["Year"].astype(int) * 12 + g["Month_Number"].astype(int)

        bonus_rows = g[g["Bonus_Flag"] == 1].copy()

        recurring_flag = 0
        cycle_len = 0
        avg_gap = 0.0
        bonus_freq = float(len(bonus_rows) / max(len(g), 1))
        avg_bonus_uplift = 1.0

        if len(bonus_rows) >= min_bonus_months:
            gaps = bonus_rows["Time_Index"].diff().dropna()

            if len(gaps) > 0:
                avg_gap = float(gaps.mean())
                rounded_gap = int(round(avg_gap))
                stable_gap_ratio = ((gaps - rounded_gap).abs() <= gap_tolerance).mean()

                avg_bonus_uplift = (
                    bonus_rows["Uplift_vs_Baseline"]
                    .replace([np.inf, -np.inf], np.nan)
                    .clip(0, 5)
                    .fillna(1.0)
                    .median()
                )

                if stable_gap_ratio >= 0.6 and avg_bonus_uplift >= uplift_threshold:
                    recurring_flag = 1
                    cycle_len = rounded_gap

        results.append({
            "ItemCode": str(item),
            "Recurring_Bonus_SKU": int(recurring_flag),
            "Bonus_Cycle_Length": int(cycle_len),
            "Avg_Bonus_Gap": float(avg_gap),
            "Bonus_Frequency_All": float(bonus_freq),
            "Avg_Bonus_Uplift": float(avg_bonus_uplift),
        })

    return pd.DataFrame(results)


def add_bonus_cycle_features(df: pd.DataFrame) -> pd.DataFrame:
    df = force_itemcode_str(df)
    df = df.sort_values(["ItemCode", "Year", "Month_Number"]).copy()

    pieces = []

    for item_code, g in df.groupby("ItemCode", sort=False):
        g = g.sort_values(["Year", "Month_Number"]).copy()
        g["Time_Index"] = g["Year"].astype(int) * 12 + g["Month_Number"].astype(int)

        bonus_time_idx = g.loc[g["Bonus_Flag"] == 1, "Time_Index"].tolist()

        months_since_last_bonus = []
        expected_bonus_month = []

        for _, r in g.iterrows():
            current_t = int(r["Time_Index"])
            past_bonus = [t for t in bonus_time_idx if t < current_t]

            months_since_last_bonus.append(999 if len(past_bonus) == 0 else current_t - past_bonus[-1])

            cyc = float(r.get("Bonus_Cycle_Length", 0) or 0)
            recurring = int(r.get("Recurring_Bonus_SKU", 0) or 0)

            if recurring == 1 and cyc > 0 and len(past_bonus) > 0:
                expected_bonus_month.append(
                    1 if abs((current_t - past_bonus[-1]) - cyc) <= 1 else 0
                )
            else:
                expected_bonus_month.append(0)

        g["Months_Since_Last_Bonus"] = months_since_last_bonus
        g["Expected_Bonus_Month"] = expected_bonus_month

        g["Bonus_Flag_Lag1"] = g["Bonus_Flag"].shift(1).fillna(0)
        g["Bonus_Flag_Lag2"] = g["Bonus_Flag"].shift(2).fillna(0)
        g["Bonus_Flag_Lag3"] = g["Bonus_Flag"].shift(3).fillna(0)

        g["Bonus_Frequency_12M"] = (
            g["Bonus_Flag"]
            .rolling(12, min_periods=1)
            .mean()
            .shift(1)
            .fillna(0)
        )

        g = g.drop(columns=["Time_Index"], errors="ignore")
        pieces.append(g)

    return pd.concat(pieces, ignore_index=True)


def add_last_bonus_demand_feature(df: pd.DataFrame) -> pd.DataFrame:
    df = force_itemcode_str(df)
    df = df.sort_values(["ItemCode", "Year", "Month_Number"]).copy()

    pieces = []

    for item, g in df.groupby("ItemCode", sort=False):
        g = g.copy()
        last_vals = []
        last_bonus_demand = 0.0

        for _, r in g.iterrows():
            last_vals.append(last_bonus_demand)

            if int(r.get("Bonus_Flag", 0)) == 1:
                last_bonus_demand = float(r.get("Clean_Demand", 0) or 0)

        g["Last_Bonus_Demand"] = last_vals
        pieces.append(g)

    return pd.concat(pieces, ignore_index=True)


# ============================================================
# STRUCTURAL DEMAND STATE
# ============================================================
def add_structural_demand_state(df: pd.DataFrame) -> pd.DataFrame:
    df = force_itemcode_str(df)
    df = df.sort_values(["ItemCode", "Year", "Month_Number"]).copy()

    pieces = []

    for item, g in df.groupby("ItemCode", sort=False):
        g = g.copy()

        roll3 = g["Clean_Demand"].rolling(3, min_periods=1).mean().shift(1)
        roll6 = g["Clean_Demand"].rolling(6, min_periods=1).mean().shift(1)

        growth_ratio = roll3 / (roll6 + 1)

        zero_rate_6m = (g["Clean_Demand"].rolling(6, min_periods=1).apply(lambda x: (x == 0).mean(), raw=False).shift(1))

        g["Demand_State"] = np.where(
            zero_rate_6m >= 0.50,
            "DYING_OR_INTERMITTENT",
            np.where(
                growth_ratio >= 1.30,
                "GROWING",
                np.where(growth_ratio <= 0.70, "DECLINING", "MATURE")
            )
        )

        pieces.append(g)

    out = pd.concat(pieces, ignore_index=True)

    state_map = {
        "MATURE": 0,
        "GROWING": 1,
        "DECLINING": 2,
        "DYING_OR_INTERMITTENT": 3,
    }

    out["Demand_State_Encoded"] = (
        out["Demand_State"].map(state_map).fillna(0).astype(int)
    )

    return out


def classify_demand_regime(row):
    lag1 = row.get("Lag1", 0)
    roll3 = row.get("Rolling3M_Mean", 0)
    roll6 = row.get("Rolling6M_Mean", roll3)
    bonus = row.get("Bonus_Flag", 0)
    supply = row.get("Supply_Constraint_Flag", 0)
    expected_bonus = row.get("Expected_Bonus_Month", 0)
    post_bonus = row.get("Post_Bonus_Month", 0)
    demand_state = row.get("Demand_State", "MATURE")

    anchor = max(roll3, roll6, 1)

    if supply == 1:
        return "SUPPLY_SHOCK"
    if bonus == 1 or expected_bonus == 1:
        return "PROMO"
    if post_bonus == 1:
        return "POST_PROMO_DROP"
    if demand_state == "GROWING":
        return "GROWING"
    if demand_state == "DECLINING":
        return "DECLINING"
    if lag1 > 1.8 * anchor:
        return "RECENT_SPIKE"
    if lag1 < 0.5 * anchor:
        return "RECENT_DROP"

    return "NORMAL"


# ============================================================
# HISTORY SEGMENTATION
# ============================================================
def add_history_segment(df: pd.DataFrame) -> pd.DataFrame:
    hist = (
        df[["ItemCode", "Year", "Month_Number"]]
        .drop_duplicates()
        .groupby("ItemCode")
        .size()
        .reset_index(name="History_Length")
    )

    df = df.drop(columns=["History_Length", "History_Segment"], errors="ignore")
    df = df.merge(hist, on="ItemCode", how="left")

    df["History_Segment"] = np.select(
        [
            df["History_Length"] >= 18,
            (df["History_Length"] >= 10) & (df["History_Length"] < 18),
            df["History_Length"] < 10,
        ],
        ["LONG", "MEDIUM", "SHORT"],
        default="SHORT"
    )

    return df


# ============================================================
# PROMO PROFILE
# ============================================================
def build_promo_profile(df: pd.DataFrame) -> pd.DataFrame:
    df = force_itemcode_str(df)
    df = df.sort_values(["ItemCode", "Year", "Month_Number"]).copy()

    out = []

    for item, g in df.groupby("ItemCode"):
        bonus_months = g[g["Bonus_Flag"] == 1]
        non_bonus_months = g[g["Bonus_Flag"] == 0]

        bonus_count = len(bonus_months)
        total_count = len(g)
        bonus_freq = bonus_count / total_count if total_count > 0 else 0.0

        bonus_avg = float(bonus_months["Clean_Demand"].mean()) if bonus_count > 0 else 0.0
        non_bonus_avg = float(non_bonus_months["Clean_Demand"].mean()) if len(non_bonus_months) > 0 else 0.0

        uplift_ratio = 1.0 if non_bonus_avg < 5 else bonus_avg / max(non_bonus_avg, 1.0)
        uplift_ratio = float(np.clip(uplift_ratio, 0, 5))

        if g["Bonus_Flag"].nunique() > 1 and g["Clean_Demand"].nunique() > 1:
            corr = g["Bonus_Flag"].corr(g["Clean_Demand"])
            corr = 0.0 if pd.isna(corr) else float(corr)
        else:
            corr = 0.0

        total_demand = float(g["Clean_Demand"].sum())
        bonus_demand_share = (
            float(bonus_months["Clean_Demand"].sum() / total_demand)
            if total_demand > 0 else 0.0
        )

        if bonus_count >= 3 and corr >= 0.70 and uplift_ratio >= 2.0 and bonus_demand_share >= 0.65:
            promo_profile = "PURE_PROMO"
        elif bonus_count >= 3 and corr >= 0.45 and uplift_ratio >= 1.25:
            promo_profile = "PROMO_INFLUENCED"
        else:
            promo_profile = "NORMAL"

        out.append({
            "ItemCode": item,
            "Promo_Profile": promo_profile,
            "Bonus_Corr": corr,
            "Bonus_Frequency_Profile": bonus_freq,
            "Bonus_Avg_Demand": bonus_avg,
            "NonBonus_Avg_Demand": non_bonus_avg,
            "Bonus_Uplift_Ratio_Profile": uplift_ratio,
            "Bonus_Demand_Share": bonus_demand_share,
            "Bonus_Month_Count": bonus_count,
        })

    return force_itemcode_str(pd.DataFrame(out))


def merge_promo_profile(df: pd.DataFrame, promo_profile_df: pd.DataFrame) -> pd.DataFrame:
    df = force_itemcode_str(df)
    promo_profile_df = force_itemcode_str(promo_profile_df)

    keep_cols = [
        "ItemCode",
        "Promo_Profile",
        "Bonus_Corr",
        "Bonus_Frequency_Profile",
        "Bonus_Avg_Demand",
        "NonBonus_Avg_Demand",
        "Bonus_Uplift_Ratio_Profile",
        "Bonus_Demand_Share",
        "Bonus_Month_Count",
    ]

    df = df.drop(columns=[c for c in keep_cols if c != "ItemCode"], errors="ignore")
    
    if promo_profile_df is None or promo_profile_df.empty or "ItemCode" not in promo_profile_df.columns:
        promo_profile_df = pd.DataFrame(columns=keep_cols)

    for c in keep_cols:
        if c not in promo_profile_df.columns:
            promo_profile_df[c] = None

    df = df.merge(promo_profile_df[keep_cols], on="ItemCode", how="left")

    df["Promo_Profile"] = df["Promo_Profile"].fillna("NORMAL")

    for c in keep_cols:
        if c not in ["ItemCode", "Promo_Profile"]:
            df[c] = df[c].fillna(0)

    return df


# ============================================================
# MODE HELPERS
# ============================================================
def _normalize_processing_mode(mode: str) -> str:
    mode = str(mode or "actual").strip().lower()
    if mode not in {"actual", "snapshot", "live"}:
        return "actual"
    return "snapshot" if mode == "live" else mode


def _standardize_snapshot_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    rename_map = {
        "Snapshot_Year": "Year",
        "Snapshot_Month": "Month_Number",
        "MTD_Secondary_Sales_Qty": "Secondary_Sales_Qty",
        "MTD_Free_Qty": "Free_Qty",
        "Available_Primary_Inventory_Qty_Current": "Available_Primary_Inventory_Qty",
        "Distributor_Inventory_Qty_Current": "Distributor_Inventory_Qty",
        "Blocked_Stock_Qty_Current": "Blocked_Stock_Qty",
        "Inspection_Stock_Qty_Current": "Inspection_Stock_Qty",
    }

    existing_map = {k: v for k, v in rename_map.items() if k in df.columns and v not in df.columns}
    if existing_map:
        df = df.rename(columns=existing_map)

    # FIXED: guard so we don't overwrite a valid existing column with a synthetic one
    if (
        "Total_Primary_Inventory_Qty" not in df.columns
        or df["Total_Primary_Inventory_Qty"].fillna(0).sum() == 0
    ):
        available = pd.to_numeric(df.get("Available_Primary_Inventory_Qty", 0), errors="coerce").fillna(0)
        blocked   = pd.to_numeric(df.get("Blocked_Stock_Qty", 0), errors="coerce").fillna(0)
        insp      = pd.to_numeric(df.get("Inspection_Stock_Qty", 0), errors="coerce").fillna(0)
        df["Total_Primary_Inventory_Qty"] = available + blocked + insp

    if "Primary_Sales_Qty" not in df.columns:
        df["Primary_Sales_Qty"] = 0

    if "Distributor_Buffer_Flag" not in df.columns:
        df["Distributor_Buffer_Flag"] = 0

    if "Bonus_Flag" not in df.columns:
        free_qty = pd.to_numeric(df.get("Free_Qty", 0), errors="coerce").fillna(0)
        df["Bonus_Flag"] = (free_qty > 0).astype(int)

    if "Supply_Constraint_Flag" not in df.columns:
        avail_qty = pd.to_numeric(df.get("Available_Primary_Inventory_Qty", 0), errors="coerce").fillna(0)
        df["Supply_Constraint_Flag"] = (avail_qty <= 0).astype(int)

    return df


def _prepare_raw_by_mode(
    raw_df: pd.DataFrame,
    mode: str,
    apply_period_filter: bool = True,
) -> pd.DataFrame:
    """
    Standardise raw input and optionally strip incomplete periods.

    apply_period_filter
    -------------------
    True  (default) — used for mode="actual" (fact_monthly_closed).
          Calls _detect_incomplete_periods() to automatically find and remove
          any month that is still open on the calendar.  No hardcoded list.

    False — used for mode="snapshot"/"live" (fact_open_month_snapshot).
          The snapshot IS the current open month by definition.  Stripping it
          would remove the only source of today's inventory/supply data.
          Never filter the snapshot path.
    """
    mode = _normalize_processing_mode(mode)
    df = force_itemcode_str(raw_df.copy())

    df = standardize_month_columns(df)

    if mode == "snapshot":
        df = _standardize_snapshot_columns(df)
        df = standardize_month_columns(df)

    if apply_period_filter and {"Year", "Month_Number"}.issubset(df.columns):
        incomplete = _detect_incomplete_periods(df)
        for year, month in incomplete:
            df = df[~((df["Year"] == year) & (df["Month_Number"] == month))].copy()
        if incomplete:
            print(
                f"[PREPROCESS] Auto-removed incomplete period(s): {incomplete} "
                f"(still open on calendar {date.today()})"
            )

    return df


def build_training_data(
    raw_df: pd.DataFrame,
    focus_codes: Optional[List[str]] = None,
    force_incomplete_periods: Optional[List[tuple]] = None,
) -> pd.DataFrame:
    """
    Entry point for notebook training pipeline.
    
    force_incomplete_periods: explicit list of (year, month) to exclude.
        If None, auto-detects using _detect_incomplete_periods().
        Pass [] to exclude nothing (all months fully closed).
        Pass [(2026, 2)] to explicitly exclude Feb 2026 (old notebook behaviour).
    
    Returns the same cleaned DataFrame that run_preprocessing() produced,
    without model features (no Lag1, Rolling3M_Mean etc.) — those are
    added by build_model_features() in the notebook.
    """
    df = _prepare_raw_by_mode(raw_df, mode="actual", apply_period_filter=False)
    
    # Apply explicit or auto-detected incomplete period filter
    if force_incomplete_periods is None:
        incomplete = _detect_incomplete_periods(df)
    else:
        incomplete = force_incomplete_periods
    
    for year, month in incomplete:
        df = df[~((df["Year"] == year) & (df["Month_Number"] == month))].copy()
    
    df = filter_to_focus_skus(df, focus_codes)
    # ... rest of cleaning pipeline identical to build_processed_data_from_raw
    # but stops BEFORE adding Lag1, Rolling3M_Mean etc.
    # (those are training-specific and added by build_model_features() in the notebook)
    return df

# ============================================================
# MAIN PREPROCESS FUNCTION
# FIXED: accepts optional focus_codes; filters before feature engineering
# ============================================================
def build_processed_data_from_raw(
    raw_df: pd.DataFrame,
    mode: str = "actual",
    focus_codes: Optional[List[str]] = None,
    apply_period_filter: bool = True,
) -> pd.DataFrame:
    """
    Full preprocessing pipeline.

    Parameters
    ----------
    raw_df : pd.DataFrame
        Raw input loaded from SAP extract / CSV.
    mode : str
        'actual' for monthly-closed data, 'snapshot' / 'live' for open-month.
    focus_codes : list[str] | None
        If provided, only rows whose ItemCode is in this list are processed.
        Pass the output of forecast_service.load_focus_item_codes() here.
        If None, all rows are processed (backward-compatible behaviour).
    apply_period_filter : bool
        True  → auto-detect and strip any month still open on the calendar.
                 Use for mode="actual" (fact_monthly_closed) only.
        False → never strip rows — snapshot is always the current open month.
                 Set automatically by process_live_raw_now().
    """
    mode = _normalize_processing_mode(mode)
    df = _prepare_raw_by_mode(raw_df, mode, apply_period_filter=apply_period_filter)

    # FIXED: apply focus-SKU filter early so rolling features are computed
    # only over the relevant SKU universe, matching notebook behaviour.
    df = filter_to_focus_skus(df, focus_codes)

    df["Data_Mode"] = mode
    df["Month_Status"] = np.where(mode == "actual", "CLOSED", "OPEN")

    if df.empty:
        return df

    numeric_cols = [
        "Secondary_Sales_Qty",
        "Primary_Sales_Qty",
        "Free_Qty",
        "Available_Primary_Inventory_Qty",
        "Blocked_Stock_Qty",
        "Inspection_Stock_Qty",
        "Total_Primary_Inventory_Qty",
        "Distributor_Inventory_Qty",
    ]

    for c in numeric_cols:
        df[c] = safe_numeric(df.get(c, 0)).clip(lower=0)

    df["Bonus_Flag"] = safe_numeric(df.get("Bonus_Flag", 0)).astype(int)
    df["Supply_Constraint_Flag"] = safe_numeric(df.get("Supply_Constraint_Flag", 0)).astype(int)
    df["Distributor_Buffer_Flag"] = safe_numeric(df.get("Distributor_Buffer_Flag", 0)).astype(int)

    if "Year" not in df.columns:
        raise ValueError("Input data must contain 'Year' or 'Snapshot_Year'.")
    if "Month_Number" not in df.columns:
        raise ValueError("Input data must contain 'Month_Number' or 'Snapshot_Month'.")

    df = df.sort_values(["ItemCode", "Year", "Month_Number"]).reset_index(drop=True)

    grp = df.groupby("ItemCode")

    df["Observed_Demand"] = df["Secondary_Sales_Qty"].clip(lower=0)

    for lag in [1, 2, 3, 6, 12]:
        df[f"Lag{lag}_Obs"] = grp["Observed_Demand"].shift(lag)

    df["Rolling3M_Obs_Mean"] = grp["Observed_Demand"].transform(
        lambda x: x.rolling(3, min_periods=1).mean().shift(1)
    )

    df["Rolling6M_Obs_Mean"] = grp["Observed_Demand"].transform(
        lambda x: x.rolling(6, min_periods=1).mean().shift(1)
    )

    df["Rolling3M_Obs_Std"] = grp["Observed_Demand"].transform(
        lambda x: x.rolling(3, min_periods=1).std().shift(1)
    ).fillna(0)

    df["Baseline_Demand"] = (
        df["Rolling3M_Obs_Mean"]
        .fillna(df["Lag1_Obs"])
        .fillna(0)
    )

    safe_baseline = np.maximum(df["Baseline_Demand"].fillna(0), 1)
    df["Uplift_vs_Baseline"] = (df["Observed_Demand"] / safe_baseline).clip(0, 5)

    df["Z_Score_Obs"] = (
        (df["Observed_Demand"] - df["Rolling3M_Obs_Mean"]) /
        (df["Rolling3M_Obs_Std"] + 1)
    ).fillna(0)

    bonus_df = detect_recurring_bonus_skus(df)

    # FIXED: drop ALL stale bonus-cycle columns before merging to prevent
    # duplicate columns on re-runs (notebook's add_bonus_profile_features does this)
    _BONUS_COLS_TO_DROP = [
        "Recurring_Bonus_SKU",
        "Bonus_Cycle_Length",
        "Avg_Bonus_Gap",
        "Bonus_Frequency_All",
        "Avg_Bonus_Uplift",
        "Months_Since_Last_Bonus",
        "Expected_Bonus_Month",
        "Bonus_Flag_Lag1",
        "Bonus_Flag_Lag2",
        "Bonus_Flag_Lag3",
        "Bonus_Frequency_12M",
    ]
    df = df.drop(columns=_BONUS_COLS_TO_DROP, errors="ignore")

    df = df.merge(bonus_df, on="ItemCode", how="left")

    for c in ["Recurring_Bonus_SKU", "Bonus_Cycle_Length", "Avg_Bonus_Gap", "Bonus_Frequency_All"]:
        df[c] = df[c].fillna(0)

    df["Avg_Bonus_Uplift"] = df["Avg_Bonus_Uplift"].fillna(1.0)

    df = add_bonus_cycle_features(df)

    df["Net_Available_Stock"] = (
        df["Total_Primary_Inventory_Qty"]
        - df["Blocked_Stock_Qty"]
        - df["Inspection_Stock_Qty"]
    ).clip(lower=0)

    df["Primary_Stock_Cover"] = np.where(
        df["Baseline_Demand"] <= 0,
        0,
        df["Net_Available_Stock"] / (df["Baseline_Demand"] + 1)
    )

    df["Distributor_Stock_Cover"] = np.where(
        df["Baseline_Demand"] <= 0,
        0,
        df["Distributor_Inventory_Qty"] / (df["Baseline_Demand"] + 1)
    )

    df["Primary_to_Distributor_Ratio"] = np.where(
        df["Distributor_Inventory_Qty"] <= 0,
        0,
        df["Net_Available_Stock"] / (df["Distributor_Inventory_Qty"] + 1)
    )

    df["Blocked_Stock_Ratio"] = np.where(
        df["Total_Primary_Inventory_Qty"] <= 0,
        0,
        df["Blocked_Stock_Qty"] / (df["Total_Primary_Inventory_Qty"] + 1)
    )

    df["Inspection_Stock_Ratio"] = np.where(
        df["Total_Primary_Inventory_Qty"] <= 0,
        0,
        df["Inspection_Stock_Qty"] / (df["Total_Primary_Inventory_Qty"] + 1)
    )

    df["Primary_Inv_Change"] = df.groupby("ItemCode")["Net_Available_Stock"].diff().fillna(0)
    df["Distributor_Inv_Change"] = df.groupby("ItemCode")["Distributor_Inventory_Qty"].diff().fillna(0)

    for c in ["Primary_to_Distributor_Ratio", "Distributor_Inv_Change", "Primary_Inv_Change"]:
        cap_source = df[c].replace([np.inf, -np.inf], np.nan).dropna()
        if len(cap_source):
            df[c] = df[c].clip(upper=cap_source.quantile(0.99))

    df["Effective_Demand"] = df["Observed_Demand"].copy()

    df["Supply_Baseline"] = (
        df["Rolling3M_Obs_Mean"]
        .fillna(df["Lag1_Obs"])
        .fillna(df["Observed_Demand"])
    )

    df["Effective_Demand"] = np.where(
        df["Supply_Constraint_Flag"] == 1,
        np.maximum(df["Observed_Demand"], 0.85 * df["Supply_Baseline"]),
        df["Observed_Demand"]
    )

    irregular_bonus_spike = (
        (df["Bonus_Flag"] == 1) &
        (df["Recurring_Bonus_SKU"] == 0) &
        (df["Z_Score_Obs"] > 2.0) &
        (df["Uplift_vs_Baseline"] > 1.6)
    )

    grp = df.groupby("ItemCode")
    prev_obs = grp["Observed_Demand"].shift(1)
    prev_primary_cover = grp["Primary_Stock_Cover"].shift(1)
    prev_dist_cover = grp["Distributor_Stock_Cover"].shift(1)

    stockout_drop = (
        (df["Observed_Demand"] < 0.65 * prev_obs.fillna(df["Observed_Demand"])) &
        (df["Supply_Constraint_Flag"] == 1) &
        (
            (prev_primary_cover.fillna(99) < 1.0) |
            (prev_dist_cover.fillna(99) < 1.0)
        )
    )

    df["Clean_Demand"] = df["Effective_Demand"].copy()

    df.loc[irregular_bonus_spike, "Clean_Demand"] = (
        0.60 * df.loc[irregular_bonus_spike, "Observed_Demand"] +
        0.40 * df.loc[irregular_bonus_spike, "Baseline_Demand"]
    )

    df.loc[stockout_drop, "Clean_Demand"] = np.maximum(
        df.loc[stockout_drop, "Observed_Demand"],
        0.90 * df.loc[stockout_drop, "Baseline_Demand"]
    )

    df["Clean_Demand"] = df["Clean_Demand"].clip(lower=0)

    df["Bonus_Shock"] = irregular_bonus_spike.astype(int)
    df["Supply_Shock"] = stockout_drop.astype(int)

    df["Free_Ratio"] = np.where(
        df["Primary_Sales_Qty"] <= 0,
        0,
        df["Free_Qty"] / (df["Primary_Sales_Qty"] + 1)
    )

    grp = df.groupby("ItemCode")

    df["Free_Qty_Lag1"] = grp["Free_Qty"].shift(1).fillna(0)
    df["Free_Ratio_Lag1"] = grp["Free_Ratio"].shift(1).fillna(0)
    df["Free_Qty_Rolling3"] = grp["Free_Qty"].transform(
        lambda x: x.rolling(3, min_periods=1).mean().shift(1)
    ).fillna(0)

    for lag in [1, 2, 3, 6, 12]:
        df[f"Lag{lag}"] = grp["Clean_Demand"].shift(lag)

    df["Rolling3M_Mean"] = grp["Clean_Demand"].transform(
        lambda x: x.rolling(3, min_periods=1).mean().shift(1)
    )

    df["Rolling6M_Mean"] = grp["Clean_Demand"].transform(
        lambda x: x.rolling(6, min_periods=1).mean().shift(1)
    )

    df["Rolling3M_Std"] = grp["Clean_Demand"].transform(
        lambda x: x.rolling(3, min_periods=1).std().shift(1)
    ).fillna(0)

    df["Momentum"] = df["Lag1"] - df["Lag3"]

    df["Bonus_Last_Month"] = df["Bonus_Flag_Lag1"].fillna(0)
    df["Bonus_2M_Ago"] = df["Bonus_Flag_Lag2"].fillna(0)

    df["Post_Bonus_Month"] = np.where(
        (df["Bonus_Last_Month"] == 1) & (df["Bonus_Flag"] == 0),
        1,
        0
    )

    df["Promo_Decay_Ratio"] = np.where(
        df["Rolling6M_Mean"].fillna(0) > 0,
        df["Lag1"] / (df["Rolling6M_Mean"] + 1),
        0
    )

    df["Current_Total_Usable_Stock"] = (
        df["Available_Primary_Inventory_Qty"].fillna(0) +
        df["Distributor_Inventory_Qty"].fillna(0)
    )

    df["Current_Stock_Cover"] = (
        df["Current_Total_Usable_Stock"] /
        (df["Rolling3M_Mean"].fillna(0) + 1)
    )

    df["Current_Stockout_Risk"] = np.where(
        df["Current_Stock_Cover"] < 0.5,
        1,
        0
    )

    df["Month_Sin"] = np.sin(2 * np.pi * df["Month_Number"] / 12)
    df["Month_Cos"] = np.cos(2 * np.pi * df["Month_Number"] / 12)

    quarter = ((df["Month_Number"] - 1) // 3) + 1
    df["Quarter_Sin"] = np.sin(2 * np.pi * quarter / 4)
    df["Quarter_Cos"] = np.cos(2 * np.pi * quarter / 4)

    df["Is_Zero"] = (df["Clean_Demand"] == 0).astype(int)

    df["ZeroRate_6M"] = grp["Is_Zero"].transform(
        lambda x: x.rolling(6, min_periods=1).mean().shift(1)
    ).fillna(0)

    df["Inventory_Pressure"] = np.where(
        df["Lag1"].fillna(0) <= 0,
        0,
        df["Available_Primary_Inventory_Qty"] / (df["Lag1"] + 1)
    )

    df["Stock_Cover_Months"] = np.where(
        df["Rolling3M_Mean"].fillna(0) <= 0,
        0,
        df["Net_Available_Stock"] / (df["Rolling3M_Mean"] + 1)
    )

    df["Demand_to_Stock_Ratio"] = np.where(
        df["Net_Available_Stock"] <= 0,
        0,
        df["Rolling3M_Mean"] / (df["Net_Available_Stock"] + 1)
    )

    df["Supply_Constraint_Lag1"] = grp["Supply_Constraint_Flag"].shift(1).fillna(0)
    df["Supply_Constraint_Lag2"] = grp["Supply_Constraint_Flag"].shift(2).fillna(0)
    df["Distributor_Stock_Cover_Lag1"] = grp["Distributor_Stock_Cover"].shift(1).fillna(0)

    df["Promo_Intensity_History"] = np.where(
        df["Rolling3M_Mean"].fillna(0) <= 0,
        0,
        df["Free_Qty_Rolling3"] / (df["Rolling3M_Mean"] + 1)
    )

    df["Expected_Bonus_NextMonth"] = np.where(
        (df["Recurring_Bonus_SKU"] == 1) &
        (df["Bonus_Cycle_Length"] > 0) &
        (np.abs((df["Months_Since_Last_Bonus"] + 1) - df["Bonus_Cycle_Length"]) <= 1),
        1,
        0
    )

    df["Recurring_Bonus_Month"] = np.where(
        (df["Recurring_Bonus_SKU"] == 1) &
        (
            (df["Bonus_Flag"] == 1) |
            (
                (df["Bonus_Cycle_Length"] > 0) &
                (np.abs(df["Months_Since_Last_Bonus"] - df["Bonus_Cycle_Length"]) <= 1)
            )
        ),
        1,
        0
    )

    safe_mean = np.maximum(df["Rolling3M_Mean"].fillna(0), 1.0)
    df["Realized_Uplift"] = (df["Clean_Demand"] / safe_mean).clip(0, 6)

    grp2 = df.groupby("ItemCode")

    df["Promo_Uplift_Lag1"] = grp2["Realized_Uplift"].shift(1).fillna(1.0).clip(0.5, 3.0)
    df["Promo_Uplift_Lag2"] = grp2["Realized_Uplift"].shift(2).fillna(1.0).clip(0.5, 3.0)
    df["Promo_Uplift_6M"] = grp2["Realized_Uplift"].transform(
        lambda x: x.rolling(6, min_periods=1).mean().shift(1)
    ).fillna(1.0).clip(0.5, 2.5)

    df = add_last_bonus_demand_feature(df)
    df = add_structural_demand_state(df)

    df["Demand_Regime"] = df.apply(classify_demand_regime, axis=1)

    regime_map = {
        "NORMAL": 0,
        "PROMO": 1,
        "POST_PROMO_DROP": 2,
        "SUPPLY_SHOCK": 3,
        "RECENT_SPIKE": 4,
        "RECENT_DROP": 5,
        "GROWING": 6,
        "DECLINING": 7,
    }

    df["Demand_Regime_Encoded"] = (
        df["Demand_Regime"].map(regime_map).fillna(0).astype(int)
    )

    df["Bonus_Sin"] = df["Bonus_Flag"] * np.sin(2 * np.pi * df["Month_Number"] / 12)
    df["Bonus_Cos"] = df["Bonus_Flag"] * np.cos(2 * np.pi * df["Month_Number"] / 12)

    df = add_history_segment(df)

    sku_profile = (
        df.groupby("ItemCode")
        .agg(
            SKU_Mean_Demand=("Clean_Demand", "mean"),
            SKU_Std_Demand=("Clean_Demand", "std"),
            SKU_ZeroRate=("Clean_Demand", lambda x: (x == 0).mean())
        )
        .reset_index()
    )

    sku_profile["SKU_Std_Demand"] = sku_profile["SKU_Std_Demand"].fillna(0)
    sku_profile["SKU_CV"] = np.where(
        sku_profile["SKU_Mean_Demand"] <= 0,
        0,
        sku_profile["SKU_Std_Demand"] / (sku_profile["SKU_Mean_Demand"] + 1)
    )

    sku_profile = sku_profile[["ItemCode", "SKU_Mean_Demand", "SKU_ZeroRate", "SKU_CV"]]
    df = df.drop(columns=["SKU_Mean_Demand", "SKU_ZeroRate", "SKU_CV"], errors="ignore")
    df = df.merge(sku_profile, on="ItemCode", how="left")

    df["Behavior_Type"] = np.select(
        [
            df["SKU_ZeroRate"] >= 0.40,
            df["Bonus_Frequency_All"] >= 0.25,
            df["SKU_CV"] >= 1.0,
        ],
        [
            "INTERMITTENT",
            "PROMO_DRIVEN",
            "VOLATILE",
        ],
        default="STABLE"
    )

    promo_profile_df = build_promo_profile(df)
    df = merge_promo_profile(df, promo_profile_df)

    df = df.replace([np.inf, -np.inf], np.nan)

    string_cols = [
        "ItemCode",
        "ItemCode_Original",
        "Promo_Profile",
        "History_Segment",
        "Data_Mode",
        "Month_Status",
        "Demand_State",
        "Demand_Regime",
        "Behavior_Type",
    ]

    fill_zero_cols = [c for c in df.columns if c not in string_cols]
    df[fill_zero_cols] = df[fill_zero_cols].fillna(0)

    df["Promo_Profile"] = df["Promo_Profile"].fillna("NORMAL")
    df["History_Segment"] = df["History_Segment"].fillna("SHORT")
    df["Demand_State"] = df["Demand_State"].fillna("MATURE")
    df["Demand_Regime"] = df["Demand_Regime"].fillna("NORMAL")
    df["Behavior_Type"] = df["Behavior_Type"].fillna("STABLE")

    if "Snapshot_Date" not in df.columns:
        df["Snapshot_Date"] = pd.NaT

    return df