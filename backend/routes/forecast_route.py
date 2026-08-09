# backend/routes/forecast_route.py ---> 🌐 API

from flask import Blueprint, request, jsonify

from engines.forecast_orchestrator import (
    get_dashboard,
    get_agencies,
    get_agency_dashboard,
    get_skus,
    get_skus_full,
    reload_data_now,
    reload_model_artifacts,
    refresh_model_now,
    retune_model_now,
    process_actual_raw_now,
    process_live_raw_now,
    export_forecast_latest_now,
    export_forecast_horizon_latest_now,
    get_health,
)

forecast_bp = Blueprint("forecast", __name__)


@forecast_bp.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Forecast routes running"}), 200


@forecast_bp.route("/dashboard", methods=["POST"])
def dashboard():
    body = request.get_json(silent=True) or {}
    item_code = body.get("item_code")

    if not item_code:
        return jsonify({"success": False, "error": "item_code required"}), 400

    try:
        result = get_dashboard(item_code)
        if result is None:
            return jsonify({"success": False, "error": "Item not found"}), 404

        return jsonify({"success": True, "data": result}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@forecast_bp.route("/skus", methods=["GET"])
def skus():
    return jsonify({"skus": get_skus()}), 200


# ============================================================
# AGENCY-WISE FORECAST PAGE (business change 4) — the Forecast
# page selector now lists agencies; KPIs/charts are the same
# computations aggregated across every SKU of the agency.
# Item-wise endpoints above are kept for backward compatibility.
# ============================================================
@forecast_bp.route("/agencies", methods=["GET"])
def agencies():
    return jsonify({"agencies": get_agencies()}), 200


@forecast_bp.route("/agency_dashboard", methods=["POST"])
def agency_dashboard():
    body = request.get_json(silent=True) or {}
    agency = body.get("agency")

    if not agency:
        return jsonify({"success": False, "error": "agency required"}), 400

    try:
        result = get_agency_dashboard(agency)
        if result is None:
            return jsonify({"success": False, "error": "Agency not found"}), 404

        return jsonify({"success": True, "data": result}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@forecast_bp.route("/reload_data", methods=["POST"])
def reload_data():
    payload, status = reload_data_now()
    return jsonify(payload), status


@forecast_bp.route("/reload_model", methods=["POST"])
def reload_model():
    payload, status = reload_model_artifacts()
    return jsonify(payload), status


@forecast_bp.route("/refresh_model", methods=["POST"])
def refresh_model():
    payload, status = refresh_model_now()
    return jsonify(payload), status


@forecast_bp.route("/retune_model", methods=["POST"])
def retune_model():
    payload, status = retune_model_now()
    return jsonify(payload), status


@forecast_bp.route("/process_actual_raw", methods=["POST"])
def process_actual_raw():
    payload, status = process_actual_raw_now()
    return jsonify(payload), status


@forecast_bp.route("/process_live_raw", methods=["POST"])
def process_live_raw():
    payload, status = process_live_raw_now()
    return jsonify(payload), status


@forecast_bp.route("/export", methods=["POST"])
def export_forecast():
    payload, status = export_forecast_latest_now()
    return jsonify(payload), status


@forecast_bp.route("/export_horizon", methods=["POST"])
def export_forecast_horizon():
    payload, status = export_forecast_horizon_latest_now()
    return jsonify(payload), status


@forecast_bp.route("/health", methods=["GET"])
def health():
    payload, status = get_health()
    return jsonify(payload), status

@forecast_bp.route("/skus_full", methods=["GET"])
def skus_full():
    return jsonify({"skus": get_skus_full()}), 200