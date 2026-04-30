# backend/services/xai_service.py 

import os
import pandas as pd

from engines.xai_engine import generate_xai_explanation
from services.artifact_service import artifact_service


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(BASE_DIR, "models")
DATA_DIR = os.path.join(BASE_DIR, "data")

FORECAST_LATEST_PATH = os.path.join(DATA_DIR, "outputs", "forecast_latest.csv")
PROCESSED_LIVE_PATH = os.path.join(DATA_DIR, "processed", "processed_data_live.csv")
PROCESSED_ACTUAL_PATH = os.path.join(DATA_DIR, "processed", "processed_data_actual.csv")

def get_model_artifacts(segment, used_model, fallback_type=None):

    used_model = str(used_model).upper()

    segment = str(segment).upper()

    if used_model in ["FALLBACK", "GRU", "UNKNOWN"]:

        return None

    if segment == "LONG":

        file_map = {

            "XGBOOST": os.path.join(MODELS_DIR, "long", "xgb_long_deploy_artifacts_residual.pkl"),

            "CATBOOST": os.path.join(MODELS_DIR, "long", "catboost_long_deploy_artifacts_residual.pkl"),

            "LIGHTGBM": os.path.join(MODELS_DIR, "long", "lgbm_long_deploy_artifacts_residual.pkl"),

        }

        path = file_map.get(used_model)

        if path and os.path.exists(path):

            return joblib.load(path)
    return None


def get_first_existing_value(df, possible_cols, default=None):
    for col in possible_cols:
        if col in df.columns:
            return df[col].iloc[0]
    return default


def normalize_itemcode(code):
    code = str(code).strip()
    if code.endswith(".0"):
        code = code.replace(".0", "")
    return code


def load_latest_forecast_row(item_code):
    if not os.path.exists(FORECAST_LATEST_PATH):
        raise FileNotFoundError("forecast_latest.csv not found. Run forecast first.")

    df = pd.read_csv(FORECAST_LATEST_PATH)
    df["ItemCode"] = df["ItemCode"].astype(str).apply(normalize_itemcode)

    item_code = normalize_itemcode(item_code)

    row = df[df["ItemCode"] == item_code]

    if row.empty:
        raise ValueError(f"No forecast found for ItemCode {item_code}")

    return row.tail(1).copy()


def load_latest_feature_row(item_code):
    item_code = normalize_itemcode(item_code)

    if os.path.exists(PROCESSED_LIVE_PATH):
        df = pd.read_csv(PROCESSED_LIVE_PATH)
    elif os.path.exists(PROCESSED_ACTUAL_PATH):
        df = pd.read_csv(PROCESSED_ACTUAL_PATH)
    else:
        raise FileNotFoundError("No processed feature data found.")

    df["ItemCode"] = df["ItemCode"].astype(str).apply(normalize_itemcode)

    row = df[df["ItemCode"] == item_code]

    if row.empty:
        raise ValueError(f"No processed feature row found for ItemCode {item_code}")

    if "Year" in row.columns and "Month_Number" in row.columns:
        row = row.sort_values(["Year", "Month_Number"])

    return row.tail(1).copy()


def get_model_artifacts(segment, used_model, fallback_type=None):
    used_model = str(used_model).upper()
    segment = str(segment).upper()

    if used_model == "FALLBACK":
        return None

    # These names should match your artifact_service keys
    if segment == "LONG":
        if used_model == "XGBOOST":
            return artifact_service.long_models.get("XGBOOST")
        if used_model == "CATBOOST":
            return artifact_service.long_models.get("CATBOOST")
        if used_model == "LIGHTGBM":
            return artifact_service.long_models.get("LIGHTGBM")
        if used_model == "GRU":
            return artifact_service.gru_long_artifacts

    if segment == "MEDIUM":
        # Example keys:
        # PROMO_HEAVY::XGBOOST
        # STABLE::CATBOOST
        # STABLE::RANDOM_FOREST

        subgroup = fallback_type

        # Better: get subgroup from forecast_latest if available
        return artifact_service.medium_models.get(used_model)

    if segment == "SHORT":
        return None

    return None


def get_forecast_xai_explanation(item_code):
    item_code = normalize_itemcode(item_code)

    forecast_row = load_latest_forecast_row(item_code)
    feature_row = load_latest_feature_row(item_code)

    segment = str(get_first_existing_value(
        forecast_row,
        ["Segment", "segment", "History_Segment"],
        "UNKNOWN"
    )).upper()

    used_model = str(get_first_existing_value(
        forecast_row,
        ["Used_Model", "used_model", "Final_Model", "final_model", "Model_Name", "model_name"],
        "UNKNOWN"
    )).upper()

    fallback_type = get_first_existing_value(
        forecast_row,
        ["Fallback_Type", "fallback_type"],
        None
    )

    forecast_value = float(get_first_existing_value(
        forecast_row,
        [
            "Forecast_Prediction",
            "forecast_prediction",
            "Forecast",
            "forecast",
            "Pred",
            "pred",
            "next_month_forecast"
        ],
        0
    ))

    model_artifacts = get_model_artifacts(
        segment=segment,
        used_model=used_model,
        fallback_type=fallback_type
    )

    result = generate_xai_explanation(
        item_code=item_code,
        segment=segment,
        used_model=used_model,
        forecast_value=forecast_value,
        feature_row=feature_row,
        model_artifacts=model_artifacts,
        fallback_type=fallback_type
    )

    return result