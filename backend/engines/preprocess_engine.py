# backend/engines/preprocess_engine.py

import pandas as pd
import numpy as np

# ============================================================
# CONFIG
# ============================================================
ALLOW_FUTURE_VALIDATION = False  # must stay False in production


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


# ============================================================
# BONUS PATTERN DETECTION
# ============================================================
def detect_recurring_bonus_skus(
    df: pd.DataFrame,
    min_bonus_months: int = 3,
    gap_tolerance: int = 1,
    uplift_threshold: float = 1.4,
) -> pd.DataFrame:
    """
    Detect recurring bonus SKUs using:
    - enough bonus months
    - stable gap/cycle between bonus months
    - meaningful uplift during bonus months
    """
    df = force_itemcode_str(df)
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
    """
    Add reusable bonus timing features from full historical data.
    Backend-safe version for processed dataset creation.
    """
    df = force_itemcode_str(df)
    df = df.sort_values(["ItemCode", "Year", "Month_Number"]).copy()

    pieces = []

    for item_code, g in df.groupby("ItemCode", sort=False):
        g = g.sort_values(["Year", "Month_Number"]).copy()
        g["Time_Index"] = g["Year"].astype(int) * 12 + g["Month_Number"].astype(int)

        bonus_time_idx = g.loc[g["Bonus_Flag"] == 1, "Time_Index"].tolist()

        months_since_last_bonus = []
        expected_bonus_this_month = []

        for i in range(len(g)):
            current_t = g["Time_Index"].iloc[i]
            past_bonus = [t for t in bonus_time_idx if t < current_t]

            if len(past_bonus) == 0:
                months_since_last_bonus.append(999)
            else:
                months_since_last_bonus.append(current_t - past_bonus[-1])

            cyc = g["Bonus_Cycle_Length"].iloc[i]
            recurring = g["Recurring_Bonus_SKU"].iloc[i]

            if recurring == 1 and cyc > 0 and len(past_bonus) > 0:
                expected_bonus_this_month.append(
                    1 if abs((current_t - past_bonus[-1]) - cyc) <= 1 else 0
                )
            else:
                expected_bonus_this_month.append(0)

        g["Months_Since_Last_Bonus"] = months_since_last_bonus
        g["Expected_Bonus_Month"] = expected_bonus_this_month

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

    return pd.concat(pieces, axis=0, ignore_index=True)


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

    df = df.merge(hist, on="ItemCode", how="left")

    df["History_Segment"] = np.select(
        [
            df["History_Length"] >= 18,
            (df["History_Length"] >= 6) & (df["History_Length"] < 18),
            df["History_Length"] < 6,
        ],
        [
            "LONG",
            "MEDIUM",
            "SHORT",
        ],
        default="SHORT"
    )

    return df


# ============================================================
# PROMO PROFILE FEATURES
# ============================================================
def build_promo_profile(
    df: pd.DataFrame,
    min_bonus_months: int = 3,
    corr_threshold_promo: float = 0.45,
    corr_threshold_pure: float = 0.70,
) -> pd.DataFrame:
    df = force_itemcode_str(df)
    df = df.copy().sort_values(["ItemCode", "Year", "Month_Number"])

    out = []

    for item, g in df.groupby("ItemCode"):
        g = g.copy()

        if "Clean_Demand" not in g.columns:
            continue

        bonus_months = g[g["Bonus_Flag"] == 1]
        non_bonus_months = g[g["Bonus_Flag"] == 0]

        bonus_count = len(bonus_months)
        total_count = len(g)
        bonus_freq = bonus_count / total_count if total_count > 0 else 0.0

        bonus_avg = float(bonus_months["Clean_Demand"].mean()) if bonus_count > 0 else 0.0
        non_bonus_avg = float(non_bonus_months["Clean_Demand"].mean()) if len(non_bonus_months) > 0 else 0.0

        bonus_std = float(bonus_months["Clean_Demand"].std()) if bonus_count > 1 else 0.0
        non_bonus_std = float(non_bonus_months["Clean_Demand"].std()) if len(non_bonus_months) > 1 else 0.0

        if non_bonus_avg < 5:
            uplift_ratio = 1.0
        else:
            uplift_ratio = bonus_avg / max(non_bonus_avg, 1.0)

        uplift_ratio = float(np.clip(uplift_ratio, 0, 5))

        if g["Bonus_Flag"].nunique() > 1 and g["Clean_Demand"].nunique() > 1:
            corr = g["Bonus_Flag"].corr(g["Clean_Demand"])
            corr = 0.0 if pd.isna(corr) else float(corr)
        else:
            corr = 0.0

        total_demand = float(g["Clean_Demand"].sum())
        bonus_demand_share = float(
            bonus_months["Clean_Demand"].sum() / total_demand
        ) if total_demand > 0 else 0.0

        if (
            bonus_count >= min_bonus_months
            and corr >= corr_threshold_pure
            and uplift_ratio >= 2.0
            and bonus_demand_share >= 0.65
        ):
            promo_profile = "PURE_PROMO"
        elif (
            bonus_count >= min_bonus_months
            and corr >= corr_threshold_promo
            and uplift_ratio >= 1.25
        ):
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
            "Bonus_Std_Demand": bonus_std,
            "NonBonus_Std_Demand": non_bonus_std,
            "Bonus_Demand_Share": bonus_demand_share,
            "Bonus_Month_Count": bonus_count,
        })

    out_df = pd.DataFrame(out)
    out_df = force_itemcode_str(out_df)
    return out_df


