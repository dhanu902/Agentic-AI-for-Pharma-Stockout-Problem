"""
recommendation_route.py
=======================
Flask blueprint. HTTP concerns only — all logic lives in the service.

Registered in app.py with url_prefix="/api/recommendation", so the
endpoint below resolves to GET /api/recommendation/results
(same convention as /api/risk/results, /api/insights/results).
"""

from flask import Blueprint, jsonify, request

from services.recommendation_service import (
    get_recommendations,
    get_recommendations_by_agency,
)

recommendation_bp = Blueprint("recommendation", __name__)


@recommendation_bp.route("/results", methods=["GET"])
def recommendations():
    """
    Query params:
        agency        (optional) filter to one AgencyName
        min_priority  (optional) HIGH | MEDIUM
    """
    try:
        result = get_recommendations(
            agency=request.args.get("agency"),
            min_priority=request.args.get("min_priority"),
        )
        # Explicit keys — a silently dropped key = empty frontend tab
        return jsonify({
            "summary": result["summary"],
            "recommendations": result["recommendations"],
            "all_items": result["all_items"],
            "factor_coverage": result["factor_coverage"],
            "kpis": result["kpis"],
            "run_meta": result["run_meta"],
        })
    except NotImplementedError as e:
        return jsonify({"error": f"data source not wired: {e}"}), 501
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# AGENCY-WISE RECOMMENDATIONS (business change 6) — the planner
# runs item-wise exactly as before; rows returned here are one
# per AGENCY (aggregated). /results stays item-wise.
# ============================================================
@recommendation_bp.route("/results_by_agency", methods=["GET"])
def recommendations_by_agency():
    """
    Query params:
        min_priority  (optional) HIGH | MEDIUM
    """
    try:
        result = get_recommendations_by_agency(
            min_priority=request.args.get("min_priority"),
        )
        return jsonify({
            "summary": result["summary"],
            "recommendations": result["recommendations"],
            "all_agencies": result["all_agencies"],
            "factor_coverage": result["factor_coverage"],
            "kpis": result["kpis"],
            "run_meta": result["run_meta"],
        })
    except NotImplementedError as e:
        return jsonify({"error": f"data source not wired: {e}"}), 501
    except Exception as e:
        return jsonify({"error": str(e)}), 500