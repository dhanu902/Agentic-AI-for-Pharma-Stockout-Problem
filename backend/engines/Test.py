import os
import pandas as pd

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
PROJECT_DIR = os.path.dirname(BACKEND_DIR)                                 # project root
path = os.path.join(PROJECT_DIR, "data", "Master Data", "Budget.xlsx")

df = pd.read_excel(path, sheet_name="All Budget 26 27 FY")
df.columns = df.columns.astype(str).str.strip()
col = next(c for c in ["ItemCode", "PID", "Code"] if c in df.columns)

numeric = pd.to_numeric(df[col], errors="coerce")
print("total rows:              ", len(df))
print("blank code rows:         ", int(df[col].isna().sum()))
print("non-numeric codes:       ", int((numeric.isna() & df[col].notna()).sum()))
print(df.loc[numeric.isna() & df[col].notna(), col].unique()[:30])
u = numeric.dropna().astype(int)
print("unique numeric SKUs:     ", u.nunique())
print("duplicated SKUs:         ", u[u.duplicated()].nunique())