import json
import subprocess

import joblib
import numpy as np
import pandas as pd
import pytest

from auroracast.model import history as hi
from auroracast.model import train as tm


def test_backtest_has_no_leak_and_grows(training_df):
    rows = hi.backtest(training_df, horizons=(24,), start="2025-01-01", min_rows=20)
    assert [r["mois"] for r in rows][:3] == ["2025-01", "2025-02", "2025-03"]
    first = rows[0]
    cutoff = pd.Timestamp("2025-01-01", tz="UTC") - pd.Timedelta(hours=24)
    expected = len(tm.known(training_df, tm.target(24)).loc[lambda d: d.index < cutoff])
    assert first["n_appris"] == expected                         # rien d'après le mois évalué
    assert rows[1]["n_appris"] > first["n_appris"]               # l'historique grandit
    assert all(0 <= r["roc_auc"] <= 1 for r in rows if r["roc_auc"] is not None)


def test_backtest_forest_retrains_each_quarter(tmp_path, monkeypatch, training_df):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"horizons": {"24": {"algorithme": "foret_aleatoire", "n_estimators": 200}}}))
    monkeypatch.setattr(tm, "CONFIG_FILE", f)
    fitted = []
    real = hi.build_model

    def spy(*a, **k):
        fitted.append(k["config"]["n_estimators"])
        return real(*a, **k)

    monkeypatch.setattr(hi, "build_model", spy)
    rows = hi.backtest(training_df, horizons=(24,), start="2025-01-01", min_rows=20)
    quarters = {(r["mois"][:4], (int(r["mois"][5:]) - 1) // 3) for r in rows}
    assert len(rows) >= 6 and len(quarters) <= len(fitted) <= len(quarters) + 1   # + le trimestre en cours
    assert set(fitted) == {hi.BACKTEST_TREES}                    # arbres limités pour tenir en 900 s
    by_month = {r["mois"]: r["n_appris"] for r in rows}
    assert by_month["2025-02"] == by_month["2025-01"]           # même modèle tout le trimestre
    assert by_month["2025-04"] > by_month["2025-01"]            # réentraîné en avril


def test_shipped_config_is_valid(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(tm, "CONFIG_FILE", Path(tm.__file__).with_name("config.json"))   # le vrai réglage
    for h in tm.HORIZONS:
        cfg = tm.model_config(h)
        assert cfg["algorithme"] in tm.ALGORITHMS and set(cfg["features"]) <= set(tm.FEATURES)
        tm.build_model(config=cfg)


@pytest.fixture
def models_repo(tmp_path, training_df):
    """Dépôt Git avec deux versions de modèles (deux commits), comme la branche models."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    git("init", "-q")
    for i, (date, auc) in enumerate([("2026-01-10T10:00:00+00:00", 0.80), ("2026-03-01T10:00:00+00:00", 0.82)]):
        model, res = tm.train(training_df, 24)
        joblib.dump({"model": model, "features": tm.FEATURES, "threshold": 0.5, "horizon_h": 24},
                    repo / tm.model_file(24))
        res["entraine_le"] = date
        res["modele"]["test"]["roc_auc"] = auc
        (repo / "metrics_24h.json").write_text(json.dumps(res))
        git("add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", f"v{i}")
    return repo


def test_versions_and_live_scores(models_repo, training_df):
    vers = hi.versions(models_repo, "HEAD", horizons=(24,))
    assert [v["roc_auc_test"] for v in vers] == [0.80, 0.82]     # du plus ancien au plus récent
    full = {v["sha"]: hi._git(models_repo, "rev-parse", v["sha"]).strip() for v in vers}
    rows = hi.live_scores(training_df, vers, lambda sha, h: hi.load_bundle(models_repo, full[sha], h))
    assert rows and rows[0]["jour"] >= "2026-01-10"
    assert {r["version"] for r in rows if r["jour"] < "2026-03-01"} == {vers[0]["sha"]}
    assert {r["version"] for r in rows if r["jour"] > "2026-03-01"} == {vers[1]["sha"]}
    assert all(r["vus"] <= r["orages"] and r["alertes_justes"] <= r["alertes"] for r in rows)
    assert all(0 <= r["brier"] <= 1 for r in rows) and np.isfinite([r["brier"] for r in rows]).all()
