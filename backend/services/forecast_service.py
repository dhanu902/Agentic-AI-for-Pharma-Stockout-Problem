# backend/services/forecast_service.py ---> 📦 Data/file manager

import os
from datetime import datetime
import pandas as pd
from typing import Optional, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # backend/
PROJECT_ROOT = os.path.dirname(BASE_DIR)                                 # project root

# ============================================================
# DIRECTORIES
# ============================================================
RAW_DATA_DIR = os.path.join(PROJECT_ROOT, "data")
BACKEND_DATA_DIR = os.path.join(BASE_DIR, "data")

PROCESSED_DIR = os.path.join(BACKEND_DATA_DIR, "processed")
OUTPUT_DIR = os.path.join(BACKEND_DATA_DIR, "outputs")
LOG_DIR = os.path.join(BACKEND_DATA_DIR, "logs")

for folder in [BACKEND_DATA_DIR, PROCESSED_DIR, OUTPUT_DIR, LOG_DIR]:
    os.makedirs(folder, exist_ok=True)

# ============================================================
# RAW INPUT FILES
# ============================================================
RAW_ACTUAL_XLSX_PATH = os.path.join(RAW_DATA_DIR, "fact_monthly_closed.xlsx")
RAW_ACTUAL_CSV_PATH = os.path.join(RAW_DATA_DIR, "fact_monthly_closed.csv")
RAW_LIVE_CSV_PATH = os.path.join(RAW_DATA_DIR, "fact_open_month_snapshot.csv")
FOCUS_ITEM_CODES_PATH = os.path.join(RAW_DATA_DIR,"Master Data","FocusItemCodes.xlsx")

# ============================================================
# PROCESSED FILES
# ============================================================
PROCESSED_ACTUAL_PATH = os.path.join(PROCESSED_DIR, "processed_data_actual.csv")
PROCESSED_LIVE_PATH = os.path.join(PROCESSED_DIR, "processed_data_live.csv")

# ============================================================
# OUTPUT FILES
# ============================================================
FORECAST_LATEST_PATH = os.path.join(OUTPUT_DIR, "forecast_latest.csv")
FORECAST_RUN_LOG_PATH = os.path.join(LOG_DIR, "forecast_run_log.csv")

# Trend baseline forecast — budgeted SKUs NOT in the model SKU list.
# Kept separate from forecast_latest.csv so model accuracy tracking,
# horizon forecasting and the risk pipeline are untouched.
TREND_FORECAST_LATEST_PATH = os.path.join(OUTPUT_DIR, "forecast_trend_latest.csv")
TREND_FORECAST_HISTORY_PATH = os.path.join(LOG_DIR, "forecast_trend_history.csv")

# Budget master — "All Budget 26 27 FY" holds ALL budgeted items
# (including SKUs with no sales / no model forecast)
BUDGET_XLSX_PATH = os.path.join(RAW_DATA_DIR, "Master Data", "Budget.xlsx")
BUDGET_ALL_SHEET_NAME = "All Budget 26 27 FY"

# ============================================================
# PATH HELPERS
# ============================================================

def get_processed_path(mode: str = "live") -> str:
    mode = str(mode).lower()
    return PROCESSED_ACTUAL_PATH if mode == "actual" else PROCESSED_LIVE_PATH


def get_raw_path(mode: str = "live") -> Optional[str]:
    mode = str(mode).lower()

    if mode == "actual":
        if os.path.exists(RAW_ACTUAL_XLSX_PATH):
            return RAW_ACTUAL_XLSX_PATH
        if os.path.exists(RAW_ACTUAL_CSV_PATH):
            return RAW_ACTUAL_CSV_PATH
        return None

    if os.path.exists(RAW_LIVE_CSV_PATH):
        return RAW_LIVE_CSV_PATH
    return None


