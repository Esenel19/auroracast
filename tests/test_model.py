import numpy as np
import pandas as pd
import pytest

from auroracast.model import train as tm


@pytest.fixture
def training_df():
    """Série synthétique 2010-2026 : le Kp futur dépend du Kp et du Bz présents."""
    rng = np.random.default_rng(0)
    idx = pd.date_range("2010-01-01", "2026-10-01", freq="1D", tz="UTC")
    n = len(idx)
    df = pd.DataFrame(index=idx)
    df["kp"] = rng.uniform(0, 7, n)
    df["bz_mean_3h"] = rng.normal(0, 4, n)
    for c in tm.FEATURES:
        if c not in df:
            df[c] = rng.normal(0, 1, n)
    df.loc[df.index[::50], "bt_mean_3h"] = np.nan                  # trous de mesure
    df["kp_max_next_24h"] = df["kp"] - 0.3 * df["bz_mean_3h"] + rng.normal(0, 1, n)
    df[tm.TARGET] = (df["kp_max_next_24h"] >= 4).astype(object)
    df.loc[df.index[-2:], [tm.TARGET, "kp_max_next_24h"]] = None   # bout de série : cible inconnue
    return df


def test_split_is_chronological_and_drops_unknown_target(training_df):
    parts = tm.split(training_df)
    assert parts["train"].index.max() < tm.VALIDATION_START <= parts["validation"].index.min()
    assert parts["validation"].index.max() < tm.TEST_START <= parts["test"].index.min()
    assert sum(len(p) for p in parts.values()) == len(training_df) - 2
    assert parts["test"][tm.TARGET].dtype == bool


def test_metrics_counts():
    y = np.array([True, True, False, False])
    m = tm.metrics(y, np.array([0.9, 0.2, 0.6, 0.1]), 0.5)
    assert (m["vp"], m["fp"], m["fn"], m["vn"]) == (1, 1, 1, 1)
    assert m["precision"] == m["rappel"] == 0.5 and m["roc_auc"] == pytest.approx(0.75)
    assert tm.metrics(np.array([False, False]), np.array([0.1, 0.2]), 0.5)["roc_auc"] is None


def test_train_beats_baselines_and_learns_signs(training_df):
    model, res = tm.train(training_df)
    test = res["modele"]["test"]
    assert test["roc_auc"] > 0.85 and test["roc_auc"] > res["climatologie"]["test"]["roc_auc"]
    assert res["coefficients"]["kp"] > 0 and res["coefficients"]["bz_mean_3h"] < 0   # Bz sud → aurores
    assert 0.05 <= res["seuil_alerte"] <= 0.95
    latest = tm.predict_latest(model, training_df, res["seuil_alerte"])
    assert 0 <= latest["probabilite_kp4_24h"] <= 1
    assert "| test | modele |" in tm.to_markdown(res, latest)


def test_baselines(training_df):
    parts = tm.split(training_df)
    clim = tm.climatology_table(parts["train"])
    assert len(clim) == 12 and clim.between(0, 1).all()
    p = tm.persistence_proba(parts["test"])
    assert set(np.unique(p)) <= {0.0, 1.0}
