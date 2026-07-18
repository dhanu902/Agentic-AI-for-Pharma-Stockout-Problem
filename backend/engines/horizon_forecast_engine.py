# backend/engines/horizon_forecast_engine.py

import pandas as pd
import numpy as np
from typing import Optional


def normalize_itemcode(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )


def safe_float(x, default=0.0) -> float:
    try:
        if pd.isna(x):
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def month_add(month_label: str, n: int) -> str:
    y, m = map(int, str(month_label).split("-"))
    m += n
    y += (m - 1) // 12
    m = ((m - 1) % 12) + 1
    return f"{y:04d}-{m:02d}"


def _safe_median(x):
    x = pd.to_numeric(x, errors="coerce").dropna()
    return float(x.median()) if len(x) else 0.0


def _safe_mean(x):
    x = pd.to_numeric(x, errors="coerce").dropna()
    return float(x.mean()) if len(x) else 0.0


def _get_sku_profile(history_df: pd.DataFrame, item_code: str) -> dict:
    hist = history_df.copy()
    hist["ItemCode"] = normalize_itemcode(hist["ItemCode"])
    sku = hist[hist["ItemCode"] == str(item_code)].copy()

    if sku.empty:
        return {
            "history_available": False,
            "behavior": "STABLE",
            "demand_state": "MATURE",
            "promo_profile": "NORMAL",
            "roll3": 0.0,
            "roll6": 0.0,
            "median6": 0.0,
            "median12": 0.0,
            "last": 0.0,
            "cv": 0.0,
            "zero_rate": 0.0,
            "trend": 0.0,
            "recurring_bonus": 0,
            "bonus_cycle": 0,
            "months_since_bonus": 999,
            "avg_bonus_uplift": 1.0,
            "last_bonus_demand": 0.0,
        }

    sku = sku.sort_values(["Year", "Month_Number"]).copy()

    demand_col = "Clean_Demand" if "Clean_Demand" in sku.columns else "Secondary_Sales_Qty"
    sales = pd.to_numeric(sku[demand_col], errors="coerce").fillna(0).clip(lower=0)

    roll3 = _safe_mean(sales.tail(3))
    roll6 = _safe_mean(sales.tail(6))
    median6 = _safe_median(sales.tail(6))
    median12 = _safe_median(sales.tail(12))
    last = float(sales.iloc[-1]) if len(sales) else 0.0

    mean_all = _safe_mean(sales)
    std_all = float(sales.std()) if len(sales) > 1 else 0.0
    cv = 0.0 if mean_all <= 0 else std_all / (mean_all + 1)
    zero_rate = float((sales.tail(6) == 0).mean()) if len(sales.tail(6)) else 0.0

    if len(sales) >= 6:
        prev3 = _safe_mean(sales.tail(6).head(3))
        recent3 = _safe_mean(sales.tail(3))
        trend = (recent3 - prev3) / 3.0
    else:
        trend = 0.0

    trend_cap = max(roll6 * 0.15, 1.0)
    trend = float(np.clip(trend, -trend_cap, trend_cap))

    latest = sku.iloc[-1]

    return {
        "history_available": True,
        "behavior": str(latest.get("Behavior_Type", "STABLE")).upper(),
        "demand_state": str(latest.get("Demand_State", "MATURE")).upper(),
        "promo_profile": str(latest.get("Promo_Profile", "NORMAL")).upper(),
        "roll3": roll3,
        "roll6": roll6,
        "median6": median6,
        "median12": median12,
        "last": last,
        "cv": cv,
        "zero_rate": zero_rate,
        "trend": trend,
        "recurring_bonus": int(safe_float(latest.get("Recurring_Bonus_SKU", 0))),
        "bonus_cycle": int(safe_float(latest.get("Bonus_Cycle_Length", 0))),
        "months_since_bonus": int(safe_float(latest.get("Months_Since_Last_Bonus", 999))),
        "avg_bonus_uplift": max(1.0, safe_float(latest.get("Avg_Bonus_Uplift", 1.0), 1.0)),
        "last_bonus_demand": safe_float(latest.get("Last_Bonus_Demand", 0.0)),
    }


def _expected_bonus_in_horizon(profile: dict, horizon_num: int) -> bool:
    if profile["recurring_bonus"] != 1:
        return False
    if profile["bonus_cycle"] <= 0:
        return False

    future_gap = profile["months_since_bonus"] + horizon_num
    return abs(future_gap - profile["bonus_cycle"]) <= 1


def _base_level(profile: dict) -> float:
    behavior = profile["behavior"]

    if behavior == "INTERMITTENT":
        return max(profile["median6"], 0.60 * profile["roll6"], 0.0)

    if behavior == "VOLATILE":
        return max(
            0.50 * profile["median6"] + 0.50 * profile["roll6"],
            0.0,
        )

    if behavior == "PROMO_DRIVEN":
        return max(
            0.40 * profile["roll3"] + 0.40 * profile["roll6"] + 0.20 * profile["median12"],
            0.0,
        )

    return max(
        0.55 * profile["roll3"] + 0.45 * profile["roll6"],
        0.0,
    )