def merge_promo_profile(df: pd.DataFrame, promo_profile_df: pd.DataFrame) -> pd.DataFrame:
    df = force_itemcode_str(df)
    promo_profile_df = force_itemcode_str(promo_profile_df)
    df = df.copy()

    keep_cols = [
        "ItemCode",
        "Promo_Profile",
        "Bonus_Corr",
        "Bonus_Frequency_Profile",
        "Bonus_Avg_Demand",
        "NonBonus_Avg_Demand",
        "Bonus_Uplift_Ratio_Profile",
        "Bonus_Std_Demand",
        "NonBonus_Std_Demand",
        "Bonus_Demand_Share",
        "Bonus_Month_Count",
    ]

    df = df.drop(columns=[c for c in keep_cols if c != "ItemCode"], errors="ignore")
    df = df.merge(promo_profile_df[keep_cols], on="ItemCode", how="left")
    df = force_itemcode_str(df)

    df["Promo_Profile"] = df["Promo_Profile"].fillna("NORMAL")

    for c in [
        "Bonus_Corr",
        "Bonus_Frequency_Profile",
        "Bonus_Avg_Demand",
        "NonBonus_Avg_Demand",
        "Bonus_Uplift_Ratio_Profile",
        "Bonus_Std_Demand",
        "NonBonus_Std_Demand",
        "Bonus_Demand_Share",
        "Bonus_Month_Count",
    ]:
        df[c] = df[c].fillna(0)

    return df


# ============================================================
# MODE HELPERS
# ============================================================
def _normalize_processing_mode(mode: str) -> str:
    mode = str(mode or "actual").strip().lower()
    if mode not in {"actual", "snapshot", "live"}:
        return "actual"
    if mode == "live":
        return "snapshot"
    return mode


