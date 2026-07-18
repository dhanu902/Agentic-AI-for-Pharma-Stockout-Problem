# backend/app.py

from flask import Flask
from flask_cors import CORS

from routes.forecast_route import forecast_bp
from routes.risk_route import risk_bp
from routes.recommendation_route import recommendation_bp
from routes.horizon_route import horizon_bp
from routes.insights_route import insights_bp

def create_app():
    app = Flask(__name__)
    CORS(app,
        resources={r"/api/*": {
            "origins": [
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ]
        }})

    app.register_blueprint(forecast_bp, url_prefix="/api/forecast")
    app.register_blueprint(risk_bp, url_prefix="/api/risk")
    app.register_blueprint(recommendation_bp, url_prefix="/api/recommendation")
    app.register_blueprint(horizon_bp, url_prefix="/api/horizon")
    app.register_blueprint(insights_bp, url_prefix="/api/insights")

    return app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5001)