# backend/engines/demand_forecast_engine.py--> 🎯 Model prediction

import numpy as np
import pandas as pd
import torch

from datetime import datetime
from typing import Iterable, Optional, Set

from services.artifact_service import artifact_service

# ============================================================
# BASIC HELPERS
# ============================================================
def normalize_itemcode(v):
    return str(v).strip().replace(".0", "")


def _safe_num(v, default=0.0):
    try:
        if pd.isna(v):
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _next_year_month(year, month_number):
    year = int(year)
    month_number = int(month_number)
    if month_number == 12:
        return year + 1, 1
    return year, month_number + 1


def _choose_segment_by_history(sku_df: pd.DataFrame) -> str:
    hist_len = sku_df[["Year", "Month_Number"]].drop_duplicates().shape[0]
    if hist_len >= 18:
        return "LONG"
    if hist_len >= 10:
        return "MEDIUM"
    return "SHORT"


# ============================================================
# RESIDUAL STRENGTH — FIXED: ported from notebook
# Asymmetrically amplifies positive residuals and clips to anchor range.
# Applied to LONG tree models only (not GRU — GRU has log-space clipping).
# ============================================================
def _apply_residual_strength(row: dict, pred_residual: float, segment: str = "LONG") -> float:
    """
    Mirror of notebook apply_residual_strength().
    Positive residuals get a stronger push (uplift more aggressively);
    negative residuals are dampened to avoid over-correcting downward.
    Output is clipped to a ±anchor range to prevent runaway predictions.
    """
    baseline = float(row.get("Residual_Baseline", row.get("Rolling3M_Mean", 0)) or 0)
    rolling3  = float(row.get("Rolling3M_Mean", 0) or 0)
    lag1      = float(row.get("Lag1", 0) or 0)
    sku_mean  = float(row.get("SKU_Mean_Demand", 0) or 0)

    anchor = max(baseline, rolling3, lag1, sku_mean, 1.0)

    if segment == "LONG":
        pos_strength = 1.25
        neg_strength = 1.05
    elif segment == "MEDIUM":
        pos_strength = 1.15
        neg_strength = 1.00
    else:
        pos_strength = 1.00
        neg_strength = 1.00

    residual = float(pred_residual)
    adjusted = residual * pos_strength if residual >= 0 else residual * neg_strength

    lower = -0.45 * anchor
    upper =  1.20 * anchor

    return float(np.clip(adjusted, lower, upper))


# ============================================================
# FEATURE REBUILD FOR NEXT-MONTH ROW
# ============================================================
def _append_next_month_stub(sku_df: pd.DataFrame) -> pd.DataFrame:
    sku_df = sku_df.sort_values(["Year", "Month_Number"]).copy().reset_index(drop=True)
    last_row = sku_df.iloc[-1].copy()

    next_year, next_month = _next_year_month(last_row["Year"], last_row["Month_Number"])

    row = last_row.copy()
    row["Year"] = next_year
    row["Month_Number"] = next_month

    zero_future_cols = [
        "Secondary_Sales_Qty",
        "Observed_Demand",
        "Effective_Demand",
        "Free_Qty",
        "Primary_Sales_Qty",
        "Bonus_Flag",
        "Bonus_Shock",
        "Supply_Shock",
        "Is_Zero",
        "Uplift_vs_Baseline",
        "Z_Score_Obs",
    ]
    for c in zero_future_cols:
        if c in row.index:
            row[c] = 0

    if "Clean_Demand" in row.index:
        row["Clean_Demand"] = 0

    return pd.concat([sku_df, pd.DataFrame([row])], ignore_index=True)


def _add_next_structural_demand_state(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["ItemCode", "Year", "Month_Number"]).copy()

    roll3 = df.groupby("ItemCode")["Clean_Demand"].transform(
        lambda x: x.rolling(3, min_periods=1).mean().shift(1)
    )
    roll6 = df.groupby("ItemCode")["Clean_Demand"].transform(
        lambda x: x.rolling(6, min_periods=1).mean().shift(1)
    )

    growth_ratio = roll3 / (roll6 + 1)

    zero_rate_6m = df.groupby("ItemCode")["Clean_Demand"].transform(
        lambda x: x.rolling(6, min_periods=1).apply(lambda y: (y == 0).mean(), raw=False).shift(1)
    )

    df["Demand_State"] = np.where(
        zero_rate_6m >= 0.50,
        "DYING_OR_INTERMITTENT",
        np.where(
            growth_ratio >= 1.30,
            "GROWING",
            np.where(growth_ratio <= 0.70, "DECLINING", "MATURE")
        )
    )

    state_map = {
        "MATURE": 0,
        "GROWING": 1,
        "DECLINING": 2,
        "DYING_OR_INTERMITTENT": 3,
    }

    df["Demand_State_Encoded"] = df["Demand_State"].map(state_map).fillna(0).astype(int)
    return df


def _classify_demand_regime(row):
    lag1 = _safe_num(row.get("Lag1", 0))
    roll3 = _safe_num(row.get("Rolling3M_Mean", 0))
    roll6 = _safe_num(row.get("Rolling6M_Mean", roll3))
    bonus = int(_safe_num(row.get("Bonus_Flag", 0)))
    supply = int(_safe_num(row.get("Supply_Constraint_Flag", 0)))
    expected_bonus = int(_safe_num(row.get("Expected_Bonus_Month", 0)))
    post_bonus = int(_safe_num(row.get("Post_Bonus_Month", 0)))
    demand_state = str(row.get("Demand_State", "MATURE"))

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


