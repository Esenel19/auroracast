"""Entraîner et évaluer le modèle « Kp >= 4 dans les 24 h ».

Lit la vue ``v_training_kp`` de l'entrepôt (une ligne par tranche de 3 h, variables connues
à la fin de la tranche), découpe le temps en trois périodes, compare une régression
logistique à deux références simples, puis enregistre le modèle et ses métriques.

    python -m auroracast.model.train            # DATABASE_URL lu dans .env
    python -m auroracast.model.train --out models

Périodes : apprentissage 2010-2021, validation 2022-2023 (choix du seuil), test 2024 →
(évalué une seule fois, à la fin).
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

TARGET = "aurora_kp4"
FEATURES = [
    "kp", "kp_mean_24h", "kp_max_24h", "kp_max_24h_27d",
    "bz_mean_3h", "bz_min_6h", "bt_mean_3h", "speed_mean_6h", "density_mean_6h",
    "f107", "doy_sin", "doy_cos",
]
VALIDATION_START = pd.Timestamp("2022-01-01", tz="UTC")
TEST_START = pd.Timestamp("2024-01-01", tz="UTC")
MODEL_FILE = "kp4_24h.joblib"


# ------------------------------------------------------------------------- données
def read_training(database_url: str) -> pd.DataFrame:
    """Lit la vue d'entraînement (toutes les tranches depuis 2010)."""
    import psycopg

    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'UTC'")
        cur.execute(f"SELECT time_utc, kp_max_next_24h, {TARGET}, {', '.join(FEATURES)} "
                    "FROM v_training_kp ORDER BY time_utc")
        cols = [c.name for c in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=cols)
    df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)
    for c in FEATURES + ["kp_max_next_24h"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df.set_index("time_utc")


def split(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Découpage temporel, sur les tranches dont la cible est connue."""
    known = df[df[TARGET].notna()].copy()
    known[TARGET] = known[TARGET].astype(bool)
    return {
        "train": known[known.index < VALIDATION_START],
        "validation": known[(known.index >= VALIDATION_START) & (known.index < TEST_START)],
        "test": known[known.index >= TEST_START],
    }


# ------------------------------------------------------------------------- modèles
def build_model() -> Pipeline:
    """Valeurs manquantes → médiane, mise à l'échelle, régression logistique."""
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("logit", LogisticRegression(max_iter=1000)),
    ])


def persistence_proba(part: pd.DataFrame) -> np.ndarray:
    """Référence « demain = aujourd'hui » : alerte si le Kp des dernières 24 h a atteint 4."""
    return (part["kp_max_24h"] >= 4).astype(float).to_numpy()


def climatology_table(train: pd.DataFrame) -> pd.Series:
    """Référence climatologique : fréquence des belles aurores par mois (2010-2021)."""
    return train[TARGET].groupby(train.index.month).mean()


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
def train(df: pd.DataFrame) -> tuple[Pipeline, dict]:
    parts = split(df)
    tr, va = parts["train"], parts["validation"]

    model = build_model().fit(tr[FEATURES], tr[TARGET])
    proba = {k: model.predict_proba(p[FEATURES])[:, 1] for k, p in parts.items()}
    threshold = best_threshold(va[TARGET].to_numpy(), proba["validation"])

    clim = climatology_table(tr)
    results: dict = {"seuil_alerte": threshold, "modele": {}, "persistance": {}, "climatologie": {}}
    for name, p in parts.items():
        y = p[TARGET].to_numpy()
        results["modele"][name] = metrics(y, proba[name], threshold)
        results["persistance"][name] = metrics(y, persistence_proba(p), 0.5)
        clim_p = climatology_proba(p, clim)
        results["climatologie"][name] = metrics(y, clim_p, best_threshold(tr[TARGET].to_numpy(),
                                                                          climatology_proba(tr, clim)))

    logit = model.named_steps["logit"]
    results["coefficients"] = {"constante": float(logit.intercept_[0]),
                               **{f: float(c) for f, c in zip(FEATURES, logit.coef_[0])}}
    results["periodes"] = {k: [p.index.min().isoformat(), p.index.max().isoformat()]
                           for k, p in parts.items()}
    results["variables"] = FEATURES
    return model, results


def predict_latest(model: Pipeline, df: pd.DataFrame, threshold: float) -> dict:
    """Probabilité pour la dernière tranche connue (prévision émise à sa fin)."""
    last = df.iloc[[-1]]
    p = float(model.predict_proba(last[FEATURES])[0, 1])
    return {"tranche_utc": last.index[0].isoformat(),
            "emise_a_utc": (last.index[0] + pd.Timedelta(hours=3)).isoformat(),
            "probabilite_kp4_24h": p, "alerte": p >= threshold,
            "variables": {f: (None if pd.isna(v) else float(v)) for f, v in last[FEATURES].iloc[0].items()}}


# ------------------------------------------------------------------------- rapport
def to_markdown(results: dict, latest: dict | None = None) -> str:
    def fmt(v):
        return "—" if v is None else f"{v:.3f}"

    lines = ["# Modèle Kp ≥ 4 dans les 24 h", "",
             (f"Entraîné le {results.get('entraine_le', '')[:16].replace('T', ' ')} UTC. "
              f"Seuil d'alerte (choisi sur la validation) : **{results['seuil_alerte']:.2f}**."), "",
             "| Période | Méthode | ROC AUC | PR AUC | Brier | Précision | Rappel | F1 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for part in ("validation", "test"):
        for name in ("modele", "persistance", "climatologie"):
            m = results[name][part]
            lines.append(f"| {part} | {name} | {fmt(m['roc_auc'])} | {fmt(m['pr_auc'])} | {fmt(m['brier'])} "
                         f"| {m['precision']:.3f} | {m['rappel']:.3f} | {m['f1']:.3f} |")
    lines += ["", "Coefficients (variables centrées-réduites : comparables entre elles) :", "",
              "| Variable | Coefficient |", "| --- | --- |"]
    lines += [f"| {k} | {v:+.3f} |" for k, v in results["coefficients"].items()]
    if latest:
        lines += ["", (f"Dernière tranche ({latest['tranche_utc'][:16].replace('T', ' ')} UTC) : "
                       f"probabilité **{latest['probabilite_kp4_24h']:.0%}** → "
                       f"{'alerte' if latest['alerte'] else 'pas d’alerte'}.")]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="models", help="dossier du modèle et des métriques")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv()
    url = args.database_url or os.environ.get("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL manquant (fichier .env ou --database-url)")

    df = read_training(url)
    model, results = train(df)
    results["entraine_le"] = datetime.now(UTC).isoformat()
    latest = predict_latest(model, df, results["seuil_alerte"])
    results["derniere_prevision"] = latest

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "features": FEATURES, "threshold": results["seuil_alerte"]}, out / MODEL_FILE)
    (out / "metrics.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    report = to_markdown(results, latest)
    (out / "rapport_modele.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
