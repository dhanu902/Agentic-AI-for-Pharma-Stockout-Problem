# backend/services/supply_service.py

import os
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BASE_DIR)

SUPPLY_PATH = os.path.join(PROJECT_ROOT, "data", "Upcoming_Supply.xlsx")


def load_upcoming_supply() -> pd.DataFrame:
    if not os.path.exists(SUPPLY_PATH):
        return pd.DataFrame(columns=[
            "ItemCode",
            "Expected_Arrival_Month",
            "Incoming_Qty",
            "Status",
        ])

    df = pd.read_excel(SUPPLY_PATH, sheet_name="PO")

    if df.empty:
        return pd.DataFrame(columns=[
            "ItemCode",
            "Expected_Arrival_Month",
            "Incoming_Qty",
            "Status",
        ])

    for col in ["ItemCode", "Expected_Arrival_Month", "Incoming_Qty"]:
        if col not in df.columns:
            df[col] = 0 if col == "Incoming_Qty" else ""

    df["ItemCode"] = df["ItemCode"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    df["Expected_Arrival_Month"] = df["Expected_Arrival_Month"].astype(str).str[:7]
    df["Incoming_Qty"] = pd.to_numeric(df["Incoming_Qty"], errors="coerce").fillna(0).clip(lower=0)

    if "Status" not in df.columns:
        df["Status"] = "UNKNOWN"

    return df