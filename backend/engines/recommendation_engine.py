"""
recommendation_engine.py
========================
Leaf engine (no service/route imports). Pure pandas — no I/O, no Flask.

AI PLANNER — final decision layer of the pipeline:

    Forecast Engine -> Risk Engine -> Inventory Projection
                                          |
                                   Recommendation Service
                                   (loads: forecast, inventory, licence)
                                          |
                                   THIS ENGINE
                                   (reasoning + decision only —
                                    NEVER recomputes upstream numbers)
                                          |
                                   Recommendations + Explanations

FACTOR REGISTRY — every out-of-stock factor is a pluggable scorer:

    scorer(inputs: dict) -> pd.DataFrame[ItemCode, score, reasons, available]

    - score      : 0..100, higher = higher stockout risk contribution
    - reasons    : list[str] reason codes
    - available  : bool, False when the underlying data is not collected yet

Live today : cover (NO-RISK stock), forecast_trust, licence (License.xlsx)
Pending    : po_pipeline, grn_reliability (data not collected yet)

Removed by design decision (not stubs — fully out of scope for now):
    budget      — not considered as a stockout factor
    item_expiry — batch-wise expiry (one SKU = many import batches, each
                  with its own mfg/expiry); needs batch-level data

DECISION LOGIC (two layers):
1) Weighted risk score over available factors (renormalised weights).
2) GATING RULES that override the score — they distinguish WHY procurement
   cannot proceed instead of just inflating risk:
     registration expired                     -> STOP_PROCUREMENT (CRITICAL)
     import licence in RISK band + stock need -> RENEW_IMPORT_LICENCE
     cover < 0.5 months                       -> REORDER_URGENT (hard floor)

BUSINESS RULES (optional `rules` input; no data collected yet so usually
None): MOQ / order multiple / max inventory days shape gap_qty into an
orderable suggested_qty. Without rules, suggested_qty = gap_qty.
"""

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Column constants — adjust here if source column names differ
# ---------------------------------------------------------------------------
COL_ITEM = "ItemCode"
COL_MONTH = "Month"              # datetime64, month start
COL_FORECAST = "Forecast"
COL_ACTUAL = "Actual"
COL_TRADE_STOCK = "TradeStock"   # WH trade + DB trade (NoRisk + ShortExp)
COL_NORISK_STOCK = "NoRiskStock" # WH NoRisk + DB NoRisk — COVER USES THIS
COL_CLASS = "ABC_Class"
# License.xlsx (sheet "License")
COL_IMPORT_EXPIRY = "Import_License_Expiry"
COL_REG_EXPIRY = "Registration_Expiry"
# Business rules (no data yet)
COL_MOQ = "MOQ"
COL_ORDER_MULTIPLE = "OrderMultiple"
COL_MAX_INV_DAYS = "MaxInventoryDays"

# ---------------------------------------------------------------------------
# Tunable thresholds
# ---------------------------------------------------------------------------

TARGET_COVER_MONTHS = {"A": 2.0, "B": 1.5, "C": 1.0}
DEFAULT_TARGET_COVER = 1.5
CRITICAL_COVER_MONTHS = 0.5

MAX_DEMAND_UPLIFT = 0.50
BIAS_LOOKBACK_MONTHS = 6
MIN_BIAS_MONTHS = 3

# Licence bands (current date vs expiry date; applies to BOTH the import
# licence and the product registration). Pharma licence renewal is a long
# regulatory process, so the horizon is measured in months, not days:
#   expired            -> score 100 (critical)
#   < 1 year   (RISK)  -> score ramps 80 -> 100 as expiry approaches
#   < 1.5 yrs  (ALERT) -> score 40 (start the renewal paperwork)
#   >= 1.5 yrs (SAFE)  -> score 0, but months/years left still reported
LICENCE_RISK_DAYS = 365
LICENCE_ALERT_DAYS = 548          # ~1.5 years
LICENCE_GATE_DAYS = LICENCE_RISK_DAYS

# 5-factor weight design (sums to 1.0). Renormalised over available factors.
# Live today: cover + forecast_trust + licence = 0.80 covered.
FACTOR_WEIGHTS = {
    "cover": 0.45,
    "forecast_trust": 0.15,
    "licence": 0.20,
    "po_pipeline": 0.15,
    "grn_reliability": 0.05,
}

