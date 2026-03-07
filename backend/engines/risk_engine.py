#risk_engine.py

from __future__ import annotations
import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Tuple
import pandas as pd


# -----------------------------
# Helpers
# -----------------------------
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
    used = min(need, available)
    remaining = need - used
    return used, remaining


def ensure_cols(df: pd.DataFrame, required: List[str], where: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"[{where}] Missing required columns: {missing}")


# -----------------------------
# Core Scenario Evaluation
# -----------------------------
@dataclass
class ScenarioResult:
    scenario: str
    met_demand: bool
    unmet: float
    used_distributor: float
    used_trade: float
    used_inspection: float
    used_blocked: float
    flags: List[str]
    reasoning: List[str]


def scenario_A_no_risk_only(F: float, D_NR: float, T_NR: float) -> ScenarioResult:
    need = F

    used_d, need = allocate_step(need, D_NR)
    used_t, need = allocate_step(need, T_NR)

    flags = []
    if need > 0:
        flags.append("STOCKOUT_RISK")

    reasoning = [
        f"Scenario A (No-Risk only): demand={F}",
        f"Step 1 Distributor No-Risk: used={used_d} / available={D_NR}, remaining={F - used_d}",
        f"Step 2 Primary No-Risk: used={used_t} / available={T_NR}, remaining={need}",
        "Only No-Risk buckets allowed.",
    ]

    return ScenarioResult(
        scenario="A_NO_RISK_ONLY",
        met_demand=(need <= 0),
        unmet=need,
        used_distributor=used_d,
        used_trade=used_t,
        used_inspection=0.0,
        used_blocked=0.0,
        flags=flags,
        reasoning=reasoning,
    )


def scenario_B_trade_allowed(F: float, D_total: float, T_trade: float, D_NR: float, T_NR: float) -> ScenarioResult:
    need = F

    used_d, need = allocate_step(need, D_total)
    used_t, need = allocate_step(need, T_trade)

    flags = []
    if need > 0:
        flags.append("STOCKOUT_RISK")

    short_expiry_used = F > (D_NR + T_NR)
    if short_expiry_used and (need <= 0):
        flags.append("SHORT_EXPIRY_USED")

    reasoning = [
        f"Scenario B (Trade allowed NR+Short-Expiry): demand={F}",
        f"Step 1 Distributor (trade): used={used_d} / available={D_total}, remaining={F - used_d}",
        f"Step 2 Primary Trade: used={used_t} / available={T_trade}, remaining={need}",
        "Trade stock allowed (includes short-expiry concept). No inspection stock used in this scenario.",
        "SHORT_EXPIRY_USED is inferred only if No-Risk split exists; otherwise treated as unknown/false.",
    ]

    return ScenarioResult(
        scenario="B_TRADE_ALLOWED",
        met_demand=(need <= 0),
        unmet=need,
        used_distributor=used_d,
        used_trade=used_t,
        used_inspection=0.0,
        used_blocked=0.0,
        flags=flags,
        reasoning=reasoning,
    )


