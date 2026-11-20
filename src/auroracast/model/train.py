"""Entraîner et évaluer les modèles « Kp >= 4 dans les 6, 24 et 48 h ».

Lit la vue ``v_training_kp`` de l'entrepôt (une ligne par tranche de 3 h, variables connues
à la fin de la tranche). Pour chaque horizon :

1. évaluation : régression logistique apprise sur 2010-2021, seuil d'alerte choisi sur
   2022-2023, notes mesurées sur 2024 → aujourd'hui, comparées à deux références simples ;
2. contrôle : si le ROC AUC du test passe sous --min-auc, rien n'est enregistré (l'ancien
   modèle reste en service) et la commande échoue ;
3. modèle de production : réappris sur TOUTES les tranches connues (jusqu'à la veille),
   avec le seuil choisi en 1. C'est lui qui intègre les nouvelles données chaque jour.

Variables retenues et hyperparamètres : config.json (un réglage par horizon), issu de
l'optimisation (``python -m auroracast.model.tune``) et adopté après lecture de son rapport.

    python -m auroracast.model.train                 # DATABASE_URL lu dans .env
    python -m auroracast.model.train --out models --horizons 6 24 48
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

HORIZONS = (6, 24, 48)
MIN_AUC = 0.70
FEATURES = [
    "kp", "kp_mean_24h", "kp_max_24h", "kp_max_24h_27d",
    "bz_mean_3h", "bz_min_6h", "bt_mean_3h", "speed_mean_6h", "density_mean_6h",
    "f107", "doy_sin", "doy_cos",
]
DEFAULT_CONFIG = {"algorithme": "regression_logistique", "features": FEATURES,
                  "C": 1.0, "penalty": "l2", "class_weight": None}
ALGORITHMS = ("regression_logistique", "foret_aleatoire")
RF_DEFAULTS = {"n_estimators": 100, "max_samples": 0.5, "min_samples_leaf": 50, "max_features": "sqrt"}
CONFIG_FILE = Path(__file__).with_name("config.json")
VALIDATION_START = pd.Timestamp("2022-01-01", tz="UTC")
TEST_START = pd.Timestamp("2024-01-01", tz="UTC")


def target(horizon: int) -> str:
    return f"aurora_kp4_{horizon}h"


def model_file(horizon: int) -> str:
    return f"kp4_{horizon}h.joblib"


# ------------------------------------------------------------------------- données
def read_training(database_url: str) -> pd.DataFrame:
    """Lit la vue d'entraînement (toutes les tranches depuis 2010)."""
    import psycopg

    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'UTC'")
        targets = ", ".join(target(h) for h in HORIZONS)
        cur.execute(f"SELECT time_utc, {targets}, {', '.join(FEATURES)} FROM v_training_kp ORDER BY time_utc")
        cols = [c.name for c in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=cols)
    df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)
    for c in FEATURES:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df.set_index("time_utc")


def known(df: pd.DataFrame, tgt: str) -> pd.DataFrame:
    """Tranches dont la cible est connue (les dernières heures attendent encore leur futur)."""
    out = df[df[tgt].notna()].copy()
    out[tgt] = out[tgt].astype(bool)
    return out


def split(df: pd.DataFrame, tgt: str) -> dict[str, pd.DataFrame]:
    """Découpage temporel, sur les tranches dont la cible est connue."""
    known_ = known(df, tgt)
    return {
        "train": known_[known_.index < VALIDATION_START],
        "validation": known_[(known_.index >= VALIDATION_START) & (known_.index < TEST_START)],
        "test": known_[known_.index >= TEST_START],
    }


# ------------------------------------------------------------------------- modèles
class SelectColumns(BaseEstimator, TransformerMixin):
    """Garde les variables retenues : le modèle reçoit toujours les 12 variables (FEATURES)."""

    def __init__(self, columns=None):
        self.columns = columns

    def fit(self, x, y=None):
        return self

    def transform(self, x):
        return x[list(self.columns)]


def model_config(horizon: int | None = None) -> dict:
    """Réglage d'un horizon dans config.json ; à défaut, le réglage d'origine (12 variables)."""
    cfg = dict(DEFAULT_CONFIG)
    if horizon is not None and CONFIG_FILE.exists():
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("horizons", {}).get(str(horizon), {}))
    unknown = set(cfg["features"]) - set(FEATURES)
    if unknown:
        raise ValueError(f"variables inconnues dans config.json : {sorted(unknown)}")
    if cfg["algorithme"] not in ALGORITHMS:
        raise ValueError(f"algorithme inconnu dans config.json : {cfg['algorithme']}")
    return cfg


