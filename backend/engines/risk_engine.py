from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Tuple

import pandas as pd


# ============================================================
# HELPERS
# ============================================================
def now_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _normalize_itemcode(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )


def safe_float(x) -> float:
    try:
        if pd.isna(x):
            return 0.0
        return float(x)
    except Exception:
        return 0.0


def safe_value(x):
    return None if pd.isna(x) else x


def allocate_step(need: float, available: float) -> Tuple[float, float]:
    used = min(max(need, 0.0), max(available, 0.0))
    remaining = max(need - used, 0.0)
    return used, remaining


def ensure_cols(df: pd.DataFrame, required: List[str], where: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"[{where}] Missing required columns: {missing}")


# ============================================================
# RESULT SHAPE
# ============================================================
@dataclass
class ScenarioResult:
    scenario: str
    met_demand: bool
    unmet: float

    used_db_no_risk: float
    used_db_short_exp: float

    used_wh_trade: float
    used_wh_inspection: float
    used_wh_blocked: float

    flags: List[str]
    reasoning: List[str]


# ============================================================
# SCENARIOS
# ============================================================
def scenario_A_no_risk_only(F: float, db_no_risk: float) -> ScenarioResult:
    """
    Scenario A:
    Use only distributor no-risk stock (X)
    """
    need = max(F, 0.0)

    used_x, need = allocate_step(need, db_no_risk)

    flags = []
    if need > 0:
        flags.append("DB_TRADE_REQUIRED")

    reasoning = [
        f"Scenario A (DB No-Risk only): demand={F}",
        f"Step 1 Distributor No-Risk: used={used_x} / available={db_no_risk}, remaining={need}",
        "Only distributor no-risk stock allowed.",
    ]

    return ScenarioResult(
        scenario="A_NO_RISK_ONLY",
        met_demand=(need <= 0),
        unmet=need,
        used_db_no_risk=used_x,
        used_db_short_exp=0.0,
        used_wh_trade=0.0,
        used_wh_inspection=0.0,
        used_wh_blocked=0.0,
        flags=flags,
        reasoning=reasoning,
    )


def scenario_B_trade_allowed(F: float, db_no_risk: float, db_short_exp: float) -> ScenarioResult:
    """
    Scenario B:
    Use distributor trade stock = X + Y
    where:
    X = distributor no-risk
    Y = distributor short-expiry
    """
    need = max(F, 0.0)

    used_x, need = allocate_step(need, db_no_risk)
    used_y, need = allocate_step(need, db_short_exp)

    flags = []
    if used_y > 0:
        flags.append("DB_SHORT_EXP_REQUIRED")
    if need > 0:
        flags.append("WH_STOCK_REQUIRED")
    if not flags:
        flags.append("NO_RISK_COVERED")

    reasoning = [
        f"Scenario B (DB Trade = No-Risk + Short-Expiry): demand={F}",
        f"Step 1 Distributor No-Risk: used={used_x} / available={db_no_risk}, remaining={F - used_x}",
        f"Step 2 Distributor Short-Expiry: used={used_y} / available={db_short_exp}, remaining={need}",
        "Distributor trade stock includes no-risk and short-expiry stock.",
    ]

    return ScenarioResult(
        scenario="B_TRADE_ALLOWED",
        met_demand=(need <= 0),
        unmet=need,
        used_db_no_risk=used_x,
        used_db_short_exp=used_y,
        used_wh_trade=0.0,
        used_wh_inspection=0.0,
        used_wh_blocked=0.0,
        flags=flags,
        reasoning=reasoning,
    )


