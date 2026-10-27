"""Accès simple aux tables propres (branche clean-data) depuis un notebook ou un script.

Récupérer les tables sur son PC, une seule fois, depuis le dossier du projet :
    git fetch origin clean-data
    git worktree add data/clean clean-data
Puis, pour les mettre à jour :
    git -C data/clean pull origin clean-data   (ou : git -C data/clean fetch origin && git -C data/clean reset --hard origin/clean-data)
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

TABLES = ("kp_3h", "solar_daily", "solar_wind_hourly", "kp_forecast", "locations", "cloud_hourly",
          "weather_daily")


def clean_dir() -> Path:
    return Path(os.getenv("AURORACAST_CLEAN_DIR", "data/clean"))


def load(name: str, directory: Path | None = None) -> pd.DataFrame:
    """Charge une table propre (fichier Parquet) par son nom."""
    if name not in TABLES:
        raise ValueError(f"table inconnue : {name} (attendu : {', '.join(TABLES)})")
    path = (directory or clean_dir()) / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} introuvable : récupérer les tables avec "
            "« git fetch origin clean-data » puis « git worktree add data/clean clean-data »")
    return pd.read_parquet(path)
