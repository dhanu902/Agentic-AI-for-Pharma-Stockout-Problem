# risk_routes.py
import os
import pandas as pd
from flask import Blueprint, jsonify
from services.risk_service import run_risk_pipeline, RISK_PATH

risk_bp = Blueprint("risk", __name__)


@risk_bp.route("/run", methods=["POST"])
def run_risk():
    result = run_risk_pipeline()

    if not result.get("ok", False):
        return jsonify(result), 400

    return jsonify(result), 200


@risk_bp.route("/results", methods=["GET"])
def get_risk_results():
    if not os.path.exists(RISK_PATH):
        return jsonify({
            "ok": False,
            "error": "risk_latest.csv not found. Run risk engine first.",
            "rows": []
        }), 404

    try:
        df = pd.read_csv(RISK_PATH)
        df = df.where(pd.notnull(df), None)

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