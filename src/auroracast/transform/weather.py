"""Tables propres de météo locale : nébulosité observée (NOAA GHCNh) et lieux cibles.

  locations    une ligne par lieu cible : coordonnées, station météo retenue, distance
  cloud_hourly une ligne par lieu et par heure UTC : couverture nuageuse (octas et %),
               ciel invisible (brouillard), nombre d'observations, écart à l'heure pile

Règles propres à la nébulosité :
  - chaque observation est rattachée à l'heure pile la plus proche (une observation de
    01:50 décrit 02:00) ; s'il y en a plusieurs, on garde la plus proche de l'heure pile ;
  - couverture = couche la plus couverte (sky_cover_layer_1 à 4), sinon sky_condition ;
    « VV:09 » (ciel invisible) compte 8 octas et lève le drapeau « obscured » ;
  - grille horaire continue par lieu ; une heure sans observation reste vide (NaN) :
    la nébulosité n'est JAMAIS interpolée (elle change trop vite d'une heure à l'autre).
"""

from __future__ import annotations

import csv
import gzip
import io
import re
from pathlib import Path

import numpy as np
import pandas as pd

from auroracast.collect import ghcnh as ghcnh_raw
from auroracast.locations import LOCATIONS
from auroracast.transform.common import latest_collection

# Fichier annuel complet (backfill) ou mise à jour quotidienne (lignes nouvelles seulement)
FILE_RE = re.compile(r"GHCNh_(?P<station>[^_]+)_(?P<year>\d{4})(_maj_\d{8}T\d{6}Z)?\.psv(\.gz)?$")


def locations_table(config: dict | None = None) -> pd.DataFrame:
    config = ghcnh_raw.load_stations_config() if config is None else config
    rows = []
    for loc in LOCATIONS:
        st = config.get(loc.name) or {}
        rows.append({"location": loc.name, "lat": loc.lat, "lon": loc.lon,
                     "station_id": st.get("station_id"), "station_name": st.get("name"),
                     "station_distance_km": st.get("distance_km")})
    return pd.DataFrame(rows)


def _observations(text: str) -> pd.DataFrame:
    """Observations de nuages d'un fichier annuel : heure, octas, ciel invisible."""
    rows = []
    for row in csv.DictReader(io.StringIO(text), delimiter="|"):
        oktas = ghcnh_raw.total_oktas(row)
        date = (row.get("DATE") or "").strip()
        if oktas is None or not date:
            continue
        layers = " ".join(row.get(c) or "" for c in ghcnh_raw.LAYER_COLUMNS)
        obscured = "VV:" in layers or (row.get("sky_condition") or "").startswith("09")
        rows.append((date[:19], oktas, obscured))
    df = pd.DataFrame(rows, columns=["obs_time", "oktas", "obscured"])
    df["obs_time"] = pd.to_datetime(df["obs_time"], utc=True, errors="coerce")
    return df.dropna(subset=["obs_time"])


def to_hourly(obs: pd.DataFrame) -> pd.DataFrame:
    """Rattache chaque observation à l'heure pile la plus proche et garde, pour chaque
    heure, l'observation la plus proche de l'heure pile."""
    if obs.empty:
        return pd.DataFrame(columns=["time", "oktas", "obscured", "n_obs", "offset_min"])
    obs = obs.copy()
    obs["time"] = obs["obs_time"].dt.round("1h")
    obs["offset_min"] = ((obs["obs_time"] - obs["time"]).dt.total_seconds() / 60).astype(int)
    obs["abs_offset"] = obs["offset_min"].abs()
    counts = obs.groupby("time").size().rename("n_obs")
    best = obs.sort_values(["time", "abs_offset", "obs_time"]).drop_duplicates("time")
    best = best.set_index("time").join(counts).reset_index()
    return best[["time", "oktas", "obscured", "n_obs", "offset_min"]]


def cloud_hourly(raw_dir: Path) -> tuple[pd.DataFrame, dict]:
    files = latest_collection(list((raw_dir / "ghcnh" / "hourly").glob("**/GHCNh_*.psv*")))
    frames, meta = [], {"files": len(files)}
    for loc_dir in sorted({p.parent.name for p in files}):
        loc_files = [p for p in files if p.parent.name == loc_dir]
        obs = []
        station = None
        for p in loc_files:
            m = FILE_RE.search(p.name)
            station = m.group("station") if m else station
            data = p.read_bytes()
            text = (gzip.decompress(data) if p.suffix == ".gz" else data).decode(
                "utf-8", errors="replace")
            obs.append(_observations(text))
        all_obs = pd.concat(obs, ignore_index=True)
        before = len(all_obs)
        # fichiers lus du plus ancien au plus récent : en cas de doublon, la collecte la
        # plus récente l'emporte (une mise à jour peut corriger une observation)
        all_obs = all_obs.drop_duplicates("obs_time", keep="last")
        hourly = to_hourly(all_obs)
        grid = pd.DataFrame({"time": pd.date_range(hourly["time"].min(), hourly["time"].max(),
                                                   freq="1h")})
        hourly = grid.merge(hourly, on="time", how="left")
        hourly["location"] = loc_dir
        hourly["station_id"] = station
        frames.append(hourly)
        meta[loc_dir] = {"observations": before, "duplicate_observations": before - len(all_obs)}
    if not frames:
        return pd.DataFrame(), meta
    df = pd.concat(frames, ignore_index=True)
    df["n_obs"] = df["n_obs"].fillna(0).astype(int)
    df["obscured"] = df["obscured"].astype("boolean")
    df["oktas"] = df["oktas"].astype(float)
    df["cloud_cover_pct"] = (df["oktas"] / 8 * 100).round(1)
    df["source"] = np.where(df["oktas"].notna(), "ghcnh", "absent")
    cols = ["location", "time", "oktas", "cloud_cover_pct", "obscured", "n_obs", "offset_min",
            "station_id", "source"]
    return df[cols], meta


def cloud_quality(df: pd.DataFrame, meta: dict) -> dict:
    report = {"files": meta.get("files", 0), "locations": {}}
    for loc, g in df.groupby("location"):
        has = g["oktas"].notna()
        clear = (g.loc[has, "oktas"] <= 2).mean() if has.any() else None
        by_year = has.groupby(g["time"].dt.year).mean().round(3)
        report["locations"][loc] = {
            "station_id": g["station_id"].iloc[0],
            "start": g["time"].min().isoformat(),
            "end": g["time"].max().isoformat(),
            "hours": len(g),
            "hours_with_cloud": int(has.sum()),
            "cloud_share": round(float(has.mean()), 4),
            "clear_share": round(float(clear), 4) if clear is not None else None,
            "obscured_hours": int(g["obscured"].fillna(False).sum()),
            "share_by_year": {int(k): float(v) for k, v in by_year.items()},
            **meta.get(loc, {}),
        }
    return report


def build(raw_dir: Path) -> tuple[dict[str, pd.DataFrame], dict]:
    clouds, meta = cloud_hourly(raw_dir)
    tables = {"locations": locations_table(), "cloud_hourly": clouds}
    return tables, {"cloud_hourly": cloud_quality(clouds, meta) if len(clouds) else meta}
