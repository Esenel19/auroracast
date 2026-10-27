"""Outils partagés : lecture des fichiers bruts, bornes physiques, rapport de qualité."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

# Bornes physiques plausibles : au-delà, la valeur est considérée comme erronée.
BOUNDS: dict[str, tuple[float, float]] = {
    "kp": (0, 9),
    "ap": (0, 400),
    "Ap": (0, 400),
    "sn": (0, 600),
    "f107_obs": (50, 600),
    "f107_adj": (50, 600),
    "bt": (0, 200),
    "by_gsm": (-200, 200),
    "bz_gsm": (-200, 200),
    "bz_min": (-200, 200),
    "speed": (150, 3000),
    "speed_max": (150, 3000),
    "density": (0, 300),
}

STAMP_RE = re.compile(r"_(\d{8}T\d{6}Z)\.")


def collected_at(path: Path) -> pd.Timestamp:
    """Horodatage de collecte inscrit dans le nom du fichier brut (…_AAAAMMJJTHHMMSSZ.ext)."""
    match = STAMP_RE.search(path.name)
    if not match:
        raise ValueError(f"nom de fichier sans horodatage : {path.name}")
    return pd.Timestamp(match.group(1).rstrip("Z")).tz_localize("UTC")


def latest_collection(paths: list[Path]) -> list[Path]:
    """Parmi des fichiers rangés par date de collecte (…/AAAA/MM/JJ/fichier), garde pour
    chaque nom de fichier la collecte la plus récente."""
    best: dict[str, Path] = {}
    for p in sorted(paths):
        best[p.name] = p  # tri par chemin = tri par date de collecte
    return sorted(best.values())


def apply_bounds(df: pd.DataFrame) -> dict[str, int]:
    """Remplace par NaN les valeurs hors bornes ; renvoie le nombre de valeurs écartées."""
    removed = {}
    for col, (lo, hi) in BOUNDS.items():
        if col in df.columns:
            bad = df[col].notna() & ~df[col].between(lo, hi)
            if bad.any():
                removed[col] = int(bad.sum())
                df.loc[bad, col] = np.nan
    return removed


def quality(df: pd.DataFrame, time_col: str, freq: str, extra: dict | None = None) -> dict:
    """Rapport de qualité d'une table propre."""
    if df.empty:
        return {"rows": 0, **(extra or {})}
    start, end = df[time_col].min(), df[time_col].max()
    expected = len(pd.date_range(start, end, freq=freq))
    values = [c for c in df.columns if c not in (time_col, "source", "status", "interpolated")
              and pd.api.types.is_numeric_dtype(df[c])]
    return {
        "rows": len(df),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "expected_rows": expected,
        "missing_periods": expected - df[time_col].nunique(),
        "missing_share": {c: round(float(df[c].isna().mean()), 4) for c in values},
        "by_source": {str(k): int(v) for k, v in df["source"].value_counts().items()}
        if "source" in df else {},
        **(extra or {}),
    }
