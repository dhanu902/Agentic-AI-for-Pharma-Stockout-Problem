#risk_service.py
import os
import pandas as pd

from engines.risk_engine import build_risk_table

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

PROCESSED_PATH = os.path.join(DATA_DIR, "processed_data.csv")
FORECAST_PATH = os.path.join(DATA_DIR, "forecast_latest.csv")
BASE_PATH = os.path.join(DATA_DIR, "base_data_latest.csv")
RISK_PATH = os.path.join(DATA_DIR, "risk_latest.csv")


def build_base_snapshot():
    """
    Create base_data_latest.csv from processed_data.csv
    (latest month per SKU)
    """
    if not os.path.exists(PROCESSED_PATH):
        raise FileNotFoundError("processed_data.csv not found. Process raw data first.")

    df = pd.read_csv(PROCESSED_PATH)

    required_cols = [
        "ItemCode",
        "Year",
        "Month_Number",
        "Distributor_Inventory_Qty",
        "Available_Primary_Inventory_Qty",
        "Inspection_Stock_Qty",
        "Blocked_Stock_Qty",
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise KeyError(f"processed_data.csv missing columns: {missing}")

    df["ItemCode"] = (
        df["ItemCode"]
        .astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )

    df["Year"] = pd.to_numeric(df["Year"], errors="coerce")
    df["Month_Number"] = pd.to_numeric(df["Month_Number"], errors="coerce")

    df = df.sort_values(["ItemCode", "Year", "Month_Number"])
    latest = df.groupby("ItemCode").tail(1).copy()

    latest["Month"] = latest.apply(
        lambda r: f"{int(r['Year'])}-{int(r['Month_Number']):02d}"
        if pd.notna(r["Year"]) and pd.notna(r["Month_Number"]) else None,
        axis=1
    )

    base = latest[
        [
            "Month",
            "ItemCode",
            "Distributor_Inventory_Qty",
            "Available_Primary_Inventory_Qty",
            "Inspection_Stock_Qty",
            "Blocked_Stock_Qty",
        ]
    ].copy()

    base.to_csv(BASE_PATH, index=False)
    return BASE_PATH


def run_risk_pipeline():
    if not os.path.exists(PROCESSED_PATH):
        return {
            "ok": False,
            "error": "processed_data.csv not found. Process raw data first."
        }

    if not os.path.exists(FORECAST_PATH):
        return {
            "ok": False,
            "error": "forecast_latest.csv not found. Export forecast first."
        }

    try:
        build_base_snapshot()

        base_df = pd.read_csv(BASE_PATH)
        forecast_df = pd.read_csv(FORECAST_PATH)

        forecast_df["ItemCode"] = (
            forecast_df["ItemCode"]
            .astype(str)
            .str.strip()
            .str.replace(r"\.0$", "", regex=True)
        )

        risk_df = build_risk_table(base_df, forecast_df)
        risk_df.to_csv(RISK_PATH, index=False)

        return {
            "ok": True,
            "rows": int(len(risk_df)),
            "path": RISK_PATH,
            "base_rows": int(len(base_df)),
            "forecast_rows": int(len(forecast_df)),
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }