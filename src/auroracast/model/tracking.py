"""Suivi des entraînements dans MLflow (expériences + registre de modèles).

Activé seulement si la variable MLFLOW_TRACKING_URI est définie, par exemple le serveur
MLflow hébergé par DagsHub : https://dagshub.com/<compte>/<dépôt>.mlflow, avec
MLFLOW_TRACKING_USERNAME (compte) et MLFLOW_TRACKING_PASSWORD (jeton). Sans elle,
l'entraînement fonctionne comme avant.

Pour chaque horizon, un « run » dans l'expérience ``auroracast-kp4`` :
  paramètres  horizon, type de modèle, variables, périodes, seuil, données apprises
  métriques   ROC AUC, PR AUC, Brier, précision, rappel, F1 du modèle et des deux
              références, sur la validation et le test
  fichiers    métriques JSON et rapport
  modèle      enregistré dans le registre ``auroracast-kp4-<h>h`` SI le contrôle qualité
              est passé ; l'alias ``production`` pointe alors sur cette version.
Un modèle refusé est tracé (tag statut = refusé) mais pas enregistré.
"""

from __future__ import annotations

import inspect
import json
import os
import tempfile
from pathlib import Path

EXPERIMENT = "auroracast-kp4"
METRICS = ("roc_auc", "pr_auc", "brier", "precision", "rappel", "f1")


def enabled() -> bool:
    return bool(os.environ.get("MLFLOW_TRACKING_URI"))


def registered_name(horizon: int) -> str:
    return f"auroracast-kp4-{horizon}h"


def flat_metrics(results: dict) -> dict[str, float]:
    """{'modele_test_roc_auc': 0.81, 'persistance_validation_f1': ..., ...} (valeurs absentes omises)."""
    out = {}
    for method in ("modele", "persistance", "climatologie"):
        for part in ("validation", "test"):
            for k in METRICS:
                v = results.get(method, {}).get(part, {}).get(k)
                if v is not None:
                    out[f"{method}_{part}_{k}"] = float(v)
    out["donnees_apprises"] = float(results.get("production", {}).get("n", 0))
    return out


def log_training(model, results: dict, x_example, accepted: bool, model_type: str = "LogisticRegression",
                 report: str | None = None) -> str | None:
    """Trace un entraînement ; renvoie la version enregistrée (ou None si refusé ou désactivé)."""
    if not enabled():
        return None
    import mlflow
    from mlflow.models import infer_signature

    h = results["horizon_h"]
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=f"{model_type} {h} h") as run:
        mlflow.set_tags({"horizon_h": h, "modele": model_type, "statut": "accepté" if accepted else "refusé",
                         "commit": os.environ.get("GITHUB_SHA", "local")[:7],
                         "declencheur": os.environ.get("GITHUB_EVENT_NAME", "manuel")})
        mlflow.log_params({
            "horizon_h": h, "modele": model_type, "seuil_alerte": results["seuil_alerte"],
            "variables": ",".join(results["variables"]),
            "periode_apprentissage": " → ".join(d[:10] for d in results["periodes"]["train"]),
            "periode_validation": " → ".join(d[:10] for d in results["periodes"]["validation"]),
            "periode_test": " → ".join(d[:10] for d in results["periodes"]["test"]),
            "production_jusqu_au": results.get("production", {}).get("jusqu_au", "")[:10],
        })
        mlflow.log_metrics(flat_metrics(results))
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / f"metrics_{h}h.json"
            p.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            mlflow.log_artifact(str(p))
            if report:
                r = Path(tmp) / f"rapport_{h}h.md"
                r.write_text(report, encoding="utf-8")
                mlflow.log_artifact(str(r))
        if not accepted:
            return None
        signature = infer_signature(x_example, model.predict_proba(x_example)[:, 1])
        extra = {}
        if "skops_trusted_types" in inspect.signature(mlflow.sklearn.log_model).parameters:
            extra["skops_trusted_types"] = ["numpy.dtype"]          # format skops (sans pickle arbitraire)
        info = mlflow.sklearn.log_model(model, name=f"kp4_{h}h", signature=signature,
                                        registered_model_name=registered_name(h), **extra)
        version = str(info.registered_model_version)
        client = mlflow.MlflowClient()
        client.set_registered_model_alias(registered_name(h), "production", version)
        client.set_model_version_tag(registered_name(h), version, "seuil_alerte", str(results["seuil_alerte"]))
        client.set_model_version_tag(registered_name(h), version, "run_id", run.info.run_id)
        return version
