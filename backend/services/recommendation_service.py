# backend/services/recommendation_service.py

import os
import pandas as pd

from services.risk_service import RISK_LATEST_PATH, RISK_HORIZON_LATEST_PATH
from engines.recommendation_engine import build_recommendation_for_sku


# ============================================================
# PATH SETUP (ROOT data folder)
# ============================================================
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SOURCE_DATA_DIR = os.path.join(PROJECT_ROOT, "data")

REGULATORY_PATH = os.path.join(SOURCE_DATA_DIR, "Regulatory_Status.xlsx")
SUPPLIER_PATH = os.path.join(SOURCE_DATA_DIR, "Supplier_Status.xlsx")
POLICY_PATH = os.path.join(SOURCE_DATA_DIR, "SKU_Policy.xlsx")


# ============================================================
# HELPERS
# ============================================================
def _normalize_itemcode(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )


def _load_excel_safe(path, sheet_name):
    """
    Safe Excel loader (returns empty df if file missing)
    """
    try:
        if not os.path.exists(path):
            return pd.DataFrame()

        return pd.read_excel(path, sheet_name=sheet_name)
    except Exception:
        return pd.DataFrame()


# ============================================================
# MAIN SERVICE
# ============================================================
def get_recommendation_dashboard(item_code: str):

    # -----------------------------
    # CHECK RISK OUTPUT
    # -----------------------------
    if not os.path.exists(RISK_LATEST_PATH):
        return {
            "ok": False,
            "error": "risk_latest.csv not found. Run risk engine first."
        }

    item_code = str(item_code).strip().replace(".0", "")

    # -----------------------------
    # LOAD RISK DATA
    # -----------------------------
    risk_df = pd.read_csv(RISK_LATEST_PATH)
    risk_df["ItemCode"] = _normalize_itemcode(risk_df["ItemCode"])

    sku_rows = risk_df[risk_df["ItemCode"] == item_code]

    if sku_rows.empty:
        return {
            "ok": False,
            "error": f"SKU {item_code} not found in risk_latest.csv"
        }

    risk_row = sku_rows.iloc[0].to_dict()

    # -----------------------------
    # LOAD HORIZON DATA (optional)
    # -----------------------------
    horizon_rows = []
    if os.path.exists(RISK_HORIZON_LATEST_PATH):
        horizon_df = pd.read_csv(RISK_HORIZON_LATEST_PATH)
        horizon_df["ItemCode"] = _normalize_itemcode(horizon_df["ItemCode"])
        horizon_rows = horizon_df[
            horizon_df["ItemCode"] == item_code
        ].to_dict(orient="records")

    # -----------------------------
    # LOAD PLACEHOLDER DATA
    # -----------------------------
    regulatory_df = _load_excel_safe(REGULATORY_PATH, "Regulatory")
    supplier_df = _load_excel_safe(SUPPLIER_PATH, "Supplier")
    policy_df = _load_excel_safe(POLICY_PATH, "Policy")

    # Normalize if not empty
    if not regulatory_df.empty:
        regulatory_df["ItemCode"] = _normalize_itemcode(regulatory_df["ItemCode"])

    if not supplier_df.empty:
        supplier_df["ItemCode"] = _normalize_itemcode(supplier_df["ItemCode"])

    if not policy_df.empty:
        policy_df["ItemCode"] = _normalize_itemcode(policy_df["ItemCode"])

    # -----------------------------
    # EXTRACT ROWS (SAFE)
    # -----------------------------
    def get_row(df):
        if df is None or df.empty:
            return {}

        if "ItemCode" not in df.columns:
            return {}

        rows = df[df["ItemCode"] == item_code]

        if rows is None or rows.empty:
            return {}

        try:
            return rows.iloc[0].to_dict()
        except Exception:
            return {}

    regulatory_row = get_row(regulatory_df)
    supplier_row = get_row(supplier_df)
    policy_row = get_row(policy_df)

    # -----------------------------
    # BUILD RECOMMENDATION
    # -----------------------------
    result = build_recommendation_for_sku(
        risk_row=risk_row,
        horizon_rows=horizon_rows,
        policy_row=policy_row,
        regulatory_row=regulatory_row,
        supplier_row=supplier_row,
    )

    return {
        "ok": True,
        "data": result
    }