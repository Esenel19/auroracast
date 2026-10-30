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