def _standardize_snapshot_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert open snapshot schema to the same working schema expected by
    downstream feature logic.

    Snapshot source example:
    - Snapshot_Year / Snapshot_Month
    - MTD_Secondary_Sales_Qty
    - MTD_Free_Qty
    - Available_Primary_Inventory_Qty_Current
    - Distributor_Inventory_Qty_Current
    - Blocked_Stock_Qty_Current
    - Inspection_Stock_Qty_Current
    """
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

    # Ensure stock total exists for shared feature logic
    if "Total_Primary_Inventory_Qty" not in df.columns:
        available = pd.to_numeric(df.get("Available_Primary_Inventory_Qty", 0), errors="coerce").fillna(0)
        blocked = pd.to_numeric(df.get("Blocked_Stock_Qty", 0), errors="coerce").fillna(0)
        insp = pd.to_numeric(df.get("Inspection_Stock_Qty", 0), errors="coerce").fillna(0)
        df["Total_Primary_Inventory_Qty"] = available + blocked + insp

    # Primary sales often unavailable in snapshot
    if "Primary_Sales_Qty" not in df.columns:
        df["Primary_Sales_Qty"] = 0

    # Bonus/Supply flags may not exist yet in snapshot source
    if "Bonus_Flag" not in df.columns:
        free_qty = pd.to_numeric(df.get("Free_Qty", 0), errors="coerce").fillna(0)
        df["Bonus_Flag"] = (free_qty > 0).astype(int)

    if "Supply_Constraint_Flag" not in df.columns:
        avail_qty = pd.to_numeric(df.get("Available_Primary_Inventory_Qty", 0), errors="coerce").fillna(0)
        df["Supply_Constraint_Flag"] = (avail_qty <= 0).astype(int)

    return df


def _prepare_raw_by_mode(raw_df: pd.DataFrame, mode: str) -> pd.DataFrame:
    """
    Shared pre-entry preparation before feature engineering.
    """
    mode = _normalize_processing_mode(mode)
    df = raw_df.copy()
    df = force_itemcode_str(df)

    if mode == "snapshot":
        df = _standardize_snapshot_columns(df)

    # Remove incomplete future month only for actual closed history
    if mode == "actual" and {"Year", "Month_Number"}.issubset(df.columns):
        df = df[~((df["Year"] == 2026) & (df["Month_Number"] == 2))].copy()

    return df


# ============================================================
# MAIN PREPROCESS FUNCTION
# ============================================================
def build_processed_data_from_raw(raw_df: pd.DataFrame, mode: str = "actual") -> pd.DataFrame:
    mode = _normalize_processing_mode(mode)
    df = _prepare_raw_by_mode(raw_df, mode)

    # ========================================================
    # 0) MODE TAG
    # ========================================================
    df["Data_Mode"] = mode
    df["Month_Status"] = np.where(mode == "actual", "CLOSED", "OPEN")
    
    # ========================================================
    # 1) BASIC CLEANING
    # ========================================================
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

    if "Year" not in df.columns:
        raise ValueError("Input data must contain 'Year' or 'Snapshot_Year'.")

    if "Month_Number" not in df.columns:
        raise ValueError("Input data must contain 'Month_Number' or 'Snapshot_Month'.")

    df = df.sort_values(["ItemCode", "Year", "Month_Number"]).reset_index(drop=True)

    # ========================================================
    # 2) OBSERVED DEMAND FEATURES (PAST ONLY)
    # ========================================================
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

    # ========================================================
    # 3) BONUS PATTERN FEATURES
    # ========================================================
    bonus_df = detect_recurring_bonus_skus(df)
    df = df.drop(columns=[
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
    ], errors="ignore")

    df = df.merge(bonus_df, on="ItemCode", how="left")

    for c in [
        "Recurring_Bonus_SKU",
        "Bonus_Cycle_Length",
        "Avg_Bonus_Gap",
        "Bonus_Frequency_All",
    ]:
        df[c] = df[c].fillna(0)

    df["Avg_Bonus_Uplift"] = df["Avg_Bonus_Uplift"].fillna(1.0)

    df = add_bonus_cycle_features(df)

    # ========================================================
    # 4) STOCK FEATURES
    # ========================================================
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

    ratio_cap = df["Primary_to_Distributor_Ratio"].replace([np.inf, -np.inf], np.nan).dropna()
    if len(ratio_cap):
        df["Primary_to_Distributor_Ratio"] = df["Primary_to_Distributor_Ratio"].clip(
            upper=ratio_cap.quantile(0.99)
        )

    dist_change_cap = df["Distributor_Inv_Change"].replace([np.inf, -np.inf], np.nan).dropna()
    if len(dist_change_cap):
        df["Distributor_Inv_Change"] = df["Distributor_Inv_Change"].clip(
            upper=dist_change_cap.quantile(0.99)
        )

    prim_change_cap = df["Primary_Inv_Change"].replace([np.inf, -np.inf], np.nan).dropna()
    if len(prim_change_cap):
        df["Primary_Inv_Change"] = df["Primary_Inv_Change"].clip(
            upper=prim_change_cap.quantile(0.99)
        )

    # ========================================================
    # 5) EFFECTIVE DEMAND
    # ========================================================
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

    # ========================================================
    # 6) CLEAN DEMAND (DEBIAS)
    # ========================================================
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

    # ========================================================
    # 7) PROMO INTENSITY FEATURES
    # ========================================================
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

    # ========================================================
    # 8) BASIC FEATURES FOR INFERENCE
    # ========================================================
    grp = df.groupby("ItemCode")

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

    # ========================================================
    # 9) TIME FEATURES
    # ========================================================
    df["Month_Sin"] = np.sin(2 * np.pi * df["Month_Number"] / 12)
    df["Month_Cos"] = np.cos(2 * np.pi * df["Month_Number"] / 12)

    quarter = ((df["Month_Number"] - 1) // 3) + 1
    df["Quarter_Sin"] = np.sin(2 * np.pi * quarter / 4)
    df["Quarter_Cos"] = np.cos(2 * np.pi * quarter / 4)

    # ========================================================
    # 10) ZERO / STOCK / SUPPLY HISTORY FEATURES
    # ========================================================
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
        1, 0
    )

    df["Post_Bonus_Month_Flag"] = grp["Bonus_Flag"].shift(1).fillna(0)

    df["Recurring_Bonus_Month"] = np.where(
        (df["Recurring_Bonus_SKU"] == 1) &
        (
            (df["Bonus_Flag"] == 1) |
            (
                (df["Bonus_Cycle_Length"] > 0) &
                (np.abs(df["Months_Since_Last_Bonus"] - df["Bonus_Cycle_Length"]) <= 1)
            )
        ),
        1, 0
    )

    safe_mean = np.maximum(df["Rolling3M_Mean"].fillna(0), 1.0)
    df["Realized_Uplift"] = (df["Clean_Demand"] / safe_mean).clip(0, 6)

    df["Bonus_Demand_Only"] = np.where(
        df["Bonus_Flag"] == 1,
        df["Clean_Demand"],
        np.nan
    )

    grp2 = df.groupby("ItemCode")
    df["Promo_Uplift_Lag1"] = grp2["Realized_Uplift"].shift(1).fillna(1.0)
    df["Promo_Uplift_Lag2"] = grp2["Realized_Uplift"].shift(2).fillna(1.0)
    df["Promo_Uplift_6M"] = grp2["Realized_Uplift"].transform(
        lambda x: x.rolling(6, min_periods=1).mean().shift(1)
    ).fillna(1.0)
    df["Last_Bonus_Demand"] = grp2["Bonus_Demand_Only"].transform(
        lambda x: x.shift(1).ffill()
    ).fillna(0)

    df["Promo_Uplift_Lag1"] = df["Promo_Uplift_Lag1"].clip(0.5, 3.0)
    df["Promo_Uplift_Lag2"] = df["Promo_Uplift_Lag2"].clip(0.5, 3.0)
    df["Promo_Uplift_6M"] = df["Promo_Uplift_6M"].clip(0.5, 2.5)

    df["Bonus_Sin"] = df["Bonus_Flag"] * np.sin(2 * np.pi * df["Month_Number"] / 12)
    df["Bonus_Cos"] = df["Bonus_Flag"] * np.cos(2 * np.pi * df["Month_Number"] / 12)

    df = df.drop(columns=["Bonus_Demand_Only"], errors="ignore")

    # ========================================================
    # 11) HISTORY SEGMENT
    # ========================================================
    df = add_history_segment(df)

    # ========================================================
    # 12) SKU PROFILE FEATURES
    # ========================================================
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
    df = df.merge(sku_profile, on="ItemCode", how="left")

    # ========================================================
    # 13) PROMO PROFILE FEATURES
    # ========================================================
    promo_profile_df = build_promo_profile(df)
    df = merge_promo_profile(df, promo_profile_df)

    # ========================================================
    # 14) FINAL SANITIZATION
    # ========================================================
    df = df.replace([np.inf, -np.inf], np.nan)

    fill_zero_cols = [c for c in df.columns if c not in ["ItemCode", "Promo_Profile", "History_Segment"]]
    df[fill_zero_cols] = df[fill_zero_cols].fillna(0)

    df["Promo_Profile"] = df["Promo_Profile"].fillna("NORMAL")
    df["History_Segment"] = df["History_Segment"].fillna("SHORT")

    # ========================================================
    # 15) SNAPSHOT / ACTUAL METADATA
    # ========================================================
    if mode == "snapshot":
        if "Snapshot_Date" not in df.columns:
            df["Snapshot_Date"] = pd.NaT
    else:
        if "Snapshot_Date" not in df.columns:
            df["Snapshot_Date"] = pd.NaT

    # IMPORTANT: DO NOT DROP ROWS
    return df