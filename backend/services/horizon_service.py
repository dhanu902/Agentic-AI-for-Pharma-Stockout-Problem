# backend/services/horizon_service.py ---> 📂 File I/O + routing for M+1..M+6 horizon
#
# Responsibility:
#   - Build/load the M+1..M+6 demand forecast (horizon_forecast_engine)
#   - Load the M+1 inventory snapshot (reuses risk_engine's snapshot via
#     risk_service, OR can load Inventory.xlsx independently — see
#     load_horizon_inventory_snapshot)
#   - Load pending supply (ToBeGRN / Pending POs)
#   - Call horizon_inventory_engine for the M+1..M+6 projection
#   - Save risk_horizon_latest.csv, route to the Horizon page
#
# NO business logic here — projection math lives in
# engines/horizon_inventory_engine.py, M+2..M+6 demand logic lives in
# engines/horizon_forecast_engine.py.

import os
from datetime import datetime
import pandas as pd
from engines import horizon_inventory_engine
from engines.horizon_forecast_engine import build_horizon_forecast_table

from services.forecast_service import (
    OUTPUT_DIR,
    LOG_DIR,
    BACKEND_DATA_DIR,
)
from services.risk_service import (
    BASE_SNAPSHOT_PATH,
    load_risk_base_snapshot,
    append_risk_run_log,
    _normalize_itemcode,
)

from engines.risk_orchestrator import build_inventory_snapshot

PROJECT_ROOT = os.path.dirname(os.path.dirname(BACKEND_DATA_DIR))
RAW_DATA_DIR = os.path.join(PROJECT_ROOT, "data")

SUPPLY_XLSX_PATH = os.path.join(RAW_DATA_DIR, "ToBeGRN.xlsx")

RISK_HORIZON_LATEST_PATH = os.path.join(OUTPUT_DIR, "risk_horizon_latest.csv")
FORECAST_HORIZON_LATEST_PATH = os.path.join(OUTPUT_DIR, "forecast_horizon_latest.csv")
FORECAST_HORIZON_HISTORY_PATH = os.path.join(LOG_DIR, "forecast_horizon_history.csv")


# ============================================================
# RAW LOADERS — pending supply / POs
# ============================================================
def load_upcoming_supply() -> pd.DataFrame:
    """
    Raw GRN / upcoming supply (Pending POs / ToBeGRN) loader.

    Source: data/ToBeGRN.xlsx, sheet = ToBeGRN
    Returns raw columns (ItemCode, OpenQty, DeliveryDate, ...).
    Normalization happens in horizon_inventory_engine.prepare_supply_df().
    """
    empty_cols = ["ItemCode", "OpenQty", "DeliveryDate"]
    if not os.path.exists(SUPPLY_XLSX_PATH):
        return pd.DataFrame(columns=empty_cols)

    df = pd.read_excel(SUPPLY_XLSX_PATH, sheet_name="ToBeGRN")
    if df.empty:
        return pd.DataFrame(columns=empty_cols)
    return df


# ============================================================
# INVENTORY SNAPSHOT FOR HORIZON
#
# Default: reuse the M+1 snapshot built by risk_service (shared opening
# stock basis between Inventory page and Horizon page).
#
# If horizon should load Inventory.xlsx independently in future (e.g.
# a different cut/timing of stock data), swap the body of this function
# to call load_inventory_workbook() + risk_engine.build_inventory_risk_snapshot()
# directly with its own runtime context.
# ============================================================
def load_horizon_inventory_snapshot(rebuild: bool = True) -> pd.DataFrame:
    if rebuild or not os.path.exists(BASE_SNAPSHOT_PATH):
        build_inventory_snapshot()
    return load_risk_base_snapshot()


# ============================================================
# FORECAST HORIZON (M+1..M+6) — save / load
# ============================================================
def save_forecast_horizon_latest(df: pd.DataFrame) -> str:
    df.to_csv(FORECAST_HORIZON_LATEST_PATH, index=False)
    return FORECAST_HORIZON_LATEST_PATH


def append_forecast_horizon_history(df: pd.DataFrame) -> str:
    if os.path.exists(FORECAST_HORIZON_HISTORY_PATH):
        existing = pd.read_csv(FORECAST_HORIZON_HISTORY_PATH)
        df = pd.concat([existing, df], ignore_index=True)
    df.to_csv(FORECAST_HORIZON_HISTORY_PATH, index=False)
    return FORECAST_HORIZON_HISTORY_PATH


def load_forecast_horizon_latest() -> pd.DataFrame:
    if not os.path.exists(FORECAST_HORIZON_LATEST_PATH):
        raise FileNotFoundError("forecast_horizon_latest.csv not found.")
    return pd.read_csv(FORECAST_HORIZON_LATEST_PATH)


def load_forecast_horizon_history() -> pd.DataFrame:
    if not os.path.exists(FORECAST_HORIZON_HISTORY_PATH):
        raise FileNotFoundError("forecast_horizon_history.csv not found.")
    return pd.read_csv(FORECAST_HORIZON_HISTORY_PATH)


