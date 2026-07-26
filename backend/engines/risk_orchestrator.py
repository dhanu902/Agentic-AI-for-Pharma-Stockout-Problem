# backend/engines/risk_orchestrator.py ---> ⚙️ M+1 risk pipeline runner

import os
from datetime import datetime

import pandas as pd

from engines import risk_engine

from services.forecast_service import FORECAST_ALL_SKUS_LATEST_PATH
from services.sku_master_service import load_sku_master_full
from services.risk_service import (
    BASE_SNAPSHOT_PATH,
    INVENTORY_SNAPSHOT_PATH,
    RISK_LATEST_PATH,
    RISK_RUN_LOG_PATH,
    load_inventory_workbook,
    load_risk_base_snapshot,
    save_inventory_snapshot,
    save_risk_base_snapshot,
    save_risk_latest,
    append_risk_run_log,
    validate_inventory_forecast_alignment,
    _normalize_itemcode,
)


# ============================================================
# INVENTORY BASE BUILD — M+1, physical stock only, FULL master universe
# ============================================================
def build_inventory_snapshot() -> str:
    """
    Build the M+1 physical-stock inventory base, scoped to every SKU in
    sku_master_full.csv (the full budgeted universe: real ItemCodes +
    synthetic codes) rather than only SKUs that have both inventory and
    forecast data. Missing inventory rows are zero-filled and flagged
    (Has_Inventory_Data=0), not dropped.

    Important business rule:
        Inventory.xlsx Month = opening stock for that same forecast month.

    Example:
        Model trained up to 2026-01
        Forecast_Month = 2026-02
        Inventory Month = 2026-02-01 00:00

    Therefore:
        Forecast_Month must match Inventory Base_Month.

    Source of Forecast_Month:
        forecast_all_skus_latest.csv — the combined model + trend-baseline
        M+1 table covering ALL budgeted SKUs, not forecast_latest.csv
        (which is AI-model / focus SKUs only).
    """
    if not os.path.exists(FORECAST_ALL_SKUS_LATEST_PATH):
        raise FileNotFoundError(
            "forecast_all_skus_latest.csv required to determine forecast month. "
            "Run /api/forecast/export first."
        )

    forecast_df = pd.read_csv(FORECAST_ALL_SKUS_LATEST_PATH)

    if "Forecast_Month" not in forecast_df.columns:
        raise ValueError("forecast_all_skus_latest.csv missing Forecast_Month column.")

    forecast_df["Forecast_Year"] = forecast_df["Forecast_Month"].str[:4].astype(int)
    forecast_df["Forecast_Month_Number"] = forecast_df["Forecast_Month"].str[5:7].astype(int)

    runtime_df = (
        forecast_df[["Forecast_Year", "Forecast_Month_Number"]]
        .drop_duplicates()
        .rename(
            columns={
                "Forecast_Year": "Year",
                "Forecast_Month_Number": "Month_Number",
            }
        )
    )

    db_df, wh_df = load_inventory_workbook()

    # Full budgeted SKU universe (real + synthetic codes). Risk output is
    # scoped to this list so leftover/trend-only SKUs and SKUs with no
    # physical stock record still get a row instead of silently vanishing.
    master_df = load_sku_master_full()  # ProductCode, ProductName, Agency, AgencyCode, Is_Synthetic_Code

    inventory_df = risk_engine.build_inventory_risk_snapshot(
        db_df=db_df,
        wh_df=wh_df,
        runtime_df=runtime_df,
        master_df=master_df,
    )

    if inventory_df is None or inventory_df.empty:
        raise ValueError(
            "Inventory base is empty. Check risk_engine or sku_master_full.csv."
        )

    save_inventory_snapshot(inventory_df)
    save_risk_base_snapshot(inventory_df)

    return BASE_SNAPSHOT_PATH


# ============================================================
# MAIN PIPELINE — M+1 risk, physical stock only, FULL master universe
# ============================================================
def run_risk_pipeline() -> dict:
    """
    Full M+1 risk pipeline, now scoped to every SKU in sku_master_full.csv.

    Inputs:
        - Inventory.xlsx DB/WH sheets
        - forecast_all_skus_latest.csv (model + trend-baseline, ALL budgeted SKUs)
        - sku_master_full.csv (full budgeted SKU universe)

    Output:
        - inventory_risk_snapshot.csv
        - risk_base_snapshot.csv
        - risk_latest.csv (now includes Has_Inventory_Data, Has_Forecast_Data,
          Is_Synthetic_Code flags, and Risk_Level values NOT_TRACKED / NO_DATA /
          NO_INVENTORY_DATA / NO_FORECAST_DATA for SKUs with missing data)

    Pending PO/GRN is intentionally NOT used here.
    """
    if not os.path.exists(FORECAST_ALL_SKUS_LATEST_PATH):
        return {
            "ok": False,
            "error": "forecast_all_skus_latest.csv not found. Run /api/forecast/export first.",
        }

    try:
        build_inventory_snapshot()

        base_df = load_risk_base_snapshot()
        forecast_df = pd.read_csv(FORECAST_ALL_SKUS_LATEST_PATH)

        base_df["ItemCode"] = _normalize_itemcode(base_df["ItemCode"])
        forecast_df["ItemCode"] = _normalize_itemcode(forecast_df["ItemCode"])

        alignment = validate_inventory_forecast_alignment(base_df, forecast_df)

        risk_df = risk_engine.build_risk_table(
            base_df=base_df,
            forecast_df=forecast_df,
            base_month_col="Base_Month",
            forecast_month_col="Forecast_Month",
            item_col="ItemCode",
            forecast_col="Forecast_Qty",
        )

        save_risk_latest(risk_df)

        run_row = {
            "Run_ID": datetime.utcnow().strftime("%Y%m%d%H%M%S"),
            "Run_Date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "Rows_Exported": int(len(risk_df)),
            "Base_Snapshot_Path": BASE_SNAPSHOT_PATH,
            "Inventory_Snapshot_Path": INVENTORY_SNAPSHOT_PATH,
            "Forecast_Path": FORECAST_ALL_SKUS_LATEST_PATH,
            "Risk_Output_Path": RISK_LATEST_PATH,
        }
        append_risk_run_log(run_row)

        risk_level_counts = (
            risk_df["Risk_Level"].value_counts().to_dict()
            if "Risk_Level" in risk_df.columns
            else {}
        )

        data_quality_summary = {
            "no_inventory_data": (
                int((risk_df["Has_Inventory_Data"] == 0).sum())
                if "Has_Inventory_Data" in risk_df.columns else 0
            ),
            "no_forecast_data": (
                int((risk_df["Has_Forecast_Data"] == 0).sum())
                if "Has_Forecast_Data" in risk_df.columns else 0
            ),
            "not_tracked_synthetic": (
                int((risk_df["Is_Synthetic_Code"] == 1).sum())
                if "Is_Synthetic_Code" in risk_df.columns else 0
            ),
        }

        return {
            "ok": True,
            "message": "M+1 risk generated successfully for the full master SKU universe.",
            "rows": int(len(risk_df)),
            "path": RISK_LATEST_PATH,
            "base_rows": int(len(base_df)),
            "forecast_rows": int(len(forecast_df)),
            "base_snapshot_path": BASE_SNAPSHOT_PATH,
            "inventory_snapshot_path": INVENTORY_SNAPSHOT_PATH,
            "log_path": RISK_RUN_LOG_PATH,
            "alignment": alignment,
            "risk_level_summary": risk_level_counts,
            "data_quality_summary": data_quality_summary,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
        }