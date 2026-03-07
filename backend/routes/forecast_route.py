# backend/routes/forecast_route.py

from flask import Blueprint, request, jsonify

from engines.demand_forecast_engine import (
    get_dashboard,
    get_skus,
    reload_data_now,
    reload_model_artifacts,
    refresh_model_now,
    retune_model_now,
    process_raw_now,
    export_forecast_latest_now,
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
        return jsonify({"error": "item_code required"}), 400

    result = get_dashboard(item_code)
    if result is None:
        return jsonify({"error": "Item not found"}), 404

    return jsonify(result), 200


@forecast_bp.route("/skus", methods=["GET"])
def skus():
    return jsonify({"skus": get_skus()}), 200


@forecast_bp.route("/reload_data", methods=["POST"])
def reload_data():
    payload, status = reload_data_now()
    return jsonify(payload), status


@forecast_bp.route("/reload_model", methods=["POST"])
def reload_model():
    result = reload_model_artifacts()
    return jsonify(result), 200


@forecast_bp.route("/refresh_model", methods=["POST"])
def refresh_model():
    payload, status = refresh_model_now()
    return jsonify(payload), status


@forecast_bp.route("/retune_model", methods=["POST"])
def retune_model():
    payload, status = retune_model_now()
    return jsonify(payload), status


@forecast_bp.route("/process_raw", methods=["POST"])
def process_raw():
    # engine reads raw file paths internally and rebuilds processed_data.csv
    payload, status = process_raw_now()
    return jsonify(payload), status


@forecast_bp.route("/export", methods=["POST"])
def export_forecast():
    payload, status = export_forecast_latest_now()
    return jsonify(payload), status


@forecast_bp.route("/health", methods=["GET"])
def health():
    payload, status = get_health()
    return jsonify(payload), status