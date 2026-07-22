# backend/services/insights_service.py

import json
import os
import pandas as pd

from engines.insights_engine import build_agency_performance_table
from services.forecast_service import FORECAST_LATEST_PATH

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "outputs")

AGENCY_PERFORMANCE_LATEST_PATH = os.path.join(
    OUTPUT_DIR, "agency_performance_latest.csv"
)
AGENCY_PERFORMANCE_META_PATH = os.path.join(
    OUTPUT_DIR, "agency_performance_meta.json"
)
BUDGET_ANALYSIS_LATEST_PATH = os.path.join(
    OUTPUT_DIR, "budget_analysis_latest.csv"
)
FORECAST_COMPARISON_LATEST_PATH = os.path.join(
    OUTPUT_DIR, "forecast_comparison_latest.csv"
)


# ─────────────────────────────────────────────────────────────
# Run engine
# ─────────────────────────────────────────────────────────────
def run_agency_performance_engine() -> dict:
    if not os.path.exists(FORECAST_LATEST_PATH):
        return {
            "ok": False,
            "error": "forecast_latest.csv not found. Export forecast first.",
        }

    try:
        df, budget_df, forecast_df, meta = build_agency_performance_table()

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        df.to_csv(AGENCY_PERFORMANCE_LATEST_PATH, index=False)
        budget_df.to_csv(BUDGET_ANALYSIS_LATEST_PATH, index=False)
        forecast_df.to_csv(FORECAST_COMPARISON_LATEST_PATH, index=False)

        with open(AGENCY_PERFORMANCE_META_PATH, "w") as f:
            json.dump(meta, f, indent=2, default=str)

        return {
            "ok": True,
            "rows": int(len(df)),
            "budget_rows": int(len(budget_df)),
            "forecast_rows": int(len(forecast_df)),
            "path": AGENCY_PERFORMANCE_LATEST_PATH,
            "data_available_upto": meta.get("data_available_upto"),
            "forecast_month": meta.get("forecast_month"),
            "realised_accuracy_available": meta.get("realised_accuracy_available"),
            "total_skus": meta.get("total_skus"),

            # Budget (ALL budgeted items)
            "budget_item_count": meta.get("budget_item_count"),
            "total_budget_qty": meta.get("total_budget_qty"),
            "total_annual_budget_qty": meta.get("total_annual_budget_qty"),
            "total_fytd_sales_qty": meta.get("total_fytd_sales_qty"),
            "total_budget_value": meta.get("total_budget_value"),
            "total_annual_budget_value": meta.get("total_annual_budget_value"),
            "total_fytd_sales_value": meta.get("total_fytd_sales_value"),

            # Loss
            "total_raw_loss_qty": meta.get("total_raw_loss_qty"),
            "total_stockout_loss_qty": meta.get("total_stockout_loss_qty"),
            "total_other_loss_qty": meta.get("total_other_loss_qty"),

            # Forecast comparison (Forecast tab)
            "forecast_comparison_sku_count": meta.get("forecast_comparison_sku_count"),
            "forecast_comparison_matched_sku_count": meta.get("forecast_comparison_matched_sku_count"),

            "message": (
                f"Agency performance built for {len(df)} SKUs "
                f"({len(budget_df)} budgeted items, "
                f"{len(forecast_df)} forecast-comparison rows). "
                f"Data up to {meta.get('data_available_upto')}. "
                f"Forecasting {meta.get('forecast_month')}."
            ),
        }

    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"Unexpected error: {str(e)}"}


