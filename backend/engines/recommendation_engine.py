# backend/engines/recommendation_engine.py 

import math
import pandas as pd


def safe_num(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def round_up_to_multiple(qty, multiple):
    qty = safe_num(qty)
    multiple = safe_num(multiple)

    if multiple <= 0:
        return qty

    return math.ceil(qty / multiple) * multiple


def build_recommendation_for_sku(
    risk_row: dict,
    horizon_rows=None,
    policy_row=None,
    regulatory_row=None,
    supplier_row=None,
):
    horizon_rows = horizon_rows or []
    policy_row = policy_row or {}
    regulatory_row = regulatory_row or {}
    supplier_row = supplier_row or {}

    item_code = str(risk_row.get("ItemCode", "")).strip()

    risk_level = (
        risk_row.get("Risk_Level")
        or risk_row.get("Final_Risk_Level")
        or risk_row.get("Status")
        or "UNKNOWN"
    )

    forecast_qty = safe_num(
        risk_row.get("Forecast_Qty")
        or risk_row.get("Forecast_Prediction")
        or risk_row.get("Sec Sales / Forecast")
    )

    unmet_qty = safe_num(
        risk_row.get("Scenario_A_Unmet")
        or risk_row.get("Unmet_Qty")
        or risk_row.get("A_Unmet")
    )

    closing_stock = safe_num(
        risk_row.get("Closing_Stock")
        or risk_row.get("Scenario_A_Closing_Stock")
    )

    incoming_qty = safe_num(
        risk_row.get("Incoming_Qty")
        or risk_row.get("Open_PO_Qty")
        or risk_row.get("PO_Qty")
    )

    moq = safe_num(policy_row.get("MOQ"), 0)
    order_multiple = safe_num(policy_row.get("Order_Multiple"), 0)

    recommended_qty = max(unmet_qty, 0)

    if recommended_qty > 0 and moq > 0:
        recommended_qty = max(recommended_qty, moq)

    if recommended_qty > 0 and order_multiple > 0:
        recommended_qty = round_up_to_multiple(recommended_qty, order_multiple)

    import_license_status = regulatory_row.get("Import_License_Status", "UNKNOWN")
    registration_status = regulatory_row.get("Registration_Status", "UNKNOWN")
    supplier_status = supplier_row.get("Supplier_Status", "UNKNOWN")

    action_type = "NO_ACTION"
    priority = "LOW"
    needs_approval = False
    flags = []
    explanation = []

    risk_upper = str(risk_level).upper()

    if "NO_RISK" in risk_upper or "NO RISK" in risk_upper:
        action_type = "NO_ACTION"
        priority = "LOW"
        recommended_qty = 0
        explanation.append("SKU is currently not at risk.")
        explanation.append("Projected stock is enough to cover forecast demand.")

    elif "SHORT" in risk_upper:
        action_type = "USE_SHORT_EXPIRY_STOCK"
        priority = "MEDIUM"
        explanation.append("Risk can be handled using short-expiry stock.")
        explanation.append("Planner should validate FEFO usage before replenishment.")

    elif "USABLE" in risk_upper or "TRADE" in risk_upper or "INSPECTION" in risk_upper or "BLOCK" in risk_upper:
        action_type = "USE_USABLE_STOCK_WITH_APPROVAL"
        priority = "HIGH"
        needs_approval = True
        explanation.append("Risk requires warehouse usable stock such as trade, inspection, or blocked stock.")
        explanation.append("Planner approval is required before using restricted stock buckets.")

    elif "CRITICAL" in risk_upper:
        action_type = "REPLENISH_OR_EXPEDITE"
        priority = "CRITICAL"
        needs_approval = True
        explanation.append("SKU is critical because available/projected stock cannot cover forecast demand.")
        explanation.append("Replenishment, PO expedition, or escalation is required.")

    else:
        action_type = "REVIEW_REQUIRED"
        priority = "MEDIUM"
        needs_approval = True
        explanation.append("Risk level is unclear. Planner review is required.")

    if incoming_qty > 0:
        flags.append({
            "type": "PO",
            "status": "AVAILABLE",
            "message": f"Incoming/Open PO quantity available: {incoming_qty:,.0f}"
        })
    else:
        flags.append({
            "type": "PO",
            "status": "NONE",
            "message": "No incoming PO quantity found."
        })

    if import_license_status in ["EXPIRING_SOON", "EXPIRED", "BLOCKED"]:
        flags.append({
            "type": "IMPORT_LICENSE",
            "status": import_license_status,
            "message": f"Import license status: {import_license_status}"
        })
        needs_approval = True

    if registration_status in ["EXPIRING_SOON", "EXPIRED", "BLOCKED"]:
        flags.append({
            "type": "REGISTRATION",
            "status": registration_status,
            "message": f"Product registration status: {registration_status}"
        })
        needs_approval = True

    if supplier_status in ["DELAYED", "HIGH_RISK", "UNRELIABLE"]:
        flags.append({
            "type": "SUPPLIER",
            "status": supplier_status,
            "message": f"Supplier reliability status: {supplier_status}"
        })
        needs_approval = True

    return {
        "item_code": item_code,
        "risk_summary": {
            "risk_level": risk_level,
            "forecast_qty": forecast_qty,
            "unmet_qty": unmet_qty,
            "closing_stock": closing_stock,
            "incoming_qty": incoming_qty,
        },
        "recommendation": {
            "action_type": action_type,
            "recommended_qty": recommended_qty,
            "priority": priority,
            "needs_approval": needs_approval,
        },
        "business_flags": flags,
        "explanation": explanation,
        "horizon": horizon_rows,
    }