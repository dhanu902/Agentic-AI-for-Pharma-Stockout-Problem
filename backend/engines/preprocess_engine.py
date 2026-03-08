 # backend/engines/preprocess_engine.py
import pandas as pd
import numpy as np

ALLOW_FUTURE_VALIDATION = False

def normalize_itemcode(series):
    return (
        series.astype(str)
              .str.strip()
              .str.replace(r"\.0$", "", regex=True)
    )

def assert_required_columns(df: pd.DataFrame, required_cols: list, where=""):
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise KeyError(f"[{where}] Missing required columns: {missing}")

def build_abc_map(df: pd.DataFrame):
    x = pd.to_numeric(df["Clean_Demand"], errors="coerce").fillna(0)
    sku_total = (
        df.assign(Clean_Demand=x)
          .groupby("ItemCode")["Clean_Demand"]
          .sum()
          .sort_values(ascending=False)
    )

    total = float(sku_total.sum())
    if total <= 0 or np.isnan(total):
        return {k: 2 for k in sku_total.index}

    cum_pct = (sku_total.cumsum() / total).clip(0, 1)

    out = {}
    for sku, p in cum_pct.items():
        if p <= 0.7:
            out[sku] = 0
        elif p <= 0.9:
            out[sku] = 1
        else:
            out[sku] = 2
    return out


