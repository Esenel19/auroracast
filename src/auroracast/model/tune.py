"""Optimisation des modèles « Kp >= 4 dans 6, 24 et 48 h » : algorithmes, variables, réglages.

Pour chaque horizon, en quatre étapes, avec le même découpage dans le temps que l'entraînement
(apprentissage 2010-2021, validation 2022-2023, test 2024 → aujourd'hui). Les choix se font sur la
validation ; le test n'est lu qu'une fois, à la fin, pour mesurer le gain.

1. Algorithmes (C5.1.3) : régression logistique, forêt aléatoire et gradient boosting par
   histogrammes (HistGradientBoosting, même famille que XGBoost), mêmes 12 variables.
2. Sélection des variables (C5.2.2) : corrélations entre variables, importance par permutation,
   puis élimination descendante : on retire une à une les variables dont l'absence dégrade le
   moins la validation, et on garde le plus petit jeu à moins de TOLERANCE du meilleur PR AUC.
3. Hyperparamètres (C5.2.4) : recherche sur grille de la régression logistique (force C,
   pénalité L1 ou L2, pondération des classes), sur les variables retenues.
4. Avant / après : réglage actuel (config.json) contre réglage proposé, notes sur le test.

Critère : PR AUC de la validation (les belles aurores sont rares ; le ROC AUC flatte les classes
déséquilibrées), à égalité le score de Brier (probabilités bien calibrées pour le directeur).

    python -m auroracast.model.tune --out optimisation        # DATABASE_URL lu dans .env

Écrit optimisation.json, rapport_optimisation.md et config_proposee.json. Rien n'est mis en
production automatiquement : le réglage proposé est recopié dans config.json après lecture.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.pipeline import Pipeline

from auroracast.model.train import (
    DEFAULT_CONFIG,
    FEATURES,
    HORIZONS,
    best_threshold,
    build_model,
    metrics,
    model_config,
    read_training,
    split,
    target,
)

TOLERANCE = 0.002                       # perte de PR AUC acceptée pour une variable en moins
CS = (0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0)
PENALTIES = ("l2", "l1")
CLASS_WEIGHTS = (None, "balanced")
CORRELATION_ALERT = 0.85


def algorithms(n_trees: int = 200) -> dict:
    return {
        "regression_logistique": lambda: build_model(config=DEFAULT_CONFIG),
        "foret_aleatoire": lambda: Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("rf", RandomForestClassifier(n_estimators=n_trees, min_samples_leaf=20, max_features="sqrt",
                                          n_jobs=-1, random_state=0)),
        ]),
        "gradient_boosting": lambda: HistGradientBoostingClassifier(
            learning_rate=0.05, max_iter=300, max_leaf_nodes=15, l2_regularization=1.0, random_state=0),
    }


def score(model, tr, va, tgt: str) -> dict:
    """Apprend sur ``tr``, note sur ``va`` : PR AUC, ROC AUC, Brier et durée d'apprentissage."""
    t0 = time.perf_counter()
    model.fit(tr[FEATURES], tr[tgt])
    duration = time.perf_counter() - t0
    proba = model.predict_proba(va[FEATURES])[:, 1]
    m = metrics(va[tgt].to_numpy(), proba, 0.5)
    return {"pr_auc": m["pr_auc"], "roc_auc": m["roc_auc"], "brier": m["brier"],
            "duree_apprentissage_s": round(duration, 3)}


def _cfg(features, **kw) -> dict:
    return {**DEFAULT_CONFIG, **kw, "features": list(features)}


def _rank(row: dict) -> tuple:
    """Meilleur PR AUC d'abord ; à 0,001 près, meilleur Brier."""
    return (-round(row["pr_auc"] or 0.0, 3), row["brier"])


# ------------------------------------------------------------------------- 1. algorithmes
def compare_algorithms(tr, va, tgt: str, n_trees: int = 200) -> list[dict]:
    rows = [{"algorithme": name, **score(make(), tr, va, tgt)} for name, make in algorithms(n_trees).items()]
    return sorted(rows, key=_rank)


# ------------------------------------------------------------------------- 2. variables
def correlated_pairs(tr) -> list[dict]:
    corr = tr[FEATURES].corr()
    pairs = []
    for i, a in enumerate(FEATURES):
        for b in FEATURES[i + 1:]:
            r = corr.loc[a, b]
            if abs(r) >= CORRELATION_ALERT:
                pairs.append({"variables": [a, b], "correlation": round(float(r), 3)})
    return pairs


