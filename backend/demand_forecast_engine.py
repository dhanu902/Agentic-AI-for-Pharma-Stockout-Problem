# demand_forecast_engine.py

import os
import joblib
import pandas as pd
import numpy as np
from flask import Flask, request, jsonify

# -----------------------------------
# Initialize Flask App
# -----------------------------------
app = Flask(__name__)

# -----------------------------------
# Load Model & Artifacts
# -----------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MODEL_PATH = os.path.join(BASE_DIR, "model_artifacts.pkl")
artifacts = joblib.load(MODEL_PATH)
model = artifacts["model"]
feature_columns = artifacts["feature_cols"]
DATA_PATH = os.path.join(BASE_DIR, "data", "processed_data.csv")


# -----------------------------------
# Load Data
# -----------------------------------

data = pd.read_csv(DATA_PATH)

# -----------------------------------
# Helper: Prepare Latest Features
# -----------------------------------

def prepare_latest_features(item_code):
    item_data = data[data["ItemCode"] == item_code].copy()

    if item_data.empty:
        return None

    # Sort by month
    item_data = item_data.sort_values("Month")

    # Take latest available row
    latest_row = item_data.iloc[-1:]

    X = latest_row[feature_columns]

    return X


# -----------------------------------
# Forecast Endpoint
# -----------------------------------

@app.route("/forecast", methods=["POST"])
def forecast():

    request_data = request.get_json()
    item_code = request_data.get("item_code")

    if not item_code:
        return jsonify({"error": "Item code is required"}), 400

    X = prepare_latest_features(item_code)

    if X is None:
        return jsonify({"error": "Item not found"}), 404

    prediction = model.predict(X)[0]

    response = {
        "item_code": item_code,
        "forecast_demand": round(float(prediction), 2)
    }

    return jsonify(response)


# -----------------------------------
# Health Check
# -----------------------------------

@app.route("/")
def home():
    return jsonify({"message": "Demand Forecast Engine Running"})


# -----------------------------------
# Run Server
# -----------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5000)