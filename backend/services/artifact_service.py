# backend/services/artifact_service.py ---> 🧠 Model loader

import os
import json
import joblib
import pandas as pd
import torch
from dataclasses import dataclass
import torch.nn as nn


class LongGRUResidualForecaster(nn.Module):
    def __init__(
        self,
        num_items,
        seq_input_dim,
        static_input_dim,
        embed_dim=32,
        hidden_size=64,
        num_layers=2,
        dropout=0.25,
    ):
        super().__init__()

        self.item_embedding = nn.Embedding(num_items, embed_dim)

        self.gru = nn.GRU(
            input_size=seq_input_dim,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )

        self.seq_fc = nn.Sequential(
            nn.Linear(hidden_size, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
        )

        self.static_fc = nn.Sequential(
            nn.Linear(static_input_dim, 32),
            nn.ReLU(),
            nn.Dropout(dropout),
        )

        self.head = nn.Sequential(
            nn.Linear(64 + 32 + embed_dim, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
        )

    def forward(self, x_seq, x_static, x_item):
        out, _ = self.gru(x_seq)
        last_hidden = out[:, -1, :]

        seq_repr = self.seq_fc(last_hidden)
        static_repr = self.static_fc(x_static)
        item_repr = self.item_embedding(x_item)

        x = torch.cat([seq_repr, static_repr, item_repr], dim=1)
        pred_res_log = self.head(x).squeeze(1)
        return pred_res_log


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
            model_pt_path = os.path.join(gru_dir, "gru_long_deploy_model.pt")
            seq_scaler_path = os.path.join(gru_dir, "gru_long_seq_scaler.pkl")
            static_scaler_path = os.path.join(gru_dir, "gru_long_static_scaler.pkl")
            promo_profile_path = os.path.join(gru_dir, "gru_long_promo_profile_df.pkl")
            sku_profile_path = os.path.join(gru_dir, "gru_long_sku_profile_df.pkl")
            item_to_idx_path = os.path.join(gru_dir, "gru_long_item_to_idx.pkl")
            itemcode_categories_path = os.path.join(gru_dir, "gru_long_itemcode_categories.pkl")
            meta_json_path = os.path.join(gru_dir, "gru_long_deploy_meta.json")

            required_paths = [
                model_pt_path,
                seq_scaler_path,
                static_scaler_path,
                promo_profile_path,
                sku_profile_path,
                item_to_idx_path,
            ]

            if all(os.path.exists(p) for p in required_paths):
                try:
                    artifact = torch.load(model_pt_path, map_location="cpu")

                    seq_scaler = joblib.load(seq_scaler_path)
                    static_scaler = joblib.load(static_scaler_path)
                    promo_profile_df = joblib.load(promo_profile_path)
                    sku_profile_df = joblib.load(sku_profile_path)
                    item_to_idx = joblib.load(item_to_idx_path)

                    itemcode_categories = None
                    if os.path.exists(itemcode_categories_path):
                        itemcode_categories = joblib.load(itemcode_categories_path)

                    deploy_meta = {}
                    if os.path.exists(meta_json_path):
                        with open(meta_json_path, "r") as f:
                            deploy_meta = json.load(f)

                    seq_features = artifact.get("seq_features", deploy_meta.get("seq_features", []))
                    static_features = artifact.get("static_features", deploy_meta.get("static_features", []))
                    seq_len = artifact.get("seq_len", deploy_meta.get("seq_len", 18))
                    embed_dim = artifact.get("embed_dim", deploy_meta.get("embed_dim", 32))
                    hidden_size = artifact.get("hidden_size", deploy_meta.get("hidden_size", 64))
                    num_layers = artifact.get("num_layers", deploy_meta.get("num_layers", 2))
                    dropout = artifact.get("dropout", deploy_meta.get("dropout", 0.25))

                    model = LongGRUResidualForecaster(
                        num_items=len(item_to_idx),
                        seq_input_dim=len(seq_features),
                        static_input_dim=len(static_features),
                        embed_dim=embed_dim,
                        hidden_size=hidden_size,
                        num_layers=num_layers,
                        dropout=dropout,
                    )

                    model.load_state_dict(artifact["model_state_dict"])
                    model.eval()

                    self.gru_bundle = {
                        "model": model,
                        "model_type": artifact.get("model_type", "GRU"),
                        "model_name": "GRU",
                        "segment": artifact.get("segment", "LONG"),
                        "seq_features": seq_features,
                        "static_features": static_features,
                        "seq_len": seq_len,
                        "embed_dim": embed_dim,
                        "hidden_size": hidden_size,
                        "num_layers": num_layers,
                        "dropout": dropout,
                        "item_to_idx": item_to_idx,
                        "abc_map": artifact.get("abc_map", {}),
                        "clip_caps": artifact.get("clip_caps", {}),
                        "promo_profile_df": promo_profile_df,
                        "sku_profile_df": sku_profile_df,
                        "itemcode_categories": itemcode_categories,
                    }

                    self.gru_scalers = LongGRUScalerBundle(
                        seq_scaler=seq_scaler,
                        static_scaler=static_scaler
                    )

                except Exception as e:
                    print(f"[WARN] Failed to load GRU artifacts: {e}")
                    self.gru_bundle = None
                    self.gru_scalers = None
            else:
                for p in required_paths:
                    if not os.path.exists(p):
                        print(f"[WARN] Missing file: {p}")



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