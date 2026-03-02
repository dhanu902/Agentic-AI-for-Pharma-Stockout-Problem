# demand_forecast_engine.py

import os
import joblib
import pandas as pd
import numpy as np
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import threading
import xgboost as xgb


# ─── Initialize Flask App ────────────────────────────────────────────────── 

app = Flask(__name__)
CORS(app)

# ─── Initiate Paths ────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)

MODEL_PATH = os.path.join(BASE_DIR, "models", "model_artifacts.pkl")
DATA_PATH = os.path.join(BASE_DIR, "data", "processed_data.csv")
RAW_XLSX_PATH = os.path.join(PROJECT_ROOT, "data", "Company Data.xlsx")
RAW_CSV_PATH  = os.path.join(PROJECT_ROOT, "data", "Company Data.csv")
PROCESSED_PATH = DATA_PATH  
STATE_PATH = os.path.join(BASE_DIR, "models", "train_state.json")

LOCK = threading.Lock()
TARGET_COL = "Target"

# Load artifacts ONCE (fast)
artifacts = joblib.load(MODEL_PATH)
model = artifacts["model"]
feature_cols = artifacts["feature_cols"]

# optional artifacts (if you saved them)
itemcode_categories = artifacts.get("itemcode_categories", None)
abc_map = artifacts.get("abc_map", None)

# Load data ONCE (fast)
data = pd.read_csv(DATA_PATH)

# normalize ItemCode as string everywhere in backend
def _normalize_itemcode(series):
    return (
        series.astype(str)
              .str.strip()
              .str.replace(r"\.0$", "", regex=True)
    )
data["ItemCode"] = _normalize_itemcode(data["ItemCode"])

# if ABC_Class missing, fill (but ideally it exists in processed_data.csv)
if "ABC_Class" not in data.columns:
    data["ABC_Class"] = 2

# Make sure required model columns exist
for c in feature_cols:
    if c not in data.columns:
        data[c] = 0


# ─── Functions ─────────────────────────────────────────────────

def _load_state():
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        return joblib.load(STATE_PATH)  # using joblib for simplicity
    except Exception:
        return {}

def _save_state(state: dict):
    joblib.dump(state, STATE_PATH)

def _cooldown_ok(key: str, cooldown_days: int):
    state = _load_state()
    last = state.get(key)
    now = datetime.now()

    if last is not None:
        last_dt = datetime.fromisoformat(last)
        days = (now - last_dt).total_seconds() / (60 * 60 * 24)
        if days < cooldown_days:
            return False, round(cooldown_days - days, 2), last

    state[key] = now.isoformat()
    _save_state(state)
    return True, 0, None

def _num(series_or_value, default=0.0):
    try:
        return float(series_or_value)
    except Exception:
        return float(default)

def _month_label(year, month_num):
    return f"{int(year):04d}-{int(month_num):02d}"

def _get_sku_df(item_code: str) -> pd.DataFrame:
    item_code = str(item_code).strip().replace(".0", "")
    sku_df = data[data["ItemCode"] == item_code].copy()

    if sku_df.empty:
        return sku_df

    if {"Year", "Month_Number"}.issubset(sku_df.columns):
        sku_df = sku_df.sort_values(["Year", "Month_Number"])
    elif "Month" in sku_df.columns:
        sku_df = sku_df.sort_values("Month")

    return sku_df.reset_index(drop=True)  

def _prepare_latest_X(sku_df: pd.DataFrame) -> pd.DataFrame:
    latest_row = sku_df.iloc[-1:].copy()

    X = latest_row[feature_cols].copy()

    if X.shape[1] != len(feature_cols):
        raise ValueError("Feature mismatch between model and dataframe")

    # Ensure numeric for xgboost
    for col in X.columns:
        X[col] = pd.to_numeric(X[col], errors="coerce")
    X = X.replace([np.inf, -np.inf], np.nan).fillna(0)

    # IMPORTANT:
    # If your model expects encoded ItemCode (int codes) from training,
    # convert it here using saved itemcode_categories.
    if "ItemCode" in X.columns and itemcode_categories is not None:
        raw_code = str(latest_row["ItemCode"].iloc[0]).strip()
        encoded = pd.Categorical([raw_code], categories=itemcode_categories).codes[0]
        # if not found -> -1
        X["ItemCode"] = int(encoded) if encoded != -1 else 0

    return X

