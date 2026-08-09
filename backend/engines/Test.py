# Test.py — SKU coverage audit across the whole pipeline
# Answers 11 business questions about which SKUs flow through which path.
# Read-only: only reads the input excels and generated CSVs, changes nothing.
#
# Run from anywhere inside backend/ (same convention as before):
#   python3 Test.py

import os
import pandas as pd

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
PROJECT_DIR = os.path.dirname(BACKEND_DIR)                                 # project root

RAW_DIR    = os.path.join(PROJECT_DIR, "data")
OUT_DIR    = os.path.join(BACKEND_DIR, "data", "outputs")
PROC_DIR   = os.path.join(BACKEND_DIR, "data", "processed")

SKU_MASTER_CSV   = os.path.join(OUT_DIR, "sku_master_full.csv")
FOCUS_XLSX       = os.path.join(RAW_DIR, "Master Data", "FocusItemCodes.xlsx")
PROCESSED_CSV    = os.path.join(PROC_DIR, "processed_data_actual.csv")
FORECAST_LATEST  = os.path.join(OUT_DIR, "forecast_latest.csv")
TREND_LATEST     = os.path.join(OUT_DIR, "forecast_trend_latest.csv")
COMBINED_CSV     = os.path.join(OUT_DIR, "forecast_all_skus_latest.csv")
MASTER_MAPPED    = os.path.join(OUT_DIR, "forecast_master_mapped.csv")
RISK_LATEST      = os.path.join(OUT_DIR, "risk_latest.csv")
AGENCY_PERF      = os.path.join(OUT_DIR, "agency_performance_latest.csv")
LICENSE_XLSX     = os.path.join(RAW_DIR, "License.xlsx")
FACT_XLSX        = os.path.join(RAW_DIR, "fact_monthly_closed.xlsx")


def norm(s):
    """ItemCode -> canonical string (int-string for numeric, as-is for SYN-)."""
    s = pd.Series(s).astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    return s


def load_csv(path, name):
    if not os.path.exists(path):
        print(f"   !! {name} not found: {path}")
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False, dtype=str)


def section(n, title):
    print(f"\n{'='*72}\n{n}) {title}\n{'-'*72}")


# ══════════════════════════════════════════════════════════════════
# 1) + 2) MASTER SKU LIST
# ══════════════════════════════════════════════════════════════════
section(1, "Total SKUs in master SKU list (sku_master_full.csv, from Budget.xlsx)")
master = load_csv(SKU_MASTER_CSV, "sku_master_full.csv")
master_codes, real_codes, syn_codes = set(), set(), set()
if not master.empty:
    master["ProductCode"] = norm(master["ProductCode"])
    master_codes = set(master["ProductCode"])
    is_syn = pd.to_numeric(master["Is_Synthetic_Code"], errors="coerce").fillna(0).astype(int)
    real_codes = set(master.loc[is_syn == 0, "ProductCode"])
    syn_codes  = set(master.loc[is_syn == 1, "ProductCode"])
    print(f"   master rows                 : {len(master)}")
    print(f"   unique ProductCodes         : {len(master_codes)}")

section(2, "How many have a STANDARD (real) product code")
print(f"   with real ItemCode          : {len(real_codes)}")
print(f"   synthetic SYN- code (no std): {len(syn_codes)}")
if syn_codes:
    print(f"   synthetic examples          : {sorted(syn_codes)[:5]}")

# ══════════════════════════════════════════════════════════════════
# 3) + 4) FOCUS LIST
# ══════════════════════════════════════════════════════════════════
section(3, "SKUs in FocusItemCodes.xlsx")
focus_codes = set()
if os.path.exists(FOCUS_XLSX):
    fdf = pd.read_excel(FOCUS_XLSX)
    codes = pd.to_numeric(fdf["Code"], errors="coerce").dropna().astype(int).astype(str)
    focus_codes = set(codes)
    print(f"   focus rows                  : {len(fdf)}")
    print(f"   unique numeric focus codes  : {len(focus_codes)}")
else:
    print(f"   !! not found: {FOCUS_XLSX}")