# ============================================================
# FORECAST HORIZON PIPELINE (M+2..M+6 rule-based)
# ============================================================
def run_horizon_forecast_pipeline(
    forecast_df: pd.DataFrame,
    history_df: pd.DataFrame,
    horizon_months: int = 6,
):
    """
    Build M+1 to M+6 forecast horizon.
        M+1        comes from forecast_latest.csv (AI champion model).
        M+2..M+6   comes from horizon_forecast_engine (rule-based).
    """
    try:
        if forecast_df is None or forecast_df.empty:
            return {
                "ok": False,
                "error": "forecast_df is empty. Export forecast_latest.csv first."
            }, 400

        run_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        run_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        forecast_df = forecast_df.copy()
        forecast_df["ItemCode"] = _normalize_itemcode(forecast_df["ItemCode"])

        forecast_horizon_df = build_horizon_forecast_table(
            forecast_df=forecast_df,
            history_df=history_df,
            horizon_months=horizon_months,
        )

        forecast_horizon_df["Run_ID"] = run_id
        forecast_horizon_df["Run_Date"] = run_date

        history_base_month = None
        if (
            history_df is not None
            and not history_df.empty
            and {"Year", "Month_Number"}.issubset(history_df.columns)
        ):
            latest_actual = history_df.sort_values(["Year", "Month_Number"]).iloc[-1]
            history_base_month = f"{int(latest_actual['Year']):04d}-{int(latest_actual['Month_Number']):02d}"

        forecast_horizon_df["History_Base_Month"] = history_base_month

        latest_path = save_forecast_horizon_latest(forecast_horizon_df)
        history_path = append_forecast_horizon_history(forecast_horizon_df)

        return {
            "ok": True,
            "message": "Forecast horizon generated successfully.",
            "rows": int(len(forecast_horizon_df)),
            "latest_path": latest_path,
            "history_path": history_path,
            "history_base_month": history_base_month,
        }, 200

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
        }, 500


# ============================================================
# HORIZON RISK PIPELINE (M+1..M+6, physical stock + pending supply)
# ============================================================
def run_horizon_risk_pipeline():
    """
    Build 6-month inventory/risk horizon.

    Uses:
        forecast_horizon_latest.csv  (M+1..M+6 forecast qty)
        risk_base_snapshot.csv       (M+1 physical stock, opening stock basis)
        ToBeGRN.xlsx                 (pending PO/GRN)

    Creates:
        risk_horizon_latest.csv
    """
    if not os.path.exists(FORECAST_HORIZON_LATEST_PATH):
        return {
            "ok": False,
            "error": "forecast_horizon_latest.csv not found. Run /forecast/export_horizon first."
        }

    try:
        inventory_df = load_horizon_inventory_snapshot(rebuild=True)
        forecast_horizon_df = pd.read_csv(FORECAST_HORIZON_LATEST_PATH)

        inventory_df["ItemCode"] = _normalize_itemcode(inventory_df["ItemCode"])
        forecast_horizon_df["ItemCode"] = _normalize_itemcode(forecast_horizon_df["ItemCode"])

        inventory_skus = set(inventory_df["ItemCode"].astype(str).unique())
        forecast_skus = set(forecast_horizon_df["ItemCode"].astype(str).unique())

        alignment = {
            "inventory_skus": len(inventory_skus),
            "forecast_skus": len(forecast_skus),
            "missing_forecast_sku_count": len(inventory_skus - forecast_skus),
            "missing_inventory_sku_count": len(forecast_skus - inventory_skus),
            "missing_forecast_skus_sample": sorted(list(inventory_skus - forecast_skus))[:20],
            "missing_inventory_skus_sample": sorted(list(forecast_skus - inventory_skus))[:20],
        }

        # Load + normalize pending supply (ToBeGRN / Pending POs)
        raw_supply_df = load_upcoming_supply()
        supply_df = pd.DataFrame()

        horizon_df = horizon_inventory_engine.build_horizon_projection_table(
            inventory_df=inventory_df,
            forecast_horizon_df=forecast_horizon_df,
            supply_df=supply_df,
            horizon_months=6,
        )
        horizon_df.to_csv(RISK_HORIZON_LATEST_PATH, index=False)

        supply_skus = 0
        supply_total = 0.0
        
        run_row = {
            "Run_ID": datetime.utcnow().strftime("%Y%m%d%H%M%S"),
            "Run_Date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "Run_Type": "HORIZON_RISK_6M",
            "Rows_Exported": int(len(horizon_df)),
            "Base_Snapshot_Path": BASE_SNAPSHOT_PATH,
            "Forecast_Horizon_Path": FORECAST_HORIZON_LATEST_PATH,
            "Risk_Output_Path": RISK_HORIZON_LATEST_PATH,
            "Supply_SKUs_Used": supply_skus,
            "Supply_Total_Qty": supply_total,
        }
        append_risk_run_log(run_row)

        return {
            "ok": True,
            "message": "Horizon risk generated successfully.",
            "rows": int(len(horizon_df)),
            "path": RISK_HORIZON_LATEST_PATH,
            "base_rows": int(len(inventory_df)),
            "forecast_horizon_rows": int(len(forecast_horizon_df)),
            "alignment": alignment,
            "supply_skus_with_incoming": supply_skus,
            "supply_total_incoming_qty": supply_total,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
        }


def get_horizon_results() -> pd.DataFrame:
    """Load risk_horizon_latest.csv for the Horizon page route."""
    if not os.path.exists(RISK_HORIZON_LATEST_PATH):
        return pd.DataFrame()
    return pd.read_csv(RISK_HORIZON_LATEST_PATH)