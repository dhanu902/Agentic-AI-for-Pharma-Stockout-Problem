# backend/engines/xai_engine.py

import numpy as np
import pandas as pd


def safe_float(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def get_artifact_model(model_artifacts):
    if model_artifacts is None:
        return None

    if isinstance(model_artifacts, dict):
        return model_artifacts.get("model")

    return model_artifacts


def get_artifact_features(model_artifacts):
    if model_artifacts is None:
        return []

    if isinstance(model_artifacts, dict):
        return (
            model_artifacts.get("feature_cols")
            or model_artifacts.get("features")
            or model_artifacts.get("model_features")
            or []
        )

    return []


def explain_tree_model(
    item_code,
    segment,
    used_model,
    forecast_value,
    feature_row,
    model_artifacts,
    top_n=8
):
    try:
        import shap

        model = get_artifact_model(model_artifacts)
        feature_cols = get_artifact_features(model_artifacts)

        if model is None:
            raise ValueError("Model object not found in artifacts.")

        if not feature_cols:
            raise ValueError("feature_cols not found in artifacts.")

        missing = [c for c in feature_cols if c not in feature_row.columns]
        if missing:
            raise ValueError(f"Missing XAI features: {missing[:10]}")

        X = feature_row[feature_cols].copy()
        X = X.replace([np.inf, -np.inf], np.nan).fillna(0)

        explainer = shap.Explainer(model)
        shap_values = explainer(X)

        values = shap_values.values[0]

        driver_df = pd.DataFrame({
            "feature": feature_cols,
            "value": X.iloc[0].values,
            "shap_value": values
        })

        driver_df["impact"] = np.where(
            driver_df["shap_value"] >= 0,
            "increase",
            "decrease"
        )

        driver_df["abs_impact"] = driver_df["shap_value"].abs()

        top_drivers = (
            driver_df
            .sort_values("abs_impact", ascending=False)
            .head(top_n)
            .drop(columns=["abs_impact"])
            .to_dict(orient="records")
        )

        explanation_text = build_tree_explanation_text(
            used_model=used_model,
            forecast_value=forecast_value,
            top_drivers=top_drivers
        )

        return {
            "item_code": item_code,
            "segment": segment,
            "used_model": used_model,
            "forecast": round(float(forecast_value), 2),
            "xai_method": "SHAP",
            "top_drivers": clean_driver_values(top_drivers),
            "explanation_text": explanation_text
        }

    except Exception as e:
        return explain_rule_summary(
            item_code=item_code,
            segment=segment,
            used_model=used_model,
            forecast_value=forecast_value,
            feature_row=feature_row,
            reason=f"SHAP explanation failed: {str(e)}"
        )


def build_tree_explanation_text(used_model, forecast_value, top_drivers):
    text = f"{used_model} generated a forecast of {forecast_value:.2f} units. "

    if not top_drivers:
        return text + "No major drivers were available."

    inc = [d for d in top_drivers if d["shap_value"] >= 0]
    dec = [d for d in top_drivers if d["shap_value"] < 0]

    if inc:
        inc_features = ", ".join([d["feature"] for d in inc[:3]])
        text += f"The forecast was mainly increased by {inc_features}. "

    if dec:
        dec_features = ", ".join([d["feature"] for d in dec[:3]])
        text += f"It was reduced by {dec_features}. "

    return text.strip()


def clean_driver_values(drivers):
    cleaned = []

    for d in drivers:
        cleaned.append({
            "feature": str(d.get("feature")),
            "value": safe_float(d.get("value")),
            "shap_value": safe_float(d.get("shap_value")),
            "impact": str(d.get("impact"))
        })

    return cleaned


def explain_gru_model(
    item_code,
    segment,
    used_model,
    forecast_value,
    feature_row
):
    important_features = [
        "Lag1",
        "Lag2",
        "Lag3",
        "Rolling3M_Mean",
        "Rolling6M_Mean",
        "Momentum",
        "Bonus_Flag",
        "Bonus_Flag_Lag1",
        "Expected_Bonus_Month",
        "Expected_Bonus_NextMonth",
        "Post_Bonus_Month_Flag",
        "Supply_Constraint_Flag",
        "Supply_Constraint_Lag1",
        "Available_Primary_Inventory_Qty",
        "Distributor_Inventory_Qty",
        "Net_Available_Stock",
        "Stock_Cover_Months",
        "SKU_CV",
        "ZeroRate_6M"
    ]

    drivers = []

    for feature in important_features:
        if feature in feature_row.columns:
            drivers.append({
                "feature": feature,
                "value": safe_float(feature_row[feature].iloc[0]),
                "impact": "sequence_context"
            })

    explanation_text = (
        f"GRU generated a forecast of {forecast_value:.2f} units. "
        "GRU is a sequence model, so the explanation is based on recent demand, "
        "promotion, supply, and inventory sequence signals rather than direct SHAP values."
    )

    return {
        "item_code": item_code,
        "segment": segment,
        "used_model": used_model,
        "forecast": round(float(forecast_value), 2),
        "xai_method": "GRU_SEQUENCE_SUMMARY",
        "top_drivers": drivers,
        "explanation_text": explanation_text
    }


def explain_fallback_model(
    item_code,
    segment,
    used_model,
    forecast_value,
    feature_row,
    fallback_type=None
):
    lag1 = safe_float(feature_row["Lag1"].iloc[0]) if "Lag1" in feature_row.columns else 0
    rolling3 = safe_float(feature_row["Rolling3M_Mean"].iloc[0]) if "Rolling3M_Mean" in feature_row.columns else 0
    sku_cv = safe_float(feature_row["SKU_CV"].iloc[0]) if "SKU_CV" in feature_row.columns else 0
    zero_rate = safe_float(feature_row["SKU_ZeroRate"].iloc[0]) if "SKU_ZeroRate" in feature_row.columns else 0

    if fallback_type == "ROLLING3":
        reason = "Rolling 3-month average fallback was used because the SKU was intermittent or model reliability was weak."
    elif fallback_type == "MAX_LAG1_ROLL3":
        reason = "Fallback used the safer value between last month demand and rolling average because the SKU was volatile."
    else:
        reason = "Fallback rule was used instead of an ML model."

    drivers = [
        {"feature": "Lag1", "value": lag1, "impact": "fallback_input"},
        {"feature": "Rolling3M_Mean", "value": rolling3, "impact": "fallback_input"},
        {"feature": "SKU_CV", "value": sku_cv, "impact": "volatility_signal"},
        {"feature": "SKU_ZeroRate", "value": zero_rate, "impact": "intermittency_signal"}
    ]

    explanation_text = (
        f"Fallback forecast is {forecast_value:.2f} units. "
        f"{reason}"
    )

    return {
        "item_code": item_code,
        "segment": segment,
        "used_model": "FALLBACK",
        "forecast": round(float(forecast_value), 2),
        "xai_method": "RULE_BASED_FALLBACK",
        "fallback_type": fallback_type,
        "top_drivers": drivers,
        "explanation_text": explanation_text
    }


def explain_rule_summary(
    item_code,
    segment,
    used_model,
    forecast_value,
    feature_row,
    reason=None
):
    key_features = [
        "Lag1",
        "Rolling3M_Mean",
        "Bonus_Flag",
        "Expected_Bonus_NextMonth",
        "Supply_Constraint_Flag",
        "Available_Primary_Inventory_Qty",
        "Distributor_Inventory_Qty"
    ]

    drivers = []

    for feature in key_features:
        if feature in feature_row.columns:
            drivers.append({
                "feature": feature,
                "value": safe_float(feature_row[feature].iloc[0]),
                "impact": "context"
            })

    explanation_text = (
        f"{used_model} forecast is {forecast_value:.2f} units. "
        "Explanation is generated from key business features."
    )

    if reason:
        explanation_text += f" Note: {reason}"

    return {
        "item_code": item_code,
        "segment": segment,
        "used_model": used_model,
        "forecast": round(float(forecast_value), 2),
        "xai_method": "RULE_SUMMARY",
        "top_drivers": drivers,
        "explanation_text": explanation_text
    }


def generate_xai_explanation(
    item_code,
    segment,
    used_model,
    forecast_value,
    feature_row,
    model_artifacts=None,
    fallback_type=None
):
    used_model = str(used_model).upper()
    segment = str(segment).upper()

    if used_model in ["XGBOOST", "CATBOOST", "LIGHTGBM", "RANDOM_FOREST"]:
        return explain_tree_model(
            item_code=item_code,
            segment=segment,
            used_model=used_model,
            forecast_value=forecast_value,
            feature_row=feature_row,
            model_artifacts=model_artifacts
        )

    if used_model == "GRU":
        return explain_gru_model(
            item_code=item_code,
            segment=segment,
            used_model=used_model,
            forecast_value=forecast_value,
            feature_row=feature_row
        )

    if used_model == "FALLBACK":
        return explain_fallback_model(
            item_code=item_code,
            segment=segment,
            used_model=used_model,
            forecast_value=forecast_value,
            feature_row=feature_row,
            fallback_type=fallback_type
        )

    return explain_rule_summary(
        item_code=item_code,
        segment=segment,
        used_model=used_model,
        forecast_value=forecast_value,
        feature_row=feature_row
    )