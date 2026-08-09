# backend/routes/insights_route.py
#
# Register in app.py:
#   from routes.insights_route import insights_bp
#   app.register_blueprint(insights_bp, url_prefix="/api/insights")
#
# NOTE: the prefix must be /api/insights — the frontend Insights page
# calls `${API_BASE}/run` and `${API_BASE}/results` with API_BASE = "/api/insights".
#
# v8 — Budget vs Actual(secondary) vs Forecast, all priced via
# DistributorPrice.xlsx (see engines/insights_engine.py header). Forecast
# is columns on the existing rows/budget_rows now (Current_Forecast_Qty/
# Value, Forecast_Vs_Actual_Loss_*) — no separate forecast_rows payload.

from flask import Blueprint, jsonify, request

from services.insights_service import (
    get_agency_performance_rows,
    get_trend_series,
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
    # performance, budget analysis and forecast analysis tables stay
    # consistent with each other.
    agency = request.args.get("agency")
    if agency:
        def _for_agency(records):
            return [
                r for r in records
                if str(r.get("Agency", "")).lower() == agency.lower()
            ]
        rows          = _for_agency(rows)
        budget_rows   = _for_agency(budget_rows)
        forecast_rows = _for_agency(forecast_rows)

    # Pass meta AND all three row sets through — the frontend needs
    # budget_rows for the Budget Analysis tab, forecast_rows for the
    # Forecast Analysis tab, and meta for the KPI strips.
    return jsonify({
        "ok":            True,
        "rows":          rows,
        "budget_rows":   budget_rows,
        "forecast_rows": forecast_rows,
        "meta":          result.get("meta"),
    }), 200


@insights_bp.route("/trend", methods=["GET"])
def get_agency_performance_trend():
    """
    FY-to-date monthly Actual vs Budget series for the Agency Performance
    trend chart.

    Query params (both optional):
        agency : limit to one agency  (default: all agencies combined)
        item   : limit to one ItemCode (default: all SKUs summed)

    Deliberately its OWN endpoint rather than another key on /results: the
    trend table is per-SKU-per-month, so folding it into the main payload
    would multiply that response's size by the number of months in the FY
    for data only one tab's flip-side chart ever needs.
    """
    result = get_trend_series(
        agency=request.args.get("agency"),
        item_code=request.args.get("item"),
    )

    if not result.get("ok"):
        return jsonify(result), 500

    return jsonify(result), 200