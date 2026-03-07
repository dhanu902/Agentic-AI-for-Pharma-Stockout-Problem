 # backend/engines/demand_forecast_engine.py

import os
import joblib
import pandas as pd
import numpy as np
from datetime import datetime
import threading
import xgboost as xgb
from typing import Optional, List

# ─── Paths ────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))          # .../backend/engines
BACKEND_DIR = os.path.dirname(BASE_DIR)                        # .../backend
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)                    # project root

MODEL_PATH = os.path.join(BACKEND_DIR, "models", "model_artifacts.pkl")
DATA_PATH = os.path.join(BACKEND_DIR, "data", "processed_data.csv")
STATE_PATH = os.path.join(BACKEND_DIR, "models", "train_state.json")

RAW_XLSX_PATH = os.path.join(PROJECT_ROOT, "data", "Company Data.xlsx")
RAW_CSV_PATH  = os.path.join(PROJECT_ROOT, "data", "Company Data.csv")
PROCESSED_PATH = DATA_PATH
FORECAST_LATEST_PATH = os.path.join(BACKEND_DIR, "data", "forecast_latest.csv")

LOCK = threading.Lock()
TARGET_COL = "Target"
ALLOW_FUTURE_VALIDATION = False

# ─── Load artifacts/data once ─────────────────────────────────────

artifacts = joblib.load(MODEL_PATH)
model = artifacts["model"]
feature_cols = artifacts["feature_cols"]
clip_caps = artifacts.get("clip_caps", None)
abc_map = artifacts.get("abc_map", None)
itemcode_categories = artifacts.get("itemcode_categories", None)

if itemcode_categories is not None:
    itemcode_categories = [str(x).strip().replace(".0", "") for x in list(itemcode_categories)]
if abc_map is not None:
    abc_map = {str(k).strip().replace(".0", ""): int(v) for k, v in abc_map.items()}

data = pd.read_csv(DATA_PATH)

# ─── Helpers ─────────────────────────────────────────────────────

def _normalize_itemcode(series):
    return (
        series.astype(str)
              .str.strip()
              .str.replace(r"\.0$", "", regex=True)
    )
def assert_required_columns(df: pd.DataFrame, required_cols: list, where=""):
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise KeyError(f"[{where}] Missing required columns: {missing}")
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
        return {k: 2 for k in sku_total.index}

    cum_pct = (sku_total.cumsum() / total).clip(0, 1)

    out = {}
    for sku, p in cum_pct.items():
        if p <= 0.7:
            out[sku] = 0
        elif p <= 0.9:
            out[sku] = 1
        else:
            out[sku] = 2
    return out

# normalize once
data["ItemCode"] = _normalize_itemcode(data["ItemCode"])
data["ItemCode_key"] = data["ItemCode"]

# ensure ABC exists
if "ABC_Class" not in data.columns:
    abc_map_local = _build_abc_map(data)
    abc_map_local = {str(k).strip().replace(".0", ""): int(v) for k, v in abc_map_local.items()}
    data["ABC_Class"] = data["ItemCode"].map(abc_map_local).fillna(2).astype(int)
else:
    data["ABC_Class"] = pd.to_numeric(data["ABC_Class"], errors="coerce").fillna(2).astype(int)

assert_required_columns(data, feature_cols, where="processed_data.csv")

def _load_state():
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        return joblib.load(STATE_PATH)
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

def _month_label(year, month_num):
    return f"{int(year):04d}-{int(month_num):02d}"