def permutation_ranking(tr, va, tgt: str) -> list[dict]:
    """Baisse du PR AUC de validation quand on mélange une variable (5 tirages)."""
    model = build_model(config=DEFAULT_CONFIG).fit(tr[FEATURES], tr[tgt])
    imp = permutation_importance(model, va[FEATURES], va[tgt].astype(int), scoring="average_precision",
                                 n_repeats=5, random_state=0)
    rows = [{"variable": f, "baisse_pr_auc": round(float(m), 4), "ecart_type": round(float(s), 4)}
            for f, m, s in zip(FEATURES, imp.importances_mean, imp.importances_std)]
    return sorted(rows, key=lambda r: -r["baisse_pr_auc"])


def backward_selection(tr, va, tgt: str, base: dict | None = None) -> tuple[list[dict], list[str]]:
    """Élimination descendante ; renvoie le chemin complet et le jeu retenu."""
    base = base or DEFAULT_CONFIG
    current = list(FEATURES)
    path = [{"n_variables": len(current), "retiree": None, "variables": list(current),
             **score(build_model(config=_cfg(current, **{k: base[k] for k in ("C", "penalty", "class_weight")})),
                     tr, va, tgt)}]
    while len(current) > 1:
        trials = []
        for f in current:
            keep = [c for c in current if c != f]
            cfg = _cfg(keep, **{k: base[k] for k in ("C", "penalty", "class_weight")})
            trials.append((f, keep, score(build_model(config=cfg), tr, va, tgt)))
        f, keep, s = min(trials, key=lambda t: _rank(t[2]))
        current = keep
        path.append({"n_variables": len(current), "retiree": f, "variables": list(current), **s})
    best = max(p["pr_auc"] for p in path)
    chosen = min((p for p in path if p["pr_auc"] >= best - TOLERANCE), key=lambda p: p["n_variables"])
    return path, chosen["variables"]


# ------------------------------------------------------------------------- 3. réglages
def grid_search(tr, va, tgt: str, features: list[str]) -> list[dict]:
    rows = []
    for c in CS:
        for pen in PENALTIES:
            for cw in CLASS_WEIGHTS:
                cfg = _cfg(features, C=c, penalty=pen, class_weight=cw)
                rows.append({"C": c, "penalty": pen, "class_weight": cw, **score(build_model(config=cfg), tr, va, tgt)})
    return sorted(rows, key=_rank)


# ------------------------------------------------------------------------- 4. avant / après
def evaluate(cfg: dict, parts: dict, tgt: str) -> dict:
    """Apprend sur 2010-2021, seuil choisi sur la validation, notes sur le test."""
    tr, va, te = parts["train"], parts["validation"], parts["test"]
    model = build_model(config=cfg).fit(tr[FEATURES], tr[tgt])
    threshold = best_threshold(va[tgt].to_numpy(), model.predict_proba(va[FEATURES])[:, 1])
    m = metrics(te[tgt].to_numpy(), model.predict_proba(te[FEATURES])[:, 1], threshold)
    return {k: m[k] for k in ("roc_auc", "pr_auc", "brier", "seuil", "precision", "rappel", "f1")}


def tune_horizon(df, horizon: int, n_trees: int = 200) -> dict:
    tgt = target(horizon)
    parts = split(df, tgt)
    tr, va = parts["train"], parts["validation"]
    t0 = time.perf_counter()
    algos = compare_algorithms(tr, va, tgt, n_trees)
    path, selected = backward_selection(tr, va, tgt)
    grid = grid_search(tr, va, tgt, selected)
    best = grid[0]
    proposed = _cfg(selected, C=best["C"], penalty=best["penalty"], class_weight=best["class_weight"])
    current = model_config(horizon)
    before, after = evaluate(current, parts, tgt), evaluate(proposed, parts, tgt)
    gain = {k: (None if before[k] is None or after[k] is None else round(after[k] - before[k], 4))
            for k in ("roc_auc", "pr_auc", "brier", "f1")}
    return {
        "horizon_h": horizon,
        "taille": {k: len(p) for k, p in parts.items()},
        "taux_positifs_apprentissage": round(float(tr[tgt].mean()), 4),
        "algorithmes": algos,
        "correlations_fortes": correlated_pairs(tr),
        "importance_permutation": permutation_ranking(tr, va, tgt),
        "selection": {"chemin": path, "retenues": selected,
                      "ecartees": [f for f in FEATURES if f not in selected], "tolerance_pr_auc": TOLERANCE},
        "grille": grid,
        "reglage_actuel": current,
        "reglage_propose": proposed,
        "test_avant": before,
        "test_apres": after,
        "gain_test": gain,
        "duree_s": round(time.perf_counter() - t0, 1),
    }


# ------------------------------------------------------------------------- rapport
def _f(v, d=3):
    return "—" if v is None else f"{v:.{d}f}"


