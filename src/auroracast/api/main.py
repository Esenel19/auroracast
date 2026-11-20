"""API AuroraCast : sert l'application du directeur et prédit à la demande.

    uvicorn auroracast.api.main:app --port 8000          # en local
    http://localhost:8000/            application du directeur (web/index.html)
    http://localhost:8000/api/docs    documentation interactive (OpenAPI)

Routes :
    GET  /                       page des prévisions des 48 h (lit /forecast.json)
    GET  /suivi.html             page de suivi des modèles (lit /suivi_modeles.json)
    GET  /analyse.html           quand et où voir les aurores : mois × lieu, saisons, tests (lit /analyse.json)
    GET  /forecast.json          prévisions calculées par la tâche « transform »
    GET  /suivi_modeles.json     historique des modèles
    GET  /api/health             état : modèles chargés, prévision disponible et son âge
    GET  /api/models             modèles en service : seuils, notes sur le test
    POST /api/predict            probabilité de Kp >= 4 dans 6, 24 ou 48 h pour des mesures données

Sources des fichiers : le stockage Blob (conteneurs « site » et « models/latest ») si
AZURE_STORAGE_ACCOUNT est défini, lu avec l'identité gérée de l'application ; sinon les
dossiers locaux AURORACAST_SITE_DIR (site) et AURORACAST_MODELS_DIR (models). Les fichiers
sont gardés en mémoire CACHE_SECONDS secondes : l'API ne relit le stockage qu'au plus une fois
toutes les 5 minutes, ce qui limite les opérations facturées.
"""

from __future__ import annotations

import io
import json
import math
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from auroracast.cloud.storage import BlobStore, LocalStore, account_name, store
from auroracast.model.train import FEATURES, HORIZONS, model_file

CACHE_SECONDS = 300
WEB_DIR = Path(os.environ.get("AURORACAST_WEB_DIR", Path(__file__).resolve().parents[3] / "web"))
PAGES = {"": "index.html", "index.html": "index.html", "suivi.html": "suivi.html", "analyse.html": "analyse.html"}
DATA_FILES = ("forecast.json", "suivi_modeles.json", "analyse.json")

app = FastAPI(title="AuroraCast", version="1.0",
              description="Prévision de la visibilité des aurores boréales (Tromsø, Kiruna, Rovaniemi).",
              docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")

_cache: dict[str, tuple[float, object]] = {}


def _site():
    return store("site", local_root=os.environ.get("AURORACAST_SITE_DIR", "site"))


def _models():
    if account_name():
        return BlobStore("models", "latest")
    return LocalStore(os.environ.get("AURORACAST_MODELS_DIR", "models"))   # dossier models/ du dépôt


def _cached(key: str, loader):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = loader()
    _cache[key] = (time.monotonic(), value)
    return value


def site_file(name: str) -> bytes | None:
    return _cached(f"site:{name}", lambda: _site().read_bytes(name))


def bundles() -> dict[int, dict]:
    def load():
        out = {}
        for h in HORIZONS:
            raw = _models().read_bytes(model_file(h))
            if raw:
                out[h] = joblib.load(io.BytesIO(raw))
        return out
    return _cached("models", load)


def metrics(h: int) -> dict | None:
    raw = _cached(f"metrics:{h}", lambda: _models().read_bytes(f"metrics_{h}h.json"))
    return json.loads(raw) if raw else None


# ------------------------------------------------------------------------- pages
@app.get("/", include_in_schema=False)
@app.get("/{page}", include_in_schema=False)
def page(page: str = ""):
    if page in DATA_FILES:
        data = site_file(page)
        if data is None:
            raise HTTPException(503, f"{page} pas encore calculé (tâche transform)")
        return Response(data, media_type="application/json", headers={"Cache-Control": "no-store"})
    if page not in PAGES:
        raise HTTPException(404)
    return FileResponse(WEB_DIR / PAGES[page], media_type="text/html")


# ------------------------------------------------------------------------- API
class Mesures(BaseModel):
    """Variables du modèle à la fin d'une tranche de 3 h ; une valeur absente est autorisée."""

    kp: float | None = Field(None, ge=0, le=9, description="Kp de la tranche en cours")
    kp_mean_24h: float | None = Field(None, ge=0, le=9)
    kp_max_24h: float | None = Field(None, ge=0, le=9)
    kp_max_24h_27d: float | None = Field(None, ge=0, le=9, description="Kp max il y a 27 jours")
    bz_mean_3h: float | None = Field(None, ge=-100, le=100, description="nT, négatif = vers le sud")
    bz_min_6h: float | None = Field(None, ge=-100, le=100)
    bt_mean_3h: float | None = Field(None, ge=0, le=100)
    speed_mean_6h: float | None = Field(None, ge=150, le=3000, description="km/s")
    density_mean_6h: float | None = Field(None, ge=0, le=200, description="particules/cm³")
    f107: float | None = Field(None, ge=0, le=500, description="flux solaire F10.7 de la veille")


class Demande(BaseModel):
    horizon_h: int = Field(24, description="6, 24 ou 48")
    date_utc: datetime | None = Field(None, description="date des mesures (saison) ; par défaut maintenant")
    mesures: Mesures


@app.get("/api/health")
def health():
    loaded = sorted(bundles())
    raw = site_file("forecast.json")
    age = None
    if raw:
        made = json.loads(raw).get("genere_utc")
        if made:
            age = round((datetime.now(UTC) - pd.Timestamp(made).to_pydatetime()).total_seconds() / 3600, 1)
    ok = bool(loaded) and raw is not None
    return {"statut": "ok" if ok else "degrade", "modeles_horizons_h": loaded,
            "prevision_disponible": raw is not None, "prevision_age_h": age,
            "stockage": "azure-blob" if account_name() else "local"}


@app.get("/api/models")
def models():
    out = []
    for h, b in sorted(bundles().items()):
        m = metrics(h) or {}
        t = m.get("modele", {}).get("test", {})
        out.append({"horizon_h": h, "seuil_alerte": b["threshold"], "entraine_le": m.get("entraine_le"),
                    "variables": b.get("features", FEATURES),
                    "test": {k: t.get(k) for k in ("roc_auc", "pr_auc", "brier", "precision", "rappel", "f1")}})
    if not out:
        raise HTTPException(503, "aucun modèle disponible")
    return out


@app.post("/api/predict")
def predict(d: Demande):
    b = bundles().get(d.horizon_h)
    if b is None:
        raise HTTPException(422 if d.horizon_h not in HORIZONS else 503,
                            f"horizon {d.horizon_h} h indisponible (choix : {list(HORIZONS)})")
    when = pd.Timestamp(d.date_utc or datetime.now(UTC))
    when = when.tz_localize("UTC") if when.tzinfo is None else when.tz_convert("UTC")
    doy = 2 * math.pi * when.day_of_year / 365.25          # même formule que la vue v_training_kp
    row = {**d.mesures.model_dump(), "doy_sin": math.sin(doy), "doy_cos": math.cos(doy)}
    x = pd.DataFrame([{f: (math.nan if row.get(f) is None else row[f]) for f in FEATURES}], dtype=float)
    p = float(b["model"].predict_proba(x)[0, 1])
    return {"horizon_h": d.horizon_h, "date_utc": when.isoformat().replace("+00:00", "Z"),
            "probabilite": round(p, 4), "seuil_alerte": b["threshold"], "alerte": p >= b["threshold"],
            "variables_absentes": [f for f in FEATURES if row.get(f) is None]}
