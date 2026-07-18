# backend/routes/horizon_route.py

import os
import pandas as pd
from flask import Blueprint, jsonify

from services.horizon_service import (
    run_horizon_risk_pipeline,
    RISK_HORIZON_LATEST_PATH,
)

horizon_bp = Blueprint("horizon", __name__)


@horizon_bp.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Horizon routes running"}), 200


@horizon_bp.route("/run", methods=["POST"])
def run_horizon_risk():
    result = run_horizon_risk_pipeline()

    if not result.get("ok", False):
        return jsonify(result), 400

    return jsonify(result), 200


@horizon_bp.route("/results", methods=["GET"])
def get_horizon_risk_results():
    if not os.path.exists(RISK_HORIZON_LATEST_PATH):
        return jsonify({
            "ok": True,
            "message": "No horizon results found. Run horizon engine first.",
            "rows": []
        }), 200

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