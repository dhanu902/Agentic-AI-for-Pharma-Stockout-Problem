# backend/routes/insights_route.py
#
# Register in app.py:
#   from routes.insights_route import insights_bp
#   app.register_blueprint(insights_bp, url_prefix="/api/insights")
#
# NOTE: the prefix must be /api/insights — the frontend Insights page
# calls `${API_BASE}/run` and `${API_BASE}/results` with API_BASE = "/api/insights".

from flask import Blueprint, jsonify, request

from services.insights_service import (
    get_agency_performance_rows,
    run_agency_performance_engine,
)

insights_bp = Blueprint("insights", __name__)


@insights_bp.route("/run", methods=["POST"])
def run_agency_performance():
    result = run_agency_performance_engine()

    if not result.get("ok"):
        return jsonify(result), 400

    return jsonify(result), 200


@insights_bp.route("/results", methods=["GET"])
def get_agency_performance_results():
    result = get_agency_performance_rows()

    if not result.get("ok"):
        return jsonify(result), 500

    rows          = result.get("rows", [])
    budget_rows   = result.get("budget_rows", [])
    forecast_rows = result.get("forecast_rows", [])

    # Optional server-side agency filter — applied to ALL row sets so the
    # performance table, budget analysis table, and forecast comparison
    # table stay consistent.
    agency = request.args.get("agency")
    if agency:
        rows = [
            r for r in rows
            if str(r.get("Agency", "")).lower() == agency.lower()
        ]
        budget_rows = [
            r for r in budget_rows
            if str(r.get("Agency", "")).lower() == agency.lower()
        ]
        forecast_rows = [
            r for r in forecast_rows
            if str(r.get("Agency", "")).lower() == agency.lower()
        ]

    # Pass meta AND budget_rows/forecast_rows through — the frontend needs
    # budget_rows for the Budget Analysis tab, forecast_rows for the new
    # Forecast tab (model vs third-party forecasts, budgeted SKUs only),
    # and meta for the KPI strips.
    return jsonify({
        "ok":            True,
        "rows":          rows,
        "budget_rows":   budget_rows,
        "forecast_rows": forecast_rows,
        "meta":          result.get("meta"),
    }), 200