section(4, "SKUs in BOTH master AND focus list (the model universe)")
focus_in_master  = focus_codes & master_codes
focus_not_master = focus_codes - master_codes
print(f"   focus ∩ master              : {len(focus_in_master)}  -> model path")
print(f"   focus NOT in master (no budget -> NOT forecast): {len(focus_not_master)}")
if focus_not_master:
    print(f"   dropped focus codes         : {sorted(focus_not_master)[:10]}")

# ══════════════════════════════════════════════════════════════════
# 5) MODEL PATH: preprocess -> champion models -> forecast_latest
# ══════════════════════════════════════════════════════════════════
section(5, "SKUs going through PREPROCESS + MODELS (forecast_latest.csv)")
proc = load_csv(PROCESSED_CSV, "processed_data_actual.csv")
proc_codes = set(norm(proc["ItemCode"])) if not proc.empty else set()
fl = load_csv(FORECAST_LATEST, "forecast_latest.csv")
model_codes = set(norm(fl["ItemCode"])) if not fl.empty else set()
print(f"   preprocessed SKUs           : {len(proc_codes)}  (focus ∩ master WITH fact rows)")
print(f"   model-forecast SKUs         : {len(model_codes)}")
no_fact = focus_in_master - proc_codes
print(f"   focus∩master with NO fact rows (fall to trend path): {len(no_fact)}")
if no_fact:
    print(f"   codes: {sorted(no_fact)[:10]}")

# ══════════════════════════════════════════════════════════════════
# 6) LEFTOVER PATH: master − focus -> trend baseline / budget-only
# ══════════════════════════════════════════════════════════════════
section(6, "SKUs NOT in preprocess/models — leftover path (forecast_trend_latest.csv)")
leftover_codes = master_codes - model_codes
print(f"   master − model              : {len(leftover_codes)}")
trend = load_csv(TREND_LATEST, "forecast_trend_latest.csv")
if not trend.empty:
    trend["ItemCode"] = norm(trend["ItemCode"])
    src = trend["Forecast_Source"].value_counts().to_dict()
    print(f"   leftover rows produced      : {len(trend)}")
    print(f"   by source                   : {src}")
    print("   how: NO preprocessing/model — TREND_BASELINE = 0.7·L3M + 0.3·L6M")
    print("        rolling average of raw fact sales; BUDGET_ONLY = no forecast,")
    print("        page shows budget number only.")

# ══════════════════════════════════════════════════════════════════
# 7) NO STANDARD CODE / NO SALES HISTORY
# ══════════════════════════════════════════════════════════════════
section(7, "SKUs with NO standard code and SKUs with NO sales history")
if not trend.empty and "Routing_Reason" in trend.columns:
    rr = trend["Routing_Reason"].value_counts().to_dict()
    print(f"   routing reasons             : {rr}")
    no_code   = trend[trend["Routing_Reason"] == "NO_STANDARD_CODE"]
    no_hist   = trend[trend["Routing_Reason"] == "NO_SALES_MAPPING"]
    print(f"   NO_STANDARD_CODE (SYN-)     : {len(no_code)}  -> BUDGET_ONLY (no fact mapping possible)")
    print(f"   NO_SALES_MAPPING (no hist)  : {len(no_hist)}  -> BUDGET_ONLY (nothing to trend)")
    print("   both are re-evaluated every run — they move to trend/model")
    print("   automatically once they get a code and/or start selling.")

# ══════════════════════════════════════════════════════════════════
# 8) FINAL FORECAST OUTPUT
# ══════════════════════════════════════════════════════════════════
section(8, "FINAL output: forecast_master_mapped.csv (full master universe)")
mm = load_csv(MASTER_MAPPED, "forecast_master_mapped.csv")
if not mm.empty:
    mm["ProductCode"] = norm(mm["ProductCode"])
    print(f"   final rows                  : {len(mm)}")
    print(f"   by Forecast_Source          : {mm['Forecast_Source'].fillna('NO_FORECAST').value_counts().to_dict()}")
    missing = master_codes - set(mm["ProductCode"])
    extra   = set(mm["ProductCode"]) - master_codes
    print(f"   master codes MISSING        : {len(missing)}  {sorted(missing)[:10] if missing else ''}")
    print(f"   rows outside master (unbudgeted): {len(extra)}")
    print(f"   FULL MASTER COVERED         : {len(missing) == 0}")

