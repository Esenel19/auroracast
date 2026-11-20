import numpy as np
import pandas as pd
import pytest

from auroracast.model import train as tm


@pytest.fixture(autouse=True)
def default_model_config(tmp_path_factory, monkeypatch):
    """Les tests ne dépendent pas du réglage de production (config.json) : réglage d'origine,
    sauf si un test pointe CONFIG_FILE vers son propre fichier."""
    monkeypatch.setattr(tm, "CONFIG_FILE", tmp_path_factory.mktemp("cfg") / "absent.json")


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
    for h in tm.HORIZONS:
        future = df["kp"] - 0.3 * df["bz_mean_3h"] + rng.normal(0, 1, n) + h / 48
        df[tm.target(h)] = (future >= 4).astype(object)
        df.loc[df.index[-2:], tm.target(h)] = None                  # bout de série : cible inconnue
    return df