def get_data_file_summary() -> dict:
    return {
        "project_root": PROJECT_ROOT,
        "raw_data_dir": RAW_DATA_DIR,
        "backend_data_dir": BACKEND_DATA_DIR,
        "raw_actual_xlsx": RAW_ACTUAL_XLSX_PATH,
        "raw_actual_csv": RAW_ACTUAL_CSV_PATH,
        "raw_live_csv": RAW_LIVE_CSV_PATH,
        "focus_item_codes": FOCUS_ITEM_CODES_PATH,
        "processed_actual": PROCESSED_ACTUAL_PATH,
        "processed_live": PROCESSED_LIVE_PATH,
        "forecast_latest": FORECAST_LATEST_PATH,
        "forecast_run_log": FORECAST_RUN_LOG_PATH,
    }


# ============================================================
# FOCUS SKU HELPERS
# ============================================================
def load_focus_item_codes() -> List[str]:
    if not os.path.exists(FOCUS_ITEM_CODES_PATH):
        return []

    focus_df = pd.read_excel(FOCUS_ITEM_CODES_PATH)

    if "Code" not in focus_df.columns:
        raise ValueError("FocusItemCodes.xlsx must contain a 'Code' column.")

    focus_df["Code"] = pd.to_numeric(focus_df["Code"], errors="coerce")
    focus_df = focus_df.dropna(subset=["Code"]).copy()
    focus_df["Code"] = focus_df["Code"].astype(int).astype(str)

    return sorted(focus_df["Code"].unique().tolist())


def filter_to_focus_items(df: pd.DataFrame, focus_codes: Optional[List[str]] = None) -> pd.DataFrame:
    df = df.copy()
    if "ItemCode" not in df.columns:
        return df
    if focus_codes is None:
        focus_codes = load_focus_item_codes()
    if not focus_codes:
        return df

    df["ItemCode"] = pd.to_numeric(df["ItemCode"], errors="coerce")
    df = df.dropna(subset=["ItemCode"]).copy()
    df["ItemCode"] = df["ItemCode"].astype(int).astype(str)

    return df[df["ItemCode"].isin(set(focus_codes))].copy()


# ============================================================
# RAW LOADERS
# ============================================================
def load_raw_data(mode: str = "live", apply_focus_filter: bool = True) -> pd.DataFrame:
    mode = str(mode).lower()

    raw_path = get_raw_path(mode)
    if raw_path is None:
        if mode == "actual":
            raise FileNotFoundError(
                "Actual raw file not found. Expected fact_monthly_closed.xlsx or fact_monthly_closed.csv"
            )
        raise FileNotFoundError(
            "Live raw file not found. Expected fact_open_month_snapshot.csv"
        )

    if raw_path.lower().endswith(".xlsx"):
        df = pd.read_excel(raw_path)
    else:
        df = pd.read_csv(raw_path)

    if apply_focus_filter:
        df = filter_to_focus_items(df)

    return df


# ============================================================
# PROCESSED DATA
# ============================================================
def save_processed_data(df: pd.DataFrame, mode: str = "live") -> str:
    out_path = get_processed_path(mode)
    df.to_csv(out_path, index=False)
    return out_path


def load_processed_data(mode: str = "live") -> pd.DataFrame:
    path = get_processed_path(mode)
    if not os.path.exists(path):
        raise FileNotFoundError(f"{os.path.basename(path)} not found.")
    return pd.read_csv(path)


# ============================================================
# FORECAST OUTPUTS
# ============================================================
def save_forecast_latest(df: pd.DataFrame) -> str:
    df.to_csv(FORECAST_LATEST_PATH, index=False)
    return FORECAST_LATEST_PATH


def save_forecast_versioned(df: pd.DataFrame, forecast_year=None, forecast_month=None) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if forecast_year is not None and forecast_month is not None:
        filename = f"forecast_{int(forecast_year):04d}_{int(forecast_month):02d}_{stamp}.csv"
    else:
        filename = f"forecast_export_{stamp}.csv"

    out_path = os.path.join(OUTPUT_DIR, filename)
    df.to_csv(out_path, index=False)
    return out_path