def _sanitize_X(dfX: pd.DataFrame) -> pd.DataFrame:
    dfX = dfX.copy()
    for c in dfX.columns:
        dfX[c] = pd.to_numeric(dfX[c], errors="coerce")
    return dfX.replace([np.inf, -np.inf], np.nan).fillna(0)

def _build_dashboard_response(item_code: str):
    sku_df = _get_sku_df(item_code)

    if sku_df.empty:
        return None

    # last row = "current month to date" in your demo meaning
    cur_row = sku_df.iloc[-1]
    prev_row = sku_df.iloc[-2] if len(sku_df) > 1 else cur_row

    current_actual = _num(cur_row.get("Clean_Demand", 0))
    last_month_actual = _num(prev_row.get("Clean_Demand", 0))

    # MoM
    mom = ((current_actual - last_month_actual) / (last_month_actual + 1e-6)) * 100

    # average sales (historical mean)
    avg_sales = _num(sku_df["Clean_Demand"].mean()) if "Clean_Demand" in sku_df.columns else 0.0

    # next month info
    if "Year" in sku_df.columns and "Month_Number" in sku_df.columns:
        cur_year = int(cur_row["Year"])
        cur_month = int(cur_row["Month_Number"])
        nxt_month = cur_month + 1
        nxt_year = cur_year
        if nxt_month > 12:
            nxt_month = 1
            nxt_year += 1
        next_label = _month_label(nxt_year, nxt_month)
        current_label = _month_label(cur_year, cur_month)
        last_label = _month_label(int(prev_row["Year"]), int(prev_row["Month_Number"]))
    else:
        next_label = "next"
        current_label = str(cur_row.get("Month", "current"))
        last_label = str(prev_row.get("Month", "last"))

    # predict next month using features of latest known month
    X = _prepare_latest_X(sku_df)
    forecast_next_month = float(model.predict(X)[0])

    # bonus / shocks KPIs (separate for current & last)
    # Use what you have: Bonus_Flag, Supply_Shock, Supply_Constraint_Flag
    bonus_qty_cur = _num(cur_row.get("Free_Qty", 0))
    bonus_qty_last = _num(prev_row.get("Free_Qty", 0))

    bonus_shock_cur = int(_num(cur_row.get("Bonus_Shock", 0)))
    bonus_shock_last = int(_num(prev_row.get("Bonus_Shock", 0)))

    supply_shock_cur = int(_num(cur_row.get("Supply_Shock", 0)))
    supply_shock_last = int(_num(prev_row.get("Supply_Shock", 0)))
    
    # last 12 months slice (for charts)
    tail = sku_df.tail(12).copy()

    # Chart 1: Sales trend (actual last 12 + one forecast point)
    sales_trend = []
    if "Year" in tail.columns and "Month_Number" in tail.columns:
        for _, r in tail.iterrows():
            sales_trend.append({
                "period": _month_label(r["Year"], r["Month_Number"]),
                "label": _month_label(r["Year"], r["Month_Number"]),
                "actual": _num(r.get("Clean_Demand", 0)),
                "predicted": None
            })
        sales_trend.append({
            "period": next_label,
            "label": next_label,
            "actual": None,
            "predicted": round(forecast_next_month, 2),
            "isForecast": True
        })
    else:
        for _, r in tail.iterrows():
            sales_trend.append({
                "period": str(r.get("Month")),
                "label": str(r.get("Month")),
                "actual": _num(r.get("Clean_Demand", 0)),
                "predicted": None
            })
        sales_trend.append({
            "period": next_label,
            "label": next_label,
            "actual": None,
            "predicted": round(forecast_next_month, 2),
            "isForecast": True
        })

    # Chart 2: Inventory trend (past only)
    inventory_trend = []
    for _, r in tail.iterrows():
        inventory_trend.append({
            "label": _month_label(r["Year"], r["Month_Number"]) if ("Year" in tail.columns and "Month_Number" in tail.columns) else str(r.get("Month")),
            "primaryInventory": _num(r.get("Available_Primary_Inventory_Qty", 0)),
            "distInventory": _num(r.get("Distributor_Inventory_Qty", 0)),
            "inventoryPressure": _num(r.get("Inventory_Pressure", 0))
        })

    # Chart 3: Bonus & shock trend (past only)
    # If Free_Qty exists, use it, else show 0
    shock_trend = []
    for _, r in tail.iterrows():
        shock_trend.append({
            "label": _month_label(r["Year"], r["Month_Number"]) if ("Year" in tail.columns and "Month_Number" in tail.columns) else str(r.get("Month")),
            "bonusQty": _num(r.get("Free_Qty", 0)),
            "bonusFlag": int(_num(r.get("Bonus_Flag", 0))),
            "supplyFlag": int(_num(r.get("Supply_Shock", r.get("Supply_Constraint_Flag", 0))))
        })

    response = {
        "item_code": str(item_code),

        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

        # KPI
        "next_month_forecast": round(forecast_next_month, 2),
        "next_month_label": next_label,

        "current_month_actual": round(current_actual, 2),
        "current_month_label": current_label,

        "last_month_actual": round(last_month_actual, 2),
        "last_month_label": last_label,

        "mom_change": round(float(mom), 2),
        "avg_monthly_sales": round(float(avg_sales), 2),

        "bonus_qty_current_month": round(bonus_qty_cur, 2),
        "bonus_qty_last_month": round(bonus_qty_last, 2),
        "bonus_shock_current_month": bonus_shock_cur,
        "bonus_shock_last_month": bonus_shock_last,

        "supply_shock_current_month": supply_shock_cur,
        "supply_shock_last_month": supply_shock_last,

        # charts
        "sales_trend": sales_trend,
        "inventory_trend": inventory_trend,
        "shock_trend": shock_trend,
    }

    return response

