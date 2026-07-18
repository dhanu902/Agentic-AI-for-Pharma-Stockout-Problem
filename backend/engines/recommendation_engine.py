"""
recommendation_engine.py
========================
Leaf engine (no service/route imports). Pure pandas — no I/O, no Flask.

AI PLANNER — final decision layer of the pipeline:

    Forecast Engine -> Risk Engine -> Inventory Projection
                                          |
                                   Recommendation Service
                                   (loads: forecast, inventory, budget,
                                    licence, business rules)
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

Live today : cover, forecast_trust, budget, licence (License.xlsx)
Stubs      : po_pipeline, grn_reliability, item_expiry

DECISION LOGIC (two layers):
1) Weighted risk score over available factors (renormalised weights).
2) GATING RULES that override the score — they distinguish WHY procurement
   cannot proceed instead of just inflating risk:
     registration expired                     -> STOP_PROCUREMENT (CRITICAL)
     import licence lapsing + stock needed    -> RENEW_IMPORT_LICENCE
     replenishment unfundable                 -> BUDGET_BLOCKED
     cover < 0.5 months                       -> REORDER_URGENT (hard floor)

BUSINESS RULES (optional `rules` input): MOQ, order multiple, max inventory
days shape gap_qty into an orderable suggested_qty.
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
COL_TRADE_STOCK = "TradeStock"   # WH available primary + DB distributor
COL_CLASS = "ABC_Class"
# License.xlsx (sheet "License")
COL_IMPORT_EXPIRY = "Import_License_Expiry"
COL_REG_EXPIRY = "Registration_Expiry"
# Business rules
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

BUDGET_OVERRUN_ALERT = 0.10

# Licence scoring bands (days remaining -> score).
# Import licence: expired or <30d = 100, 30-60 = 60, 60-90 = 30, >90 = 0.
# Registration:   expired = 100, <30d = 80, 30-60 = 40, healthy = 0.
IMPORT_BANDS = [(30, 100.0), (60, 60.0), (90, 30.0)]
REG_BANDS = [(0, 100.0), (30, 80.0), (60, 40.0)]

# Gating: import licence expiring within this window while stock is needed
# -> renewing the licence IS the stockout-prevention action, not a PO.
LICENCE_GATE_DAYS = 30

# 7-factor weight design (sums to 1.0). Renormalised over available factors.
FACTOR_WEIGHTS = {
    "cover": 0.35,
    "forecast_trust": 0.10,
    "budget": 0.15,
    "licence": 0.10,
    "po_pipeline": 0.15,
    "grn_reliability": 0.05,
    "item_expiry": 0.10,
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
    # history may carry actuals only (no recoverable past forecasts yet):
    # bias/wmape would be meaningless zeros -> report unavailable instead
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
    Months of cover — consumes upstream numbers, never recomputes them.
        effective_demand = forecast_M+1 * (1 + demand_uplift)
        cover_months     = trade_stock / effective_demand
        score            = clip(100 * (1 - cover/target), 0, 100)
        gap_qty          = max(0, target * effective_demand - trade_stock)
    Targets: A=2.0, B=1.5, C=1.0 months.
    """
    cur = inputs["current"]
    trust = inputs.get("forecast_trust")

    d = cur.copy()
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
        d["effective_demand"] > 0, d[COL_TRADE_STOCK] / d["effective_demand"], np.inf,
    )
    d["score"] = np.where(
        np.isfinite(d["cover_months"]),
        np.clip(100.0 * (1.0 - d["cover_months"] / d["target_cover"]), 0, 100),
        0.0,
    )
    d["gap_qty"] = np.clip(
        d["target_cover"] * d["effective_demand"] - d[COL_TRADE_STOCK], 0, None
    ).round(0)

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
    return d[[COL_ITEM, "score", "reasons", "available",
              "cover_months", "effective_demand", "gap_qty", "target_cover"]]


