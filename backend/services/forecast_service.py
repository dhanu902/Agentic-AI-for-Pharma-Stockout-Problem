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
FOCUS_ITEM_CODES_PATH = os.path.join(RAW_DATA_DIR, "FocusItemCodes.xlsx")

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