def _ensure_feature_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if "ABC_Class" not in df.columns:
        df["ABC_Class"] = 2

    for c in feature_cols:
        if c not in df.columns:
            df[c] = 0

    # 🔥 enforce numeric types for model columns
    for c in feature_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df[feature_cols] = df[feature_cols].replace([np.inf, -np.inf], np.nan).fillna(0)

    return df

def _reload_data_into_memory():
    """Reload processed_data.csv into global `data`."""
    global data
    df = pd.read_csv(DATA_PATH)
    df["ItemCode"] = _normalize_itemcode(df["ItemCode"])
    df = _ensure_feature_columns(df)
    data = df
    return {
        "rows": int(len(df)),
        "unique_skus": int(df["ItemCode"].nunique()),
        "min_year": int(df["Year"].min()) if "Year" in df.columns and len(df) else None,
        "max_year": int(df["Year"].max()) if "Year" in df.columns and len(df) else None,
    }

def _build_abc_map(df: pd.DataFrame):
    x = pd.to_numeric(df["Clean_Demand"], errors="coerce").fillna(0)

    sku_total = (
        df.assign(Clean_Demand=x)
          .groupby("ItemCode")["Clean_Demand"]
          .sum()
          .sort_values(ascending=False)
    )

    total = float(sku_total.sum())
    if total <= 0 or np.isnan(total):
        return {k: 2 for k in sku_total.index}  # all C

    cum_pct = (sku_total.cumsum() / total).clip(0, 1)

    abc_map = {}
    for sku, p in cum_pct.items():
        if p <= 0.7:
            abc_map[sku] = 0
        elif p <= 0.9:
            abc_map[sku] = 1
        else:
            abc_map[sku] = 2
    return abc_map

def _encode_itemcode(df: pd.DataFrame, categories: list) -> pd.DataFrame:
    """
    Encode ItemCode -> int using stable category list.
    Unknowns become 0 (or you can use -1, but keep consistent).
    """
    df = df.copy()
    item_to_id = {c: i for i, c in enumerate(categories)}
    df["ItemCode"] = df["ItemCode"].map(item_to_id).fillna(0).astype(int)
    return df