def score_budget(inputs: dict) -> pd.DataFrame:
    """
    Budget: overrun ramp (10% over -> 20 pts, 50%+ -> 100) plus a
    budget_blocked flag when est_gap_value > remaining FY budget.
    """
    b = inputs["budget"]
    items = inputs["items"][COL_ITEM]
    if b is None or b.empty:
        return _empty_scores(items)

    d = b.copy()
    d["overrun"] = np.where(
        d["ytd_budget_qty"] > 0,
        (d["ytd_actual_qty"] - d["ytd_budget_qty"]) / d["ytd_budget_qty"], 0.0,
    )
    d["score"] = np.clip(100.0 * np.clip(d["overrun"], 0, None) / 0.5, 0, 100)

    has_val = {"fy_budget_val", "ytd_actual_val", "est_gap_value"}.issubset(d.columns)
    if has_val:
        d["remaining_budget_val"] = d["fy_budget_val"] - d["ytd_actual_val"]
        d["budget_blocked"] = d["est_gap_value"] > d["remaining_budget_val"]
    else:
        d["budget_blocked"] = False

    def _reasons(r):
        out = []
        if r["overrun"] > BUDGET_OVERRUN_ALERT:
            out.append(f"BUDGET_OVERRUN_{r['overrun']:.0%}")
        if r["budget_blocked"]:
            out.append("BUDGET_BLOCKED")
        return out

    d["reasons"] = d.apply(_reasons, axis=1)
    d["available"] = True
    keep = [COL_ITEM, "score", "reasons", "available", "budget_blocked"]
    out = items.to_frame().merge(d[keep], on=COL_ITEM, how="left")
    out["score"] = out["score"].fillna(0.0)
    out["available"] = out["available"].fillna(True)
    out["budget_blocked"] = out["budget_blocked"].fillna(False)
    out["reasons"] = out["reasons"].apply(lambda v: v if isinstance(v, list) else [])
    return out


def score_licence(inputs: dict) -> pd.DataFrame:
    """
    Licence factor (import licence + product registration from License.xlsx).

    Scoring bands on days remaining (as of `as_of`, default today):
        Import  : expired/<30d = 100 | 30-60 = 60 | 60-90 = 30 | >90 = 0
        Registr.: expired = 100 | <30d = 80 | 30-60 = 40 | healthy = 0
        factor score = max(import_score, registration_score)

    Also emits GATING columns consumed by the action layer:
        reg_expired  : registration lapsed -> STOP_PROCUREMENT
        import_gate  : import licence expires < LICENCE_GATE_DAYS ->
                       RENEW_IMPORT_LICENCE when stock is also needed
    Gating is separate from the score on purpose: an expiring licence must
    CHANGE THE ACTION, not just nudge a number.
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
    d["import_score"] = np.select(
        [imp.isna(), imp < IMPORT_BANDS[0][0], imp <= IMPORT_BANDS[1][0], imp <= IMPORT_BANDS[2][0]],
        [0.0, IMPORT_BANDS[0][1], IMPORT_BANDS[1][1], IMPORT_BANDS[2][1]],
        default=0.0,
    )
    reg = d["reg_days"]
    d["reg_score"] = np.select(
        [reg.isna(), reg < REG_BANDS[0][0], reg < REG_BANDS[1][0], reg <= REG_BANDS[2][0]],
        [0.0, REG_BANDS[0][1], REG_BANDS[1][1], REG_BANDS[2][1]],
        default=0.0,
    )
    d["score"] = np.maximum(d["import_score"], d["reg_score"])
    d["reg_expired"] = reg.notna() & (reg < 0)
    d["import_gate"] = imp.notna() & (imp < LICENCE_GATE_DAYS)

    def _reasons(r):
        out = []
        if pd.notna(r["import_days"]):
            if r["import_days"] < 0:
                out.append("IMPORT_LICENSE_EXPIRED")
            elif r["import_days"] <= 90 and r["import_score"] > 0:
                out.append(f"IMPORT_LICENSE_EXPIRING_{int(r['import_days'])}D")
        if pd.notna(r["reg_days"]):
            if r["reg_days"] < 0:
                out.append("PRODUCT_REGISTRATION_EXPIRED")
            elif r["reg_score"] > 0:
                out.append(f"PRODUCT_REGISTRATION_EXPIRING_{int(r['reg_days'])}D")
        return out

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
# Stubs — implement when data collection starts
# ---------------------------------------------------------------------------

def score_po_pipeline(inputs: dict) -> pd.DataFrame:
    """Factor: POs — STUB. Planned: time-phase open PO qty into projected
    cover; PO past confirmed ETA without GRN -> LATE_SUPPLY."""
    return _empty_scores(inputs["items"][COL_ITEM])


def score_grn_reliability(inputs: dict) -> pd.DataFrame:
    """Factor: GRNs — STUB. Planned: realised lead-time stats
    (mean, p90, late rate) per vendor/SKU from PO->GRN deltas."""
    return _empty_scores(inputs["items"][COL_ITEM])


def score_item_expiry(inputs: dict) -> pd.DataFrame:
    """Factor: batch expiry — STUB. Planned: FEFO effective-available;
    stock expiring before it can sell is excluded from cover."""
    return _empty_scores(inputs["items"][COL_ITEM])


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
FACTOR_REGISTRY = {
    "forecast_trust": score_forecast_trust,   # runs first: cover consumes uplift
    "cover": score_cover,
    "budget": score_budget,
    "licence": score_licence,
    "po_pipeline": score_po_pipeline,
    "grn_reliability": score_grn_reliability,
    "item_expiry": score_item_expiry,
}


# ---------------------------------------------------------------------------
# Business rules -> suggested order qty
# ---------------------------------------------------------------------------

def _apply_business_rules(agg: pd.DataFrame, rules) -> pd.DataFrame:
    """
    Shape gap_qty into an orderable suggested_qty:
        1. floor at MOQ                      (reason MOQ_APPLIED)
        2. round UP to order multiple        (reason ORDER_MULTIPLE_APPLIED)
        3. cap so stock after order <= effective_demand * max_inv_days/30
           (reason CAPPED_MAX_INVENTORY_DAYS; guards expiry exposure)
    Cap conflicts (cap < MOQ) emit RULE_CONFLICT_MOQ_VS_MAX_DAYS and keep
    the cap — over-stocking pharma past max days is the worse failure.
    Without a rules table, suggested_qty = gap_qty.
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
        a.get("trade_stock", pd.Series(np.nan, index=a.index)).fillna(np.nan),
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