def scenario_C_total_usable(F: float, D_total: float, T_trade: float, I_insp: float, B_blocked: float) -> ScenarioResult:
    need = F

    used_d, need = allocate_step(need, D_total)
    used_t, need = allocate_step(need, T_trade)
    used_i, need = allocate_step(need, I_insp)
    used_b, need = allocate_step(need, B_blocked)

    flags = []
    if used_i > 0:
        flags.append("INSPECTION_USED")
    if used_b > 0:
        flags.append("BLOCKED_USED")
    if need > 0:
        flags.append("CRITICAL_STOCKOUT")

    reasoning = [
        f"Scenario C (Total usable): demand={F}",
        f"Step 1 Distributor (trade): used={used_d} / available={D_total}, remaining={F - used_d}",
        f"Step 2 Primary Trade: used={used_t} / available={T_trade}, remaining={F - used_d - used_t}",
        f"Step 3 Inspection: used={used_i} / available={I_insp}, remaining={F - used_d - used_t - used_i}",
        f"Step 4 Blocked: used={used_b} / available={B_blocked}, remaining={need}",
        "Total usable allows trade + inspection + blocked.",
    ]

    return ScenarioResult(
        scenario="C_TOTAL_USABLE",
        met_demand=(need <= 0),
        unmet=need,
        used_distributor=used_d,
        used_trade=used_t,
        used_inspection=used_i,
        used_blocked=used_b,
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


# -----------------------------
# Pipeline
# -----------------------------
def build_risk_table(
    base_df: pd.DataFrame,
    forecast_df: pd.DataFrame,
    base_month_col: str = "Month",
    forecast_month_col: str = "Month",
    item_col: str = "ItemCode",
    forecast_col: str = "Forecast_Qty",
) -> pd.DataFrame:

    required_base = [
        item_col,
        "Distributor_Inventory_Qty",
        "Available_Primary_Inventory_Qty",
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
        how="inner",
        suffixes=("_base", "_forecast"),
    )

    run_id = now_run_id()
    out_rows = []

    for _, r in merged.iterrows():
        item = r[item_col]

        base_month = safe_value(r.get(f"{base_month_col}_base", r.get(base_month_col, None)))
        forecast_month = safe_value(r.get(f"{forecast_month_col}_forecast", r.get(forecast_month_col, None)))

        F = safe_float(r[forecast_col])

        D_total = safe_float(r["Distributor_Inventory_Qty"])
        T_trade = safe_float(r["Available_Primary_Inventory_Qty"])
        I_insp = safe_float(r["Inspection_Stock_Qty"])
        B_blocked = safe_float(r["Blocked_Stock_Qty"])

        D_NR = safe_float(r.get("Distributor_NoRisk_Qty", D_total))
        T_NR = safe_float(r.get("Primary_NoRisk_Qty", T_trade))

        A = scenario_A_no_risk_only(F, D_NR, T_NR)
        B = scenario_B_trade_allowed(F, D_total, T_trade, D_NR, T_NR)
        C = scenario_C_total_usable(F, D_total, T_trade, I_insp, B_blocked)

        risk_level = classify_risk(A, B, C)

        out_rows.append(
            {
                "run_id": run_id,
                "Base_Month": base_month,
                "Forecast_Month": forecast_month,
                "ItemCode": item,
                "Forecast_Qty": F,

                "A_met": A.met_demand,
                "A_unmet": A.unmet,
                "A_used_dist": A.used_distributor,
                "A_used_trade": A.used_trade,
                "A_flags": json.dumps(A.flags),
                "A_reasoning": json.dumps(A.reasoning),

                "B_met": B.met_demand,
                "B_unmet": B.unmet,
                "B_used_dist": B.used_distributor,
                "B_used_trade": B.used_trade,
                "B_flags": json.dumps(B.flags),
                "B_reasoning": json.dumps(B.reasoning),

                "C_met": C.met_demand,
                "C_unmet": C.unmet,
                "C_used_dist": C.used_distributor,
                "C_used_trade": C.used_trade,
                "C_used_insp": C.used_inspection,
                "C_used_block": C.used_blocked,
                "C_flags": json.dumps(C.flags),
                "C_reasoning": json.dumps(C.reasoning),

                "Risk_Level": risk_level,
            }
        )

    return pd.DataFrame(out_rows)


def run_risk_engine(base_path: str, forecast_path: str, out_path: str):
    base_df = pd.read_csv(base_path)
    forecast_df = pd.read_csv(forecast_path)

    risk_df = build_risk_table(base_df, forecast_df)
    risk_df.to_csv(out_path, index=False)

    return {
        "rows": int(len(risk_df)),
        "path": out_path,
        "run_id": risk_df["run_id"].iloc[0] if len(risk_df) else None
    }


def main():
    parser = argparse.ArgumentParser(description="Phase-1 Risk Engine (pure stock projection).")
    parser.add_argument("--base", required=True)
    parser.add_argument("--forecast", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--base-month-col", default="Month")
    parser.add_argument("--forecast-month-col", default="Month")
    parser.add_argument("--item-col", default="ItemCode")
    parser.add_argument("--forecast-col", default="Forecast_Qty")

    args = parser.parse_args()

    base_df = pd.read_csv(args.base)
    forecast_df = pd.read_csv(args.forecast)

    risk_df = build_risk_table(
        base_df=base_df,
        forecast_df=forecast_df,
        base_month_col=args.base_month_col,
        forecast_month_col=args.forecast_month_col,
        item_col=args.item_col,
        forecast_col=args.forecast_col,
    )

    risk_df.to_csv(args.out, index=False)
    print(f"✅ Risk output saved: {args.out}")
    print(f"Rows: {len(risk_df)} | run_id: {risk_df['run_id'].iloc[0] if len(risk_df) else 'N/A'}")


if __name__ == "__main__":
    main()