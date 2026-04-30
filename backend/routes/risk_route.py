# backend/routes/risk_route.py 

import os
import pandas as pd
from flask import Blueprint, jsonify

from services.risk_service import (
    run_risk_pipeline,
    run_horizon_risk_pipeline,
    RISK_LATEST_PATH,
    RISK_HORIZON_LATEST_PATH,
)

risk_bp = Blueprint("risk", __name__)


@risk_bp.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Risk routes running"}), 200


@risk_bp.route("/run", methods=["POST"])
def run_risk():
    result = run_risk_pipeline()

    if not result.get("ok", False):
        return jsonify(result), 400

    return jsonify(result), 200


@risk_bp.route("/results", methods=["GET"])
def get_risk_results():
    if not os.path.exists(RISK_LATEST_PATH):
        return jsonify({
            "ok": False,
            "error": "risk_latest.csv not found. Run risk engine first.",
            "rows": []
        }), 404

    try:
        df = pd.read_csv(RISK_LATEST_PATH)

        # Replace NaN / inf with None for valid JSON
        df = df.replace([float("inf"), float("-inf")], None)
        df = df.astype(object).where(pd.notnull(df), None)

        return jsonify({
            "ok": True,
            "rows": df.to_dict(orient="records")
        }), 200

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "rows": []
        }), 500   
    

@risk_bp.route("/horizon/run", methods=["POST"])
def run_horizon_risk():
    result = run_horizon_risk_pipeline()

    if not result.get("ok", False):
        return jsonify(result), 400

    return jsonify(result), 200


@risk_bp.route("/horizon/results", methods=["GET"])
def get_horizon_risk_results():
    if not os.path.exists(RISK_HORIZON_LATEST_PATH):
        return jsonify({
            "ok": False,
            "error": "risk_horizon_latest.csv not found. Run horizon risk engine first.",
            "rows": []
        }), 404

    try:
        df = pd.read_csv(RISK_HORIZON_LATEST_PATH)
        df = df.replace([float("inf"), float("-inf")], None)
        df = df.astype(object).where(pd.notnull(df), None)

        return jsonify({
            "ok": True,
            "rows": df.to_dict(orient="records")
        }), 200

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "rows": []
        }), 500