def build_model(horizon: int | None = None, config: dict | None = None) -> Pipeline:
    """Variables retenues → médiane pour les manquantes → mise à l'échelle → régression logistique,
    ou forêt aléatoire si config.json le demande (algorithme = foret_aleatoire)."""
    cfg = {**DEFAULT_CONFIG, **(config or model_config(horizon))}
    if cfg["algorithme"] == "foret_aleatoire":
        from sklearn.ensemble import RandomForestClassifier

        rf = {k: cfg.get(k, v) for k, v in RF_DEFAULTS.items()}
        return Pipeline([
            ("select", SelectColumns(list(cfg["features"]))),
            ("imputer", SimpleImputer(strategy="median")),
            ("rf", RandomForestClassifier(**rf, n_jobs=-1, random_state=0)),
        ])
    return Pipeline([
        ("select", SelectColumns(list(cfg["features"]))),
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("logit", _logistic(cfg)),
    ])


def _logistic(cfg: dict) -> LogisticRegression:
    """L1 ou L2 ; scikit-learn >= 1.8 remplace « penalty » par « l1_ratio » (0 = L2, 1 = L1)."""
    l1 = cfg["penalty"] == "l1"
    kw = {"C": cfg["C"], "class_weight": cfg["class_weight"], "max_iter": 1000,
          "solver": "liblinear" if l1 else "lbfgs"}
    if inspect.signature(LogisticRegression).parameters["penalty"].default == "deprecated":
        kw["l1_ratio"] = 1.0 if l1 else 0.0
    else:
        kw["penalty"] = cfg["penalty"]
    return LogisticRegression(**kw)


def persistence_proba(part: pd.DataFrame) -> np.ndarray:
    """Référence « demain = aujourd'hui » : alerte si le Kp des dernières 24 h a atteint 4."""
    return (part["kp_max_24h"] >= 4).astype(float).to_numpy()


def climatology_table(train: pd.DataFrame, tgt: str) -> pd.Series:
    """Référence climatologique : fréquence des belles aurores par mois (2010-2021)."""
    return train[tgt].groupby(train.index.month).mean()


def climatology_proba(part: pd.DataFrame, table: pd.Series) -> np.ndarray:
    return table.reindex(part.index.month).to_numpy()