# ─────────────────────────────────────────────────────────────
# Get latest rows
# ─────────────────────────────────────────────────────────────
def get_agency_performance_rows() -> dict:
    if not os.path.exists(AGENCY_PERFORMANCE_LATEST_PATH):
        return {
            "ok": False,
            "rows": [],
            "budget_rows": [],
            "forecast_rows": [],
            "meta": None,
            "error": "No agency performance results found. Run the engine first.",
        }

    try:
        df = pd.read_csv(AGENCY_PERFORMANCE_LATEST_PATH)

        df = df.replace([float("inf"), float("-inf")], None)
        df = df.astype(object).where(pd.notnull(df), None)

        rows = df.to_dict(orient="records")
        meta = _load_meta(df)

        budget_rows = []
        if os.path.exists(BUDGET_ANALYSIS_LATEST_PATH):
            try:
                bdf = pd.read_csv(BUDGET_ANALYSIS_LATEST_PATH)
                # ItemCode may parse as float from CSV — normalise back to string
                if "ItemCode" in bdf.columns:
                    bdf["ItemCode"] = (
                        pd.to_numeric(bdf["ItemCode"], errors="coerce")
                        .astype("Int64").astype(str).replace("<NA>", None)
                    )
                bdf = bdf.replace([float("inf"), float("-inf")], None)
                bdf = bdf.astype(object).where(pd.notnull(bdf), None)
                budget_rows = bdf.to_dict(orient="records")
            except Exception as e:
                print(f"[INSIGHTS] Warning loading budget analysis rows: {e}")

        forecast_rows = []
        if os.path.exists(FORECAST_COMPARISON_LATEST_PATH):
            try:
                fdf = pd.read_csv(FORECAST_COMPARISON_LATEST_PATH)
                if "ItemCode" in fdf.columns:
                    fdf["ItemCode"] = (
                        pd.to_numeric(fdf["ItemCode"], errors="coerce")
                        .astype("Int64").astype(str).replace("<NA>", None)
                    )
                fdf = fdf.replace([float("inf"), float("-inf")], None)
                fdf = fdf.astype(object).where(pd.notnull(fdf), None)
                forecast_rows = fdf.to_dict(orient="records")
            except Exception as e:
                print(f"[INSIGHTS] Warning loading forecast comparison rows: {e}")

        return {
            "ok": True,
            "rows": rows,
            "budget_rows": budget_rows,
            "forecast_rows": forecast_rows,
            "meta": meta,
        }

    except Exception as e:
        return {
            "ok": False,
            "rows": [],
            "budget_rows": [],
            "forecast_rows": [],
            "meta": None,
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────
# Meta loader
# ─────────────────────────────────────────────────────────────
def _load_meta(df: pd.DataFrame) -> dict:
    if os.path.exists(AGENCY_PERFORMANCE_META_PATH):
        try:
            with open(AGENCY_PERFORMANCE_META_PATH) as f:
                return json.load(f)
        except Exception:
            pass

    meta = {
        "data_available_upto": None,
        "forecast_month": None,
        "realised_accuracy_available": False,
        "total_skus": int(len(df)),
        "agencies": [],
    }

    if "Data_Available_Upto" in df.columns:
        vals = df["Data_Available_Upto"].dropna().unique()
        meta["data_available_upto"] = str(vals[0]) if len(vals) else None

    if "Forecast_Month" in df.columns:
        vals = df["Forecast_Month"].dropna().unique()
        meta["forecast_month"] = str(vals[0]) if len(vals) else None

    if "Realised_Accuracy_Available" in df.columns:
        meta["realised_accuracy_available"] = bool(
            df["Realised_Accuracy_Available"].iloc[0]
            if len(df) else False
        )

    if "Agency" in df.columns:
        meta["agencies"] = sorted(df["Agency"].dropna().unique().tolist())

    # Loss metrics
    if "Raw_Loss_Qty" in df.columns:
        meta["total_raw_loss_qty"] = float(
            pd.to_numeric(df["Raw_Loss_Qty"], errors="coerce").fillna(0).sum()
        )

    if "Other_Loss_Qty" in df.columns:
        meta["total_other_loss_qty"] = float(
            pd.to_numeric(df["Other_Loss_Qty"], errors="coerce").fillna(0).sum()
        )

    if "Stockout_Loss_Qty" in df.columns:
        meta["total_stockout_loss_qty"] = float(
            pd.to_numeric(df["Stockout_Loss_Qty"], errors="coerce").fillna(0).sum()
        )

    if "Current_Month_Loss_Qty" in df.columns:
        meta["total_current_month_loss_qty"] = float(
            pd.to_numeric(df["Current_Month_Loss_Qty"], errors="coerce").fillna(0).sum()
        )

    if "Stockout_Flag" in df.columns:
        meta["stockout_sku_count"] = int(
            pd.to_numeric(df["Stockout_Flag"], errors="coerce").fillna(0).sum()
        )

    if "Current_Month_Label" in df.columns:
        vals = df["Current_Month_Label"].dropna().unique()
        meta["current_month_label"] = (
            str(vals[0]) if len(vals) else meta.get("data_available_upto")
        )

    # Budget metrics — fallback from the budget analysis CSV (ALL items)
    if os.path.exists(BUDGET_ANALYSIS_LATEST_PATH):
        try:
            bdf = pd.read_csv(BUDGET_ANALYSIS_LATEST_PATH)
            meta["budget_item_count"] = int(len(bdf))
            for src, key in [
                ("Budget_Qty",          "total_budget_qty"),
                ("Annual_Budget_Qty",   "total_annual_budget_qty"),
                ("FYTD_Sales_Qty",      "total_fytd_sales_qty"),
                ("Budget_Value",        "total_budget_value"),
                ("Annual_Budget_Value", "total_annual_budget_value"),
                ("FYTD_Sales_Value",    "total_fytd_sales_value"),
            ]:
                if src in bdf.columns:
                    meta[key] = float(
                        pd.to_numeric(bdf[src], errors="coerce").fillna(0).sum()
                    )
        except Exception:
            pass

    # Forecast comparison metrics — fallback from the forecast comparison CSV
    if os.path.exists(FORECAST_COMPARISON_LATEST_PATH):
        try:
            fdf = pd.read_csv(FORECAST_COMPARISON_LATEST_PATH)
            meta["forecast_comparison_sku_count"] = int(len(fdf))
            external_cols = [
                c for c in [
                    "Approved_Consensus_Forecast_Qty", "Best_Fit_With_MI_Forecast_Qty",
                    "Consensus_Forecast_Qty", "Final_Forecast_Qty",
                ] if c in fdf.columns
            ]
            if external_cols:
                meta["forecast_comparison_matched_sku_count"] = int(
                    fdf[external_cols].notna().any(axis=1).sum()
                )
        except Exception:
            pass

    return meta