def build_processed_data_from_raw(raw_df: pd.DataFrame) -> pd.DataFrame:
    df = raw_df.copy()

    # ---- basic cleanup ----
    df["ItemCode"] = df["ItemCode"].astype(str).str.strip()
    df = df.sort_values(["ItemCode", "Year", "Month_Number"])

    # ---- Demand Signal Engineering ----
    df["Secondary_Sales_Qty"] = pd.to_numeric(df["Secondary_Sales_Qty"], errors="coerce").fillna(0)
    df["Free_Qty"] = pd.to_numeric(df.get("Free_Qty", 0), errors="coerce").fillna(0)

    df["Bonus_Flag"] = pd.to_numeric(df.get("Bonus_Flag", 0), errors="coerce").fillna(0).astype(int)
    df["Supply_Constraint_Flag"] = pd.to_numeric(df.get("Supply_Constraint_Flag", 0), errors="coerce").fillna(0).astype(int)

    # Start with base demand
    df["Effective_Demand"] = df["Secondary_Sales_Qty"].clip(lower=0)

    # Rolling 3 month avg secondary sales
    df["Rolling3M_SecSales"] = (
        df.groupby("ItemCode")["Secondary_Sales_Qty"]
          .transform(lambda x: x.rolling(3, min_periods=1).mean().shift(1))
    )

    # Supply constraint adjustment
    df["Effective_Demand"] = np.where(
        df["Supply_Constraint_Flag"] == 1,
        np.minimum(df["Effective_Demand"], df["Rolling3M_SecSales"].fillna(df["Effective_Demand"])),
        df["Effective_Demand"]
    )

    # ---- Demand Cleansing Layer ----
    df["Rolling3M_Avg"] = (
        df.groupby("ItemCode")["Effective_Demand"]
          .transform(lambda x: x.rolling(3, min_periods=1).mean().shift(1))
    )

    df["Rolling3M_Std_Eff"] = (
        df.groupby("ItemCode")["Effective_Demand"]
          .transform(lambda x: x.rolling(3, min_periods=1).std().shift(1))
    ).fillna(0)

    df["Z_Score"] = (df["Effective_Demand"] - df["Rolling3M_Avg"]) / (df["Rolling3M_Std_Eff"] + 1)

    df["Next_Month_Drop"] = (
        df.groupby("ItemCode")["Effective_Demand"].shift(-1) < 0.7 * df["Effective_Demand"]
    ).fillna(False)

    df["Clean_Demand"] = df["Effective_Demand"]

    bonus_spike_condition = (
        (df["Bonus_Flag"] == 1) &
        (df["Z_Score"] > 2) &
        (df["Next_Month_Drop"])
    )

    df.loc[bonus_spike_condition, "Clean_Demand"] = (
        0.7 * df.loc[bonus_spike_condition, "Effective_Demand"] +
        0.3 * df.loc[bonus_spike_condition, "Rolling3M_Avg"]
    )

    sales_drop_condition = (
        (df["Effective_Demand"] < 0.6 * df.groupby("ItemCode")["Effective_Demand"].shift(1)) &
        (df["Supply_Constraint_Flag"] == 1) &
        (df["Next_Month_Drop"])
    )

    df.loc[sales_drop_condition, "Clean_Demand"] = df["Rolling3M_Avg"]
    df["Clean_Demand"] = pd.to_numeric(df["Clean_Demand"], errors="coerce").fillna(0).clip(lower=0)

    # shock indicators
    df["Bonus_Shock"] = bonus_spike_condition.astype(int)
    df["Supply_Shock"] = sales_drop_condition.astype(int)

    # ---- Feature Engineering ----
    df["Target"] = df.groupby("ItemCode")["Clean_Demand"].shift(-1)

    for lag in [1, 2, 3, 6, 12]:
        df[f"Lag{lag}"] = df.groupby("ItemCode")["Clean_Demand"].shift(lag)

    df["Rolling3M_Mean"] = df.groupby("ItemCode")["Clean_Demand"].transform(lambda x: x.rolling(3).mean().shift(1))
    df["Rolling6M_Mean"] = df.groupby("ItemCode")["Clean_Demand"].transform(lambda x: x.rolling(6).mean().shift(1))

    df["Rolling3M_Std"] = df.groupby("ItemCode")["Clean_Demand"].transform(lambda x: x.rolling(3).std().shift(1))
    if df["Rolling3M_Std"].notna().any():
        df["Rolling3M_Std"] = df["Rolling3M_Std"].clip(upper=df["Rolling3M_Std"].quantile(0.99))

    df["Momentum"] = df["Lag1"] - df["Lag3"]

    df["Month_Number"] = pd.to_numeric(df["Month_Number"], errors="coerce")
    df["Month_Sin"] = np.sin(2 * np.pi * df["Month_Number"] / 12)
    df["Month_Cos"] = np.cos(2 * np.pi * df["Month_Number"] / 12)

    df["Available_Primary_Inventory_Qty"] = pd.to_numeric(df.get("Available_Primary_Inventory_Qty", 0), errors="coerce").fillna(0)
    df["Distributor_Inventory_Qty"] = pd.to_numeric(df.get("Distributor_Inventory_Qty", 0), errors="coerce").fillna(0)
    df["Blocked_Stock_Qty"] = pd.to_numeric(df.get("Blocked_Stock_Qty", 0), errors="coerce").fillna(0)

    df["Inventory_Pressure"] = np.where(
        df["Lag1"].fillna(0) == 0,
        0,
        df["Available_Primary_Inventory_Qty"] / (df["Lag1"].fillna(0) + 1)
    )
    if df["Inventory_Pressure"].notna().any():
        df["Inventory_Pressure"] = df["Inventory_Pressure"].clip(upper=df["Inventory_Pressure"].quantile(0.99))

    # ---- Drop NaNs like notebook ----
    required_cols = ["Target", "Lag1", "Lag2", "Lag3", "Lag6", "Lag12", "Rolling3M_Mean", "Rolling6M_Mean", "Rolling3M_Std"]
    df = df.dropna(subset=required_cols).copy()
    df = df[df["Target"] >= 0].copy()

    return df


