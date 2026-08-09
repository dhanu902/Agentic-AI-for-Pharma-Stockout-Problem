# backend/routes/risk_route.py 

import os
import pandas as pd
from flask import Blueprint, jsonify

from engines.risk_orchestrator import run_risk_pipeline
from services.risk_service import (
    RISK_LATEST_PATH,
    get_risk_results_by_agency,
    get_risk_results_with_agency,
)

risk_bp = Blueprint("risk", __name__)

def _normalize_row(row: dict) -> dict:
    """
    Ensure boolean fields (A_met, B_met, C_met) are sent as true Python
    booleans — not as numpy bool_ or string — so JSON serialisation is
    clean and the JSX triple-equality checks work correctly.
    """
    for key in ("A_met", "B_met", "C_met"):
        if key in row:
            val = row[key]
            if isinstance(val, str):
                row[key] = val.lower() == "true"
            else:
                row[key] = bool(val)
    return row


@risk_bp.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Risk routes running"}), 200


@risk_bp.route("/run", methods=["POST"])
def run_risk():
    """
    Trigger the full risk pipeline:
      Inventory.xlsx  →  inventory snapshot
      ToBeGRN.xlsx    →  supply deduction
      forecast_latest →  risk scenarios
      → risk_latest.csv
 
    Returns JSON matching Inventory.jsx expectations:
      { ok: bool, rows: int, path: str, ... }
    """
    result = run_risk_pipeline()
 
    if not result.get("ok"):
        return jsonify(result), 500
 
    return jsonify(result), 200
 
 
@risk_bp.route("/results", methods=["GET"])
def get_results():
    """
    Load risk_latest.csv and return all rows.
 
    Returns JSON matching Inventory.jsx expectations:
      { rows: [ { ItemCode, Risk_Level, Forecast_Qty, ... } ] }
    """
    if not os.path.exists(RISK_LATEST_PATH):
        return jsonify({
            "rows":    [],
            "message": "No risk results found. Run the risk engine first.",
        }), 200

    try:
        # CHANGED: rows now carry Agency / AgencyCode / ProductName display
        # columns (joined from the master SKU list) so the Inventory page
        # can filter SKUs within a selected agency. Risk logic unchanged.
        df = get_risk_results_with_agency()
 
        # Fill NaN so JSON serialisation never produces null in numeric columns
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        df[numeric_cols] = df[numeric_cols].fillna(0)
 
        # Fill NaN in string columns
        str_cols = df.select_dtypes(include="object").columns.tolist()
        df[str_cols] = df[str_cols].fillna("")
 
        rows = [_normalize_row(r) for r in df.to_dict(orient="records")]

        return jsonify({"rows": rows}), 200

    except Exception as e:
        return jsonify({"rows": [], "error": str(e)}), 500


# ============================================================
# AGENCY-WISE INVENTORY PROJECTION (business change 5)
# Same risk rows aggregated per agency — quantities summed,
# Risk_Level = worst case among the agency's items.
# /results above stays item-wise for backward compatibility.
# ============================================================
@risk_bp.route("/results_by_agency", methods=["GET"])
def get_results_by_agency():
    """
    Returns JSON matching the agency-wise Inventory.jsx expectations:
      { rows: [ { Agency, AgencyCode, SKU_Count, Risk_Level,
                  Forecast_Qty, ...stock buckets..., A_met, A_unmet, ... } ] }
    """
    try:
        df = get_risk_results_by_agency()

        if df is None or df.empty:
            return jsonify({
                "rows":    [],
                "message": "No risk results found. Run the risk engine first.",
            }), 200

        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        df[numeric_cols] = df[numeric_cols].fillna(0)

        str_cols = df.select_dtypes(include="object").columns.tolist()
        df[str_cols] = df[str_cols].fillna("")

        rows = [_normalize_row(r) for r in df.to_dict(orient="records")]

        return jsonify({"rows": rows}), 200

    except Exception as e:
        return jsonify({"rows": [], "error": str(e)}), 500