def _num(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return float(default)

def _sanitize_X(X: pd.DataFrame) -> pd.DataFrame:
    X = X.copy()
    for c in X.columns:
        X[c] = pd.to_numeric(X[c], errors="coerce")
    return X.replace([np.inf, -np.inf], np.nan).fillna(0)

def _ensure_feature_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if "ABC_Class" not in df.columns:
        df["ABC_Class"] = 2

    for c in feature_cols:
        if c not in df.columns:
            df[c] = 0

    for c in feature_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df[feature_cols] = df[feature_cols].replace([np.inf, -np.inf], np.nan).fillna(0)
    return df

def _reload_data_into_memory():
    global data

    df = pd.read_csv(DATA_PATH)
    df["ItemCode"] = _normalize_itemcode(df["ItemCode"])
    df["ItemCode_key"] = df["ItemCode"]

    if "ABC_Class" not in df.columns:
        abc_map_local = _build_abc_map(df)
        df["ABC_Class"] = df["ItemCode"].map(abc_map_local).fillna(2).astype(int)
    else:
        df["ABC_Class"] = pd.to_numeric(df["ABC_Class"], errors="coerce").fillna(2).astype(int)

    df = _ensure_feature_columns(df)
    data = df

    return {
        "rows": int(len(df)),
        "unique_skus": int(df["ItemCode"].nunique()),
        "min_year": int(df["Year"].min()) if "Year" in df.columns and len(df) else None,
        "max_year": int(df["Year"].max()) if "Year" in df.columns and len(df) else None,
    }

def _get_sku_df(item_code: str) -> pd.DataFrame:
    item_key = str(item_code).strip().replace(".0", "")
    sku_df = data[data["ItemCode_key"] == item_key].copy()

    if sku_df.empty:
        return sku_df

    if {"Year", "Month_Number"}.issubset(sku_df.columns):
        sku_df = sku_df.sort_values(["Year", "Month_Number"])
    elif "Month" in sku_df.columns:
        sku_df = sku_df.sort_values("Month")

    return sku_df.reset_index(drop=True)

def _encode_itemcode(df: pd.DataFrame, categories: list) -> pd.DataFrame:
    df = df.copy()
    categories = [str(x).strip().replace(".0", "") for x in categories]
    unk_code = len(categories)
    item_to_id = {c: i for i, c in enumerate(categories)}
    df["ItemCode"] = df["ItemCode"].map(item_to_id).fillna(unk_code).astype(int)
    return df

def compute_clip_caps(train_df, cols, q=0.99):
    caps = {}
    for c in cols:
        if c in train_df.columns:
            s = pd.to_numeric(train_df[c], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
            if len(s):
                caps[c] = float(s.quantile(q))
    return caps

def _apply_clip_caps(df: pd.DataFrame, caps: dict) -> pd.DataFrame:
    if not caps:
        return df
    df = df.copy()
    for c, cap in caps.items():
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).clip(upper=float(cap))
    return df

def recency_weights(df, yearly_boost=0.25, base=1.0):
    y0 = int(df["Year"].min())
    return base + (df["Year"] - y0) * yearly_boost

def _encode_itemcode_value(raw_code: str, categories: Optional[List[str]]) -> int:
    if categories is None:
        try:
            return int(float(raw_code))
        except Exception:
            return 0
    raw_code = str(raw_code).strip().replace(".0", "")
    codes = pd.Categorical([raw_code], categories=categories).codes
    unk = len(categories)
    return int(codes[0]) if codes[0] != -1 else int(unk)

def build_processed_data_from_raw(raw_df: pd.DataFrame) -> pd.DataFrame:
    df = raw_df.copy()

    # ---- basic cleanup ----
    df["ItemCode"] = (df["ItemCode"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True))
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

    if ALLOW_FUTURE_VALIDATION:
        df["Next_Month_Drop"] = (df.groupby("ItemCode")["Effective_Demand"].shift(-1) < 0.7 * df["Effective_Demand"]).fillna(False)
    else:
        df["Next_Month_Drop"] = False

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

    # ---- ZERO BEHAVIOR FEATURES ----
    df["Is_Zero"] = (df["Clean_Demand"] == 0).astype(int)
    df["ZeroRate_6M"] = (
        df.groupby("ItemCode")["Is_Zero"].transform(lambda x: x.rolling(6, min_periods=1).mean().shift(1))).fillna(0)

    # ---- NET AVAILABLE STOCK + COVER ----
    df["Total_Primary_Inventory_Qty"] = pd.to_numeric(df.get("Total_Primary_Inventory_Qty", 0), errors="coerce").fillna(0)
    df["Inspection_Stock_Qty"] = pd.to_numeric(df.get("Inspection_Stock_Qty", 0), errors="coerce").fillna(0)

    df["Net_Available_Stock"] = (df["Total_Primary_Inventory_Qty"] - df["Blocked_Stock_Qty"] - df["Inspection_Stock_Qty"]).clip(lower=0)

    df["Stock_Cover_Months"] = np.where(df["Rolling3M_Mean"].fillna(0) == 0,
                                        0,
                                        df["Net_Available_Stock"] / (df["Rolling3M_Mean"] + 1)
                                        )
    
    abc_map_local = _build_abc_map(df)
    df["ABC_Class"] = df["ItemCode"].map(abc_map_local).fillna(2).astype(int)

    # ---- Drop NaNs like notebook ----
    required_cols = ["Target", "Lag1", "Lag2", "Lag3", "Lag6", "Lag12", "Rolling3M_Mean", "Rolling6M_Mean", "Rolling3M_Std"]
    df = df.dropna(subset=required_cols).copy()
    df = df[df["Target"] >= 0].copy()

    return df

def _processed_is_fresh() -> bool:
    if not os.path.exists(PROCESSED_PATH):
        return False
    if not (os.path.exists(RAW_XLSX_PATH) or os.path.exists(RAW_CSV_PATH)):
        return True  # no raw found, don't block here
    raw_path = RAW_XLSX_PATH if os.path.exists(RAW_XLSX_PATH) else RAW_CSV_PATH
    return os.path.getmtime(PROCESSED_PATH) >= os.path.getmtime(raw_path)

# ─── Forecast inference ───────────────────────────────────────────

def prepare_next_month_X(sku_df: pd.DataFrame) -> pd.DataFrame:
    if sku_df is None or sku_df.empty:
        raise ValueError("sku_df is empty")

    df = sku_df.copy().sort_values(["Year", "Month_Number"])

    if "ABC_Class" not in df.columns:
        df["ABC_Class"] = 2
    df["ABC_Class"] = pd.to_numeric(df["ABC_Class"], errors="coerce").fillna(2).astype(int)

    last_row = df.iloc[-1:].copy()
    cur_year = int(last_row["Year"].iloc[0])
    cur_month = int(last_row["Month_Number"].iloc[0])

    nxt_month = cur_month + 1
    nxt_year = cur_year
    if nxt_month > 12:
        nxt_month = 1
        nxt_year += 1

    new_row = last_row.copy()
    new_row["Year"] = nxt_year
    new_row["Month_Number"] = nxt_month

    df_ext = pd.concat([df, new_row], ignore_index=True)
    df_ext = df_ext.sort_values(["ItemCode", "Year", "Month_Number"]).copy()

    for lag in [1, 2, 3, 6, 12]:
        df_ext[f"Lag{lag}"] = df_ext.groupby("ItemCode")["Clean_Demand"].shift(lag)

    df_ext["Rolling3M_Mean"] = df_ext.groupby("ItemCode")["Clean_Demand"].transform(
        lambda x: x.rolling(3, min_periods=1).mean().shift(1)
    )
    df_ext["Rolling6M_Mean"] = df_ext.groupby("ItemCode")["Clean_Demand"].transform(
        lambda x: x.rolling(6, min_periods=1).mean().shift(1)
    )
    df_ext["Rolling3M_Std"] = df_ext.groupby("ItemCode")["Clean_Demand"].transform(
        lambda x: x.rolling(3, min_periods=1).std().shift(1)
    ).fillna(0)

    df_ext["Momentum"] = df_ext["Lag1"] - df_ext["Lag3"]

    df_ext["Is_Zero"] = (pd.to_numeric(df_ext["Clean_Demand"], errors="coerce").fillna(0) == 0).astype(int)
    df_ext["ZeroRate_6M"] = df_ext.groupby("ItemCode")["Is_Zero"].transform(
        lambda x: x.rolling(6, min_periods=1).mean().shift(1)
    ).fillna(0)

    mn = pd.to_numeric(df_ext["Month_Number"], errors="coerce").fillna(0)
    df_ext["Month_Sin"] = np.sin(2 * np.pi * mn / 12)
    df_ext["Month_Cos"] = np.cos(2 * np.pi * mn / 12)

    lag1 = pd.to_numeric(df_ext["Lag1"], errors="coerce").fillna(0)
    avail = pd.to_numeric(df_ext.get("Available_Primary_Inventory_Qty", 0), errors="coerce").fillna(0)
    df_ext["Inventory_Pressure"] = np.where(lag1 == 0, 0, avail / (lag1 + 1))

    if {"Total_Primary_Inventory_Qty", "Blocked_Stock_Qty", "Inspection_Stock_Qty"}.issubset(df_ext.columns):
        total = pd.to_numeric(df_ext["Total_Primary_Inventory_Qty"], errors="coerce").fillna(0)
        blocked = pd.to_numeric(df_ext["Blocked_Stock_Qty"], errors="coerce").fillna(0)
        insp = pd.to_numeric(df_ext["Inspection_Stock_Qty"], errors="coerce").fillna(0)
        df_ext["Net_Available_Stock"] = (total - blocked - insp).clip(lower=0)

        r3 = pd.to_numeric(df_ext["Rolling3M_Mean"], errors="coerce").fillna(0)
        df_ext["Stock_Cover_Months"] = np.where(r3 == 0, 0, df_ext["Net_Available_Stock"] / (r3 + 1))

    df_ext = _apply_clip_caps(df_ext, clip_caps)

    next_row = df_ext[(df_ext["Year"] == nxt_year) & (df_ext["Month_Number"] == nxt_month)].iloc[-1:].copy()

    next_row["ItemCode"] = _encode_itemcode_value(next_row["ItemCode"].iloc[0], itemcode_categories)

    missing = [c for c in feature_cols if c not in next_row.columns]
    if missing:
        raise KeyError(f"[INFERENCE-next_row] Missing required columns: {missing}")

    return _sanitize_X(next_row[feature_cols].copy())

def forecast_next_month(sku_df: pd.DataFrame) -> float:
    X = prepare_next_month_X(sku_df)
    return float(model.predict(X)[0])

# ─── Dashboard payload builder ────────────────────────────────────

def _build_dashboard_response(item_code: str):
    sku_df = _get_sku_df(item_code)
    if sku_df.empty:
        return None

    item_key = str(item_code).strip().replace(".0", "")
    if "ABC_Class" in sku_df.columns and pd.notna(sku_df["ABC_Class"].iloc[-1]):
        abc_class = int(float(sku_df["ABC_Class"].iloc[-1]))
    elif abc_map is not None and item_key in abc_map:
        abc_class = int(abc_map[item_key])
    else:
        abc_class = 2
    abc_label = {0: "A", 1: "B", 2: "C"}.get(abc_class, "C")

    cur_row = sku_df.iloc[-1]
    prev_row = sku_df.iloc[-2] if len(sku_df) > 1 else cur_row

    current_actual = _num(cur_row.get("Clean_Demand", 0))
    last_month_actual = _num(prev_row.get("Clean_Demand", 0))
    mom = ((current_actual - last_month_actual) / (last_month_actual + 1e-6)) * 100
    avg_sales = _num(sku_df["Clean_Demand"].mean()) if "Clean_Demand" in sku_df.columns else 0.0

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

    forecast_next = forecast_next_month(sku_df)

    bonus_qty_cur = _num(cur_row.get("Free_Qty", 0))
    bonus_qty_last = _num(prev_row.get("Free_Qty", 0))

    bonus_shock_cur = int(_num(cur_row.get("Bonus_Shock", 0)))
    bonus_shock_last = int(_num(prev_row.get("Bonus_Shock", 0)))

    supply_shock_cur = int(_num(cur_row.get("Supply_Shock", 0)))
    supply_shock_last = int(_num(prev_row.get("Supply_Shock", 0)))

    tail = sku_df.tail(12).copy()

    sales_trend = []
    for _, r in tail.iterrows():
        label = _month_label(r["Year"], r["Month_Number"])
        sales_trend.append({"period": label, "label": label, "actual": _num(r.get("Clean_Demand", 0)), "predicted": None})
    sales_trend.append({"period": next_label, "label": next_label, "actual": None, "predicted": round(forecast_next, 2), "isForecast": True})

    inventory_trend = []
    for _, r in tail.iterrows():
        label = _month_label(r["Year"], r["Month_Number"])
        inventory_trend.append({
            "label": label,
            "primaryInventory": _num(r.get("Available_Primary_Inventory_Qty", 0)),
            "distInventory": _num(r.get("Distributor_Inventory_Qty", 0)),
            "inventoryPressure": _num(r.get("Inventory_Pressure", 0)),
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

    recent = sku_df.tail(12)
    sum_demand = float(recent["Clean_Demand"].sum()) if "Clean_Demand" in recent.columns else 0.0
    zero_rate = float((recent["Clean_Demand"] == 0).mean()) if "Clean_Demand" in recent.columns and len(recent) else 1.0
    if abc_label == "C" and (sum_demand == 0 or zero_rate > 0.9):
        demand_status = "Inactive / near-zero demand"
    elif abc_label == "C":
        demand_status = "Low-volume demand"
    else:
        demand_status = "Active demand"

    return {
        "item_code": str(item_code),
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "abc_class": abc_class,
        "abc_category": abc_label,
        "demand_status": demand_status,
        "next_month_forecast": round(forecast_next, 2),
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
        "sales_trend": sales_trend,
        "inventory_trend": inventory_trend,
        "shock_trend": shock_trend,
    }

# ─── Save Forecasts────────────────────────────────────

def export_forecast_all_skus(out_path: Optional[str] = None):
    """
    Generate next-month forecast for every SKU in processed_data.csv.

    Output columns:
      Month, ItemCode, Forecast_Qty, created_at

    If out_path is provided, also saves CSV.
    Returns (df, next_month_label).
    """
    # group on normalized ItemCode already done
    created_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    rows = []
    next_month_label = None

    for item, g in data.groupby("ItemCode"):
        g = g.sort_values(["Year", "Month_Number"]).copy()

        # Need at least 2 rows to have lag/rolling features stable-ish
        if len(g) < 2:
            continue

        try:
            pred = float(forecast_next_month(g))

            # compute next month label once (same for all SKUs)
            if next_month_label is None:
                last = g.iloc[-1]
                cur_year = int(last["Year"])
                cur_month = int(last["Month_Number"])
                m2 = cur_month + 1
                y2 = cur_year
                if m2 > 12:
                    m2 = 1
                    y2 += 1
                next_month_label = _month_label(y2, m2)

            rows.append({
                "Month": next_month_label,
                "ItemCode": str(item),
                "Forecast_Qty": round(pred, 2),
                "created_at": created_at
            })

        except Exception as e:
            # Skip bad SKUs but keep count/debug
            # If you want to see failures: print(item, e)
            continue

    df_forecast = pd.DataFrame(rows)

    if out_path:
        df_forecast.to_csv(out_path, index=False)

    return df_forecast, next_month_label


# ─── PUBLIC API: called by routes ─────────────────────────────────

def get_dashboard(item_code: str):
    return _build_dashboard_response(item_code)

def get_skus():
    return sorted(data["ItemCode"].unique().tolist())

def reload_data_now():
    with LOCK:
        info = _reload_data_into_memory()
    return {"ok": True, "message": "Data reloaded from processed_data.csv", **info}, 200

def reload_model_artifacts():
    global artifacts, model, feature_cols, itemcode_categories, abc_map, clip_caps
    with LOCK:
        artifacts = joblib.load(MODEL_PATH)
        model = artifacts["model"]
        feature_cols = artifacts["feature_cols"]
        itemcode_categories = artifacts.get("itemcode_categories", None)
        abc_map = artifacts.get("abc_map", None)
        clip_caps = artifacts.get("clip_caps", None)

        if itemcode_categories is not None:
            itemcode_categories = [str(x).strip().replace(".0", "") for x in list(itemcode_categories)]
        if abc_map is not None:
            abc_map = {str(k).strip().replace(".0", ""): int(v) for k, v in abc_map.items()}
    return {"ok": True, "message": "Model artifacts reloaded"}

def refresh_model_now():
    global model, artifacts, itemcode_categories, abc_map, clip_caps

    with LOCK:
        if not _processed_is_fresh():
            return {"ok": False, "error": "Processed data is older than raw. Click 'Process Raw' first."}, 400
        
        ok, remaining, last = _cooldown_ok("retrain", cooldown_days=7)
        if not ok:
            return {
                "ok": False,
                "error": "Retrain already done recently",
                "cooldown": "weekly",
                "last_trained": last,
                "days_remaining": remaining
            }, 429

        info = _reload_data_into_memory()
        df = data.copy()

        abc_map_new = _build_abc_map(df)
        df["ABC_Class"] = df["ItemCode"].map(abc_map_new).fillna(2).astype(int)

        itemcode_categories_new = sorted(df["ItemCode"].unique().tolist())
        df_encoded = _encode_itemcode(df, itemcode_categories_new)

        if TARGET_COL not in df_encoded.columns:
            return {"ok": False, "error": f"'{TARGET_COL}' column not found in processed_data.csv"}, 400

        train_df = df_encoded.dropna(subset=[TARGET_COL]).copy()
        train_df = _ensure_feature_columns(train_df)

        caps_new = compute_clip_caps(train_df, cols=["Inventory_Pressure", "Stock_Cover_Months"], q=0.99)
        clip_caps = caps_new
        train_df = _apply_clip_caps(train_df, caps_new)

        if len(train_df) == 0:
            return {"ok": False, "error": "No valid training rows after preprocessing"}, 400

        X_train = _sanitize_X(train_df[feature_cols])
        y_train = pd.to_numeric(train_df[TARGET_COL], errors="coerce").fillna(0).astype(float)

        best_params = artifacts.get("best_params", {})
        params = {"objective": "reg:tweedie", "eval_metric": "rmse", "random_state": 42, **best_params}

        w_train = recency_weights(train_df, yearly_boost=0.25).astype(float)
        w_train *= np.where(train_df["ABC_Class"] == 0, 2.5, np.where(train_df["ABC_Class"] == 1, 1.2, 1.0))

        new_model = xgb.XGBRegressor(**params)
        new_model.fit(X_train, y_train, sample_weight=w_train, verbose=False)

        new_artifacts = {
            **artifacts,
            "model": new_model,
            "feature_cols": feature_cols,
            "best_params": best_params,
            "itemcode_categories": itemcode_categories_new,
            "clip_caps": caps_new,
            "abc_map": abc_map_new,
        }
        joblib.dump(new_artifacts, MODEL_PATH)

        artifacts = new_artifacts
        model = new_model
        itemcode_categories = itemcode_categories_new
        abc_map = abc_map_new

    return {"ok": True, "message": "Model refreshed (retrained) using latest data + saved best_params", **info}, 200

def retune_model_now():
    global model, artifacts, itemcode_categories, abc_map, clip_caps

    with LOCK:
        if not _processed_is_fresh():
            return {"ok": False, "error": "Processed data is older than raw. Click 'Process Raw' first."}, 400
     
        ok, remaining, last = _cooldown_ok("retune", cooldown_days=30)
        if not ok:
            return {
                "ok": False,
                "error": "Retune already done recently",
                "cooldown": "monthly",
                "last_retuned": last,
                "days_remaining": remaining
            }, 429

        info = _reload_data_into_memory()
        df = data.copy()

        if TARGET_COL not in df.columns:
            return {"ok": False, "error": f"{TARGET_COL} not found in processed_data.csv"}, 400

        abc_map_new = _build_abc_map(df)
        df["ABC_Class"] = df["ItemCode"].map(abc_map_new).fillna(2).astype(int)

        itemcode_categories_new = sorted(df["ItemCode"].unique().tolist())
        df_encoded = _encode_itemcode(df, itemcode_categories_new)

        train_df = df_encoded.dropna(subset=[TARGET_COL]).copy()
        train_df = _ensure_feature_columns(train_df)

        caps_new = compute_clip_caps(train_df, cols=["Inventory_Pressure", "Stock_Cover_Months"], q=0.99)
        clip_caps = caps_new
        train_df = _apply_clip_caps(train_df, caps_new)

        if len(train_df) == 0:
            return {"ok": False, "error": "No valid rows available for tuning"}, 400

        X_train = _sanitize_X(train_df[feature_cols])
        y_train = pd.to_numeric(train_df[TARGET_COL], errors="coerce").fillna(0).astype(float)

        param_grid = [
            {"max_depth": 4, "learning_rate": 0.05, "n_estimators": 300},
            {"max_depth": 5, "learning_rate": 0.05, "n_estimators": 400},
            {"max_depth": 6, "learning_rate": 0.03, "n_estimators": 500},
            {"max_depth": 5, "learning_rate": 0.03, "n_estimators": 600},
        ]

        w_train = recency_weights(train_df, yearly_boost=0.25).astype(float)
        w_train *= np.where(train_df["ABC_Class"] == 0, 2.5, np.where(train_df["ABC_Class"] == 1, 1.2, 1.0))

        best_rmse, best_params, best_model = float("inf"), None, None

        for p in param_grid:
            params = {"objective": "reg:tweedie", "eval_metric": "rmse", "random_state": 42, **p}
            m = xgb.XGBRegressor(**params)
            m.fit(X_train, y_train, sample_weight=w_train, verbose=False)
            preds = m.predict(X_train)
            rmse = float(np.sqrt(np.mean((y_train - preds) ** 2)))
            if rmse < best_rmse:
                best_rmse, best_params, best_model = rmse, p, m

        new_artifacts = {
            **artifacts,
            "model": best_model,
            "best_params": best_params,
            "feature_cols": feature_cols,
            "itemcode_categories": itemcode_categories_new,
            "abc_map": abc_map_new,
            "clip_caps": caps_new,
        }
        joblib.dump(new_artifacts, MODEL_PATH)

        artifacts = new_artifacts
        model = best_model
        itemcode_categories = itemcode_categories_new
        abc_map = abc_map_new

    return {"ok": True, "message": "Model retuned successfully (monthly)", "best_params": best_params, "train_rmse": round(best_rmse, 4), **info}, 200

def process_raw_now():
    with LOCK:
        if os.path.exists(RAW_XLSX_PATH):
            raw_df = pd.read_excel(RAW_XLSX_PATH)
        elif os.path.exists(RAW_CSV_PATH):
            raw_df = pd.read_csv(RAW_CSV_PATH)
        else:
            return {"ok": False, "error": "No raw file found in /data (Company Data.xlsx or Company Data.csv)"}, 400

        processed = build_processed_data_from_raw(raw_df)
        processed.to_csv(PROCESSED_PATH, index=False)
        info = _reload_data_into_memory()

    return {
        "ok": True,
        "message": "Raw data processed -> processed_data.csv generated + reloaded",
        "processed_rows": int(len(processed)),
        **info
    }, 200

def get_health():
    health_info = {}
    health_info["model_loaded"] = model is not None
    health_info["artifact_keys"] = list(artifacts.keys())
    health_info["missing_features_in_data"] = [c for c in feature_cols if c not in data.columns]
    health_info["rows"] = int(len(data))
    health_info["unique_skus"] = int(data["ItemCode"].nunique()) if "ItemCode" in data.columns else 0

    try:
        any_sku = str(data["ItemCode"].iloc[-1])
        sku_df = _get_sku_df(any_sku)
        test_pred = forecast_next_month(sku_df)
        health_info["sample_prediction"] = round(float(test_pred), 4)
        health_info["prediction_status"] = "OK"
        health_info["status"] = "HEALTHY"
        return health_info, 200
    except Exception as e:
        health_info["prediction_status"] = f"FAILED: {str(e)}"
        health_info["status"] = "UNHEALTHY"
        return health_info, 500

def export_forecast_latest_now():
    """
    Called by forecast blueprint route.
    Saves backend/data/forecast_latest.csv
    """
    out_path = os.path.join(BACKEND_DIR, "data", "forecast_latest.csv")
    df_forecast, label = export_forecast_all_skus(out_path=out_path)

    return {
        "ok": True,
        "message": "Forecast exported for all SKUs",
        "next_month": label,
        "rows": int(len(df_forecast)),
        "path": out_path
    }, 200

'''

# ─── Paths ────────────────────────────────────────────────────────

MODEL_PATH = os.path.join(BASE_DIR, "models", "model_artifacts.pkl")
DATA_PATH = os.path.join(BASE_DIR, "data", "processed_data.csv")
RAW_XLSX_PATH = os.path.join(PROJECT_ROOT, "data", "Company Data.xlsx")
RAW_CSV_PATH  = os.path.join(PROJECT_ROOT, "data", "Company Data.csv")
PROCESSED_PATH = DATA_PATH  
STATE_PATH = os.path.join(BASE_DIR, "models", "train_state.json")


# ─── Functions ─────────────────────────────────────────────────


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

def _get_sku_df(item_code: str) -> pd.DataFrame:
    item_key = str(item_code).strip().replace(".0", "")

    # fast filter on precomputed key
    sku_df = data[data["ItemCode_key"] == item_key].copy()

    if sku_df.empty:
        print("DEBUG: SKU not found:", item_key)
        return sku_df

    if {"Year", "Month_Number"}.issubset(sku_df.columns):
        sku_df = sku_df.sort_values(["Year", "Month_Number"])
    elif "Month" in sku_df.columns:
        sku_df = sku_df.sort_values("Month")

    return sku_df.reset_index(drop=True)

def _build_dashboard_response(item_code: str):
    sku_df = _get_sku_df(item_code)

    if sku_df.empty:
        return None
    
    # ABC label (prefer artifact abc_map, fallback to current data column)
    item_key = str(item_code).strip().replace(".0","")
    # 1) Prefer ABC from CURRENT processed_data (best for UI)
    if "ABC_Class" in sku_df.columns and pd.notna(sku_df["ABC_Class"].iloc[-1]):
        abc_class = int(float(sku_df["ABC_Class"].iloc[-1]))
    # 2) Fallback: use training abc_map ONLY if it actually contains the SKU
    elif abc_map is not None and item_key in abc_map:
        abc_class = int(abc_map[item_key])
    # 3) Default
    else:
        abc_class = 2
    abc_label = {0: "A", 1: "B", 2: "C"}.get(abc_class, "C")

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
    forecast_next = forecast_next_month(sku_df)

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
            "predicted": round(forecast_next, 2),
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
            "predicted": round(forecast_next, 2),
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

    recent = sku_df.tail(12)
    sum_demand = float(recent["Clean_Demand"].sum()) if "Clean_Demand" in recent.columns else 0.0
    zero_rate = float((recent["Clean_Demand"] == 0).mean()) if "Clean_Demand" in recent.columns and len(recent) else 1.0
    if abc_label == "C" and (sum_demand == 0 or zero_rate > 0.9):
        demand_status = "Inactive / near-zero demand"
    elif abc_label == "C":
        demand_status = "Low-volume demand"
    else:
        demand_status = "Active demand"

    response = {
        "item_code": str(item_code),

        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

        # KPI
        "abc_class": abc_class,
        "abc_category": abc_label,
        "demand_status": demand_status,

        "next_month_forecast": round(forecast_next, 2),
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
    global data
    df = pd.read_csv(DATA_PATH)

    df["ItemCode"] = _normalize_itemcode(df["ItemCode"])
    df["ItemCode_key"] = df["ItemCode"]   # ✅

    # ensure ABC exists after reload too
    if "ABC_Class" not in df.columns:
        abc_map_local = _build_abc_map(df)
        df["ABC_Class"] = df["ItemCode"].map(abc_map_local).fillna(2).astype(int)
    else:
        df["ABC_Class"] = pd.to_numeric(df["ABC_Class"], errors="coerce").fillna(2).astype(int)

    df = _ensure_feature_columns(df)
    data = df

    return {
        "rows": int(len(df)),
        "unique_skus": int(df["ItemCode"].nunique()),
        "min_year": int(df["Year"].min()) if "Year" in df.columns and len(df) else None,
        "max_year": int(df["Year"].max()) if "Year" in df.columns and len(df) else None,
    }

def _encode_itemcode(df: pd.DataFrame, categories: list) -> pd.DataFrame:
    df = df.copy()
    categories = [str(x).strip().replace(".0","") for x in categories]
    unk_code = len(categories)

    item_to_id = {c: i for i, c in enumerate(categories)}
    df["ItemCode"] = df["ItemCode"].map(item_to_id).fillna(unk_code).astype(int)
    return df
   
    
def recency_weights(df, yearly_boost=0.25, base=1.0):
    y0 = int(df["Year"].min())
    return base + (df["Year"] - y0) * yearly_boost

def compute_clip_caps(train_df, cols, q=0.99):
    caps = {}
    for c in cols:
        if c in train_df.columns:
            s = pd.to_numeric(train_df[c], errors="coerce").replace([np.inf,-np.inf], np.nan).dropna()
            if len(s):
                caps[c] = float(s.quantile(q))
    return caps

def _apply_clip_caps(df: pd.DataFrame, caps: dict) -> pd.DataFrame:
    if not caps:
        return df
    df = df.copy()
    for c, cap in caps.items():
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).clip(upper=float(cap))
    return df

def _encode_itemcode_value(raw_code: str, categories: list | None) -> int:
    if categories is None:
        try:
            return int(float(raw_code))
        except Exception:
            return 0
    raw_code = str(raw_code).strip().replace(".0", "")
    codes = pd.Categorical([raw_code], categories=categories).codes
    unk = len(categories)
    return int(codes[0]) if codes[0] != -1 else int(unk)

def prepare_next_month_X(sku_df: pd.DataFrame) -> pd.DataFrame:
    if sku_df is None or sku_df.empty:
        raise ValueError("sku_df is empty")

    df = sku_df.copy().sort_values(["Year", "Month_Number"])

    if "ABC_Class" not in df.columns:
        df["ABC_Class"] = 2
    df["ABC_Class"] = pd.to_numeric(df["ABC_Class"], errors="coerce").fillna(2).astype(int)

    last_row = df.iloc[-1:].copy()
    cur_year = int(last_row["Year"].iloc[0])
    cur_month = int(last_row["Month_Number"].iloc[0])

    nxt_month = cur_month + 1
    nxt_year = cur_year
    if nxt_month > 12:
        nxt_month = 1
        nxt_year += 1

    new_row = last_row.copy()
    new_row["Year"] = nxt_year
    new_row["Month_Number"] = nxt_month

    df_ext = pd.concat([df, new_row], ignore_index=True)
    df_ext = df_ext.sort_values(["ItemCode", "Year", "Month_Number"]).copy()

    # lags
    for lag in [1, 2, 3, 6, 12]:
        df_ext[f"Lag{lag}"] = df_ext.groupby("ItemCode")["Clean_Demand"].shift(lag)

    # rolling + momentum
    df_ext["Rolling3M_Mean"] = df_ext.groupby("ItemCode")["Clean_Demand"].transform(
        lambda x: x.rolling(3, min_periods=1).mean().shift(1)
    )
    df_ext["Rolling6M_Mean"] = df_ext.groupby("ItemCode")["Clean_Demand"].transform(
        lambda x: x.rolling(6, min_periods=1).mean().shift(1)
    )
    df_ext["Rolling3M_Std"] = df_ext.groupby("ItemCode")["Clean_Demand"].transform(
        lambda x: x.rolling(3, min_periods=1).std().shift(1)
    ).fillna(0)

    df_ext["Momentum"] = df_ext["Lag1"] - df_ext["Lag3"]

    # zero behavior
    df_ext["Is_Zero"] = (pd.to_numeric(df_ext["Clean_Demand"], errors="coerce").fillna(0) == 0).astype(int)
    df_ext["ZeroRate_6M"] = df_ext.groupby("ItemCode")["Is_Zero"].transform(
        lambda x: x.rolling(6, min_periods=1).mean().shift(1)
    ).fillna(0)

    # seasonality
    mn = pd.to_numeric(df_ext["Month_Number"], errors="coerce").fillna(0)
    df_ext["Month_Sin"] = np.sin(2 * np.pi * mn / 12)
    df_ext["Month_Cos"] = np.cos(2 * np.pi * mn / 12)

    # inventory pressure
    lag1 = pd.to_numeric(df_ext["Lag1"], errors="coerce").fillna(0)
    avail = pd.to_numeric(df_ext.get("Available_Primary_Inventory_Qty", 0), errors="coerce").fillna(0)
    df_ext["Inventory_Pressure"] = np.where(lag1 == 0, 0, avail / (lag1 + 1))

    # net stock + cover (optional)
    if {"Total_Primary_Inventory_Qty", "Blocked_Stock_Qty", "Inspection_Stock_Qty"}.issubset(df_ext.columns):
        total = pd.to_numeric(df_ext["Total_Primary_Inventory_Qty"], errors="coerce").fillna(0)
        blocked = pd.to_numeric(df_ext["Blocked_Stock_Qty"], errors="coerce").fillna(0)
        insp = pd.to_numeric(df_ext["Inspection_Stock_Qty"], errors="coerce").fillna(0)
        df_ext["Net_Available_Stock"] = (total - blocked - insp).clip(lower=0)

        r3 = pd.to_numeric(df_ext["Rolling3M_Mean"], errors="coerce").fillna(0)
        df_ext["Stock_Cover_Months"] = np.where(r3 == 0, 0, df_ext["Net_Available_Stock"] / (r3 + 1))

    # caps (use artifact caps)
    df_ext = _apply_clip_caps(df_ext, clip_caps)

    next_row = df_ext[(df_ext["Year"] == nxt_year) & (df_ext["Month_Number"] == nxt_month)].iloc[-1:].copy()

    # encode itemcode for model input
    next_row["ItemCode"] = _encode_itemcode_value(next_row["ItemCode"].iloc[0], itemcode_categories)

    # ensure all features exist
    missing = [c for c in feature_cols if c not in next_row.columns]
    if missing:
        raise KeyError(f"[INFERENCE-next_row] Missing required columns: {missing}")

    X = _sanitize_X(next_row[feature_cols].copy())
    return X

def forecast_next_month(sku_df: pd.DataFrame) -> float:
    X = prepare_next_month_X(sku_df)
    return float(model.predict(X)[0])

def forecast_all_skus(df_all: pd.DataFrame) -> pd.DataFrame:
    out = []
    for item, g in df_all.groupby("ItemCode"):
        g = g.sort_values(["Year", "Month_Number"])
        if len(g) < 2:
            continue
        pred = forecast_next_month(g)

        last = g.iloc[-1]
        nxt_month = int(last["Month_Number"]) + 1
        nxt_year = int(last["Year"])
        if nxt_month > 12:
            nxt_month = 1
            nxt_year += 1
        label = f"{nxt_year:04d}-{nxt_month:02d}"

        out.append({"Month": label, "ItemCode": str(item), "Forecast_Qty": round(pred, 2)})
    return pd.DataFrame(out)

def export_forecast_latest(df_all: pd.DataFrame, out_path: str) -> str:
    df_forecast = forecast_all_skus(df_all).copy()
    df_forecast["run_id"] = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    df_forecast["created_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    df_forecast.to_csv(out_path, index=False)
    return out_path

'''