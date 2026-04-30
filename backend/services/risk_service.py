# backend/services/risk_service.py

import os
from datetime import datetime
import pandas as pd

from engines.risk_engine import build_risk_table
from engines.inventory_engine import build_inventory_risk_snapshot
from services.forecast_service import (
    FORECAST_LATEST_PATH,
    OUTPUT_DIR,
    LOG_DIR,
)
from engines.horizon_engine import (
    build_default_horizon_forecast,
    build_horizon_projection_table,
)

from services.supply_service import load_upcoming_supply

# ============================================================
# PATHS
# ============================================================
BASE_SNAPSHOT_PATH = os.path.join(OUTPUT_DIR, "risk_base_snapshot.csv")
RISK_LATEST_PATH = os.path.join(OUTPUT_DIR, "risk_latest.csv")
RISK_RUN_LOG_PATH = os.path.join(LOG_DIR, "risk_run_log.csv")
RISK_HORIZON_LATEST_PATH = os.path.join(OUTPUT_DIR, "risk_horizon_latest.csv")

# ============================================================
# HELPERS
# ============================================================
def _normalize_itemcode(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )


def _next_month_label(month_label: str) -> str:
    year, month = map(int, month_label.split("-"))
    if month == 12:
        return f"{year + 1}-01"
    return f"{year}-{month + 1:02d}"