def _apply_state_adjustment(qty: float, profile: dict, horizon_num: int) -> float:
    state = profile["demand_state"]
    trend = profile["trend"]

    if state == "GROWING":
        qty += trend * horizon_num
        qty *= 1.02 ** horizon_num

    elif state == "DECLINING":
        qty += trend * horizon_num
        qty *= 0.97 ** horizon_num

    elif state == "DYING_OR_INTERMITTENT":
        qty *= 0.85 ** min(horizon_num, 3)

    return max(qty, 0.0)


def _apply_promo_adjustment(qty: float, profile: dict, horizon_num: int) -> tuple[float, str]:
    if _expected_bonus_in_horizon(profile, horizon_num):
        uplift = min(profile["avg_bonus_uplift"], 2.5)
        bonus_anchor = max(profile["last_bonus_demand"], qty * uplift)
        qty = max(qty * uplift, bonus_anchor * 0.80)
        return qty, "PROMO_CYCLE_UPLIFT"

    if profile["behavior"] == "PROMO_DRIVEN":
        qty *= 0.95
        return qty, "PROMO_DRIVEN_NON_BONUS_NORMALIZATION"

    return qty, "NO_PROMO_ADJUSTMENT"


def _apply_horizon_uncertainty_cap(qty: float, m1_forecast: float, profile: dict, horizon_num: int) -> float:
    anchor = max(profile["roll6"], profile["roll3"], m1_forecast, 1.0)

    if profile["behavior"] == "INTERMITTENT":
        upper = max(anchor * 1.20, profile["last_bonus_demand"] * 1.10, 1.0)
    elif profile["behavior"] == "VOLATILE":
        upper = anchor * 1.60
    elif profile["behavior"] == "PROMO_DRIVEN":
        upper = anchor * 2.20
    else:
        upper = anchor * 1.45

    lower = 0.0
    return float(np.clip(qty, lower, upper))


def _forecast_horizon_qty(m1_forecast: float, horizon_num: int, profile: dict) -> tuple[float, str]:
    if horizon_num == 1:
        return max(float(m1_forecast), 0.0), "AI_CHAMPION_MODEL"

    base = _base_level(profile)

    # M+2 trusts M+1 more. M+6 trusts historical baseline more.
    model_weight = max(0.25, 0.75 - (horizon_num - 2) * 0.10)
    base_weight = 1.0 - model_weight

    qty = (m1_forecast * model_weight) + (base * base_weight)
    qty = _apply_state_adjustment(qty, profile, horizon_num)

    qty, promo_rule = _apply_promo_adjustment(qty, profile, horizon_num)
    qty = _apply_horizon_uncertainty_cap(qty, m1_forecast, profile, horizon_num)

    source = f"RULE_BASED_HORIZON::{profile['behavior']}::{profile['demand_state']}::{promo_rule}"
    return max(qty, 0.0), source


def build_horizon_forecast_table(
    forecast_df: pd.DataFrame,
    history_df: Optional[pd.DataFrame] = None,
    horizon_months: int = 6,
) -> pd.DataFrame:

    if forecast_df is None or forecast_df.empty:
        return pd.DataFrame(columns=[
            "ItemCode", "Horizon", "Forecast_Month", "Forecast_Qty", "Forecast_Source"
        ])

    required_cols = ["ItemCode", "Forecast_Month", "Forecast_Qty"]
    missing = [c for c in required_cols if c not in forecast_df.columns]
    if missing:
        raise KeyError(f"forecast_df missing columns: {missing}")

    df = forecast_df.copy()
    df["ItemCode"] = normalize_itemcode(df["ItemCode"])

    hist = pd.DataFrame()
    if history_df is not None and not history_df.empty:
        hist = history_df.copy()
        hist["ItemCode"] = normalize_itemcode(hist["ItemCode"])

    rows = []

    for _, r in df.iterrows():
        item = str(r["ItemCode"])
        base_month = str(r["Forecast_Month"])
        m1_forecast = safe_float(r["Forecast_Qty"])

        profile = _get_sku_profile(hist, item) if not hist.empty else _get_sku_profile(pd.DataFrame(), item)

        for h in range(1, horizon_months + 1):
            forecast_month = month_add(base_month, h - 1)
            qty, source = _forecast_horizon_qty(m1_forecast, h, profile)

            rows.append({
                "ItemCode": item,
                "Horizon": f"M+{h}",
                "Forecast_Month": forecast_month,
                "Forecast_Qty": round(float(qty), 2),
                "Forecast_Source": source,
                "M1_Forecast_Qty": round(float(m1_forecast), 2),
                "Rolling3M_Baseline": round(float(profile["roll3"]), 2),
                "Rolling6M_Baseline": round(float(profile["roll6"]), 2),
                "Median6M_Baseline": round(float(profile["median6"]), 2),
                "Monthly_Trend": round(float(profile["trend"]), 2),
                "Behavior_Type": profile["behavior"],
                "Demand_State": profile["demand_state"],
                "Promo_Profile": profile["promo_profile"],
                "Recurring_Bonus_SKU": profile["recurring_bonus"],
                "Bonus_Cycle_Length": profile["bonus_cycle"],
                "History_Available": int(profile["history_available"]),
            })

    return pd.DataFrame(rows)