def build_processed_data_from_raw(raw_df: pd.DataFrame) -> pd.DataFrame:
    df = raw_df.copy()

    # ---- basic cleanup ----
    df["ItemCode"] = (df["ItemCode"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True))
    df = df.sort_values(["ItemCode", "Year", "Month_Number"])

    # ---- Demand Signal Engineering ----
    df["Secondary_Sales_Qty"] = pd.to_numeric(df["Secondary_Sales_Qty"], errors="coerce").fillna(0)
    df["Free_Qty"] = pd.to_numeric(df.get("Free_Qty", 0), errors="coerce").fillna(0)

    df["Bonus_Flag"] = pd.to_numeric(df.get("Bonus_Flag", 0), errors="coerce").fillna(0).astype(int)
    df["Supply_Constraint_Flag"] = pd.to_numeric(df.get("Supply_Constraint_Flag", 0), errors="coerce").fillna(0).astype(int)

    # Start with base demand
    df["Effective_Demand"] = df["Secondary_Sales_Qty"].clip(lower=0)

    # Rolling 3 month avg secondary sales
    df["Rolling3M_SecSales"] = (
        df.groupby("ItemCode")["Secondary_Sales_Qty"]
          .transform(lambda x: x.rolling(3, min_periods=1).mean().shift(1))
    )

    # Supply constraint adjustment
    df["Effective_Demand"] = np.where(
        df["Supply_Constraint_Flag"] == 1,
        np.minimum(df["Effective_Demand"], df["Rolling3M_SecSales"].fillna(df["Effective_Demand"])),
        df["Effective_Demand"]
    )

    # ---- Demand Cleansing Layer ----
    df["Rolling3M_Avg"] = (
        df.groupby("ItemCode")["Effective_Demand"]
          .transform(lambda x: x.rolling(3, min_periods=1).mean().shift(1))
    )

    df["Rolling3M_Std_Eff"] = (
        df.groupby("ItemCode")["Effective_Demand"]
          .transform(lambda x: x.rolling(3, min_periods=1).std().shift(1))
    ).fillna(0)

    df["Z_Score"] = (df["Effective_Demand"] - df["Rolling3M_Avg"]) / (df["Rolling3M_Std_Eff"] + 1)

    if ALLOW_FUTURE_VALIDATION:
        df["Next_Month_Drop"] = (df.groupby("ItemCode")["Effective_Demand"].shift(-1) < 0.7 * df["Effective_Demand"]).fillna(False)
    else:
        df["Next_Month_Drop"] = False

    df["Clean_Demand"] = df["Effective_Demand"]

    bonus_spike_condition = (
        (df["Bonus_Flag"] == 1) &
        (df["Z_Score"] > 2) &
        (df["Next_Month_Drop"])
    )

    df.loc[bonus_spike_condition, "Clean_Demand"] = (
        0.7 * df.loc[bonus_spike_condition, "Effective_Demand"] +
        0.3 * df.loc[bonus_spike_condition, "Rolling3M_Avg"]
    )

    sales_drop_condition = (
        (df["Effective_Demand"] < 0.6 * df.groupby("ItemCode")["Effective_Demand"].shift(1)) &
        (df["Supply_Constraint_Flag"] == 1) &
        (df["Next_Month_Drop"])
    )

    df.loc[sales_drop_condition, "Clean_Demand"] = df["Rolling3M_Avg"]
    df["Clean_Demand"] = pd.to_numeric(df["Clean_Demand"], errors="coerce").fillna(0).clip(lower=0)

    # shock indicators
    df["Bonus_Shock"] = bonus_spike_condition.astype(int)
    df["Supply_Shock"] = sales_drop_condition.astype(int)

    # ---- Feature Engineering ----
    df["Target"] = df.groupby("ItemCode")["Clean_Demand"].shift(-1)

    for lag in [1, 2, 3, 6, 12]:
        df[f"Lag{lag}"] = df.groupby("ItemCode")["Clean_Demand"].shift(lag)

    df["Rolling3M_Mean"] = df.groupby("ItemCode")["Clean_Demand"].transform(lambda x: x.rolling(3).mean().shift(1))
    df["Rolling6M_Mean"] = df.groupby("ItemCode")["Clean_Demand"].transform(lambda x: x.rolling(6).mean().shift(1))

    df["Rolling3M_Std"] = df.groupby("ItemCode")["Clean_Demand"].transform(lambda x: x.rolling(3).std().shift(1))
    if df["Rolling3M_Std"].notna().any():
        df["Rolling3M_Std"] = df["Rolling3M_Std"].clip(upper=df["Rolling3M_Std"].quantile(0.99))

    df["Momentum"] = df["Lag1"] - df["Lag3"]

    df["Month_Number"] = pd.to_numeric(df["Month_Number"], errors="coerce")
    df["Month_Sin"] = np.sin(2 * np.pi * df["Month_Number"] / 12)
    df["Month_Cos"] = np.cos(2 * np.pi * df["Month_Number"] / 12)

    df["Available_Primary_Inventory_Qty"] = pd.to_numeric(df.get("Available_Primary_Inventory_Qty", 0), errors="coerce").fillna(0)
    df["Distributor_Inventory_Qty"] = pd.to_numeric(df.get("Distributor_Inventory_Qty", 0), errors="coerce").fillna(0)
    df["Blocked_Stock_Qty"] = pd.to_numeric(df.get("Blocked_Stock_Qty", 0), errors="coerce").fillna(0)

    df["Inventory_Pressure"] = np.where(
        df["Lag1"].fillna(0) == 0,
        0,
        df["Available_Primary_Inventory_Qty"] / (df["Lag1"].fillna(0) + 1)
    )
    if df["Inventory_Pressure"].notna().any():
        df["Inventory_Pressure"] = df["Inventory_Pressure"].clip(upper=df["Inventory_Pressure"].quantile(0.99))

    # ---- ZERO BEHAVIOR FEATURES ----
    df["Is_Zero"] = (df["Clean_Demand"] == 0).astype(int)
    df["ZeroRate_6M"] = (
        df.groupby("ItemCode")["Is_Zero"].transform(lambda x: x.rolling(6, min_periods=1).mean().shift(1))).fillna(0)

    # ---- NET AVAILABLE STOCK + COVER ----
    df["Total_Primary_Inventory_Qty"] = pd.to_numeric(df.get("Total_Primary_Inventory_Qty", 0), errors="coerce").fillna(0)
    df["Inspection_Stock_Qty"] = pd.to_numeric(df.get("Inspection_Stock_Qty", 0), errors="coerce").fillna(0)

    df["Net_Available_Stock"] = (df["Total_Primary_Inventory_Qty"] - df["Blocked_Stock_Qty"] - df["Inspection_Stock_Qty"]).clip(lower=0)

    df["Stock_Cover_Months"] = np.where(df["Rolling3M_Mean"].fillna(0) == 0,
                                        0,
                                        df["Net_Available_Stock"] / (df["Rolling3M_Mean"] + 1)
                                        )
    
    abc_map_local = build_abc_map(df)
    df["ABC_Class"] = df["ItemCode"].map(abc_map_local).fillna(2).astype(int)

    # ---- Drop NaNs like notebook ----
    required_cols = ["Target", "Lag1", "Lag2", "Lag3", "Lag6", "Lag12", "Rolling3M_Mean", "Rolling6M_Mean", "Rolling3M_Std"]
    df = df.dropna(subset=required_cols).copy()
    df = df[df["Target"] >= 0].copy()

    return df