def _add_medium_profile_for_next_row(next_row: pd.DataFrame, artifact: dict) -> pd.DataFrame:
    df = next_row.copy()

    profile = artifact.get("medium_profile_df")
    if profile is None or not isinstance(profile, pd.DataFrame) or profile.empty:
        df["Medium_SKU_Type"] = "STABLE"
        df["Medium_Subgroup"] = "STABLE"
        df["Medium_SKU_Type_Encoded"] = 0
        for c in [
            "Medium_Bonus_Frequency", "Medium_Bonus_Demand_Share",
            "Medium_CV", "Medium_ZeroRate", "Medium_Supply_Rate",
            "Medium_Trend_Slope"
        ]:
            df[c] = 0
        return df

    profile = profile.copy()
    profile["ItemCode"] = profile["ItemCode"].astype(str).str.replace(r"\.0$", "", regex=True)
    df["ItemCode"] = df["ItemCode"].astype(str).str.replace(r"\.0$", "", regex=True)

    keep_cols = [
        "ItemCode", "Medium_SKU_Type", "Medium_Subgroup",
        "Medium_Bonus_Frequency", "Medium_Bonus_Demand_Share",
        "Medium_CV", "Medium_ZeroRate", "Medium_Supply_Rate",
        "Medium_Trend_Slope"
    ]

    df = df.drop(columns=[c for c in keep_cols if c != "ItemCode"], errors="ignore")
    df = df.merge(profile[keep_cols], on="ItemCode", how="left")

    df["Medium_SKU_Type"] = df["Medium_SKU_Type"].fillna("STABLE")
    df["Medium_Subgroup"] = df["Medium_Subgroup"].fillna("STABLE")

    type_map = {
        "STABLE": 0,
        "PROMO_HEAVY": 1,
        "TRENDING": 2,
        "SUPPLY_AFFECTED": 3,
        "INTERMITTENT": 4,
    }

    df["Medium_SKU_Type_Encoded"] = df["Medium_SKU_Type"].map(type_map).fillna(0).astype(int)

    for c in [
        "Medium_Bonus_Frequency", "Medium_Bonus_Demand_Share",
        "Medium_CV", "Medium_ZeroRate", "Medium_Supply_Rate",
        "Medium_Trend_Slope"
    ]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    return df