def append_forecast_run_log(row: dict) -> str:
    log_df = pd.DataFrame([row])

    if os.path.exists(FORECAST_RUN_LOG_PATH):
        existing = pd.read_csv(FORECAST_RUN_LOG_PATH)
        log_df = pd.concat([existing, log_df], ignore_index=True)

    log_df.to_csv(FORECAST_RUN_LOG_PATH, index=False)
    return FORECAST_RUN_LOG_PATH


# ============================================================
# FRESHNESS CHECK
# ============================================================
def processed_is_fresh(mode: str = "live") -> bool:
    processed_path = get_processed_path(mode)

    if not os.path.exists(processed_path):
        return False

    raw_path = get_raw_path(mode)
    if raw_path is None or not os.path.exists(raw_path):
        return True

    return os.path.getmtime(processed_path) >= os.path.getmtime(raw_path)


def get_file_status_summary() -> dict:
    def meta(path: str) -> dict:
        exists = os.path.exists(path)
        return {
            "exists": exists,
            "path": path,
            "modified_at": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S") if exists else None,
            "size_bytes": os.path.getsize(path) if exists else 0,
        }

    return {
        "raw_actual_xlsx": meta(RAW_ACTUAL_XLSX_PATH),
        "raw_actual_csv": meta(RAW_ACTUAL_CSV_PATH),
        "raw_live_csv": meta(RAW_LIVE_CSV_PATH),
        "focus_item_codes": meta(FOCUS_ITEM_CODES_PATH),
        "processed_actual": meta(PROCESSED_ACTUAL_PATH),
        "processed_live": meta(PROCESSED_LIVE_PATH),
        "forecast_latest": meta(FORECAST_LATEST_PATH),
        "forecast_run_log": meta(FORECAST_RUN_LOG_PATH),
    }


# ============================================================
# GET FORECAST 
# ============================================================

def load_forecast_latest() -> pd.DataFrame:
    if not os.path.exists(FORECAST_LATEST_PATH):
        raise FileNotFoundError("forecast_latest.csv not found.")
    return pd.read_csv(FORECAST_LATEST_PATH)

def get_forecast_row(item_code: str) -> Optional[dict]:
    if not os.path.exists(FORECAST_LATEST_PATH):
        return None

    df = pd.read_csv(FORECAST_LATEST_PATH)
    if "ItemCode" not in df.columns:
        return None

    item_code = str(item_code).strip().replace(".0", "")
    df["ItemCode"] = df["ItemCode"].astype(str).str.replace(r"\.0$", "", regex=True)

    row = df[df["ItemCode"] == item_code]
    if row.empty:
        return None

    return row.iloc[0].to_dict()


# ============================================================
# BUDGET SKU HELPER (ALL budgeted items)
# ============================================================
def load_budget_item_codes() -> List[str]:
    """ItemCodes from the 'All Budget 26 27 FY' sheet — every budgeted SKU,
    including items with no sales history and no model forecast."""
    if not os.path.exists(BUDGET_XLSX_PATH):
        return []

    df = pd.read_excel(BUDGET_XLSX_PATH, sheet_name=BUDGET_ALL_SHEET_NAME, header=0)
    df.columns = df.columns.astype(str).str.strip()

    itemcode_col = next((c for c in ["ItemCode", "PID", "Code"] if c in df.columns), None)
    if itemcode_col is None:
        raise ValueError(
            f"Budget sheet '{BUDGET_ALL_SHEET_NAME}' has no ItemCode/PID column. "
            f"Found: {list(df.columns)}"
        )

    # Real codes are numeric -> canonical int-string. Placeholder rows for
    # new / not-yet-coded products ("New", "Getz Pharma1", ...) are KEPT so
    # the full budgeted list is covered (they simply get TREND_NO_HISTORY / 0
    # in the trend output).
    #
    # Different products can share one placeholder label (several distinct
    # products all coded "New") -> unmapped rows use a composite key
    # <label>::<agency>::<product>, matching insights_engine.load_budget_lookup.
    itemname_col = next((c for c in ["Product", "ItemName", "Name"] if c in df.columns), None)
    agency_series = (
        df["Agency"].ffill().astype(str).str.strip()
        if "Agency" in df.columns else pd.Series("", index=df.index)
    )
    name_series = (
        df[itemname_col].astype(str).str.strip().replace({"nan": "", "None": ""})
        if itemname_col else pd.Series("", index=df.index)
    )

    raw = df[itemcode_col].astype(str).str.strip()
    num = pd.to_numeric(df[itemcode_col], errors="coerce")
    synthetic = raw + "::" + agency_series + "::" + name_series

    codes = num.astype("Int64").astype(str).where(num.notna(), synthetic)
    blank = num.isna() & raw.isin(["", "nan", "None", "NaN", "<NA>"])
    codes = codes[~blank]
    return sorted(codes.unique().tolist())