# ─── End Ponits ────────────────────────────────────────────────── 

# Dashboard Endpoint (main)

@app.route("/dashboard", methods=["POST"])
def dashboard():
    body = request.get_json() or {}
    item_code = body.get("item_code")

    if not item_code:
        return jsonify({"error": "item_code required"}), 400

    result = _build_dashboard_response(item_code)
    if result is None:
        return jsonify({"error": "Item not found"}), 404

    return jsonify(result)


@app.route("/skus", methods=["GET"])
def skus():
    # return unique itemcodes
    items = sorted(data["ItemCode"].unique().tolist())
    return jsonify({"skus": items})

@app.route("/reload_data", methods=["POST"])
def reload_data():
    """Reload CSV into memory (fast)."""
    with LOCK:
        info = _reload_data_into_memory()
    return jsonify({"ok": True, "message": "Data reloaded", **info})

@app.route("/refresh_model", methods=["POST"])
def refresh_model():
    """
    Retrain model using latest processed_data.csv + saved best_params.
    Does NOT run Optuna.
    Updates model_artifacts.pkl and in-memory model.
    """
    global model, artifacts, itemcode_categories, abc_map, data

    with LOCK:

        ok, remaining, last = _cooldown_ok("retrain", cooldown_days=7)
        if not ok:
            return jsonify({
                "ok": False,
                "error": "Retrain already done recently",
                "cooldown": "weekly",
                "last_trained": last,
                "days_remaining": remaining
            }), 429

        # 1) reload data
        info = _reload_data_into_memory()
        df = data.copy()

        # 2) rebuild mappings from latest data
        abc_map_new = _build_abc_map(df)
        df["ABC_Class"] = df["ItemCode"].map(abc_map_new).fillna(2).astype(int)

        itemcode_categories_new = sorted(df["ItemCode"].unique().tolist())
        df_encoded = _encode_itemcode(df, itemcode_categories_new)

        # 3) train frame (must have Target)
        if TARGET_COL not in df_encoded.columns:
            return jsonify({"ok": False, "error": f"'{TARGET_COL}' column not found in processed_data.csv"}), 400

        train_df = df_encoded.dropna(subset=[TARGET_COL]).copy()
        train_df = _ensure_feature_columns(train_df)

        X_train = _sanitize_X(train_df[feature_cols])
        y_train = pd.to_numeric(train_df[TARGET_COL], errors="coerce").fillna(0).astype(float)

        print("Feature sanity:")
        print(train_df[feature_cols].describe().T[["min","max"]])

        # 4) params
        best_params = artifacts.get("best_params", {})
        # keep these stable
        params = {
            "objective": "reg:tweedie",
            "eval_metric": "rmse",
            "random_state": 42,
            **best_params
        }

        # Safety Check
        if len(train_df) == 0:
            return jsonify({
                "ok": False,
                "error": "No valid training rows after preprocessing"
            }), 400

        # 5) retrain
        new_model = xgb.XGBRegressor(**params)
        new_model.fit(X_train, y_train, verbose=False)

        # 6) save + swap in memory
        new_artifacts = {
            **artifacts,
            "model": new_model,
            "feature_cols": feature_cols,
            "best_params": best_params,
            "itemcode_categories": itemcode_categories_new,
            "abc_map": abc_map_new,
        }

        joblib.dump(new_artifacts, MODEL_PATH)

        # swap globals
        artifacts = new_artifacts
        model = new_model
        itemcode_categories = itemcode_categories_new
        abc_map = abc_map_new

    return jsonify({
        "ok": True,
        "message": "Model refreshed (retrained) using latest data + saved best_params",
        **info
    })