# ══════════════════════════════════════════════════════════════════
# 9) INVENTORY PROJECTION (risk_latest.csv)
# ══════════════════════════════════════════════════════════════════
section(9, "Inventory projection coverage (risk_latest.csv)")
risk = load_csv(RISK_LATEST, "risk_latest.csv")
if not risk.empty:
    risk["ItemCode"] = norm(risk["ItemCode"])
    lvl = risk["Risk_Level"].value_counts().to_dict()
    gap_levels = {"NOT_TRACKED", "NO_DATA", "NO_INVENTORY_DATA", "NO_FORECAST_DATA"}
    assessed = risk[~risk["Risk_Level"].isin(gap_levels)]
    gaps     = risk[risk["Risk_Level"].isin(gap_levels)]
    print(f"   total rows (master scope)   : {len(risk)}")
    print(f"   PROPERLY ASSESSED           : {len(assessed)}")
    print(f"   could NOT be assessed       : {len(gaps)}")
    print(f"     breakdown                 : { {k: v for k, v in lvl.items() if k in gap_levels} }")
    print("     NOT_TRACKED       = no standard code (SYN-) — stock can't be tracked")
    print("     NO_INVENTORY_DATA = real code but no stock record this month")
    print("     NO_FORECAST_DATA  = no forecast qty (demand assumed 0)")
    print("     NO_DATA           = neither inventory nor forecast")

# ══════════════════════════════════════════════════════════════════
# 10) INSIGHTS PAGE COVERAGE
# ══════════════════════════════════════════════════════════════════
section(10, "SKUs contributing to the Insights page")
perf = load_csv(AGENCY_PERF, "agency_performance_latest.csv")
if not perf.empty and "ItemCode" in perf.columns:
    perf["ItemCode"] = norm(perf["ItemCode"])
    print(f"   performance-table SKUs      : {perf['ItemCode'].nunique()}  (focus SKUs with sales history)")
print(f"   budget-analysis universe    : {len(master_codes)}  (FULL master SKU list)")
print("   -> KPI cards (budget/loss) aggregate over the full master universe;")
print("      the per-SKU performance table covers the focus/sales subset.")

# ══════════════════════════════════════════════════════════════════
# 11) LICENSE MAPPING
# ══════════════════════════════════════════════════════════════════
section(11, "SKUs with NO license mapped (License.xlsx vs master real codes)")
if os.path.exists(LICENSE_XLSX):
    lic = pd.read_excel(LICENSE_XLSX, sheet_name="License")
    lic.columns = lic.columns.astype(str).str.strip()
    code_col = next((c for c in ["Code", "ItemCode", "PID"] if c in lic.columns), None)
    if code_col:
        lic_codes = set(
            pd.to_numeric(lic[code_col], errors="coerce").dropna().astype(int).astype(str)
        )
        mapped   = real_codes & lic_codes
        unmapped = real_codes - lic_codes
        print(f"   license rows                : {len(lic)}  (unique codes: {len(lic_codes)})")
        print(f"   master real-coded SKUs      : {len(real_codes)}")
        print(f"   WITH license mapped         : {len(mapped)}")
        print(f"   WITHOUT license mapped      : {len(unmapped)}")
        print(f"   synthetic (can never map)   : {len(syn_codes)}")
        if unmapped:
            print(f"   unmapped examples           : {sorted(unmapped)[:10]}")
    else:
        print(f"   !! no code column found. Columns: {list(lic.columns)}")
else:
    print(f"   !! License.xlsx not found: {LICENSE_XLSX}")

# ══════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════
print(f"\n{'='*72}\nPIPELINE SUMMARY\n{'-'*72}")
print(f"   master SKUs                       : {len(master_codes)}")
print(f"   ├─ model path (focus∩master+fact) : {len(model_codes)}")
print(f"   ├─ trend path (history, no model) : "
      f"{len(trend[trend['Forecast_Source']=='TREND_BASELINE']) if not trend.empty else '?'}")
print(f"   └─ budget-only (no code/history)  : "
      f"{len(trend[trend['Forecast_Source']=='BUDGET_ONLY']) if not trend.empty else '?'}")
if not mm.empty:
    total_out = len(mm)
    print(f"   final combined output             : {total_out} "
          f"({'COMPLETE' if master_codes <= set(mm['ProductCode']) else 'INCOMPLETE!'})")