def validate_inventory_forecast_alignment(base_df: pd.DataFrame, forecast_df: pd.DataFrame):
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

    expected_forecast_month = _next_month_label(base_month)

    if forecast_month != expected_forecast_month:
        raise ValueError(
            f"Month mismatch blocked: inventory Base_Month={base_month}, "
            f"forecast Forecast_Month={forecast_month}. "
            f"Expected Forecast_Month={expected_forecast_month} because risk uses current inventory to cover next-month forecast."
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


# ============================================================
# INVENTORY SNAPSHOT (REAL-TIME)
# ============================================================
def build_inventory_snapshot():
    """
    Build real-time inventory snapshot from Inventory.xlsx

    Output:
    risk_base_snapshot.csv
    """

    if not os.path.exists(FORECAST_LATEST_PATH):
        raise FileNotFoundError(
            "forecast_latest.csv required to determine forecast month."
        )

    forecast_df = pd.read_csv(FORECAST_LATEST_PATH)

    # Extract forecast month (needed for cutoff logic)
    # Convert forecast month → BASE month (previous month)
    forecast_df["Forecast_Year"] = forecast_df["Forecast_Month"].str[:4].astype(int)
    forecast_df["Forecast_Month_Number"] = forecast_df["Forecast_Month"].str[5:7].astype(int)

    def prev_month(year, month):
        if month == 1:
            return year - 1, 12
        return year, month - 1

    runtime_rows = []

    for _, r in forecast_df[["Forecast_Year", "Forecast_Month_Number"]].drop_duplicates().iterrows():
        y, m = prev_month(int(r["Forecast_Year"]), int(r["Forecast_Month_Number"]))
        runtime_rows.append({"Year": y, "Month_Number": m})

    runtime_df = pd.DataFrame(runtime_rows)

    # Build inventory snapshot
    inventory_df = build_inventory_risk_snapshot(
        runtime_df=runtime_df,
        save=True
    )

    if inventory_df is None or len(inventory_df) == 0:
        raise ValueError("Inventory snapshot is empty. Check inventory_engine.")

    inventory_df.to_csv(BASE_SNAPSHOT_PATH, index=False)

    return BASE_SNAPSHOT_PATH


# ============================================================
# INVENTORY SNAPSHOT (HORIZON)
# ============================================================
def run_horizon_risk_pipeline():
    """
    6-month horizon projection:
    risk_base_snapshot.csv + forecast_latest.csv + Upcoming_Supply.xlsx
    -> risk_horizon_latest.csv
    """

    if not os.path.exists(FORECAST_LATEST_PATH):
        return {
            "ok": False,
            "error": "forecast_latest.csv not found. Export forecast first."
        }

    try:
        # Build latest inventory snapshot first
        build_inventory_snapshot()

        inventory_df = pd.read_csv(BASE_SNAPSHOT_PATH)
        forecast_df = pd.read_csv(FORECAST_LATEST_PATH)

        inventory_df["ItemCode"] = _normalize_itemcode(inventory_df["ItemCode"])
        forecast_df["ItemCode"] = _normalize_itemcode(forecast_df["ItemCode"])

        # Validate M -> M+1 alignment using existing logic
        alignment = validate_inventory_forecast_alignment(inventory_df, forecast_df)

        # Temporary 6M forecast until real M+2..M+6 model exists
        forecast_horizon_df = build_default_horizon_forecast(
            forecast_df=forecast_df,
            horizon_months=6,
        )

        supply_df = load_upcoming_supply()

        horizon_df = build_horizon_projection_table(
            inventory_df=inventory_df,
            forecast_horizon_df=forecast_horizon_df,
            supply_df=supply_df,
            horizon_months=6,
        )

        horizon_df.to_csv(RISK_HORIZON_LATEST_PATH, index=False)

        run_row = {
            "Run_ID": datetime.utcnow().strftime("%Y%m%d%H%M%S"),
            "Run_Date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "Run_Type": "HORIZON_RISK_6M",
            "Rows_Exported": int(len(horizon_df)),
            "Base_Snapshot_Path": BASE_SNAPSHOT_PATH,
            "Forecast_Path": FORECAST_LATEST_PATH,
            "Risk_Output_Path": RISK_HORIZON_LATEST_PATH,
        }

        append_risk_run_log(run_row)

        return {
            "ok": True,
            "rows": int(len(horizon_df)),
            "path": RISK_HORIZON_LATEST_PATH,
            "base_rows": int(len(inventory_df)),
            "forecast_rows": int(len(forecast_df)),
            "alignment": alignment,
            "note": "M+2 to M+6 currently use repeated M+1 forecast until true horizon model is added.",
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }


# ============================================================
# MAIN PIPELINE
# ============================================================
def run_risk_pipeline():
    """
    Full risk pipeline:

    Inventory.xlsx → inventory_engine →
    risk_base_snapshot →
    risk_engine →
    risk_latest.csv
    """

    if not os.path.exists(FORECAST_LATEST_PATH):
        return {
            "ok": False,
            "error": "forecast_latest.csv not found. Export forecast first."
        }

    try:
        # ----------------------------------------------------
        # STEP 1: Build inventory snapshot (REAL-TIME)
        # ----------------------------------------------------
        build_inventory_snapshot()

        # ----------------------------------------------------
        # STEP 2: Load inputs
        # ----------------------------------------------------
        base_df = pd.read_csv(BASE_SNAPSHOT_PATH)
        forecast_df = pd.read_csv(FORECAST_LATEST_PATH)

        base_df["ItemCode"] = _normalize_itemcode(base_df["ItemCode"])
        forecast_df["ItemCode"] = _normalize_itemcode(forecast_df["ItemCode"])

        alignment = validate_inventory_forecast_alignment(base_df, forecast_df)

        # ----------------------------------------------------
        # STEP 3: Run risk engine
        # ----------------------------------------------------
        risk_df = build_risk_table(
            base_df=base_df,
            forecast_df=forecast_df,
            base_month_col="Base_Month",
            forecast_month_col="Forecast_Month",
            item_col="ItemCode",
            forecast_col="Forecast_Qty",
        )

        risk_df.to_csv(RISK_LATEST_PATH, index=False)

        # ----------------------------------------------------
        # STEP 4: Logging
        # ----------------------------------------------------
        run_row = {
            "Run_ID": datetime.utcnow().strftime("%Y%m%d%H%M%S"),
            "Run_Date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "Rows_Exported": int(len(risk_df)),
            "Base_Snapshot_Path": BASE_SNAPSHOT_PATH,
            "Forecast_Path": FORECAST_LATEST_PATH,
            "Risk_Output_Path": RISK_LATEST_PATH,
        }

        append_risk_run_log(run_row)

        # ----------------------------------------------------
        # SUCCESS RESPONSE
        # ----------------------------------------------------
        return {
            "ok": True,
            "rows": int(len(risk_df)),
            "path": RISK_LATEST_PATH,
            "base_rows": int(len(base_df)),
            "forecast_rows": int(len(forecast_df)),
            "base_snapshot_path": BASE_SNAPSHOT_PATH,
            "log_path": RISK_RUN_LOG_PATH,
            "alignment": alignment,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }