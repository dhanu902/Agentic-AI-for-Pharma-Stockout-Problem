# backend/services/risk_service.py ---> 📂 File I/O for M+1 risk
#
# Responsibility:
#   - Define risk/inventory file paths
#   - Load Inventory.xlsx DB/WH sheets
#   - Load saved inventory/risk outputs
#   - Append risk run logs
#   - Provide risk_latest.csv results to Forecast page and Inventory page
#
# NO orchestration here.
# Pipeline sequencing lives in engines/risk_orchestrator.py.
# Risk calculations live in engines/risk_engine.py.

import os
import pandas as pd

from services.forecast_service import (
    OUTPUT_DIR,
    LOG_DIR,
    BACKEND_DATA_DIR,
)

# ============================================================
# PATHS
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(BACKEND_DATA_DIR))
RAW_DATA_DIR = os.path.join(PROJECT_ROOT, "data")

INVENTORY_XLSX_PATH = os.path.join(RAW_DATA_DIR, "Inventory.xlsx")

PROCESSED_DIR = os.path.join(BACKEND_DATA_DIR, "processed")
os.makedirs(PROCESSED_DIR, exist_ok=True)

INVENTORY_SNAPSHOT_PATH = os.path.join(PROCESSED_DIR, "inventory_risk_snapshot.csv")
BASE_SNAPSHOT_PATH = os.path.join(OUTPUT_DIR, "risk_base_snapshot.csv")
RISK_LATEST_PATH = os.path.join(OUTPUT_DIR, "risk_latest.csv")
RISK_RUN_LOG_PATH = os.path.join(LOG_DIR, "risk_run_log.csv")


# ============================================================
# RAW LOADERS
# ============================================================
def load_inventory_workbook() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load the raw Inventory.xlsx workbook.

    Returns (db_df, wh_df) — DB sheet (distributor batches) and WH sheet
    (warehouse/primary batches), untouched. All normalization and
    expiry-bucket classification happens in risk_engine.
    """
    if not os.path.exists(INVENTORY_XLSX_PATH):
        raise FileNotFoundError(f"Inventory workbook not found: {INVENTORY_XLSX_PATH}")
    db_df = pd.read_excel(INVENTORY_XLSX_PATH, sheet_name="DB")
    wh_df = pd.read_excel(INVENTORY_XLSX_PATH, sheet_name="WH")
    return db_df, wh_df


# ============================================================
# HELPERS
# ============================================================
def _normalize_itemcode(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )

def _next_month_label(label: str) -> str:
    year, month = map(int, label.split("-"))
    if month == 12:
        return f"{year + 1}-01"
    return f"{year}-{month + 1:02d}"

def validate_inventory_forecast_alignment(
    base_df: pd.DataFrame,
    forecast_df: pd.DataFrame,
) -> dict:
    if "Base_Month" not in base_df.columns:
        raise ValueError("Inventory snapshot missing Base_Month column.")
    if "Forecast_Month" not in forecast_df.columns:
        raise ValueError("forecast_latest.csv missing Forecast_Month column.")

    base_months = sorted(base_df["Base_Month"].dropna().astype(str).unique().tolist())
    forecast_months = sorted(forecast_df["Forecast_Month"].dropna().astype(str).unique().tolist())

    if len(base_months) != 1:
        raise ValueError(f"Inventory snapshot must contain exactly one Base_Month. Found: {base_months}")
    if len(forecast_months) != 1:
        raise ValueError(f"Forecast file must contain exactly one Forecast_Month. Found: {forecast_months}")

    base_month = base_months[0]
    forecast_month = forecast_months[0]
    expected_forecast_month = base_month

    if forecast_month != expected_forecast_month:
        raise ValueError(
            f"Month mismatch blocked: inventory Base_Month={base_month}, "
            f"forecast Forecast_Month={forecast_month}. "
            f"Expected Forecast_Month={expected_forecast_month} because inventory "
            f"Month represents opening stock for the same forecast month."
        )

    return {
        "base_month": base_month,
        "forecast_month": forecast_month,
        "expected_forecast_month": expected_forecast_month,
    }

def append_risk_run_log(row: dict) -> str:
    log_df = pd.DataFrame([row])
    if os.path.exists(RISK_RUN_LOG_PATH):
        existing = pd.read_csv(RISK_RUN_LOG_PATH)
        log_df = pd.concat([existing, log_df], ignore_index=True)
    log_df.to_csv(RISK_RUN_LOG_PATH, index=False)
    return RISK_RUN_LOG_PATH


def save_inventory_snapshot(df: pd.DataFrame) -> str:
    df.to_csv(INVENTORY_SNAPSHOT_PATH, index=False)
    return INVENTORY_SNAPSHOT_PATH

def save_risk_base_snapshot(df: pd.DataFrame) -> str:
    df.to_csv(BASE_SNAPSHOT_PATH, index=False)
    return BASE_SNAPSHOT_PATH

def save_risk_latest(df: pd.DataFrame) -> str:
    df.to_csv(RISK_LATEST_PATH, index=False)
    return RISK_LATEST_PATH


def load_inventory_snapshot() -> pd.DataFrame:
    if not os.path.exists(INVENTORY_SNAPSHOT_PATH):
        raise FileNotFoundError(f"Inventory risk snapshot not found: {INVENTORY_SNAPSHOT_PATH}")
    return pd.read_csv(INVENTORY_SNAPSHOT_PATH)

def load_risk_base_snapshot() -> pd.DataFrame:
    if not os.path.exists(BASE_SNAPSHOT_PATH):
        raise FileNotFoundError(f"Risk base snapshot not found: {BASE_SNAPSHOT_PATH}")
    return pd.read_csv(BASE_SNAPSHOT_PATH)

def load_risk_latest() -> pd.DataFrame:
    if not os.path.exists(RISK_LATEST_PATH):
        return pd.DataFrame()
    return pd.read_csv(RISK_LATEST_PATH)


def get_risk_results() -> pd.DataFrame:
    return load_risk_latest()