# ------------------------------------------------------------------------- métriques
def metrics(y: np.ndarray, proba: np.ndarray, threshold: float) -> dict:
    """Métriques de probabilité (AUC, Brier) et d'alerte au seuil donné."""
    y = np.asarray(y, dtype=bool)
    alert = proba >= threshold
    tp = int((alert & y).sum())
    fp = int((alert & ~y).sum())
    fn = int((~alert & y).sum())
    tn = int((~alert & ~y).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    single_class = y.all() or (~y).all()
    return {
        "n": len(y), "taux_positifs": float(y.mean()),
        "roc_auc": None if single_class else float(roc_auc_score(y, proba)),
        "pr_auc": None if single_class else float(average_precision_score(y, proba)),
        "brier": float(brier_score_loss(y, proba)),
        "seuil": float(threshold), "precision": precision, "rappel": recall, "f1": f1,
        "vp": tp, "fp": fp, "fn": fn, "vn": tn,
    }


def best_threshold(y: np.ndarray, proba: np.ndarray) -> float:
    """Seuil d'alerte qui maximise le F1 (choisi sur la validation, jamais sur le test)."""
    grid = np.round(np.arange(0.05, 0.951, 0.01), 2)
    return float(max(grid, key=lambda t: metrics(y, proba, t)["f1"]))


# ------------------------------------------------------------------------- entraînement
def train(df: pd.DataFrame, horizon: int = 24) -> tuple[Pipeline, dict]:
    """Évalue le modèle pour un horizon, puis renvoie le modèle de production (toutes données)."""
    tgt = target(horizon)
    parts = split(df, tgt)
    tr, va = parts["train"], parts["validation"]

    cfg = model_config(horizon)
    model = build_model(config=cfg).fit(tr[FEATURES], tr[tgt])
    proba = {k: model.predict_proba(p[FEATURES])[:, 1] for k, p in parts.items()}
    threshold = best_threshold(va[tgt].to_numpy(), proba["validation"])

    clim = climatology_table(tr, tgt)
    clim_threshold = best_threshold(tr[tgt].to_numpy(), climatology_proba(tr, clim))
    results: dict = {"horizon_h": horizon, "seuil_alerte": threshold,
                     "modele": {}, "persistance": {}, "climatologie": {}}
    for name, p in parts.items():
        y = p[tgt].to_numpy()
        results["modele"][name] = metrics(y, proba[name], threshold)
        results["persistance"][name] = metrics(y, persistence_proba(p), 0.5)
        results["climatologie"][name] = metrics(y, climatology_proba(p, clim), clim_threshold)

    if "logit" in model.named_steps:
        logit = model.named_steps["logit"]
        results["coefficients"] = {"constante": float(logit.intercept_[0]),
                                   **{f: float(c) for f, c in zip(cfg["features"], logit.coef_[0])}}
    else:
        rf = model.named_steps["rf"]
        results["importances"] = {f: float(v) for f, v in zip(cfg["features"], rf.feature_importances_)}
    results["periodes"] = {k: [p.index.min().isoformat(), p.index.max().isoformat()]
                           for k, p in parts.items()}

    # Modèle de production : mêmes réglages, appris sur tout ce qui est connu aujourd'hui.
    everything = known(df, tgt)
    production = build_model(config=cfg).fit(everything[FEATURES], everything[tgt])
    results["production"] = {"n": len(everything), "jusqu_au": everything.index.max().isoformat()}
    results["variables"] = list(cfg["features"])
    results["reglage"] = {k: v for k, v in cfg.items() if k != "features"}
    return production, results


def predict_latest(model: Pipeline, df: pd.DataFrame, threshold: float) -> dict:
    """Probabilité pour la dernière tranche connue (prévision émise à sa fin)."""
    last = df.iloc[[-1]]
    p = float(model.predict_proba(last[FEATURES])[0, 1])
    return {"tranche_utc": last.index[0].isoformat(),
            "emise_a_utc": (last.index[0] + pd.Timedelta(hours=3)).isoformat(),
            "probabilite": p, "alerte": p >= threshold,
            "variables": {f: (None if pd.isna(v) else float(v)) for f, v in last[FEATURES].iloc[0].items()}}


# ------------------------------------------------------------------------- rapport
def to_markdown(results: dict, latest: dict | None = None) -> str:
    def fmt(v):
        return "—" if v is None else f"{v:.3f}"

    lines = [f"# Modèle Kp ≥ 4 dans les {results['horizon_h']} h", "",
             (f"Entraîné le {results.get('entraine_le', '')[:16].replace('T', ' ')} UTC. "
              f"Seuil d'alerte (choisi sur la validation) : **{results['seuil_alerte']:.2f}**. "
              f"Modèle en service appris sur {results['production']['n']} tranches "
              f"(jusqu'au {results['production']['jusqu_au'][:10]})."), "",
             "| Période | Méthode | ROC AUC | PR AUC | Brier | Précision | Rappel | F1 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for part in ("validation", "test"):
        for name in ("modele", "persistance", "climatologie"):
            m = results[name][part]
            lines.append(f"| {part} | {name} | {fmt(m['roc_auc'])} | {fmt(m['pr_auc'])} | {fmt(m['brier'])} "
                         f"| {m['precision']:.3f} | {m['rappel']:.3f} | {m['f1']:.3f} |")
    if "coefficients" in results:
        lines += ["", "Coefficients du modèle évalué (variables centrées-réduites : comparables entre elles) :", "",
                  "| Variable | Coefficient |", "| --- | --- |"]
        lines += [f"| {k} | {v:+.3f} |" for k, v in results["coefficients"].items()]
    else:
        lines += ["", "Importance des variables dans la forêt aléatoire (somme = 1) :", "",
                  "| Variable | Importance |", "| --- | --- |"]
        lines += [f"| {k} | {v:.3f} |" for k, v in sorted(results["importances"].items(), key=lambda kv: -kv[1])]
    if latest:
        lines += ["", (f"Dernière tranche ({latest['tranche_utc'][:16].replace('T', ' ')} UTC) : "
                       f"probabilité **{latest['probabilite']:.0%}** → "
                       f"{'alerte' if latest['alerte'] else 'pas d’alerte'}.")]
    return "\n".join(lines) + "\n"


def _model_type(results: dict) -> str:
    algo = results.get("reglage", {}).get("algorithme", "regression_logistique")
    return "RandomForestClassifier" if algo == "foret_aleatoire" else "LogisticRegression"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="models", help="dossier des modèles et des métriques")
    parser.add_argument("--horizons", type=int, nargs="+", default=list(HORIZONS), choices=HORIZONS)
    parser.add_argument("--min-auc", type=float, default=MIN_AUC, help="ROC AUC minimal sur le test")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv()
    url = args.database_url or os.environ.get("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL manquant (fichier .env ou --database-url)")

    from auroracast.model import tracking

    df = read_training(url)
    example = df[FEATURES].dropna().tail(5)
    trained = {}
    for h in args.horizons:
        model, results = train(df, h)
        auc = results["modele"]["test"]["roc_auc"]
        if auc is None or auc < args.min_auc:
            tracking.log_training(model, results, example, accepted=False, model_type=_model_type(results))
            print(f"Horizon {h} h : ROC AUC du test {auc} < {args.min_auc}. Rien n'est enregistré, "
                  "l'ancien modèle reste en service.")
            return 1
        results["entraine_le"] = datetime.now(UTC).isoformat()
        results["derniere_prevision"] = predict_latest(model, df, results["seuil_alerte"])
        trained[h] = (model, results)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    reports = []
    for h, (model, results) in trained.items():
        joblib.dump({"model": model, "features": FEATURES, "threshold": results["seuil_alerte"],
                     "horizon_h": h}, out / model_file(h))
        (out / f"metrics_{h}h.json").write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                                encoding="utf-8")
        reports.append(to_markdown(results, results["derniere_prevision"]))
        version = tracking.log_training(model, results, example, accepted=True, report=reports[-1],
                                        model_type=_model_type(results))
        if version:
            print(f"MLflow : {tracking.registered_name(h)} version {version} (alias production)")
    report = "\n".join(reports)
    (out / "rapport_modele.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