def _rebuild_next_month_features(sku_df: pd.DataFrame) -> pd.DataFrame:
    df = _append_next_month_stub(sku_df)
    df = df.sort_values(["Year", "Month_Number"]).copy().reset_index(drop=True)

    grp = df.groupby("ItemCode")

    df["Month_Sin"] = np.sin(2 * np.pi * df["Month_Number"] / 12)
    df["Month_Cos"] = np.cos(2 * np.pi * df["Month_Number"] / 12)

    quarter = ((df["Month_Number"] - 1) // 3) + 1
    df["Quarter_Sin"] = np.sin(2 * np.pi * quarter / 4)
    df["Quarter_Cos"] = np.cos(2 * np.pi * quarter / 4)

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

    df["Is_Zero"] = (df["Clean_Demand"] == 0).astype(int)
    df["ZeroRate_6M"] = grp["Is_Zero"].transform(
        lambda x: x.rolling(6, min_periods=1).mean().shift(1)
    ).fillna(0)

    if all(c in df.columns for c in ["Total_Primary_Inventory_Qty", "Blocked_Stock_Qty", "Inspection_Stock_Qty"]):
        df["Net_Available_Stock"] = (
            df["Total_Primary_Inventory_Qty"]
            - df["Blocked_Stock_Qty"]
            - df["Inspection_Stock_Qty"]
        ).clip(lower=0)

    if "Available_Primary_Inventory_Qty" in df.columns:
        df["Inventory_Pressure"] = np.where(
            df["Lag1"].fillna(0) <= 0,
            0,
            df["Available_Primary_Inventory_Qty"] / (df["Lag1"] + 1)
        )

    if "Net_Available_Stock" in df.columns:
        df["Stock_Cover_Months"] = np.where(
            df["Rolling3M_Mean"].fillna(0) <= 0,
            0,
            df["Net_Available_Stock"] / (df["Rolling3M_Mean"] + 1)
        )
        df["Primary_Stock_Cover"] = np.where(
            df["Rolling3M_Mean"].fillna(0) <= 0,
            0,
            df["Net_Available_Stock"] / (df["Rolling3M_Mean"] + 1)
        )

    if "Distributor_Inventory_Qty" in df.columns:
        df["Distributor_Stock_Cover"] = np.where(
            df["Rolling3M_Mean"].fillna(0) <= 0,
            0,
            df["Distributor_Inventory_Qty"] / (df["Rolling3M_Mean"] + 1)
        )

    if all(c in df.columns for c in ["Net_Available_Stock", "Distributor_Inventory_Qty"]):
        df["Primary_to_Distributor_Ratio"] = np.where(
            df["Distributor_Inventory_Qty"] <= 0,
            0,
            df["Net_Available_Stock"] / (df["Distributor_Inventory_Qty"] + 1)
        )

    if all(c in df.columns for c in ["Net_Available_Stock", "Rolling3M_Mean"]):
        df["Demand_to_Stock_Ratio"] = np.where(
            df["Net_Available_Stock"] <= 0,
            0,
            df["Rolling3M_Mean"] / (df["Net_Available_Stock"] + 1)
        )

    if all(c in df.columns for c in ["Blocked_Stock_Qty", "Total_Primary_Inventory_Qty"]):
        df["Blocked_Stock_Ratio"] = np.where(
            df["Total_Primary_Inventory_Qty"] <= 0,
            0,
            df["Blocked_Stock_Qty"] / (df["Total_Primary_Inventory_Qty"] + 1)
        )

    if all(c in df.columns for c in ["Inspection_Stock_Qty", "Total_Primary_Inventory_Qty"]):
        df["Inspection_Stock_Ratio"] = np.where(
            df["Total_Primary_Inventory_Qty"] <= 0,
            0,
            df["Inspection_Stock_Qty"] / (df["Total_Primary_Inventory_Qty"] + 1)
        )

    if "Net_Available_Stock" in df.columns:
        df["Primary_Inv_Change"] = grp["Net_Available_Stock"].diff().fillna(0)

    if "Distributor_Inventory_Qty" in df.columns:
        df["Distributor_Inv_Change"] = grp["Distributor_Inventory_Qty"].diff().fillna(0)

    if "Supply_Constraint_Flag" in df.columns:
        df["Supply_Constraint_Lag1"] = grp["Supply_Constraint_Flag"].shift(1).fillna(0)
        df["Supply_Constraint_Lag2"] = grp["Supply_Constraint_Flag"].shift(2).fillna(0)

    if "Primary_Stock_Cover" in df.columns:
        df["Primary_Stock_Cover_Lag1"] = grp["Primary_Stock_Cover"].shift(1).fillna(0)

    if "Distributor_Stock_Cover" in df.columns:
        df["Distributor_Stock_Cover_Lag1"] = grp["Distributor_Stock_Cover"].shift(1).fillna(0)

    df["Current_Total_Usable_Stock"] = (
        df.get("Available_Primary_Inventory_Qty", 0) +
        df.get("Distributor_Inventory_Qty", 0)
    )

    df["Current_Stock_Cover"] = (
        df["Current_Total_Usable_Stock"] /
        (df["Rolling3M_Mean"].fillna(0) + 1)
    )

    df["Current_Stockout_Risk"] = np.where(df["Current_Stock_Cover"] < 0.5, 1, 0)

    if "Distributor_Buffer_Flag" not in df.columns:
        df["Distributor_Buffer_Flag"] = 0

    if "Free_Qty" in df.columns:
        df["Free_Qty_Lag1"] = grp["Free_Qty"].shift(1).fillna(0)
        df["Free_Qty_Rolling3"] = grp["Free_Qty"].transform(
            lambda x: x.rolling(3, min_periods=1).mean().shift(1)
        ).fillna(0)

    if all(c in df.columns for c in ["Free_Qty", "Primary_Sales_Qty"]):
        df["Free_Ratio"] = np.where(
            df["Primary_Sales_Qty"] <= 0,
            0,
            df["Free_Qty"] / (df["Primary_Sales_Qty"] + 1)
        )
        df["Free_Ratio_Lag1"] = grp["Free_Ratio"].shift(1).fillna(0)

    if all(c in df.columns for c in ["Free_Qty_Rolling3", "Rolling3M_Mean"]):
        df["Promo_Intensity_History"] = np.where(
            df["Rolling3M_Mean"].fillna(0) <= 0,
            0,
            df["Free_Qty_Rolling3"] / (df["Rolling3M_Mean"] + 1)
        )

    if "Bonus_Flag" in df.columns:
        df["Bonus_Flag_Lag1"] = grp["Bonus_Flag"].shift(1).fillna(0)
        df["Bonus_Flag_Lag2"] = grp["Bonus_Flag"].shift(2).fillna(0)
        df["Bonus_Flag_Lag3"] = grp["Bonus_Flag"].shift(3).fillna(0)

        df["Bonus_Last_Month"] = df["Bonus_Flag_Lag1"].fillna(0)
        df["Bonus_2M_Ago"]     = df["Bonus_Flag_Lag2"].fillna(0)

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

        df["Bonus_Frequency_12M"] = grp["Bonus_Flag"].transform(
            lambda x: x.rolling(12, min_periods=1).mean().shift(1)
        ).fillna(0)

        months_since = []
        for _, g in df.groupby("ItemCode", sort=False):
            g = g.sort_values(["Year", "Month_Number"]).copy()
            g["Time_Index"] = g["Year"].astype(int) * 12 + g["Month_Number"].astype(int)
            bonus_time_idx = g.loc[g["Bonus_Flag"] == 1, "Time_Index"].tolist()

            out = []
            for current_t in g["Time_Index"]:
                past_bonus = [t for t in bonus_time_idx if t < current_t]
                out.append(999 if len(past_bonus) == 0 else current_t - past_bonus[-1])
            months_since.extend(out)

        df["Months_Since_Last_Bonus"] = months_since

        if all(c in df.columns for c in ["Recurring_Bonus_SKU", "Bonus_Cycle_Length"]):
            df["Expected_Bonus_Month"] = np.where(
                (df["Recurring_Bonus_SKU"] == 1) &
                (df["Bonus_Cycle_Length"] > 0) &
                (np.abs(df["Months_Since_Last_Bonus"] - df["Bonus_Cycle_Length"]) <= 1),
                1, 0
            )

            df["Expected_Bonus_NextMonth"] = np.where(
                (df["Recurring_Bonus_SKU"] == 1) &
                (df["Bonus_Cycle_Length"] > 0) &
                (np.abs((df["Months_Since_Last_Bonus"] + 1) - df["Bonus_Cycle_Length"]) <= 1),
                1, 0
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
                1, 0
            )

        df["Post_Bonus_Month_Flag"] = grp["Bonus_Flag"].shift(1).fillna(0)

    safe_mean = np.maximum(df["Rolling3M_Mean"].fillna(0), 1.0)
    df["Realized_Uplift"] = (df["Clean_Demand"] / safe_mean).clip(0, 6)

    df["Bonus_Demand_Only"] = np.where(
        df.get("Bonus_Flag", 0) == 1,
        df["Clean_Demand"],
        np.nan
    )

    grp2 = df.groupby("ItemCode")
    df["Promo_Uplift_Lag1"] = grp2["Realized_Uplift"].shift(1).fillna(1.0)
    df["Promo_Uplift_Lag2"] = grp2["Realized_Uplift"].shift(2).fillna(1.0)
    df["Promo_Uplift_6M"]   = grp2["Realized_Uplift"].transform(
        lambda x: x.rolling(6, min_periods=1).mean().shift(1)
    ).fillna(1.0)

    df["Last_Bonus_Demand"] = grp2["Bonus_Demand_Only"].transform(
        lambda x: x.shift(1).ffill()
    ).fillna(0)

    df["Promo_Uplift_Lag1"] = df["Promo_Uplift_Lag1"].clip(0.5, 3.0)
    df["Promo_Uplift_Lag2"] = df["Promo_Uplift_Lag2"].clip(0.5, 3.0)
    df["Promo_Uplift_6M"]   = df["Promo_Uplift_6M"].clip(0.5, 2.5)

    if "Bonus_Flag" in df.columns:
        df["Bonus_Sin"] = df["Bonus_Flag"] * np.sin(2 * np.pi * df["Month_Number"] / 12)
        df["Bonus_Cos"] = df["Bonus_Flag"] * np.cos(2 * np.pi * df["Month_Number"] / 12)

    # FIXED: compute Residual_Baseline with correct formula matching notebook
    # Rolling3M_Mean.fillna(Lag1).fillna(0) — not just Rolling3M_Mean alone
    df["Residual_Baseline"] = df["Rolling3M_Mean"].fillna(df["Lag1"]).fillna(0)

    # FIXED: compute Demand_State BEFORE Demand_Regime so the regime
    # classifier reads a freshly-computed state, not a stale carryforward value.
    df = _add_next_structural_demand_state(df)

    df["Demand_Regime"] = df.apply(_classify_demand_regime, axis=1)

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

    df["Demand_Regime_Encoded"] = df["Demand_Regime"].map(regime_map).fillna(0).astype(int)

    df = df.drop(columns=["Bonus_Demand_Only"], errors="ignore")
    df = df.replace([np.inf, -np.inf], np.nan).fillna(0)

    return df.tail(1).copy()


# ============================================================
# ABC CLASS HELPER
# FIXED: reads abc_map from artifact and applies to next_row
# ============================================================
def _apply_abc_class(next_row: pd.DataFrame, artifact: dict) -> pd.DataFrame:
    """
    Apply the ABC classification stored in the training artifact to the
    inference row.  Without this, ABC_Class is always 0 (fillna default),
    which degrades every tree split that depends on it.
    """
    abc_map = artifact.get("abc_map", {})
    if not abc_map:
        return next_row

    df = next_row.copy()
    item_code = normalize_itemcode(df.iloc[0]["ItemCode"])

    # abc_map keys may be int-encoded (after encode_itemcode) or original strings.
    # The original string form is stored in ItemCode_Original if present.
    item_key = normalize_itemcode(df.iloc[0].get("ItemCode_Original", item_code))

    # Try string key first, then fall back to the numeric-encoded key.
    abc_class = abc_map.get(item_key, abc_map.get(item_code, 2))

    try:
        abc_class = int(abc_class)
    except (TypeError, ValueError):
        abc_class = 2  # C-class default

    df["ABC_Class"] = abc_class
    return df


# ============================================================
# TABULAR PREDICTION
# ============================================================
def _prepare_tabular_features(next_row: pd.DataFrame, artifact: dict) -> pd.DataFrame:
    df = next_row.copy()

    # FIXED: apply clip_caps AFTER medium profile merge (caller must ensure
    # medium profile is merged before calling this function, then re-apply caps)
    caps = artifact.get("clip_caps", {})
    for c, cap in caps.items():
        if c in df.columns:
            df[c] = df[c].clip(upper=cap)

    feature_cols = artifact.get("feature_cols")
    if feature_cols is None:
        raise ValueError("Invalid artifact: missing feature_cols")

    for c in feature_cols:
        if c not in df.columns:
            df[c] = 0

    if "ItemCode" in feature_cols:
        categories = artifact.get("itemcode_categories", [])
        # FIXED: guard for None or empty categories to avoid KeyError/AttributeError
        if categories is not None and len(categories) > 0:
            categories = [str(x) for x in categories]
            code_map = {k: i for i, k in enumerate(categories)}
            unk_code = len(code_map)
            df["ItemCode"] = df["ItemCode"].astype(str).map(code_map).fillna(unk_code).astype(int)
        # If categories is empty/None, leave ItemCode as-is (models without
        # ItemCode in their feature_cols are unaffected).

    X = df[feature_cols].replace([np.inf, -np.inf], np.nan).fillna(0)
    return X


def _predict_tabular(next_row: pd.DataFrame, artifact: dict, segment: str = "LONG") -> float:
    model = artifact.get("model")
    feature_cols = artifact.get("feature_cols")

    if model is None or feature_cols is None:
        raise ValueError("Invalid artifact: missing model or feature_cols")

    # FIXED: apply ABC class before preparing features
    next_row = _apply_abc_class(next_row, artifact)

    X = _prepare_tabular_features(next_row, artifact)
    pred_raw = float(model.predict(X)[0])

    target_mode = str(artifact.get("target_mode", "residual")).lower()

    if target_mode == "residual":
        baseline_col = artifact.get("baseline_col", "Residual_Baseline")
        baseline_val = _safe_num(
            next_row.iloc[0].get(baseline_col,
                next_row.iloc[0].get("Rolling3M_Mean", 0))
        )
        pred_residual = pred_raw
        row_dict = next_row.iloc[0].to_dict()
        pred_residual = _apply_residual_strength(row_dict, pred_residual, segment=segment)
        pred = baseline_val + pred_residual

        # MEDIUM blending: all MEDIUM SKUs (10–17 months) blend model with fallback rule
        # Mirrors notebook predict_residual_model_sku() hist_len < 18 branch
        if segment == "MEDIUM":
            hist_len = float(next_row.iloc[0].get("History_Length", 999) or 999)
            if hist_len < 18:
                rule_pred = _predict_long_fallback(next_row, "ROLLING3")
                if hist_len < 12:
                    pred = 0.4 * pred + 0.6 * rule_pred   # 10–11 months: heavy rule weight
                else:
                    pred = 0.7 * pred + 0.3 * rule_pred   # 12–17 months: lighter rule weight

    elif target_mode == "direct":
        pred = pred_raw
    else:
        pred = pred_raw

    return max(0.0, float(pred))


# ============================================================
# GRU PREDICTION
# ============================================================
def _predict_gru(sku_df: pd.DataFrame, next_row: pd.DataFrame, bundle: dict, scalers) -> float:
    model = bundle["model"]
    seq_features    = bundle["seq_features"]
    static_features = bundle["static_features"]
    seq_len         = int(bundle["seq_len"])
    item_to_idx     = bundle["item_to_idx"]

    # FIXED: read log_clip_value from the stored artifact rather than hardcoding 7.0
    log_clip_value = float(bundle.get("gru_log_clip_value", 7.0))

    item_code = normalize_itemcode(next_row.iloc[0]["ItemCode"])
    item_idx  = item_to_idx.get(item_code)

    if item_idx is None:
        raise ValueError(f"GRU item not found in item_to_idx: {item_code}")

    # FIXED: apply ABC class using the bundle's abc_map before building sequence
    abc_map = bundle.get("abc_map", {})
    item_key = normalize_itemcode(next_row.iloc[0].get("ItemCode_Original", item_code))
    abc_class = int(abc_map.get(item_key, abc_map.get(item_code, 2)))

    hist = sku_df.sort_values(["Year", "Month_Number"]).copy()
    temp = pd.concat([hist, next_row], ignore_index=True).sort_values(["Year", "Month_Number"]).reset_index(drop=True)

    if len(temp) < seq_len:
        raise ValueError(f"Not enough history for GRU sequence: need {seq_len}")

    seq_df = temp.tail(seq_len).copy()

    # FIXED: set ABC_Class on the full sequence slice using the stored abc_map
    seq_df["ABC_Class"] = abc_class

    # FIXED: apply clip_caps to the sequence history rows before scaling.
    # In the notebook, the full training df was clipped before sequences were
    # extracted. Without this, unclipped history values corrupt the scaled input.
    clip_caps = bundle.get("clip_caps", {})
    for c, cap in clip_caps.items():
        if c in seq_df.columns:
            seq_df[c] = seq_df[c].clip(upper=cap)

    for c in seq_features:
        if c not in seq_df.columns:
            seq_df[c] = 0

    for c in static_features:
        if c not in seq_df.columns:
            seq_df[c] = 0

    seq_vals = seq_df[seq_features].replace([np.inf, -np.inf], np.nan).fillna(0).values
    seq_vals = scalers.seq_scaler.transform(seq_vals)

    static_vals = (
        seq_df.iloc[-1][static_features]
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0)
        .values
        .reshape(1, -1)
    )
    static_vals = scalers.static_scaler.transform(static_vals)

    x_seq    = torch.tensor(seq_vals.reshape(1, seq_len, len(seq_features)), dtype=torch.float32)
    x_static = torch.tensor(static_vals, dtype=torch.float32)
    x_item   = torch.tensor([item_idx], dtype=torch.long)

    device = next(model.parameters()).device
    x_seq    = x_seq.to(device)
    x_static = x_static.to(device)
    x_item   = x_item.to(device)

    with torch.no_grad():
        pred_res_log = model(x_seq, x_static, x_item).cpu().numpy()[0]

    # FIXED: clip with the artifact's stored value, not hardcoded 7.0
    pred_res_log = float(np.clip(pred_res_log, -log_clip_value, log_clip_value))
    pred_residual = float(np.sign(pred_res_log) * np.expm1(np.abs(pred_res_log)))

    baseline_val = _safe_num(
        next_row.iloc[0].get("Residual_Baseline", next_row.iloc[0].get("Rolling3M_Mean", 0))
    )

    row_dict = next_row.iloc[0].to_dict()

    pred_residual = _apply_residual_strength(row_dict,pred_residual,segment="LONG")
    pred = max(0.0, baseline_val + pred_residual)
    
    return pred


