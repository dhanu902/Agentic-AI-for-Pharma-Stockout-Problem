# backend/engines/forecast_orchestrator.py ---> ⚙️ Pipeline runner

import os
import threading
from datetime import datetime
from typing import Optional

import pandas as pd

from services.forecast_service import (
    load_raw_data,
    load_processed_data,
    save_processed_data,
    save_forecast_latest,
    append_forecast_run_log,
    get_file_status_summary,
    processed_is_fresh,
    load_forecast_latest,
)

from engines.preprocess_engine import (
    build_processed_data_from_raw,
    normalize_itemcode,
)

from services.artifact_service import artifact_service
from engines.demand_forecast_engine import forecast_one_sku


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(BASE_DIR)

LOCK = threading.Lock()

# Runtime stores
actual_data = pd.DataFrame()   # closed history
live_data = pd.DataFrame()     # open snapshot
data = pd.DataFrame()          # merged inference base


# ============================================================
# DATA LOADING HELPERS
# ============================================================
def _month_label(year, month_num):
    return f"{int(year):04d}-{int(month_num):02d}"


def _num(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return float(default)


def _normalize_runtime_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame()

    df = df.copy()
    if "ItemCode" in df.columns:
        df["ItemCode"] = normalize_itemcode(df["ItemCode"])
        df["ItemCode_key"] = df["ItemCode"]

    return df


def _load_processed_safe(mode: str) -> pd.DataFrame:
    try:
        df = load_processed_data(mode=mode)
        return _normalize_runtime_df(df)
    except FileNotFoundError:
        return pd.DataFrame()


def _merge_actual_and_live(actual_df: pd.DataFrame, live_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build inference base:
    - closed months come from ACTUAL
    - open month comes from SNAPSHOT
    - if same ItemCode + Year + Month_Number exists in actual, keep ACTUAL
    """
    actual_df = _normalize_runtime_df(actual_df)
    live_df = _normalize_runtime_df(live_df)

    if actual_df.empty and live_df.empty:
        return pd.DataFrame()

    if actual_df.empty:
        merged = live_df.copy()
        merged["Runtime_Source"] = "SNAPSHOT"
        return merged.sort_values(["ItemCode", "Year", "Month_Number"]).reset_index(drop=True)

    if live_df.empty:
        merged = actual_df.copy()
        merged["Runtime_Source"] = "ACTUAL"
        return merged.sort_values(["ItemCode", "Year", "Month_Number"]).reset_index(drop=True)

    actual_df = actual_df.copy()
    live_df = live_df.copy()

    actual_df["Runtime_Source"] = "ACTUAL"
    live_df["Runtime_Source"] = "SNAPSHOT"

    key_cols = ["ItemCode", "Year", "Month_Number"]

    actual_keys = set(
        zip(
            actual_df["ItemCode"].astype(str),
            actual_df["Year"].astype(int),
            actual_df["Month_Number"].astype(int),
        )
    )

    live_df = live_df[
        ~live_df.apply(
            lambda r: (
                str(r["ItemCode"]),
                int(r["Year"]),
                int(r["Month_Number"])
            ) in actual_keys,
            axis=1
        )
    ].copy()

    merged = pd.concat([actual_df, live_df], ignore_index=True, sort=False)

    if {"Year", "Month_Number"}.issubset(merged.columns):
        merged = merged.sort_values(["ItemCode", "Year", "Month_Number"]).reset_index(drop=True)

    return merged


def _reload_data_into_memory():
    """
    Reload both processed layers and build final runtime inference base.
    """
    global actual_data, live_data, data

    actual_data = _load_processed_safe(mode="actual")
    live_data = _load_processed_safe(mode="live")
    data = _merge_actual_and_live(actual_data, live_data)

    return {
        "rows": int(len(data)),
        "unique_skus": int(data["ItemCode"].nunique()) if "ItemCode" in data.columns and len(data) else 0,
        "actual_rows": int(len(actual_data)),
        "live_rows": int(len(live_data)),
        "actual_skus": int(actual_data["ItemCode"].nunique()) if "ItemCode" in actual_data.columns and len(actual_data) else 0,
        "live_skus": int(live_data["ItemCode"].nunique()) if "ItemCode" in live_data.columns and len(live_data) else 0,
        "min_year": int(data["Year"].min()) if "Year" in data.columns and len(data) else None,
        "max_year": int(data["Year"].max()) if "Year" in data.columns and len(data) else None,
    }


def _get_sku_df(item_code: str) -> pd.DataFrame:
    if data.empty or "ItemCode_key" not in data.columns:
        return pd.DataFrame()

    item_key = str(item_code).strip().replace(".0", "")
    sku_df = data[data["ItemCode_key"] == item_key].copy()

    if sku_df.empty:
        return sku_df

    if {"Year", "Month_Number"}.issubset(sku_df.columns):
        sku_df = sku_df.sort_values(["Year", "Month_Number"])
    elif "Month" in sku_df.columns:
        sku_df = sku_df.sort_values("Month")

    return sku_df.reset_index(drop=True)


# ============================================================
# ARTIFACT BOOTSTRAP
# ============================================================
def initialize_artifacts():
    """
    Load all model artifacts, champion maps, and GRU bundle
    from backend/models/...
    """
    artifact_service.load_all()
    return artifact_service.summary()


# ============================================================
# FINAL ROUTING HELPERS
# ============================================================
def get_sku_history_length(raw_data, sku_code):
    sku_code = str(sku_code).strip().replace(".0", "")
    g = raw_data[raw_data["ItemCode"].astype(str).str.replace(r"\.0$", "", regex=True) == sku_code].copy()
    if g.empty:
        return 0
    return g[["Year", "Month_Number"]].drop_duplicates().shape[0]


def choose_segment_by_history(raw_data, sku_code):
    hist_len = get_sku_history_length(raw_data, sku_code)

    if hist_len >= 18:
        return "LONG"
    if hist_len >= 6:
        return "MEDIUM"
    return "SHORT"


# ============================================================
# PUBLIC FORECAST FUNCTIONS
# ============================================================
def forecast_sku_next_month(item_code: str) -> Optional[dict]:
    if data.empty:
        return None

    sku_df = _get_sku_df(item_code)
    if sku_df.empty:
        return None

    return forecast_one_sku(item_code, data.copy())


def export_forecast_all_skus():
    if data.empty or "ItemCode" not in data.columns:
        return pd.DataFrame(columns=[
            "Run_Date",
            "Forecast_Month",
            "ItemCode",
            "Forecast_Qty",
            "Segment",
            "Used_Model",
            "Fallback_Used",
            "Target_Mode",
            "Routing_Reason",
        ]), None

    created_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    rows = []
    next_month_label = None

    unique_skus = sorted(data["ItemCode"].astype(str).unique().tolist())

    for sku in unique_skus:
        try:
            result = forecast_one_sku(sku, data.copy())
            if result is None:
                continue

            row_month_label = _month_label(
                result["Forecast_Year"],
                result["Forecast_Month"]
            )

            if next_month_label is None:
                next_month_label = row_month_label

            rows.append({
                "Run_Date": created_at,
                "Forecast_Month": row_month_label,
                "ItemCode": str(result["ItemCode"]),
                "Forecast_Qty": round(float(result["Forecast_Prediction"]), 2),
                "Segment": result.get("Segment", ""),
                "Used_Model": result.get("Used_Model", ""),
                "Fallback_Used": int(result.get("Fallback_Used", 0)),
                "Target_Mode": result.get("Target_Mode", ""),
                "Routing_Reason": result.get("Routing_Reason", ""),
            })

        except Exception:
            continue

    df_forecast = pd.DataFrame(rows)
    return df_forecast, next_month_label


# ============================================================
# DASHBOARD
# ============================================================
def _get_forecast_file_row(item_code: str) -> Optional[dict]:
    try:
        df = load_forecast_latest().copy()
    except FileNotFoundError:
        return None

    if df.empty or "ItemCode" not in df.columns:
        return None

    item_key = str(item_code).strip().replace(".0", "")
    df["ItemCode"] = df["ItemCode"].astype(str).str.replace(r"\.0$", "", regex=True)

    row = df[df["ItemCode"] == item_key]
    if row.empty:
        return None

    return row.iloc[0].to_dict()

def _build_dashboard_response(item_code: str):
    sku_df = _get_sku_df(item_code)
    if sku_df.empty:
        return None

    forecast_row = _get_forecast_file_row(item_code)

    if forecast_row is None:
        # fallback: if forecast file not generated yet, calculate live
        result = forecast_sku_next_month(item_code)
        if result is None:
            return None

        forecast_prediction = round(float(result["Forecast_Prediction"]), 2)
        forecast_year = int(result["Forecast_Year"])
        forecast_month = int(result["Forecast_Month"])

        segment = result.get("Segment", "")
        used_model = result.get("Used_Model", "")
        fallback_used = int(result.get("Fallback_Used", 0))
        target_mode = result.get("Target_Mode", "")
        baseline_used = float(result.get("Baseline_Used", 0))
        routing_reason = result.get("Routing_Reason", "")
        forecast_source = "LIVE_RUNTIME"

    else:
        forecast_prediction = round(float(forecast_row.get("Forecast_Qty", 0)), 2)

        forecast_month_label = str(forecast_row.get("Forecast_Month", ""))
        if "-" not in forecast_month_label:
            return None

        yy, mm = forecast_month_label.split("-")
        forecast_year = int(yy)
        forecast_month = int(mm)

        segment = forecast_row.get("Segment", "")
        used_model = forecast_row.get("Used_Model", "")
        fallback_used = int(float(forecast_row.get("Fallback_Used", 0) or 0))
        target_mode = forecast_row.get("Target_Mode", "")
        baseline_used = 0.0
        routing_reason = forecast_row.get("Routing_Reason", "")
        forecast_source = "FORECAST_FILE"

    cur_row = sku_df.iloc[-1]
    prev_row = sku_df.iloc[-2] if len(sku_df) > 1 else cur_row

    current_actual = _num(cur_row.get("Clean_Demand", 0))
    last_month_actual = _num(prev_row.get("Clean_Demand", 0))
    mom = ((current_actual - last_month_actual) / (last_month_actual + 1e-6)) * 100
    avg_sales = _num(sku_df["Clean_Demand"].mean()) if "Clean_Demand" in sku_df.columns else 0.0

    cur_year = int(cur_row["Year"])
    cur_month = int(cur_row["Month_Number"])

    current_label = _month_label(cur_year, cur_month)
    last_label = _month_label(int(prev_row["Year"]), int(prev_row["Month_Number"]))
    next_label = _month_label(forecast_year, forecast_month)

    tail = sku_df.tail(12).copy()

    sales_trend = []
    for _, r in tail.iterrows():
        label = _month_label(r["Year"], r["Month_Number"])
        sales_trend.append({
            "period": label,
            "label": label,
            "actual": _num(r.get("Clean_Demand", 0)),
            "predicted": None
        })

    sales_trend.append({
        "period": next_label,
        "label": next_label,
        "actual": None,
        "predicted": forecast_prediction,
        "isForecast": True
    })

    inventory_trend = []
    for _, r in tail.iterrows():
        label = _month_label(r["Year"], r["Month_Number"])
        inventory_trend.append({
            "label": label,
            "primaryInventory": _num(r.get("Available_Primary_Inventory_Qty", 0)),
            "distInventory": _num(r.get("Distributor_Inventory_Qty", 0)),
        })

    shock_trend = []
    for _, r in tail.iterrows():
        label = _month_label(r["Year"], r["Month_Number"])
        shock_trend.append({
            "label": label,
            "bonusQty": _num(r.get("Free_Qty", 0)),
            "bonusFlag": int(_num(r.get("Bonus_Flag", 0))),
            "supplyFlag": int(_num(r.get("Supply_Shock", r.get("Supply_Constraint_Flag", 0)))),
        })

    bonus_qty_current = _num(cur_row.get("Free_Qty", 0))
    bonus_qty_last = _num(prev_row.get("Free_Qty", 0))
    bonus_shock_current = int(_num(cur_row.get("Bonus_Shock", 0)))
    bonus_shock_last = int(_num(prev_row.get("Bonus_Shock", 0)))
    supply_shock_current = int(_num(cur_row.get("Supply_Shock", cur_row.get("Supply_Constraint_Flag", 0))))
    supply_shock_last = int(_num(prev_row.get("Supply_Shock", prev_row.get("Supply_Constraint_Flag", 0))))

    return {
        "item_code": str(item_code),
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "forecast_source": forecast_source,
        "segment": segment,
        "used_model": used_model,
        "fallback_used": fallback_used,
        "target_mode": target_mode,
        "baseline_used": baseline_used,
        "routing_reason": routing_reason,
        "next_month_forecast": forecast_prediction,
        "next_month_label": next_label,
        "current_month_actual": round(current_actual, 2),
        "current_month_label": current_label,
        "last_month_actual": round(last_month_actual, 2),
        "last_month_label": last_label,
        "mom_change": round(float(mom), 2),
        "avg_monthly_sales": round(float(avg_sales), 2),
        "bonus_qty_current_month": round(float(bonus_qty_current), 2),
        "bonus_qty_last_month": round(float(bonus_qty_last), 2),
        "bonus_shock_current_month": bonus_shock_current,
        "bonus_shock_last_month": bonus_shock_last,
        "supply_shock_current_month": supply_shock_current,
        "supply_shock_last_month": supply_shock_last,
        "sales_trend": sales_trend,
        "inventory_trend": inventory_trend,
        "shock_trend": shock_trend,
    }


# ============================================================
# RAW PROCESSING
# ============================================================
def process_actual_raw_now():
    with LOCK:
        try:
            raw_df = load_raw_data(mode="actual")
        except FileNotFoundError as e:
            return {"ok": False, "error": str(e)}, 400

        processed = build_processed_data_from_raw(raw_df)
        save_processed_data(processed, mode="actual")
        info = _reload_data_into_memory()

    return {
        "ok": True,
        "message": "Actual raw data processed -> processed_data_actual.csv generated + runtime refreshed",
        "processed_rows": int(len(processed)),
        "unique_skus": int(processed["ItemCode"].nunique()) if "ItemCode" in processed.columns else 0,
        **info
    }, 200


def process_live_raw_now():
    with LOCK:
        try:
            raw_df = load_raw_data(mode="live")
        except FileNotFoundError as e:
            return {"ok": False, "error": str(e)}, 400

        processed = build_processed_data_from_raw(raw_df)
        save_processed_data(processed, mode="live")
        info = _reload_data_into_memory()

    return {
        "ok": True,
        "message": "Live snapshot raw data processed -> processed_data_live.csv generated + runtime refreshed",
        "processed_rows": int(len(processed)),
        "unique_skus": int(processed["ItemCode"].nunique()) if "ItemCode" in processed.columns else 0,
        **info
    }, 200


# ============================================================
# PUBLIC API
# ============================================================
def get_dashboard(item_code: str):
    return _build_dashboard_response(item_code)


def get_skus():
    if data.empty or "ItemCode" not in data.columns:
        return []
    return sorted(data["ItemCode"].astype(str).unique().tolist())


def reload_data_now():
    with LOCK:
        info = _reload_data_into_memory()
    return {
        "ok": True,
        "message": "Runtime data reloaded from processed actual + processed live snapshot",
        **info
    }, 200


def reload_model_artifacts():
    try:
        summary = initialize_artifacts()
        return {
            "ok": True,
            "message": "Model artifacts reloaded successfully",
            "artifact_summary": summary
        }, 200
    except Exception as e:
        return {
            "ok": False,
            "message": f"Failed to reload model artifacts: {str(e)}"
        }, 500


def refresh_model_now():
    return reload_model_artifacts()


def retune_model_now():
    return {
        "ok": False,
        "message": "Model retuning is not supported in backend. Use notebook pipeline."
    }, 400


def retrain_model_now():
    return retune_model_now()


def train_model_now():
    return retune_model_now()


def get_health():
    artifact_summary = artifact_service.summary()

    health_info = {
        "rows": int(len(data)),
        "unique_skus": int(data["ItemCode"].nunique()) if "ItemCode" in data.columns and len(data) else 0,
        "actual_rows": int(len(actual_data)),
        "live_rows": int(len(live_data)),
        "actual_skus": int(actual_data["ItemCode"].nunique()) if "ItemCode" in actual_data.columns and len(actual_data) else 0,
        "live_skus": int(live_data["ItemCode"].nunique()) if "ItemCode" in live_data.columns and len(live_data) else 0,
        "artifact_summary": artifact_summary,
        "model_loaded": (
            len(artifact_summary.get("long_models_loaded", [])) > 0 or
            len(artifact_summary.get("medium_models_loaded", [])) > 0 or
            len(artifact_summary.get("short_rules_loaded", [])) > 0
        ),
        "gru_loaded": artifact_summary.get("gru_long_loaded", False),
        "champion_map_loaded": (
            artifact_summary.get("champion_long_loaded", False) or
            artifact_summary.get("champion_medium_loaded", False)
        ),
        "file_status": get_file_status_summary(),
        "processed_actual_fresh": processed_is_fresh("actual"),
        "processed_live_fresh": processed_is_fresh("live"),
    }

    if data.empty or "ItemCode" not in data.columns:
        health_info["prediction_status"] = "FAILED: merged runtime data not loaded"
        health_info["status"] = "UNHEALTHY"
        return health_info, 500

    try:
        any_sku = str(data["ItemCode"].iloc[-1])
        test_pred = _build_dashboard_response(any_sku)
        health_info["sample_prediction"] = test_pred
        health_info["prediction_status"] = "OK"
        health_info["status"] = "HEALTHY"
        return health_info, 200
    except Exception as e:
        health_info["prediction_status"] = f"FAILED: {str(e)}"
        health_info["status"] = "UNHEALTHY"
        return health_info, 500


def export_forecast_latest_now():
    df_forecast, label = export_forecast_all_skus()
    out_path = save_forecast_latest(df_forecast)

    history_base_month = None
    if len(actual_data) and "Year" in actual_data.columns and "Month_Number" in actual_data.columns:
        latest_actual = actual_data.sort_values(["Year", "Month_Number"]).iloc[-1]
        history_base_month = _month_label(latest_actual["Year"], latest_actual["Month_Number"])

    run_type = "SNAPSHOT_INFERENCE" if len(live_data) > 0 else "ACTUAL_ONLY_INFERENCE"

    run_row = {
        "Run_ID": datetime.utcnow().strftime("%Y%m%d%H%M%S"),
        "Run_Date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "Forecast_Month": label,
        "History_Base_Month": history_base_month,
        "Run_Type": run_type,
        "Rows_Exported": int(len(df_forecast)),
        "Output_Path": out_path,
    }
    append_forecast_run_log(run_row)

    return {
        "ok": True,
        "message": "Forecast exported for all SKUs",
        "next_month": label,
        "rows": int(len(df_forecast)),
        "path": out_path
    }, 200


# ============================================================
# INIT ON IMPORT
# ============================================================
try:
    initialize_artifacts()
except Exception as e:
    print(f"[Artifact Init Warning] {e}")

try:
    _reload_data_into_memory()
except Exception:
    actual_data = pd.DataFrame()
    live_data = pd.DataFrame()
    data = pd.DataFrame()