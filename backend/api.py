from fastapi import FastAPI
import joblib
import pandas as pd
import numpy as np

app = FastAPI()

# Load model
final_model = joblib.load("final_model.pkl")
Data = pd.read_csv("Company Data.csv")

@app.get("/sku/{sku_code}")
def get_sku_data(sku_code: int):

    sku_df = Data[Data["ItemCode"] == sku_code].copy()

    # Run recursive forecast
    forward = recursive_next_month_forecast(
        sku_code,
        final_model,
        Data
    )

    return {
        "forward_forecast": forward,
        "history": sku_df.tail(24).to_dict(orient="records")
    }