# ============================================================
# FALLBACK / SHORT HELPERS
# ============================================================
def _predict_long_fallback(next_row: pd.DataFrame, fallback_type: str = "ROLLING3") -> float:
    row = next_row.iloc[0]

    lag1     = _safe_num(row.get("Lag1", row.get("Clean_Demand", 0)))
    lag2     = _safe_num(row.get("Lag2", lag1))
    rolling3 = _safe_num(row.get("Rolling3M_Mean", lag1))

    fallback_type = str(fallback_type or "ROLLING3").upper()

    if fallback_type == "MAX_LAG1_ROLL3":
        return max(lag1, rolling3, 0.0)

    if fallback_type == "MEAN_LAGS":
        return max(float(np.mean([lag1, lag2, rolling3])), 0.0)

    return max(rolling3, 0.0)


def _predict_short_rule(next_row: pd.DataFrame, rule_artifact: dict, route_type: str) -> float:
    """
    Full port of notebook short_rule_predict().
    route_type: "PROMO_RULE" | "NORMAL_RULE" | "BASE_RULE"
    """
    row = next_row.iloc[0]

    lag1     = _safe_num(row.get("Lag1", row.get("Clean_Demand", 0)))
    lag2     = _safe_num(row.get("Lag2", lag1))
    rolling3 = _safe_num(row.get("Rolling3M_Mean", lag1))
    sku_mean = _safe_num(row.get("SKU_Mean_Demand", 0))

    last_bonus_demand = _safe_num(row.get("Last_Bonus_Demand", 0))
    avg_bonus_uplift  = _safe_num(row.get("Avg_Bonus_Uplift", 1.0), 1.0)
    expected_bonus    = int(_safe_num(row.get("Expected_Bonus_Month", 0)))
    supply_flag       = int(_safe_num(row.get("Supply_Constraint_Flag", 0)))
    primary_stock     = _safe_num(row.get("Available_Primary_Inventory_Qty", 0))
    distributor_stock = _safe_num(row.get("Distributor_Inventory_Qty", 0))

    # hist_len from Short_History_Length (preprocess_engine sets this via build_short_sku_profile)
    hist_len = int(_safe_num(row.get("Short_History_Length", 0)))
    if hist_len == 0:
    # Fall back to History_Length (set by preprocess_engine.py add_history_segment)
        hist_len = int(_safe_num(row.get("History_Length", 0)))

    anchors = [x for x in [lag1, lag2, rolling3, sku_mean] if x > 0]

    if len(anchors) == 0:
        pred = 0.0
    elif hist_len <= 2:
        pred = float(np.mean(anchors))
    else:
        pred = max(
            0.50 * float(np.mean(anchors)) + 0.50 * float(np.max(anchors)),
            0.75 * sku_mean if sku_mean > 0 else 0.0
        )

    # SHORT_PROMO uplift — guard requires avg_bonus_uplift >= 1.15
    is_promo_route = route_type == "PROMO_RULE"
    if is_promo_route and expected_bonus == 1 and avg_bonus_uplift >= 1.15:
        promo_anchor = max(pred, last_bonus_demand, rolling3 * avg_bonus_uplift)
        pred = 0.50 * pred + 0.50 * promo_anchor
        pred = pred * min(max(avg_bonus_uplift, 1.0), 1.5)

    # Supply constraint cap
    if supply_flag == 1:
        safe_cap = max(lag1, rolling3, sku_mean, 0)
        pred = min(pred, 1.10 * safe_cap)

    # Zero stock haircut
    if (primary_stock + distributor_stock) <= 0:
        pred *= 0.90

    return max(pred, 0.0)


