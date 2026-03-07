from flask import Flask
from flask_cors import CORS
from routes.forecast_route import forecast_bp
from routes.risk_route import risk_bp

def create_app():
    app = Flask(__name__)
    CORS(app)

    app.register_blueprint(forecast_bp, url_prefix="/api/forecast")

    app.register_blueprint(risk_bp, url_prefix="/api/risk")

    return app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)