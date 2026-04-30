# backend/routes/recommendation_route.py 

from flask import Blueprint, jsonify, request

from services.recommendation_service import get_recommendation_dashboard

recommendation_bp = Blueprint("recommendation", __name__)


@recommendation_bp.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Recommendation routes running"}), 200


@recommendation_bp.route("/dashboard", methods=["POST"])
def recommendation_dashboard():
    payload = request.get_json(silent=True) or {}
    item_code = payload.get("item_code")

    if not item_code:
        return jsonify({
            "ok": False,
            "error": "item_code is required"
        }), 400

    result = get_recommendation_dashboard(item_code)

    if not result.get("ok"):
        return jsonify(result), 400

    return jsonify(result), 200