"""API FastAPI : pages, prévisions, état, modèles et prédiction à la demande."""

import json

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

from auroracast.model.train import FEATURES

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from auroracast.api import main as api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT", raising=False)
    models, site = tmp_path / "models", tmp_path / "site"
    models.mkdir()
    site.mkdir()
    rng = np.random.default_rng(0)
    x = pd.DataFrame(rng.normal(size=(200, len(FEATURES))), columns=FEATURES)
    y = (x["kp"] > 0.5).astype(int)
    model = make_pipeline(SimpleImputer(strategy="median"), LogisticRegression()).fit(x, y)
    joblib.dump({"model": model, "features": FEATURES, "threshold": 0.3, "horizon_h": 24},
                models / "kp4_24h.joblib")
    (models / "metrics_24h.json").write_text(json.dumps(
        {"entraine_le": "2026-11-20T10:13:00+00:00", "modele": {"test": {"roc_auc": 0.88}}}))
    (site / "forecast.json").write_text(json.dumps({"genere_utc": "2026-11-20T09:00Z", "lieux": []}))
    monkeypatch.setenv("AURORACAST_MODELS_DIR", str(models))
    monkeypatch.setenv("AURORACAST_SITE_DIR", str(site))
    api._cache.clear()
    return TestClient(api.app)


def test_pages_and_data(client):
    r = client.get("/")
    assert r.status_code == 200 and "forecast.json" in r.text
    assert client.get("/suivi.html").status_code == 200
    assert client.get("/forecast.json").json()["genere_utc"] == "2026-11-20T09:00Z"
    assert client.get("/suivi_modeles.json").status_code == 503      # pas encore calculé
    assert client.get("/../pyproject.toml").status_code == 404
    assert client.get("/secret.txt").status_code == 404


def test_health_and_models(client):
    h = client.get("/api/health").json()
    assert h["statut"] == "ok" and h["modeles_horizons_h"] == [24] and h["stockage"] == "local"
    assert h["prevision_age_h"] > 0
    m = client.get("/api/models").json()
    assert m[0]["horizon_h"] == 24 and m[0]["seuil_alerte"] == 0.3 and m[0]["test"]["roc_auc"] == 0.88


def test_predict(client):
    strong = client.post("/api/predict", json={"horizon_h": 24, "date_utc": "2026-03-20T21:00:00Z",
                                               "mesures": {"kp": 6, "bz_mean_3h": -10}})
    weak = client.post("/api/predict", json={"horizon_h": 24, "mesures": {"kp": -0.0}})
    assert strong.status_code == 200 and weak.status_code == 200
    s = strong.json()
    assert 0 <= s["probabilite"] <= 1 and s["alerte"] == (s["probabilite"] >= 0.3)
    assert "f107" in s["variables_absentes"] and "kp" not in s["variables_absentes"]
    assert s["probabilite"] > weak.json()["probabilite"]


def test_predict_rejects_bad_input(client):
    assert client.post("/api/predict", json={"horizon_h": 12, "mesures": {}}).status_code == 422
    assert client.post("/api/predict", json={"horizon_h": 6, "mesures": {}}).status_code == 503
    assert client.post("/api/predict", json={"horizon_h": 24, "mesures": {"kp": 12}}).status_code == 422
