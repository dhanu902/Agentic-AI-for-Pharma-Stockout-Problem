# backend/services/artifact_service.py ---> 🧠 Model loader

import os
import json
import joblib
import pandas as pd
import torch
from dataclasses import dataclass
from engines.demand_forecast_engine import LongGRUResidualForecaster


@dataclass
class LongGRUScalerBundle:
    seq_scaler: object
    static_scaler: object

class ArtifactService:
    def __init__(self):
        self.BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # backend/
        self.MODELS_DIR = os.path.join(self.BASE_DIR, "models")

        self.champion_long_map_df = None
        self.champion_medium_map_df = None

        self.long_artifacts = {}
        self.medium_artifacts = {}

        self.short_rule_artifacts = None
        self.short_promo_artifacts = None
        self.short_normal_artifacts = None

        self.gru_bundle = None
        self.gru_scalers = None

    # ============================================================
    # BASIC HELPERS
    # ============================================================
    def _safe_load_pickle(self, path):
        if not os.path.exists(path):
            print(f"[WARN] Missing file: {path}")
            return None
        return joblib.load(path)

    def _normalize_itemcode(self, v):
        return str(v).strip().replace(".0", "")

    def _load_first_existing(self, candidates):
        for path in candidates:
            if os.path.exists(path):
                return self._safe_load_pickle(path)
        for path in candidates:
            print(f"[WARN] Missing file: {path}")
        return None

    # ============================================================
    # LOAD ALL
    # ============================================================
    def load_all(self):
        registry_dir = os.path.join(self.MODELS_DIR, "registry")
        long_dir = os.path.join(self.MODELS_DIR, "long")
        medium_dir = os.path.join(self.MODELS_DIR, "medium")
        short_dir = os.path.join(self.MODELS_DIR, "short")

        # ---------------------------
        # Champion maps
        # ---------------------------
        self.champion_long_map_df = self._load_first_existing([
            os.path.join(registry_dir, "champion_long.pkl"),
        ])

        self.champion_medium_map_df = self._load_first_existing([
            os.path.join(registry_dir, "champion_medium.pkl"),
        ])

        if isinstance(self.champion_long_map_df, pd.DataFrame) and "ItemCode" in self.champion_long_map_df.columns:
            self.champion_long_map_df["ItemCode"] = (
                self.champion_long_map_df["ItemCode"]
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
            )

        if isinstance(self.champion_medium_map_df, pd.DataFrame) and "ItemCode" in self.champion_medium_map_df.columns:
            self.champion_medium_map_df["ItemCode"] = (
                self.champion_medium_map_df["ItemCode"]
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
            )

        # ---------------------------
        # LONG artifacts
        # ---------------------------
        self.long_artifacts = {}

        long_candidates = {
            "XGBOOST": [os.path.join(long_dir, "xgb_long.pkl")],
            "CATBOOST": [os.path.join(long_dir, "catboost_long.pkl")],
            "LIGHTGBM": [os.path.join(long_dir, "lgbm_long.pkl")],
        }

        for model_name, paths in long_candidates.items():
            art = self._load_first_existing(paths)
            if art is not None:
                self.long_artifacts[model_name] = art

        # ---------------------------
        # MEDIUM artifacts
        # ---------------------------
        self.medium_artifacts = {}

        medium_candidates = {
            ("PROMO_HEAVY", "XGBOOST"): [
                os.path.join(medium_dir, "promo_heavy", "xgboost.pkl"),
            ],
            ("PROMO_HEAVY", "CATBOOST"): [
                os.path.join(medium_dir, "promo_heavy", "catboost.pkl"),
            ],
            ("STABLE", "RANDOM_FOREST"): [
                os.path.join(medium_dir, "stable", "random_forest.pkl"),
            ],
        }

        for key, paths in medium_candidates.items():
            art = self._load_first_existing(paths)
            if art is not None:
                self.medium_artifacts[key] = art

        # ---------------------------
        # SHORT artifacts
        # ---------------------------
        self.short_rule_artifacts = self._load_first_existing([
            os.path.join(short_dir, "base_rule.pkl"),
        ])

        self.short_promo_artifacts = self._load_first_existing([
            os.path.join(short_dir, "promo_rule.pkl"),
        ])

        self.short_normal_artifacts = self._load_first_existing([
            os.path.join(short_dir, "normal_rule.pkl"),
        ])

        # ---------------------------
        # GRU artifacts
        # ---------------------------
        gru_dir_candidates = [
            os.path.join(long_dir, "gru"),
            os.path.join(long_dir, "gru_long_deploy_artifacts"),
        ]

        gru_dir = None
        for p in gru_dir_candidates:
            if os.path.isdir(p):
                gru_dir = p
                break

        self.gru_bundle = None
        self.gru_scalers = None

        if gru_dir is not None:
            bundle_candidates = [
                os.path.join(gru_dir, "gru_bundle.pkl"),
                os.path.join(gru_dir, "bundle.pkl"),
            ]
            scaler_candidates = [
                os.path.join(gru_dir, "gru_scalers.pkl"),
                os.path.join(gru_dir, "scalers.pkl"),
            ]

            self.gru_bundle = self._load_first_existing(bundle_candidates)
            self.gru_scalers = self._load_first_existing(scaler_candidates)

    # ============================================================
    # SUMMARY
    # ============================================================
    def summary(self):
        return {
            "champion_long_loaded": self.champion_long_map_df is not None,
            "champion_medium_loaded": self.champion_medium_map_df is not None,
            "long_models_loaded": sorted(list(self.long_artifacts.keys())),
            "medium_models_loaded": [f"{k[0]}::{k[1]}" for k in sorted(self.medium_artifacts.keys())],
            "short_rules_loaded": [
                name for name, obj in [
                    ("BASE_RULE", self.short_rule_artifacts),
                    ("PROMO_RULE", self.short_promo_artifacts),
                    ("NORMAL_RULE", self.short_normal_artifacts),
                ] if obj is not None
            ],
            "gru_long_loaded": self.gru_bundle is not None and self.gru_scalers is not None,
            "device": "mps",
        }

    # ============================================================
    # ROUTING HELPERS
    # ============================================================
    def get_long_routing_row(self, item_code):
        if self.champion_long_map_df is None:
            return None
        item_code = self._normalize_itemcode(item_code)
        row = self.champion_long_map_df[self.champion_long_map_df["ItemCode"].astype(str) == item_code]
        if row.empty:
            return None
        return row.iloc[0].to_dict()

    def get_medium_routing_row(self, item_code):
        if self.champion_medium_map_df is None:
            return None
        item_code = self._normalize_itemcode(item_code)
        row = self.champion_medium_map_df[self.champion_medium_map_df["ItemCode"].astype(str) == item_code]
        if row.empty:
            return None
        return row.iloc[0].to_dict()

    # ============================================================
    # ARTIFACT GETTERS
    # ============================================================
    def get_long_artifact(self, model_name):
        return self.long_artifacts.get(str(model_name).upper())

    def get_medium_artifact(self, subgroup, model_name):
        return self.medium_artifacts.get((str(subgroup).upper(), str(model_name).upper()))

    def get_short_artifact(self, promo_profile=None):
        promo_profile = str(promo_profile or "").upper()

        if promo_profile in {"PROMO_HEAVY", "PROMO_INFLUENCED", "PURE_PROMO"}:
            return self.short_promo_artifacts

        if promo_profile == "NORMAL":
            return self.short_normal_artifacts

        return self.short_rule_artifacts

    def get_gru_long_bundle(self):
        return self.gru_bundle, self.gru_scalers


artifact_service = ArtifactService()