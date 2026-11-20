"""Optimisation : sélection des variables, grille, avant/après, configuration par horizon."""

import json

import pytest

from auroracast.model import train as tm
from auroracast.model import tune


def test_backward_selection_keeps_the_real_signal(training_df):
    parts = tm.split(training_df, tm.target(24))
    path, kept = tune.backward_selection(parts["train"], parts["validation"], tm.target(24))
    assert path[0]["n_variables"] == len(tm.FEATURES) and path[-1]["n_variables"] == 1
    assert {"kp", "bz_mean_3h"} <= set(kept)          # les deux seules variables utiles du jeu fictif
    assert len(kept) < len(tm.FEATURES)                # du bruit a été écarté
    best = max(p["pr_auc"] for p in path)
    chosen = next(p for p in path if p["variables"] == kept)
    assert chosen["pr_auc"] >= best - tune.TOLERANCE


def test_grid_is_sorted_and_complete(training_df):
    parts = tm.split(training_df, tm.target(24))
    grid = tune.grid_search(parts["train"], parts["validation"], tm.target(24), ["kp", "bz_mean_3h"])
    assert len(grid) == len(tune.CS) * len(tune.PENALTIES) * len(tune.CLASS_WEIGHTS)
    assert [tune._rank(r) for r in grid] == sorted(tune._rank(r) for r in grid)


def test_tune_horizon_report(training_df):
    res = tune.tune_horizon(training_df, 24, n_trees=20)
    assert {r["algorithme"] for r in res["algorithmes"]} == {"regression_logistique", "foret_aleatoire",
                                                            "gradient_boosting"}
    assert res["choix"] in {"reglage_actuel", "logistique_optimisee", "foret_optimisee"}
    assert res["validation_glissante"]["annees"] == list(tune.ROLLING_YEARS)
    assert len(res["grille_foret"]) == len(tune.RF_GRID)
    assert res["test_apres"] == res["test_candidats"][res["choix"]]
    assert res["test_avant"]["roc_auc"] and res["test_apres"]["roc_auc"]
    assert res["gain_test"]["pr_auc"] == pytest.approx(res["test_apres"]["pr_auc"] - res["test_avant"]["pr_auc"],
                                                       abs=1e-4)
    md = tune.to_markdown(res)
    assert "Choix sur la validation glissante" in md and "Élimination descendante" in md


@pytest.mark.parametrize("horizon", [6, 48])
def test_rolling_folds_have_no_target_overlap(training_df, horizon):
    import pandas as pd

    folds = tune.rolling_folds(training_df, horizon)
    assert [y for y, _, _ in folds] == list(tune.ROLLING_YEARS)
    for y, tr, va in folds:
        start = pd.Timestamp(f"{y}-01-01", tz="UTC")
        assert tr.index.max() < start - pd.Timedelta(hours=horizon)       # cible jamais dans l'année validée
        assert va.index.min() >= start and va.index.max() < pd.Timestamp(f"{y + 1}-01-01", tz="UTC")


def test_forest_config_trains_and_reports_importances(tmp_path, monkeypatch, training_df):
    cfg = {"horizons": {"24": {"algorithme": "foret_aleatoire", "n_estimators": 20, "min_samples_leaf": 20}}}
    f = tmp_path / "config.json"
    f.write_text(json.dumps(cfg))
    monkeypatch.setattr(tm, "CONFIG_FILE", f)
    model, results = tm.train(training_df, 24)
    assert "rf" in model.named_steps and "coefficients" not in results
    top = max(results["importances"], key=results["importances"].get)
    assert top in {"kp", "bz_mean_3h"}
    assert results["reglage"]["algorithme"] == "foret_aleatoire"
    assert "Importance des variables" in tm.to_markdown(results)
    f.write_text(json.dumps({"horizons": {"24": {"algorithme": "xgboost"}}}))
    with pytest.raises(ValueError):
        tm.model_config(24)


def test_config_drives_the_model(tmp_path, monkeypatch, training_df):
    cfg = {"horizons": {"24": {"features": ["kp", "bz_mean_3h"], "C": 0.3, "penalty": "l1",
                               "class_weight": None}}}
    f = tmp_path / "config.json"
    f.write_text(json.dumps(cfg))
    monkeypatch.setattr(tm, "CONFIG_FILE", f)
    assert tm.model_config(6)["features"] == tm.FEATURES               # horizon absent : réglage d'origine
    model, results = tm.train(training_df, 24)
    assert results["variables"] == ["kp", "bz_mean_3h"] and results["reglage"]["penalty"] == "l1"
    assert set(results["coefficients"]) == {"constante", "kp", "bz_mean_3h"}
    assert model.predict_proba(training_df[tm.FEATURES].tail(3)).shape == (3, 2)   # reçoit les 12 variables
    f.write_text(json.dumps({"horizons": {"24": {"features": ["kp", "inconnue"]}}}))
    with pytest.raises(ValueError):
        tm.model_config(24)


def test_main_writes_report(tmp_path, monkeypatch, training_df):
    monkeypatch.setattr(tune, "read_training", lambda url: training_df)
    assert tune.main(["--out", str(tmp_path), "--horizons", "6", "--database-url", "x", "--n-trees", "10"]) == 0
    proposed = json.loads((tmp_path / "config_proposee.json").read_text(encoding="utf-8"))
    assert set(proposed["horizons"]) == {"6"}
    assert (tmp_path / "rapport_optimisation.md").read_text(encoding="utf-8").startswith("# Optimisation")
    assert json.loads((tmp_path / "optimisation.json").read_text(encoding="utf-8"))["horizons"][0]["horizon_h"] == 6
