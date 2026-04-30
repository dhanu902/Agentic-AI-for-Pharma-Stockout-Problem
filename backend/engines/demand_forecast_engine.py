# backend/engines/demand_forecast_engine.py--> 🎯 Model prediction

import numpy as np
import pandas as pd
import torch

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
    if hist_len >= 6:
        return "MEDIUM"
    return "SHORT"


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


def _rebuild_next_month_features(sku_df: pd.DataFrame) -> pd.DataFrame:
    df = _append_next_month_stub(sku_df)
    df = df.sort_values(["Year", "Month_Number"]).copy().reset_index(drop=True)

    grp = df.groupby("ItemCode")

    # ------------------------------
    # Calendar features
    # ------------------------------
    df["Month_Sin"] = np.sin(2 * np.pi * df["Month_Number"] / 12)
    df["Month_Cos"] = np.cos(2 * np.pi * df["Month_Number"] / 12)

    quarter = ((df["Month_Number"] - 1) // 3) + 1
    df["Quarter_Sin"] = np.sin(2 * np.pi * quarter / 4)
    df["Quarter_Cos"] = np.cos(2 * np.pi * quarter / 4)

    # ------------------------------
    # Demand history from Clean_Demand
    # ------------------------------
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

    # ------------------------------
    # Zero-behavior
    # ------------------------------
    df["Is_Zero"] = (df["Clean_Demand"] == 0).astype(int)
    df["ZeroRate_6M"] = grp["Is_Zero"].transform(
        lambda x: x.rolling(6, min_periods=1).mean().shift(1)
    ).fillna(0)

    # ------------------------------
    # Stock features
    # ------------------------------
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

    # ------------------------------
    # Promo intensity/history
    # ------------------------------
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

    # ------------------------------
    # Bonus cycle/timing features
    # ------------------------------
    if "Bonus_Flag" in df.columns:
        df["Bonus_Flag_Lag1"] = grp["Bonus_Flag"].shift(1).fillna(0)
        df["Bonus_Flag_Lag2"] = grp["Bonus_Flag"].shift(2).fillna(0)
        df["Bonus_Flag_Lag3"] = grp["Bonus_Flag"].shift(3).fillna(0)

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
                if len(past_bonus) == 0:
                    out.append(999)
                else:
                    out.append(current_t - past_bonus[-1])
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

    # ------------------------------
    # Promo uplift
    # ------------------------------
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
    df["Promo_Uplift_6M"] = grp2["Realized_Uplift"].transform(
        lambda x: x.rolling(6, min_periods=1).mean().shift(1)
    ).fillna(1.0)

    df["Last_Bonus_Demand"] = grp2["Bonus_Demand_Only"].transform(
        lambda x: x.shift(1).ffill()
    ).fillna(0)

    df["Promo_Uplift_Lag1"] = df["Promo_Uplift_Lag1"].clip(0.5, 3.0)
    df["Promo_Uplift_Lag2"] = df["Promo_Uplift_Lag2"].clip(0.5, 3.0)
    df["Promo_Uplift_6M"] = df["Promo_Uplift_6M"].clip(0.5, 2.5)

    if "Bonus_Flag" in df.columns:
        df["Bonus_Sin"] = df["Bonus_Flag"] * np.sin(2 * np.pi * df["Month_Number"] / 12)
        df["Bonus_Cos"] = df["Bonus_Flag"] * np.cos(2 * np.pi * df["Month_Number"] / 12)

    # ------------------------------
    # Residual baseline
    # ------------------------------
    df["Residual_Baseline"] = df["Rolling3M_Mean"].fillna(df["Lag1"]).fillna(0)

    # ------------------------------
    # Final clean
    # ------------------------------
    df = df.drop(columns=["Bonus_Demand_Only"], errors="ignore")
    df = df.replace([np.inf, -np.inf], np.nan).fillna(0)

    return df.tail(1).copy()


# ============================================================
# TABULAR PREDICTION
# ============================================================
def _prepare_tabular_features(next_row: pd.DataFrame, artifact: dict) -> pd.DataFrame:
    df = next_row.copy()

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
        categories = [str(x) for x in categories]
        code_map = {k: i for i, k in enumerate(categories)}
        unk_code = len(code_map)
        df["ItemCode"] = df["ItemCode"].astype(str).map(code_map).fillna(unk_code).astype(int)

    X = df[feature_cols].replace([np.inf, -np.inf], np.nan).fillna(0)
    return X


def _predict_tabular(next_row: pd.DataFrame, artifact: dict) -> float:
    model = artifact.get("model")
    feature_cols = artifact.get("feature_cols")

    if model is None or feature_cols is None:
        raise ValueError("Invalid artifact: missing model or feature_cols")

    X = _prepare_tabular_features(next_row, artifact)
    pred_raw = float(model.predict(X)[0])

    target_mode = str(artifact.get("target_mode", "residual")).lower()

    if target_mode == "residual":
        baseline_col = artifact.get("baseline_col", "Residual_Baseline")
        baseline_val = _safe_num(
            next_row.iloc[0].get(
                baseline_col,
                next_row.iloc[0].get("Rolling3M_Mean", 0)
            )
        )
        pred = baseline_val + pred_raw
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
    seq_features = bundle["seq_features"]
    static_features = bundle["static_features"]
    seq_len = int(bundle["seq_len"])
    item_to_idx = bundle["item_to_idx"]

    item_code = normalize_itemcode(next_row.iloc[0]["ItemCode"])
    item_idx = item_to_idx.get(item_code)

    if item_idx is None:
        raise ValueError(f"GRU item not found in item_to_idx: {item_code}")

    hist = sku_df.sort_values(["Year", "Month_Number"]).copy()
    temp = pd.concat([hist, next_row], ignore_index=True).sort_values(["Year", "Month_Number"]).reset_index(drop=True)

    if len(temp) < seq_len:
        raise ValueError(f"Not enough history for GRU sequence: need {seq_len}")

    seq_df = temp.tail(seq_len).copy()

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

    x_seq = torch.tensor(seq_vals.reshape(1, seq_len, len(seq_features)), dtype=torch.float32)
    x_static = torch.tensor(static_vals, dtype=torch.float32)
    x_item = torch.tensor([item_idx], dtype=torch.long)

    device = next(model.parameters()).device
    x_seq = x_seq.to(device)
    x_static = x_static.to(device)
    x_item = x_item.to(device)

    with torch.no_grad():
        pred_res_log = model(x_seq, x_static, x_item).cpu().numpy()[0]

    pred_residual = np.sign(pred_res_log) * np.expm1(np.abs(pred_res_log))

    baseline_val = _safe_num(
        next_row.iloc[0].get("Residual_Baseline", next_row.iloc[0].get("Rolling3M_Mean", 0))
    )

    pred = max(0.0, baseline_val + float(pred_residual))
    return pred


# ============================================================
# FALLBACK / SHORT HELPERS
# ============================================================
def _predict_long_fallback(next_row: pd.DataFrame, fallback_type: str = "ROLLING3") -> float:
    row = next_row.iloc[0]

    lag1 = _safe_num(row.get("Lag1", row.get("Clean_Demand", 0)))
    lag2 = _safe_num(row.get("Lag2", lag1))
    rolling3 = _safe_num(row.get("Rolling3M_Mean", lag1))

    fallback_type = str(fallback_type or "ROLLING3").upper()

    if fallback_type == "MAX_LAG1_ROLL3":
        return max(lag1, rolling3, 0.0)

    if fallback_type == "MEAN_LAGS":
        return max(float(np.mean([lag1, lag2, rolling3])), 0.0)

    return max(rolling3, 0.0)


def _predict_short_rule(next_row: pd.DataFrame, rule_artifact: dict, route_type: str) -> float:
    row = next_row.iloc[0]

    lag1 = _safe_num(row.get("Lag1", row.get("Clean_Demand", 0)))
    lag2 = _safe_num(row.get("Lag2", lag1))
    rolling3 = _safe_num(row.get("Rolling3M_Mean", lag1))

    if route_type == "PROMO_RULE":
        uplift = _safe_num(row.get("Avg_Bonus_Uplift", 1.0), 1.0)
        return max(max(rolling3, lag1) * max(1.0, uplift), 0.0)

    if route_type == "NORMAL_RULE":
        return max(float(np.mean([lag1, lag2, rolling3])), 0.0)

    return max(rolling3 if rolling3 > 0 else lag1, 0.0)


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

    sku_df = sku_df.sort_values(["Year", "Month_Number"]).reset_index(drop=True)
    segment = _choose_segment_by_history(sku_df)
    next_row = _rebuild_next_month_features(sku_df)

    used_model = None
    fallback_used = 0
    target_mode_used = "unknown"
    baseline_used = float(next_row.iloc[0].get("Residual_Baseline", 0))
    routing_reason = "OK"

    if segment == "LONG":
        route = artifact_service.get_long_routing_row(item_code)

        final_model = "XGBOOST"
        best_model = "XGBOOST"
        fallback_type = "ROLLING3"
        use_fallback = 0

        if route is not None:
            final_model = str(route.get("Final_Model") or "XGBOOST").upper()
            best_model = str(route.get("Best_Model") or final_model).upper()
            fallback_type = str(route.get("Fallback_Type") or "ROLLING3").upper()
            use_fallback = int(_safe_num(route.get("Use_Fallback", 0)))
        else:
            final_model = "XGBOOST"
            best_model = "XGBOOST"

        chosen_model = final_model or best_model

        if chosen_model == "GRU":
            try:
                bundle, scalers = artifact_service.get_gru_long_bundle()
                if bundle is None or scalers is None:
                    raise ValueError("GRU long bundle not loaded")
                pred = _predict_gru(sku_df, next_row, bundle, scalers)
                used_model = "GRU"
                target_mode_used = "residual"
            except Exception:
                pred = _predict_long_fallback(next_row, fallback_type)
                used_model = "FALLBACK"
                fallback_used = 1
                target_mode_used = "rule"
                routing_reason = "GRU_FAIL_TO_FALLBACK"

        elif chosen_model == "FALLBACK" or use_fallback == 1:
            pred = _predict_long_fallback(next_row, fallback_type)
            used_model = "FALLBACK"
            fallback_used = 1
            target_mode_used = "rule"
            routing_reason = "CHAMPION_FALLBACK"

        else:
            artifact = artifact_service.get_long_artifact(chosen_model)
            if artifact is None:
                pred = _predict_long_fallback(next_row, fallback_type)
                used_model = "FALLBACK"
                fallback_used = 1
                target_mode_used = "rule"
                routing_reason = "NO_LONG_ARTIFACT_TO_FALLBACK"
            else:
                try:
                    pred = _predict_tabular(next_row, artifact)
                    used_model = chosen_model
                    target_mode_used = str(artifact.get("target_mode", "residual")).lower()
                except Exception:
                    pred = _predict_long_fallback(next_row, fallback_type)
                    used_model = "FALLBACK"
                    fallback_used = 1
                    target_mode_used = "rule"
                    routing_reason = "LONG_MODEL_FAIL_TO_FALLBACK"

    elif segment == "MEDIUM":
        route = artifact_service.get_medium_routing_row(item_code)

        subgroup = "STABLE"
        model_name = "XGBOOST"

        if route is not None:
            subgroup = str(route.get("Medium_Subgroup", "STABLE")).upper()
            model_name = str(route.get("Final_Model") or route.get("Best_Model") or "XGBOOST").upper()

        artifact = artifact_service.get_medium_artifact(subgroup, model_name)

        if artifact is None:
            pred = _predict_long_fallback(next_row, "ROLLING3")
            used_model = f"{subgroup}::FALLBACK"
            fallback_used = 1
            target_mode_used = "rule"
            routing_reason = "NO_MEDIUM_ARTIFACT_TO_FALLBACK"
        else:
            try:
                pred = _predict_tabular(next_row, artifact)
                used_model = f"{subgroup}::{model_name}"
                target_mode_used = str(artifact.get("target_mode", "residual")).lower()
            except Exception:
                pred = _predict_long_fallback(next_row, "ROLLING3")
                used_model = f"{subgroup}::FALLBACK"
                fallback_used = 1
                target_mode_used = "rule"
                routing_reason = "MEDIUM_MODEL_FAIL_TO_FALLBACK"

    else:
        promo_profile = str(next_row.iloc[0].get("Promo_Profile", "NORMAL")).upper()
        route_type = "BASE_RULE"

        if int(_safe_num(next_row.iloc[0].get("Bonus_Flag", 0))) == 1:
            route_type = "PROMO_RULE"
        elif int(_safe_num(next_row.iloc[0].get("Recurring_Bonus_SKU", 0))) == 0:
            route_type = "NORMAL_RULE"

        short_artifact = artifact_service.get_short_artifact(promo_profile=promo_profile)

        pred = _predict_short_from_artifact(next_row, short_artifact, route_type)
        used_model = route_type
        fallback_used = 1
        target_mode_used = "rule"

    return {
        "ItemCode": item_code,
        "Segment": segment,
        "Forecast_Year": int(next_row.iloc[0]["Year"]),
        "Forecast_Month": int(next_row.iloc[0]["Month_Number"]),
        "Forecast_Prediction": float(pred),
        "Used_Model": used_model,
        "Fallback_Used": int(fallback_used),
        "Target_Mode": target_mode_used,
        "Baseline_Used": float(baseline_used),
        "Routing_Reason": routing_reason,
    }