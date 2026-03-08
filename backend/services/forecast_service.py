# backend/services/forecast_service.py
import os
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # --> backend/
PROJECT_ROOT = os.path.dirname(BASE_DIR)                                 # --> project root

BACKEND_DATA_DIR = os.path.join(BASE_DIR, "data") # project root/backend/data/..
RAW_DATA_DIR = os.path.join(PROJECT_ROOT, "data") # project root/data/..

RAW_ACTUAL_XLSX_PATH = os.path.join(RAW_DATA_DIR, "Company Data.xlsx")      ## project root/data/..
RAW_ACTUAL_CSV_PATH = os.path.join(RAW_DATA_DIR, "Company Data.csv")        ## project root/data/..
RAW_LIVE_CSV_PATH = os.path.join(RAW_DATA_DIR, "Company Data Live.csv")     ## project root/data/..

PROCESSED_LIVE_PATH = os.path.join(BACKEND_DATA_DIR, "processed_data_live.csv")         # project root/backend/data/..
PROCESSED_ACTUAL_PATH = os.path.join(BACKEND_DATA_DIR, "processed_data_actual.csv")     # project root/backend/data/..
FORECAST_LATEST_PATH = os.path.join(BACKEND_DATA_DIR, "forecast_latest.csv")            # project root/backend/data/..


def get_processed_path(mode: str = "live") -> str:
    return PROCESSED_ACTUAL_PATH if mode == "actual" else PROCESSED_LIVE_PATH


def load_raw_data(mode: str = "live") -> pd.DataFrame:
    if mode == "actual":
        if os.path.exists(RAW_ACTUAL_XLSX_PATH):
            return pd.read_excel(RAW_ACTUAL_XLSX_PATH)
        if os.path.exists(RAW_ACTUAL_CSV_PATH):
            return pd.read_csv(RAW_ACTUAL_CSV_PATH)
        raise FileNotFoundError("Actual raw file not found.")
    else:
        if os.path.exists(RAW_LIVE_CSV_PATH):
            return pd.read_csv(RAW_LIVE_CSV_PATH)
        raise FileNotFoundError("Live raw file not found.")


def save_processed_data(df: pd.DataFrame, mode: str = "live") -> str:
    out_path = get_processed_path(mode)
    df.to_csv(out_path, index=False)
    return out_path


def load_processed_data(mode: str = "live") -> pd.DataFrame:
    path = get_processed_path(mode)
    if not os.path.exists(path):
        raise FileNotFoundError(f"{os.path.basename(path)} not found.")
    return pd.read_csv(path)


def save_forecast_latest(df: pd.DataFrame) -> str:
    df.to_csv(FORECAST_LATEST_PATH, index=False)
    return FORECAST_LATEST_PATH


def processed_is_fresh(mode="live") -> bool:
    processed_path = get_processed_path(mode)

    if not os.path.exists(processed_path):
        return False

    if mode == "actual":
        raw_candidates = [RAW_ACTUAL_XLSX_PATH, RAW_ACTUAL_CSV_PATH]
    else:
        raw_candidates = [RAW_LIVE_CSV_PATH]

    existing_raw = [p for p in raw_candidates if os.path.exists(p)]
    if not existing_raw:
        return True

    raw_path = max(existing_raw, key=os.path.getmtime)
    return os.path.getmtime(processed_path) >= os.path.getmtime(raw_path)