def _predict_short_from_artifact(next_row: pd.DataFrame, artifact: dict, default_route_type: str) -> float:
    if artifact is None:
        return _predict_short_rule(next_row, {}, default_route_type)

    rule_name = str(
        artifact.get("rule_name") or artifact.get("target_mode") or default_route_type
    ).upper()

    if "PROMO" in rule_name:
        route_type = "PROMO_RULE"
    elif "NORMAL" in rule_name:
        route_type = "NORMAL_RULE"
    else:
        route_type = default_route_type

    return _predict_short_rule(next_row, artifact, route_type)


# ============================================================
# PUBLIC ENTRY
# ============================================================
def forecast_one_sku(item_code: str, full_data: pd.DataFrame):
    item_code = normalize_itemcode(item_code)

    df = full_data.copy()
    df["ItemCode"] = df["ItemCode"].astype(str).str.replace(r"\.0$", "", regex=True)

    sku_df = df[df["ItemCode"] == item_code].copy()
    if sku_df.empty:
        return None

    sku_df   = sku_df.sort_values(["Year", "Month_Number"]).reset_index(drop=True)
    segment  = _choose_segment_by_history(sku_df)
    next_row = _rebuild_next_month_features(sku_df)

    used_model      = None
    fallback_used   = 0
    target_mode_used = "unknown"
    baseline_used   = float(next_row.iloc[0].get("Residual_Baseline", 0))
    routing_reason  = "OK"

    # ------------------------------------------------------------------
    # LONG segment
    # ------------------------------------------------------------------
    if segment == "LONG":
        route = artifact_service.get_long_routing_row(item_code)

        final_model  = "XGBOOST"
        best_model   = "XGBOOST"
        fallback_type = "ROLLING3"
        use_fallback = 0

        if route is not None:
            final_model   = str(route.get("Final_Model") or "XGBOOST").upper()
            best_model    = str(route.get("Best_Model") or final_model).upper()
            fallback_type = str(route.get("Fallback_Type") or "ROLLING3").upper()
            use_fallback  = int(_safe_num(route.get("Use_Fallback", 0)))

        chosen_model = final_model or best_model

        if chosen_model == "GRU":
            try:
                bundle, scalers = artifact_service.get_gru_long_bundle()
                if bundle is None or scalers is None:
                    raise ValueError("GRU long bundle not loaded")
                pred = _predict_gru(sku_df, next_row, bundle, scalers)
                used_model       = "GRU"
                target_mode_used = "residual"
            except Exception:
                pred             = _predict_long_fallback(next_row, fallback_type)
                used_model       = "FALLBACK"
                fallback_used    = 1
                target_mode_used = "rule"
                routing_reason   = "GRU_FAIL_TO_FALLBACK"

        elif chosen_model == "FALLBACK" or use_fallback == 1:
            pred             = _predict_long_fallback(next_row, fallback_type)
            used_model       = "FALLBACK"
            fallback_used    = 1
            target_mode_used = "rule"
            routing_reason   = "CHAMPION_FALLBACK"

        else:
            artifact = artifact_service.get_long_artifact(chosen_model)

            if artifact is None:
                pred             = _predict_long_fallback(next_row, fallback_type)
                used_model       = "FALLBACK"
                fallback_used    = 1
                target_mode_used = "rule"
                routing_reason   = "NO_LONG_ARTIFACT_TO_FALLBACK"
            else:
                try:
                    # segment="LONG" so _apply_residual_strength uses LONG multipliers
                    pred             = _predict_tabular(next_row, artifact, segment="LONG")
                    used_model       = chosen_model
                    target_mode_used = str(artifact.get("target_mode", "residual")).lower()
                except Exception:
                    pred             = _predict_long_fallback(next_row, fallback_type)
                    used_model       = "FALLBACK"
                    fallback_used    = 1
                    target_mode_used = "rule"
                    routing_reason   = "LONG_MODEL_FAIL_TO_FALLBACK"

    # ------------------------------------------------------------------
    # MEDIUM segment
    # ------------------------------------------------------------------
    elif segment == "MEDIUM":
        route = artifact_service.get_medium_routing_row(item_code)

        subgroup     = "STABLE"
        model_name   = "XGBOOST"
        # FIXED: read Use_Fallback and Fallback_Type from route, matching LONG branch
        use_fallback  = 0
        fallback_type = "ROLLING3"

        if route is not None:
            subgroup      = str(route.get("Medium_Subgroup", "STABLE")).upper()
            model_name    = str(route.get("Final_Model") or route.get("Best_Model") or "XGBOOST").upper()
            use_fallback  = int(_safe_num(route.get("Use_Fallback", 0)))
            fallback_type = str(route.get("Fallback_Type") or "ROLLING3").upper()

        if model_name == "FALLBACK" or use_fallback == 1:
            pred             = _predict_long_fallback(next_row, fallback_type)
            used_model       = f"{subgroup}::FALLBACK"
            fallback_used    = 1
            target_mode_used = "rule"
            routing_reason   = "MEDIUM_CHAMPION_FALLBACK"

        else:
            artifact = artifact_service.get_medium_artifact(subgroup, model_name)

            # FIXED: if medium_profile_df is None in artifact, read subgroup
            # from the champion map routing row instead of silently defaulting to STABLE
            if artifact is not None:
                if artifact.get("medium_profile_df") is None and route is not None:
                    # Inject the routing subgroup so _add_medium_profile_for_next_row
                    # can fall back gracefully with the correct subgroup label
                    next_row = next_row.copy()
                    next_row["Medium_Subgroup"] = subgroup
                    next_row["Medium_SKU_Type"] = subgroup
                    next_row["Medium_SKU_Type_Encoded"] = (
                        1 if subgroup == "PROMO_HEAVY" else 0
                    )
                else:
                    next_row = _add_medium_profile_for_next_row(next_row, artifact)

                # FIXED: re-apply clip_caps after medium profile columns are added
                # so Medium_CV, Medium_Trend_Slope etc. are clipped to training range
                caps = artifact.get("clip_caps", {})
                for c, cap in caps.items():
                    if c in next_row.columns:
                        next_row[c] = next_row[c].clip(upper=cap)

            if artifact is None:
                pred             = _predict_long_fallback(next_row, fallback_type)
                used_model       = f"{subgroup}::FALLBACK"
                fallback_used    = 1
                target_mode_used = "rule"
                routing_reason   = "NO_MEDIUM_ARTIFACT_TO_FALLBACK"
            else:
                try:
                    pred             = _predict_tabular(next_row, artifact, segment="MEDIUM")
                    used_model       = f"{subgroup}::{model_name}"
                    target_mode_used = str(artifact.get("target_mode", "residual")).lower()
                except Exception:
                    pred             = _predict_long_fallback(next_row, fallback_type)
                    used_model       = f"{subgroup}::FALLBACK"
                    fallback_used    = 1
                    target_mode_used = "rule"
                    routing_reason   = "MEDIUM_MODEL_FAIL_TO_FALLBACK"

    # ------------------------------------------------------------------
    # SHORT segment
    # ------------------------------------------------------------------
    else:
        short_profile = artifact_service.get_short_profile_df()
        if short_profile is not None and not short_profile.empty:
            short_profile = short_profile.copy()
            
            short_profile["ItemCode"] = (
                short_profile["ItemCode"]
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
            )

            next_row["ItemCode"] = (
                next_row["ItemCode"]
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
            )

            merge_cols = [
                "ItemCode",
                "Short_History_Length",
                "Short_SKU_Type",
                "Short_Mean_Demand",
                "Short_Bonus_Frequency",
                "Avg_Bonus_Uplift"
            ]

            available_cols = [c for c in merge_cols if c in short_profile.columns]

            next_row = next_row.drop(
                columns=[c for c in available_cols if c != "ItemCode"],
                errors="ignore"
            )

            next_row = next_row.merge(
                short_profile[available_cols],
                on="ItemCode",
                how="left"
            )

        short_sku_type = str(next_row.iloc[0].get("Short_SKU_Type", "")).upper()
        if short_sku_type == "SHORT_PROMO":
            promo_profile = "PROMO"
        else:
            promo_profile = "NORMAL"
        
        route_type    = "BASE_RULE"

        if int(_safe_num(next_row.iloc[0].get("Expected_Bonus_Month", 0))) == 1:
            route_type = "PROMO_RULE"
        elif int(_safe_num(next_row.iloc[0].get("Recurring_Bonus_SKU", 0))) == 0:
            route_type = "NORMAL_RULE"
        else:
            route_type = "BASE_RULE"

        short_artifact = artifact_service.get_short_artifact(promo_profile=promo_profile)

        pred             = _predict_short_from_artifact(next_row, short_artifact, route_type)
        used_model       = route_type
        fallback_used    = 1
        target_mode_used = "rule"

    return {
        "ItemCode":            item_code,
        "Segment":             segment,
        "Forecast_Year":       int(next_row.iloc[0]["Year"]),
        "Forecast_Month":      int(next_row.iloc[0]["Month_Number"]),
        "Forecast_Prediction": float(pred),
        "Used_Model":          used_model,
        "Fallback_Used":       int(fallback_used),
        "Target_Mode":         target_mode_used,
        "Baseline_Used":       float(baseline_used),
        "Routing_Reason":      routing_reason,
    }