@app.route("/retune_model", methods=["POST"])
def retune_model():
    """
    Run hyperparameter tuning (monthly).
    Updates best_params and retrains model.
    """

    global model, artifacts, itemcode_categories, abc_map, data

    with LOCK:

        # 🔒 Monthly cooldown (30 days)
        ok, remaining, last = _cooldown_ok("retune", cooldown_days=30)
        if not ok:
            return jsonify({
                "ok": False,
                "error": "Retune already done recently",
                "cooldown": "monthly",
                "last_retuned": last,
                "days_remaining": remaining
            }), 429

        # 1️⃣ Reload latest processed data
        info = _reload_data_into_memory()
        df = data.copy()

        if TARGET_COL not in df.columns:
            return jsonify({
                "ok": False,
                "error": f"{TARGET_COL} not found in processed_data.csv"
            }), 400

        # 2️⃣ Rebuild ABC + encoding
        abc_map_new = _build_abc_map(df)
        df["ABC_Class"] = df["ItemCode"].map(abc_map_new).fillna(2).astype(int)

        itemcode_categories_new = sorted(df["ItemCode"].unique().tolist())
        df_encoded = _encode_itemcode(df, itemcode_categories_new)

        train_df = df_encoded.dropna(subset=[TARGET_COL]).copy()
        train_df = _ensure_feature_columns(train_df)

        if len(train_df) == 0:
            return jsonify({
                "ok": False,
                "error": "No valid rows available for tuning"
            }), 400

        X_train = _sanitize_X(train_df[feature_cols])
        y_train = pd.to_numeric(train_df[TARGET_COL], errors="coerce").fillna(0)

        # 3️⃣ Simple lightweight tuning (safe for production)
        param_grid = [
            {"max_depth": 4, "learning_rate": 0.05, "n_estimators": 300},
            {"max_depth": 5, "learning_rate": 0.05, "n_estimators": 400},
            {"max_depth": 6, "learning_rate": 0.03, "n_estimators": 500},
            {"max_depth": 5, "learning_rate": 0.03, "n_estimators": 600},
        ]

        best_rmse = float("inf")
        best_params = None
        best_model = None

        for p in param_grid:
            params = {
                "objective": "reg:tweedie",
                "eval_metric": "rmse",
                "random_state": 42,
                **p
            }

            m = xgb.XGBRegressor(**params)
            m.fit(X_train, y_train, verbose=False)

            preds = m.predict(X_train)
            rmse = np.sqrt(np.mean((y_train - preds) ** 2))

            if rmse < best_rmse:
                best_rmse = rmse
                best_params = p
                best_model = m

        # 4️⃣ Save new artifacts
        new_artifacts = {
            **artifacts,
            "model": best_model,
            "best_params": best_params,
            "feature_cols": feature_cols,
            "itemcode_categories": itemcode_categories_new,
            "abc_map": abc_map_new,
        }

        joblib.dump(new_artifacts, MODEL_PATH)

        # swap globals
        artifacts = new_artifacts
        model = best_model
        itemcode_categories = itemcode_categories_new
        abc_map = abc_map_new

    return jsonify({
        "ok": True,
        "message": "Model retuned successfully (monthly)",
        "best_params": best_params,
        "train_rmse": round(best_rmse, 4),
        **info
    })