def _needs_stock(row) -> bool:
    return _cover_critical(row) or (row.get("gap_qty") or 0) > 0


def _action(row) -> str:
    """Gating order matters — it encodes WHY procurement cannot proceed:
    1. Registration expired: selling/importing is illegal. STOP_PROCUREMENT,
       escalate regulatory — ordering stock now is the worst possible move.
    2. Import licence lapsing while stock is needed: the PO would arrive
       against a dead licence. The stockout-prevention action IS the
       licence renewal, so RENEW_IMPORT_LICENCE, not 'place PO'.
    3. Unfundable replenishment: BUDGET_BLOCKED.
    4. Then the score ladder with the cover<0.5 hard floor."""
    if row.get("reg_expired", False):
        return "STOP_PROCUREMENT"
    if row.get("import_gate", False) and _needs_stock(row):
        return "RENEW_IMPORT_LICENCE"
    if row.get("budget_blocked", False) and (
        row["risk_score"] >= SCORE_MONITOR or _cover_critical(row)
    ):
        return "BUDGET_BLOCKED"
    if row["risk_score"] >= SCORE_CRITICAL or _cover_critical(row):
        return "REORDER_URGENT"
    if row["risk_score"] >= SCORE_REORDER:
        return "REORDER_REVIEW"
    if row["risk_score"] >= SCORE_MONITOR:
        return "MONITOR"
    return "OK"


def _priority(row) -> str:
    if row.get("reg_expired", False):
        return "CRITICAL"
    if (row.get("import_gate", False) and _needs_stock(row)) \
            or row["risk_score"] >= SCORE_CRITICAL or _cover_critical(row):
        return "HIGH"
    if row["risk_score"] >= SCORE_REORDER:
        return "MEDIUM"
    # an unfundable replenishment need is never a LOW-priority situation
    if row.get("budget_blocked", False) and _needs_stock(row):
        return "MEDIUM"
    return "LOW"


def build_recommendations(inputs: dict) -> dict:
    """
    Entry point. inputs:
        items    : DataFrame[ItemCode, ABC_Class, ...]  (universe)
        current  : DataFrame[ItemCode, TradeStock, Forecast(M+1), ABC_Class]
        history  : DataFrame[ItemCode, Month, Forecast, Actual] (closed)
        budget   : DataFrame[ItemCode, ytd_budget_qty, ytd_actual_qty, ...]
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

    # detail columns: cover, budget, licence
    cover = factor_results["cover"]
    agg = agg.merge(
        cover[[COL_ITEM, "cover_months", "effective_demand", "gap_qty", "target_cover"]],
        on=COL_ITEM, how="left",
    )
    # trade stock for the max-days cap
    agg = agg.merge(
        inputs["current"][[COL_ITEM, COL_TRADE_STOCK]].rename(
            columns={COL_TRADE_STOCK: "trade_stock"}),
        on=COL_ITEM, how="left",
    )

    budget = factor_results["budget"]
    if "budget_blocked" in budget.columns:
        agg = agg.merge(budget[[COL_ITEM, "budget_blocked"]], on=COL_ITEM, how="left")
        agg["budget_blocked"] = agg["budget_blocked"].fillna(False)
    else:
        agg["budget_blocked"] = False

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

    # business rules -> suggested_qty
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

    prio_rank = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
    agg["__prio__"] = agg["priority"].map(prio_rank)
    recs = (
        agg[agg["action"] != "OK"]
        .sort_values(["__prio__", "risk_score"], ascending=[False, False])
        .reset_index(drop=True)
    )

    cols = [COL_ITEM, "risk_score", "action", "priority", "confidence",
            "cover_months", "effective_demand", "gap_qty", "suggested_qty",
            "target_cover", "trade_stock", "budget_blocked",
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