# ============================================================
# TREND BASELINE — 📈 simple algorithmic forecast for budgeted
# SKUs that are NOT in the model (focus) SKU list
# ============================================================
# Business context
# ----------------
# The champion models cover only the focus SKU list. The budget
# ("All Budget 26 27 FY") contains additional SKUs with little/no sales
# history — forcing them into the model list would degrade model accuracy.
# For those SKUs this section produces a simple trend baseline (last-month /
# rolling averages from fact_monthly_closed), so the Insights page can show
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
        # NOTE: normalize_itemcode() in this engine is scalar — use the
        # vectorised equivalent for the history frame.
        hist["ItemCode"] = (
            hist["ItemCode"].astype(str).str.strip()
            .str.replace(r"\.0$", "", regex=True)
        )
        hist = hist.sort_values(["ItemCode", "Year", "Month_Number"])

    # Derive forecast month from fact history when not given
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

        qty, used_model, reason = _trend_forecast_for_sku(sales)

        sales_num = pd.to_numeric(sales, errors="coerce").fillna(0).clip(lower=0)
        rows.append({
            "Run_Date":        run_date,
            "Forecast_Month":  forecast_month_label,
            "ItemCode":        sku,
            "Forecast_Qty":    round(float(qty), 2),
            "Segment":         "TREND",
            "Used_Model":      used_model,
            "Fallback_Used":   1,
            "Target_Mode":     "rule",
            "Routing_Reason":  reason,
            "Forecast_Source": FORECAST_SOURCE_TAG,
            "Last_Month_Qty":  round(float(sales_num.iloc[-1]), 2) if len(sales_num) else 0.0,
            "L3M_Avg":         round(_safe_mean(sales_num.tail(TREND_WINDOW_SHORT)), 2),
            "L6M_Avg":         round(_safe_mean(sales_num.tail(TREND_WINDOW_LONG)), 2),
            "History_Months":  int(len(sales_num)),
        })

    return pd.DataFrame(rows, columns=out_cols)