CONFIDENCE_BANDS = [(0.80, "HIGH"), (0.50, "MEDIUM"), (0.0, "LOW")]

SCORE_CRITICAL = 75
SCORE_REORDER = 50
SCORE_MONITOR = 25


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_scores(items: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({
        COL_ITEM: items,
        "score": np.nan,
        "reasons": [[] for _ in range(len(items))],
        "available": False,
    })


def _target_cover(abc) -> float:
    return TARGET_COVER_MONTHS.get(str(abc).strip().upper(), DEFAULT_TARGET_COVER)


# ---------------------------------------------------------------------------
# Scorers
# ---------------------------------------------------------------------------

def score_forecast_trust(inputs: dict) -> pd.DataFrame:
    """
    Forecast vs actual over trailing BIAS_LOOKBACK_MONTHS closed months.
        bias  = (sum(actual) - sum(forecast)) / sum(forecast)
        wmape = sum(|actual - forecast|) / sum(actual)
    score = clip(100 * (max(bias, 0) + 0.5 * wmape), 0, 100)
    Emits demand_uplift = clip(bias, 0, 0.5) consumed by score_cover.
    """
    hist = inputs["history"]
    items = inputs["items"][COL_ITEM]
    if hist is None or hist.empty:
        return _empty_scores(items)
    if COL_FORECAST not in hist.columns or hist[COL_FORECAST].isna().all():
        return _empty_scores(items)

    hist = hist.dropna(subset=[COL_FORECAST])
    cutoff = hist[COL_MONTH].max() - pd.DateOffset(months=BIAS_LOOKBACK_MONTHS - 1)
    h = hist[hist[COL_MONTH] >= cutoff].copy()
    h["__abs_err__"] = (h[COL_ACTUAL] - h[COL_FORECAST]).abs()
    g = h.groupby(COL_ITEM).agg(
        f_sum=(COL_FORECAST, "sum"),
        a_sum=(COL_ACTUAL, "sum"),
        n_months=(COL_MONTH, "nunique"),
        abs_err=("__abs_err__", "sum"),
    )
    g["bias"] = np.where(g["f_sum"] > 0, (g["a_sum"] - g["f_sum"]) / g["f_sum"], 0.0)
    g["wmape"] = np.where(g["a_sum"] > 0, g["abs_err"] / g["a_sum"], 0.0)
    g["demand_uplift"] = np.where(
        g["n_months"] >= MIN_BIAS_MONTHS,
        np.clip(g["bias"], 0.0, MAX_DEMAND_UPLIFT), 0.0,
    )
    g["score"] = np.clip(100.0 * (np.clip(g["bias"], 0, None) + 0.5 * g["wmape"]), 0, 100)

    def _reasons(r):
        out = []
        if r["bias"] > 0.10 and r["n_months"] >= MIN_BIAS_MONTHS:
            out.append(f"UNDER_FORECAST_{r['bias']:.0%}")
        if r["wmape"] > 0.40:
            out.append("FORECAST_VOLATILE")
        return out

    g["reasons"] = g.apply(_reasons, axis=1)
    g["available"] = True
    out = g.reset_index()[[COL_ITEM, "score", "reasons", "available", "demand_uplift"]]
    return items.to_frame().merge(out, on=COL_ITEM, how="left").fillna(
        {"score": 0.0, "available": True, "demand_uplift": 0.0}
    ).assign(reasons=lambda d: d["reasons"].apply(lambda v: v if isinstance(v, list) else []))


def score_cover(inputs: dict) -> pd.DataFrame:
    """
    Months of cover — computed on NO-RISK STOCK ONLY (WH NoRisk + DB
    NoRisk). Short-expiry stock is deliberately excluded: it may lapse
    before it sells, so it is not dependable cover. Falls back to trade
    stock only when the snapshot carries no NoRisk columns.

        effective_demand = forecast_M+1 * (1 + demand_uplift)
        cover_months     = no_risk_stock / effective_demand
        score            = clip(100 * (1 - cover/target), 0, 100)
        gap_qty          = max(0, target * effective_demand - no_risk_stock)

    Targets: A=2.0, B=1.5, C=1.0 months.
    """
    cur = inputs["current"]
    trust = inputs.get("forecast_trust")

    d = cur.copy()
    stock_col = COL_NORISK_STOCK if COL_NORISK_STOCK in d.columns else COL_TRADE_STOCK
    d["__stock__"] = pd.to_numeric(d[stock_col], errors="coerce").fillna(0.0)

    uplift = (
        trust.set_index(COL_ITEM)["demand_uplift"]
        if trust is not None and "demand_uplift" in trust.columns
        else pd.Series(dtype=float)
    )
    d["demand_uplift"] = d[COL_ITEM].map(uplift).fillna(0.0)
    d["effective_demand"] = d[COL_FORECAST] * (1.0 + d["demand_uplift"])
    d["target_cover"] = d[COL_CLASS].map(_target_cover) if COL_CLASS in d.columns \
        else DEFAULT_TARGET_COVER
    d["cover_months"] = np.where(
        d["effective_demand"] > 0, d["__stock__"] / d["effective_demand"], np.inf,
    )
    d["score"] = np.where(
        np.isfinite(d["cover_months"]),
        np.clip(100.0 * (1.0 - d["cover_months"] / d["target_cover"]), 0, 100),
        0.0,
    )
    d["gap_qty"] = np.clip(
        d["target_cover"] * d["effective_demand"] - d["__stock__"], 0, None
    ).round(0)
    d["no_risk_stock"] = d["__stock__"]

    def _reasons(r):
        out = []
        if not np.isfinite(r["cover_months"]):
            return out
        if r["cover_months"] < CRITICAL_COVER_MONTHS:
            out.append("COVER_CRITICAL")
        elif r["cover_months"] < 1.0:
            out.append("COVER_BELOW_1M")
        elif r["cover_months"] < r["target_cover"]:
            out.append("COVER_BELOW_TARGET")
        if r["demand_uplift"] > 0:
            out.append("DEMAND_UPLIFTED_FOR_BIAS")
        return out

    d["reasons"] = d.apply(_reasons, axis=1)
    d["available"] = True
    return d[[COL_ITEM, "score", "reasons", "available", "cover_months",
              "effective_demand", "gap_qty", "target_cover", "no_risk_stock"]]


def _licence_band_score(days: pd.Series) -> pd.Series:
    """days remaining -> score. Expired 100; RISK band ramps 80->100 as
    expiry approaches; ALERT band 40; SAFE 0. NaN (no date) -> 0."""
    frac = np.clip(days / LICENCE_RISK_DAYS, 0, 1)
    risk_ramp = 80.0 + 20.0 * (1.0 - frac)
    return pd.Series(np.select(
        [days.isna(), days < 0, days < LICENCE_RISK_DAYS, days < LICENCE_ALERT_DAYS],
        [0.0, 100.0, risk_ramp, 40.0],
        default=0.0,
    ), index=days.index)


def _months_left(days) -> int:
    return int(round(days / 30.44))


def score_licence(inputs: dict) -> pd.DataFrame:
    """
    Licence factor — CURRENT DATE vs expiry date (RegLicense /
    ImportLicense in License.xlsx), same bands for both licences:

        expired          -> 100  ..._EXPIRED
        < 1 year   RISK  -> 80..100 (ramp)  ..._RISK_{n}MO
        < 1.5 yrs  ALERT -> 40   ..._ALERT_{n}MO
        >= 1.5 yrs SAFE  -> 0    (no reason; months left still reported
                                  via import_days / reg_days for the UI)

        factor score = max(import_score, registration_score)

    GATING columns consumed by the action layer:
        reg_expired  : registration lapsed -> STOP_PROCUREMENT
        import_gate  : import licence inside RISK band (< 1 year) ->
                       RENEW_IMPORT_LICENCE when stock is also needed
    """
    lic = inputs.get("licence")
    items = inputs["items"][COL_ITEM]
    if lic is None or lic.empty:
        return _empty_scores(items)

    as_of = pd.Timestamp(inputs.get("as_of", pd.Timestamp.today().normalize()))
    d = lic.copy()
    d["import_days"] = (pd.to_datetime(d[COL_IMPORT_EXPIRY], errors="coerce") - as_of).dt.days
    d["reg_days"] = (pd.to_datetime(d[COL_REG_EXPIRY], errors="coerce") - as_of).dt.days

    imp = d["import_days"]
    reg = d["reg_days"]
    d["import_score"] = _licence_band_score(imp)
    d["reg_score"] = _licence_band_score(reg)
    d["score"] = np.maximum(d["import_score"], d["reg_score"])
    d["reg_expired"] = reg.notna() & (reg < 0)
    d["import_gate"] = imp.notna() & (imp < LICENCE_GATE_DAYS)

    def _lic_reasons(prefix, days):
        if pd.isna(days):
            return []
        if days < 0:
            return [f"{prefix}_EXPIRED"]
        if days < LICENCE_RISK_DAYS:
            return [f"{prefix}_RISK_{_months_left(days)}MO"]
        if days < LICENCE_ALERT_DAYS:
            return [f"{prefix}_ALERT_{_months_left(days)}MO"]
        return []

    def _reasons(r):
        return (_lic_reasons("IMPORT_LICENSE", r["import_days"])
                + _lic_reasons("PRODUCT_REGISTRATION", r["reg_days"]))

    d["reasons"] = d.apply(_reasons, axis=1)
    d["available"] = True
    keep = [COL_ITEM, "score", "reasons", "available",
            "import_days", "reg_days", "reg_expired", "import_gate"]
    out = items.to_frame().merge(d[keep], on=COL_ITEM, how="left")
    out["score"] = out["score"].fillna(0.0)
    out["available"] = out["available"].fillna(True)
    out["reg_expired"] = out["reg_expired"].fillna(False)
    out["import_gate"] = out["import_gate"].fillna(False)
    out["reasons"] = out["reasons"].apply(lambda v: v if isinstance(v, list) else [])
    return out


# ---------------------------------------------------------------------------
# Pending factors — data not collected yet
# ---------------------------------------------------------------------------

def score_po_pipeline(inputs: dict) -> pd.DataFrame:
    """Factor: POs — PENDING (no data). Planned: time-phase open PO qty
    into projected cover; PO past confirmed ETA without GRN -> LATE_SUPPLY."""
    return _empty_scores(inputs["items"][COL_ITEM])


def score_grn_reliability(inputs: dict) -> pd.DataFrame:
    """Factor: GRN / supplier reliability — PENDING (no data). Planned:
    realised lead-time stats (mean, p90, late rate) from PO->GRN deltas."""
    return _empty_scores(inputs["items"][COL_ITEM])


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
FACTOR_REGISTRY = {
    "forecast_trust": score_forecast_trust,   # runs first: cover consumes uplift
    "cover": score_cover,
    "licence": score_licence,
    "po_pipeline": score_po_pipeline,
    "grn_reliability": score_grn_reliability,
}


# ---------------------------------------------------------------------------
# Business rules -> suggested order qty (no data yet -> passthrough)
# ---------------------------------------------------------------------------

def _apply_business_rules(agg: pd.DataFrame, rules) -> pd.DataFrame:
    """
    Shape gap_qty into an orderable suggested_qty (MOQ floor, order-multiple
    round-up, max-inventory-days cap on the NO-RISK stock basis). MOQ /
    supplier data is not collected yet, so `rules` is normally None and
    suggested_qty = gap_qty.
    """
    a = agg.copy()
    if rules is None or (hasattr(rules, "empty") and rules.empty):
        a["suggested_qty"] = a["gap_qty"]
        a["rule_reasons"] = [[] for _ in range(len(a))]
        return a

    r = rules.set_index(COL_ITEM)
    moq = a[COL_ITEM].map(r.get(COL_MOQ, pd.Series(dtype=float))).fillna(0.0)
    mult = a[COL_ITEM].map(r.get(COL_ORDER_MULTIPLE, pd.Series(dtype=float))).fillna(1.0).replace(0, 1.0)
    max_days = a[COL_ITEM].map(r.get(COL_MAX_INV_DAYS, pd.Series(dtype=float)))

    qty = a["gap_qty"].fillna(0.0).astype(float)
    sug, rule_reasons = [], []
    for q, m, ml, md, dem, stock in zip(
        qty, moq, mult, max_days, a["effective_demand"].fillna(0.0),
        a.get("no_risk_stock", pd.Series(np.nan, index=a.index)).fillna(np.nan),
    ):
        reasons = []
        if q <= 0:
            sug.append(0.0); rule_reasons.append(reasons); continue
        s = q
        if m > 0 and s < m:
            s = m; reasons.append("MOQ_APPLIED")
        if ml > 1 and s % ml != 0:
            s = np.ceil(s / ml) * ml; reasons.append("ORDER_MULTIPLE_APPLIED")
        if pd.notna(md) and dem > 0:
            stock_now = stock if pd.notna(stock) else 0.0
            cap = max(0.0, dem * (md / 30.0) - stock_now)
            if s > cap:
                s = np.floor(cap / ml) * ml if ml > 1 else cap
                reasons.append("CAPPED_MAX_INVENTORY_DAYS")
                if s < m:
                    reasons.append("RULE_CONFLICT_MOQ_VS_MAX_DAYS")
        sug.append(round(float(s))); rule_reasons.append(reasons)

    a["suggested_qty"] = sug
    a["rule_reasons"] = rule_reasons
    return a


# ---------------------------------------------------------------------------
# Decision layer
# ---------------------------------------------------------------------------

def _confidence(covered_weight: float) -> str:
    for threshold, label in CONFIDENCE_BANDS:
        if covered_weight >= threshold:
            return label
    return "LOW"


def _cover_critical(row) -> bool:
    cm = row.get("cover_months")
    return cm is not None and np.isfinite(cm) and cm < CRITICAL_COVER_MONTHS


def _cover_below_1m(row) -> bool:
    cm = row.get("cover_months")
    return cm is not None and np.isfinite(cm) and cm < 1.0


def _needs_stock(row) -> bool:
    return _cover_critical(row) or (row.get("gap_qty") or 0) > 0


def _action(row) -> str:
    """Gating order encodes WHY procurement cannot proceed:
    1. Registration expired -> STOP_PROCUREMENT (ordering is illegal).
    2. Import licence in RISK band + stock needed -> RENEW_IMPORT_LICENCE
       (the renewal IS the stockout-prevention action, not a PO).
    3. Score ladder — with two guards:
       - ACTIONS REQUIRE A PHYSICAL STOCK NEED (gap_qty > 0 or critical
         cover). A stock-rich SKU whose score comes only from forecast
         volatility is a forecast-quality issue, not a stockout action —
         its score and reasons stay visible in all_items, action = OK.
       - COVER FLOORS: < 0.5 months -> REORDER_URGENT regardless of the
         blend; < 1.0 month -> at least REORDER_REVIEW. Stock that runs
         dry inside a month must never sit in Monitor."""
    if row.get("reg_expired", False):
        return "STOP_PROCUREMENT"
    if row.get("import_gate", False) and _needs_stock(row):
        return "RENEW_IMPORT_LICENCE"
    if _cover_critical(row):
        return "REORDER_URGENT"
    if not _needs_stock(row):
        return "OK"
    if row["risk_score"] >= SCORE_CRITICAL:
        return "REORDER_URGENT"
    if row["risk_score"] >= SCORE_REORDER or _cover_below_1m(row):
        return "REORDER_REVIEW"
    if row["risk_score"] >= SCORE_MONITOR:
        return "MONITOR"
    return "OK"


def _priority(row) -> str:
    if row.get("reg_expired", False):
        return "CRITICAL"
    if (row.get("import_gate", False) and _needs_stock(row)) \
            or _cover_critical(row) \
            or (_needs_stock(row) and row["risk_score"] >= SCORE_CRITICAL):
        return "HIGH"
    if _needs_stock(row) and (
        row["risk_score"] >= SCORE_REORDER or _cover_below_1m(row)
    ):
        return "MEDIUM"
    return "LOW"


def build_recommendations(inputs: dict) -> dict:
    """
    Entry point. inputs:
        items    : DataFrame[ItemCode, ABC_Class, ...]  (universe)
        current  : DataFrame[ItemCode, NoRiskStock, TradeStock,
                             Forecast(M+1), ABC_Class]
        history  : DataFrame[ItemCode, Month, Forecast, Actual] (closed)
        licence  : DataFrame[ItemCode, Import_License_Expiry,
                             Registration_Expiry]              (or None)
        rules    : DataFrame[ItemCode, MOQ, OrderMultiple,
                             MaxInventoryDays]                 (or None)
        as_of    : date for licence day counts (default today)

    Returns dict: recommendations, all_items, factor_coverage.
    """
    items = inputs["items"][[COL_ITEM]].drop_duplicates().reset_index(drop=True)
    inputs = dict(inputs)

    factor_results = {}
    for name, fn in FACTOR_REGISTRY.items():
        res = fn(inputs)
        factor_results[name] = res
        if name == "forecast_trust":
            inputs["forecast_trust"] = res

    # weighted aggregate over available factors
    agg = items.copy()
    agg["weighted_sum"] = 0.0
    agg["weight_covered"] = 0.0
    all_reasons = {code: [] for code in agg[COL_ITEM]}

    availability = {}
    for name, res in factor_results.items():
        w = FACTOR_WEIGHTS[name]
        avail = bool(res["available"].any())
        availability[name] = avail
        if not avail:
            continue
        m = agg[[COL_ITEM]].merge(res[[COL_ITEM, "score", "reasons"]],
                                  on=COL_ITEM, how="left")
        agg["weighted_sum"] += w * m["score"].fillna(0.0).values
        agg["weight_covered"] += w
        for code, reasons in zip(m[COL_ITEM], m["reasons"]):
            if isinstance(reasons, list):
                all_reasons[code].extend(reasons)

    agg["risk_score"] = np.where(
        agg["weight_covered"] > 0,
        (agg["weighted_sum"] / agg["weight_covered"]).round(1), 0.0,
    )
    agg["reasons"] = agg[COL_ITEM].map(all_reasons)

    # detail columns: cover + licence
    cover = factor_results["cover"]
    agg = agg.merge(
        cover[[COL_ITEM, "cover_months", "effective_demand", "gap_qty",
               "target_cover", "no_risk_stock"]],
        on=COL_ITEM, how="left",
    )
    # trade stock kept for display alongside the no-risk basis
    if COL_TRADE_STOCK in inputs["current"].columns:
        agg = agg.merge(
            inputs["current"][[COL_ITEM, COL_TRADE_STOCK]].rename(
                columns={COL_TRADE_STOCK: "trade_stock"}),
            on=COL_ITEM, how="left",
        )
    else:
        agg["trade_stock"] = np.nan

    lic = factor_results["licence"]
    lic_cols = ["import_days", "reg_days", "reg_expired", "import_gate"]
    if set(lic_cols).issubset(lic.columns):
        agg = agg.merge(lic[[COL_ITEM] + lic_cols], on=COL_ITEM, how="left")
        agg["reg_expired"] = agg["reg_expired"].fillna(False)
        agg["import_gate"] = agg["import_gate"].fillna(False)
    else:
        agg["import_days"] = np.nan
        agg["reg_days"] = np.nan
        agg["reg_expired"] = False
        agg["import_gate"] = False

    # business rules -> suggested_qty (passthrough while no rules data)
    agg = _apply_business_rules(agg, inputs.get("rules"))
    agg["reasons"] = [
        base + extra for base, extra in zip(agg["reasons"], agg["rule_reasons"])
    ]
    agg = agg.drop(columns=["rule_reasons"])

    covered = float(agg["weight_covered"].iloc[0]) if len(agg) else 0.0
    agg["confidence"] = _confidence(covered)
    agg["action"] = agg.apply(_action, axis=1)
    agg["priority"] = agg.apply(_priority, axis=1)

    # a STOP_PROCUREMENT item must never carry an order quantity
    agg.loc[agg["action"] == "STOP_PROCUREMENT", "suggested_qty"] = 0.0

    # JSON-safe
    agg["cover_months"] = agg["cover_months"].replace(np.inf, None)
    for c in ("import_days", "reg_days"):
        agg[c] = agg[c].astype(object).where(agg[c].notna(), None)
    agg["trade_stock"] = agg["trade_stock"].astype(object).where(
        agg["trade_stock"].notna(), None)

    prio_rank = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
    agg["__prio__"] = agg["priority"].map(prio_rank)
    recs = (
        agg[agg["action"] != "OK"]
        .sort_values(["__prio__", "risk_score"], ascending=[False, False])
        .reset_index(drop=True)
    )

    cols = [COL_ITEM, "risk_score", "action", "priority", "confidence",
            "cover_months", "effective_demand", "gap_qty", "suggested_qty",
            "target_cover", "no_risk_stock", "trade_stock",
            "import_days", "reg_days", "reasons"]
    return {
        "recommendations": recs[cols].to_dict(orient="records"),
        "all_items": agg[cols].to_dict(orient="records"),
        "factor_coverage": {
            "availability": availability,
            "weight_covered": round(covered, 2),
            "confidence": _confidence(covered),
        },
    }