@app.route("/reload_model", methods=["POST"])
def reload_model():
    global artifacts, model, feature_cols, itemcode_categories, abc_map
    with LOCK:
        artifacts = joblib.load(MODEL_PATH)
        model = artifacts["model"]
        feature_cols = artifacts["feature_cols"]
        itemcode_categories = artifacts.get("itemcode_categories", None)
        abc_map = artifacts.get("abc_map", None)
    return jsonify({"ok": True, "message": "Model artifacts reloaded"})

@app.route("/process_raw", methods=["POST"])
def process_raw():
    """
    Reads raw Company Data (xlsx or csv) -> builds processed_data.csv -> reloads into memory.
    Does NOT retrain. Use /refresh_model after this.
    """
    with LOCK:
        # choose source
        if os.path.exists(RAW_XLSX_PATH):
            raw_df = pd.read_excel(RAW_XLSX_PATH)
        elif os.path.exists(RAW_CSV_PATH):
            raw_df = pd.read_csv(RAW_CSV_PATH)
        else:
            return jsonify({"ok": False, "error": "No raw file found in /data (Company Data.xlsx or Company Data.csv)"}), 400

        processed = build_processed_data_from_raw(raw_df)

        # save processed_data.csv where backend expects it
        processed.to_csv(PROCESSED_PATH, index=False)

        # reload into global memory
        info = _reload_data_into_memory()

    return jsonify({
        "ok": True,
        "message": "Raw data processed -> processed_data.csv generated + reloaded",
        "processed_rows": int(len(processed)),
        **info
    })


# ─── Health Check ────────────────────────────────────────────────── 

@app.route("/health", methods=["GET"])
def health():
    try:
        global model, artifacts, data, feature_cols

        health_info = {}

        # 1️⃣ Model loaded?
        health_info["model_loaded"] = model is not None

        # 2️⃣ Artifact keys
        health_info["artifact_keys"] = list(artifacts.keys())

        # 3️⃣ Feature consistency
        missing_features = [c for c in feature_cols if c not in data.columns]
        health_info["missing_features_in_data"] = missing_features

        # 4️⃣ Data status
        health_info["rows"] = int(len(data))
        health_info["unique_skus"] = int(data["ItemCode"].nunique()) if "ItemCode" in data.columns else 0

        # 5️⃣ Sample prediction sanity test
        try:
            sample = data.iloc[-1:]
            X = sample[feature_cols]
            X = X.apply(pd.to_numeric, errors="coerce").fillna(0)
            test_pred = float(model.predict(X)[0])
            health_info["sample_prediction"] = round(test_pred, 4)
            health_info["prediction_status"] = "OK"
        except Exception as e:
            health_info["prediction_status"] = f"FAILED: {str(e)}"

        health_info["status"] = "HEALTHY"

        return jsonify(health_info)

    except Exception as e:
        return jsonify({
            "status": "UNHEALTHY",
            "error": str(e)
        }), 500


@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Demand Forecast Engine Running"})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
