# backend/routes/xai_route.py 

from flask import Blueprint, request, jsonify
from services.xai_service import get_forecast_xai_explanation

xai_bp = Blueprint("xai", __name__, url_prefix="/api/xai")


@xai_bp.route("/forecast-explanation", methods=["POST"])
def forecast_explanation():
    try:
        payload = request.get_json(force=True) or {}
        item_code = payload.get("item_code")

        if not item_code:
            return jsonify({
                "success": False,
                "error": "item_code is required"
            }), 400

        result = get_forecast_xai_explanation(str(item_code))

        return jsonify({
            "success": True,
            "data": result
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500