# ============================================================
# ALL-SKU FACT HISTORY (no focus filter, closed months only)
# ============================================================
def load_fact_history_all_skus() -> pd.DataFrame:
    """
    fact_monthly_closed for ALL SKUs (focus filter NOT applied), aggregated to
    ItemCode x Year x Month_Number with Secondary_Sales_Qty.
    Any still-open calendar month is excluded (same rule as preprocess_engine).
    """
    df = load_raw_data(mode="actual", apply_focus_filter=False)

    # Month standardization (mirror of preprocess_engine.standardize_month_columns)
    if "MonthNo" in df.columns and "Month_Number" not in df.columns:
        df = df.rename(columns={"MonthNo": "Month_Number"})
    if "Month" in df.columns and (
        "Year" not in df.columns or "Month_Number" not in df.columns
    ):
        month_dt = pd.to_datetime(df["Month"], errors="coerce")
        if "Year" not in df.columns:
            df["Year"] = month_dt.dt.year
        if "Month_Number" not in df.columns:
            df["Month_Number"] = month_dt.dt.month

    required = ["ItemCode", "Year", "Month_Number", "Secondary_Sales_Qty"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"fact_monthly_closed missing columns: {missing}")

    df = df.copy()
    df["ItemCode"] = (
        df["ItemCode"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    )
    df["Year"] = pd.to_numeric(df["Year"], errors="coerce")
    df["Month_Number"] = pd.to_numeric(df["Month_Number"], errors="coerce")
    df = df.dropna(subset=["ItemCode", "Year", "Month_Number"])
    df["Secondary_Sales_Qty"] = (
        pd.to_numeric(df["Secondary_Sales_Qty"], errors="coerce").fillna(0).clip(lower=0)
    )

    # Exclude any still-open calendar month
    now = datetime.utcnow()
    current_period = now.year * 12 + now.month
    period_idx = df["Year"].astype(int) * 12 + df["Month_Number"].astype(int)
    df = df[period_idx < current_period].copy()

    return (
        df.groupby(["ItemCode", "Year", "Month_Number"], as_index=False)
        ["Secondary_Sales_Qty"].sum()
        .sort_values(["ItemCode", "Year", "Month_Number"])
        .reset_index(drop=True)
    )


# ============================================================
# TREND FORECAST OUTPUTS
# ============================================================
def save_trend_forecast_latest(df: pd.DataFrame) -> str:
    df.to_csv(TREND_FORECAST_LATEST_PATH, index=False)
    return TREND_FORECAST_LATEST_PATH


def append_trend_forecast_history(df: pd.DataFrame) -> str:
    if os.path.exists(TREND_FORECAST_HISTORY_PATH):
        existing = pd.read_csv(TREND_FORECAST_HISTORY_PATH)
        df = pd.concat([existing, df], ignore_index=True)
    df.to_csv(TREND_FORECAST_HISTORY_PATH, index=False)
    return TREND_FORECAST_HISTORY_PATH


def load_trend_forecast_latest() -> pd.DataFrame:
    if not os.path.exists(TREND_FORECAST_LATEST_PATH):
        return pd.DataFrame()
    return pd.read_csv(TREND_FORECAST_LATEST_PATH)


def load_trend_forecast_history() -> pd.DataFrame:
    if not os.path.exists(TREND_FORECAST_HISTORY_PATH):
        return pd.DataFrame()
    return pd.read_csv(TREND_FORECAST_HISTORY_PATH)