def scenario_C_total_usable(
    F: float,
    db_no_risk: float,
    db_short_exp: float,
    wh_no_risk: float,
    wh_short_exp: float,
    wh_insp: float,
    wh_blocked: float,
) -> ScenarioResult:
    """
    Scenario C:
    Use X + Y + WH
    X = distributor no-risk
    Y = distributor short-expiry
    WH = primary trade + inspection + blocked
    """
    need = max(F, 0.0)

    used_x, need = allocate_step(need, db_no_risk)
    used_y, need = allocate_step(need, db_short_exp)
    used_wh_nr, need = allocate_step(need, wh_no_risk)
    used_wh_se, need = allocate_step(need, wh_short_exp)
    used_wh_i, need = allocate_step(need, wh_insp)
    used_wh_b, need = allocate_step(need, wh_blocked)

    flags = []
    if used_y > 0:
        flags.append("DB_SHORT_EXP_REQUIRED")
    if used_wh_nr > 0:
        flags.append("WH_NO_RISK_REQUIRED")
    if used_wh_se > 0:
        flags.append("WH_SHORT_EXP_REQUIRED")
    if used_wh_i > 0:
        flags.append("WH_INSPECTION_REQUIRED")
    if used_wh_b > 0:
        flags.append("WH_BLOCKED_REQUIRED")
    if need > 0:
        flags.append("CRITICAL_STOCKOUT")

    reasoning = [
        f"Scenario C (DB Trade + Warehouse usable): demand={F}",
        f"Step 1 Distributor No-Risk: used={used_x} / available={db_no_risk}, remaining={F - used_x}",
        f"Step 2 Distributor Short-Expiry: used={used_y} / available={db_short_exp}, remaining={F - used_x - used_y}",
        f"Step 3 Warehouse No-Risk: used={used_wh_nr} / available={wh_no_risk}, remaining={F - used_x - used_y - used_wh_nr}",
        f"Step 4 Warehouse Short-Expiry: used={used_wh_se} / available={wh_short_exp}, remaining={F - used_x - used_y - used_wh_nr - used_wh_se}",
        f"Step 5 Inspection: used={used_wh_i} / available={wh_insp}, remaining={F - used_x - used_y - used_wh_nr - used_wh_se - used_wh_i}",
        f"Step 6 Blocked: used={used_wh_b} / available={wh_blocked}, remaining={need}",
        "Scenario C includes DB no-risk + DB short-expiry + WH no-risk + WH short-expiry + inspection + blocked.",
    ]

    return ScenarioResult(
        scenario="C_TOTAL_USABLE",
        met_demand=(need <= 0),
        unmet=need,
        used_db_no_risk=used_x,
        used_db_short_exp=used_y,
        used_wh_trade=used_wh_nr + used_wh_se,
        used_wh_inspection=used_wh_i,
        used_wh_blocked=used_wh_b,
        flags=flags,
        reasoning=reasoning,
    )


def classify_risk(A: ScenarioResult, B: ScenarioResult, C: ScenarioResult) -> str:
    if A.met_demand:
        return "NO_RISK"
    if B.met_demand:
        return "SHORT_EXPIRY_REQUIRED"
    if C.met_demand:
        return "USABLE_STOCK_REQUIRED"
    return "CRITICAL_STOCKOUT"