def to_markdown(res: dict) -> str:
    h = res["horizon_h"]
    sel = res["selection"]
    lines = [f"## Horizon {h} h", "",
             (f"Apprentissage {res['taille']['train']} tranches, validation {res['taille']['validation']}, "
             f"test {res['taille']['test']} ; belles aurores : {res['taux_positifs_apprentissage']:.1%} des tranches."), "",
             "### 1. Algorithmes (validation, 12 variables)", "",
             "| Algorithme | PR AUC | ROC AUC | Brier | Apprentissage (s) |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| {r['algorithme']} | {_f(r['pr_auc'])} | {_f(r['roc_auc'])} | {_f(r['brier'], 4)} | "
              f"{r['duree_apprentissage_s']:.2f} |" for r in res["algorithmes"]]
    lines += ["", "### 2. Sélection des variables", "",
              "Corrélations fortes (|r| ≥ 0,85, apprentissage) : "
              + (", ".join(f"{a} / {b} ({r:+.2f})" for (a, b), r in
                           ((p["variables"], p["correlation"]) for p in res["correlations_fortes"])) or "aucune") + ".", "",
              "| Variable | Baisse du PR AUC si mélangée |", "| --- | --- |"]
    lines += [f"| {r['variable']} | {r['baisse_pr_auc']:+.4f} ± {r['ecart_type']:.4f} |" for r in res["importance_permutation"]]
    lines += ["", "Élimination descendante (validation) :", "",
              "| Variables | Retirée | PR AUC | Brier |", "| --- | --- | --- | --- |"]
    lines += [f"| {p['n_variables']} | {p['retiree'] or '—'} | {_f(p['pr_auc'])} | {_f(p['brier'], 4)} |" for p in sel["chemin"]]
    lines += ["", (f"Retenues ({len(sel['retenues'])}) : {', '.join(sel['retenues'])}. "
              f"Écartées : {', '.join(sel['ecartees']) or 'aucune'}."), "",
              "### 3. Hyperparamètres (5 meilleurs réglages sur 28)", "",
              "| C | Pénalité | Pondération | PR AUC | Brier |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| {r['C']} | {r['penalty']} | {r['class_weight'] or 'aucune'} | {_f(r['pr_auc'])} | {_f(r['brier'], 4)} |"
              for r in res["grille"][:5]]
    b, a, g = res["test_avant"], res["test_apres"], res["gain_test"]
    lines += ["", "### 4. Avant / après, sur le test (jamais vu pendant les choix)", "",
              "| | ROC AUC | PR AUC | Brier | F1 |", "| --- | --- | --- | --- | --- |",
              f"| Réglage actuel | {_f(b['roc_auc'])} | {_f(b['pr_auc'])} | {_f(b['brier'], 4)} | {_f(b['f1'])} |",
              f"| Réglage proposé | {_f(a['roc_auc'])} | {_f(a['pr_auc'])} | {_f(a['brier'], 4)} | {_f(a['f1'])} |",
              f"| Gain | {_f(g['roc_auc'], 4)} | {_f(g['pr_auc'], 4)} | {_f(g['brier'], 4)} | {_f(g['f1'], 4)} |", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="optimisation")
    parser.add_argument("--horizons", type=int, nargs="+", default=list(HORIZONS), choices=HORIZONS)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--n-trees", type=int, default=200, help="arbres de la forêt aléatoire")
    args = parser.parse_args(argv)

    import os

    from dotenv import load_dotenv
    load_dotenv()
    url = args.database_url or os.environ.get("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL manquant (fichier .env ou --database-url)")
    df = read_training(url)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for h in args.horizons:
        res = tune_horizon(df, h, args.n_trees)
        results.append(res)
        print(f"Horizon {h} h : {len(res['selection']['retenues'])} variables, "
              f"C={res['reglage_propose']['C']} {res['reglage_propose']['penalty']} "
              f"{res['reglage_propose']['class_weight'] or ''} ; gain PR AUC test {res['gain_test']['pr_auc']:+.4f} "
              f"({res['duree_s']} s)", flush=True)
    made = datetime.now(UTC).isoformat(timespec="minutes")
    (out / "optimisation.json").write_text(json.dumps({"genere_le": made, "horizons": results},
                                                      indent=2, ensure_ascii=False), encoding="utf-8")
    proposed = {"note": f"Réglage proposé par auroracast.model.tune le {made} (à recopier dans config.json après lecture du rapport).",
                "horizons": {str(r["horizon_h"]): r["reglage_propose"] for r in results}}
    (out / "config_proposee.json").write_text(json.dumps(proposed, indent=2, ensure_ascii=False), encoding="utf-8")
    report = [f"# Optimisation des modèles AuroraCast ({made[:16].replace('T', ' ')} UTC)", "",
              "Choix faits sur la validation (2022-2023) ; le test (2024 → aujourd'hui) ne sert qu'à mesurer le gain.", ""]
    report += [to_markdown(r) for r in results]
    (out / "rapport_optimisation.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