# ============================================================
# MAIN BUILD
# ============================================================
def build_risk_table(
    base_df: pd.DataFrame,
    forecast_df: pd.DataFrame,
    base_month_col: str = "Month",
    forecast_month_col: str = "Forecast_Month",
    item_col: str = "ItemCode",
    forecast_col: str = "Forecast_Qty",
) -> pd.DataFrame:
    """
    Expected base_df columns:
    - ItemCode
    - Month
    - Distributor_NoRisk_Qty
    - Distributor_ShortExp_Qty
    - Distributor_Expired_Qty
    - Distributor_Trade_Qty
    - Primary_NoRisk_Qty
    - Primary_ShortExp_Qty
    - Primary_Expired_Qty
    - Primary_Trade_Qty
    - Inspection_Stock_Qty
    - Blocked_Stock_Qty

    Expected forecast_df columns:
    - ItemCode
    - Forecast_Qty
    - Forecast_Month
    """

    required_base = [
        item_col,
        "Distributor_NoRisk_Qty",
        "Distributor_ShortExp_Qty",
        "Distributor_Expired_Qty",
        "Distributor_Trade_Qty",
        "Primary_Trade_Qty",
        "Inspection_Stock_Qty",
        "Blocked_Stock_Qty",
    ]
    ensure_cols(base_df, required_base, "base_data")
    ensure_cols(forecast_df, [item_col, forecast_col], "forecast_data")

    base_df = base_df.copy()
    forecast_df = forecast_df.copy()

    base_df[item_col] = _normalize_itemcode(base_df[item_col])
    forecast_df[item_col] = _normalize_itemcode(forecast_df[item_col])

    keep_forecast_cols = [item_col, forecast_col]
    if forecast_month_col in forecast_df.columns:
        keep_forecast_cols.append(forecast_month_col)

    merged = base_df.merge(
        forecast_df[keep_forecast_cols],
        on=item_col,
        how="inner"
    )

    run_id = now_run_id()
    out_rows = []

    for _, r in merged.iterrows():
        item = r[item_col]

        base_month = safe_value(r.get(base_month_col, None))
        forecast_month = safe_value(r.get(forecast_month_col, None))

        F = safe_float(r[forecast_col])

        # Distributor buckets
        db_no_risk = safe_float(r.get("Distributor_NoRisk_Qty", 0))
        db_short_exp = safe_float(r.get("Distributor_ShortExp_Qty", 0))
        db_expired = safe_float(r.get("Distributor_Expired_Qty", 0))
        db_trade = db_no_risk + db_short_exp

        # Primary / warehouse buckets
        primary_no_risk = safe_float(r.get("Primary_NoRisk_Qty", 0))
        primary_short_exp = safe_float(r.get("Primary_ShortExp_Qty", 0))
        primary_expired = safe_float(r.get("Primary_Expired_Qty", 0))
        primary_trade = primary_no_risk + primary_short_exp
        inspection_qty = safe_float(r.get("Inspection_Stock_Qty", 0))
        blocked_qty = safe_float(r.get("Blocked_Stock_Qty", 0))

        A = scenario_A_no_risk_only(F, db_no_risk)
        B = scenario_B_trade_allowed(F, db_no_risk, db_short_exp)
        C = scenario_C_total_usable(
            F,
            db_no_risk,
            db_short_exp,
            primary_no_risk,
            primary_short_exp,
            inspection_qty,
            blocked_qty,
        )

        risk_level = classify_risk(A, B, C)

        out_rows.append({
            "run_id": run_id,
            "Base_Month": base_month,
            "Forecast_Month": forecast_month,
            "ItemCode": item,
            "Forecast_Qty": F,

            # -------------------------
            # KPI STOCK BUCKETS
            # -------------------------
            "Distributor_NoRisk_Qty": db_no_risk,
            "Distributor_ShortExp_Qty": db_short_exp,
            "Distributor_Expired_Qty": db_expired,
            "Distributor_Trade_Qty": db_trade,

            "Primary_NoRisk_Qty": primary_no_risk,
            "Primary_ShortExp_Qty": primary_short_exp,
            "Primary_Expired_Qty": primary_expired,
            "Primary_Trade_Qty": primary_trade,
            "Inspection_Stock_Qty": inspection_qty,
            "Blocked_Stock_Qty": blocked_qty,

            # -------------------------
            # Scenario A
            # -------------------------
            "A_met": A.met_demand,
            "A_unmet": A.unmet,
            "A_used_db_no_risk": A.used_db_no_risk,
            "A_used_db_short_exp": A.used_db_short_exp,
            "A_used_wh_trade": A.used_wh_trade,
            "A_used_wh_insp": A.used_wh_inspection,
            "A_used_wh_blocked": A.used_wh_blocked,
            "A_flags": json.dumps(A.flags),
            "A_reasoning": json.dumps(A.reasoning),

            # -------------------------
            # Scenario B
            # -------------------------
            "B_met": B.met_demand,
            "B_unmet": B.unmet,
            "B_used_db_no_risk": B.used_db_no_risk,
            "B_used_db_short_exp": B.used_db_short_exp,
            "B_used_wh_trade": B.used_wh_trade,
            "B_used_wh_insp": B.used_wh_inspection,
            "B_used_wh_blocked": B.used_wh_blocked,
            "B_flags": json.dumps(B.flags),
            "B_reasoning": json.dumps(B.reasoning),

            # -------------------------
            # Scenario C
            # -------------------------
            "C_met": C.met_demand,
            "C_unmet": C.unmet,
            "C_used_db_no_risk": C.used_db_no_risk,
            "C_used_db_short_exp": C.used_db_short_exp,
            "C_used_wh_trade": C.used_wh_trade,
            "C_used_wh_insp": C.used_wh_inspection,
            "C_used_wh_blocked": C.used_wh_blocked,
            "C_flags": json.dumps(C.flags),
            "C_reasoning": json.dumps(C.reasoning),

            # -------------------------
            # Final class
            # -------------------------
            "Risk_Level": risk_level,
        })

    return pd.DataFrame(out_rows)