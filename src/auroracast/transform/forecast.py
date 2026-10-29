"""Table propre des prévisions météo locales (MET Norway, Locationforecast 2.0 complete).

  weather_forecast  une ligne par lieu, par prévision émise (issued_at) et par heure visée
                    (target_time) : nébulosité totale et par étage, brouillard, température,
                    point de rosée, humidité, pression, vent, rafales, précipitations

Toutes les prévisions collectées sont gardées (une par passage de collecte et par mise à
jour du modèle) : c'est ce qui permettra de comparer, plus tard, ce qui était prévu à ce qui
a été observé (weather_hourly, cloud_hourly), et de mesurer la qualité de la prévision selon
l'échéance (lead_hours).

Règles :
  - issued_at = meta.updated_at (heure de mise à jour du modèle), collected_at = horodatage
    de la collecte ; lead_hours = target_time - issued_at, en heures ;
  - step_hours = 1 pour les pas horaires (environ 50 à 60 premières heures, bloc
    next_1_hours), 6 au-delà (bloc next_6_hours) ; les précipitations sont celles du pas ;
  - le brouillard (fog_pct), les rafales et l'étage des nuages ne sont donnés qu'à courte
    échéance (moins de 60 h environ) : vides au-delà ;
  - bornes : pourcentages entre 0 et 100, sinon NaN (compté) ;
  - doublons (même lieu, même issued_at, même target_time, collectés deux fois) : la collecte
    la plus récente l'emporte.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from auroracast.transform.common import collected_at

INSTANT = {  # variable MET Norway -> colonne de la table propre
    "cloud_area_fraction": "cloud_cover_pct",
    "cloud_area_fraction_low": "cloud_low_pct",
    "cloud_area_fraction_medium": "cloud_medium_pct",
    "cloud_area_fraction_high": "cloud_high_pct",
    "fog_area_fraction": "fog_pct",
    "air_temperature": "temp_c",
    "dew_point_temperature": "dewpoint_c",
    "relative_humidity": "rel_humidity_pct",
    "air_pressure_at_sea_level": "pressure_hpa",
    "wind_speed": "wind_speed_ms",
    "wind_from_direction": "wind_dir_deg",
    "wind_speed_of_gust": "wind_gust_ms",
}
PERCENT = [c for c in INSTANT.values() if c.endswith("_pct")]
COLUMNS = ["location", "issued_at", "collected_at", "target_time", "lead_hours", "step_hours",
           *INSTANT.values(), "precip_mm", "precip_probability_pct", "symbol_code", "source_file"]


def parse_forecast(data: dict) -> list[dict]:
    """Une ligne par pas de temps d'une réponse Locationforecast."""
    props = data["properties"]
    issued = props["meta"]["updated_at"]
    rows = []
    for step in props["timeseries"]:
        d = step.get("data", {})
        details = d.get("instant", {}).get("details", {})
        period, hours = ((d["next_1_hours"], 1) if "next_1_hours" in d
                         else (d["next_6_hours"], 6) if "next_6_hours" in d else ({}, None))
        if hours is None:
            continue  # dernier pas sans période : pas de précipitation ni de symbole
        row = {"issued_at": issued, "target_time": step["time"], "step_hours": hours}
        for src, dst in INSTANT.items():
            row[dst] = details.get(src)
        row["precip_mm"] = period.get("details", {}).get("precipitation_amount")
        row["precip_probability_pct"] = period.get("details", {}).get("probability_of_precipitation")
        row["symbol_code"] = period.get("summary", {}).get("symbol_code")
        rows.append(row)
    return rows


def weather_forecast(raw_dir: Path) -> tuple[pd.DataFrame, dict]:
    files = sorted((raw_dir / "metno" / "locationforecast").glob("**/complete_*.json.gz"))
    rows, errors = [], []
    for path in files:
        try:
            data = json.loads(gzip.decompress(path.read_bytes()))
            for row in parse_forecast(data):
                row.update(location=path.parent.name, collected_at=collected_at(path),
                           source_file=path.relative_to(raw_dir).as_posix())
                rows.append(row)
        except (OSError, ValueError, KeyError) as exc:
            errors.append(f"{path.name} : {exc}")
    meta = {"files": len(files), "unreadable_files": errors}
    if not rows:
        return pd.DataFrame(columns=COLUMNS), meta
    df = pd.DataFrame(rows)
    for col in ("issued_at", "target_time"):
        df[col] = pd.to_datetime(df[col], utc=True)
    df["lead_hours"] = ((df["target_time"] - df["issued_at"]).dt.total_seconds() / 3600).round(2)
    out_of_bounds = {}
    for col in PERCENT + ["precip_probability_pct"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        bad = df[col].notna() & ~df[col].between(0, 100)
        if bad.any():
            out_of_bounds[col] = int(bad.sum())
            df.loc[bad, col] = np.nan
    before = len(df)
    df = (df.sort_values(["location", "issued_at", "target_time", "collected_at"])
            .drop_duplicates(["location", "issued_at", "target_time"], keep="last"))
    meta.update(out_of_bounds=out_of_bounds, duplicate_rows=before - len(df))
    df["step_hours"] = df["step_hours"].astype(int)
    return df[COLUMNS].reset_index(drop=True), meta


def forecast_quality(df: pd.DataFrame, meta: dict) -> dict:
    report = {**meta, "locations": {}}
    for loc, g in df.groupby("location"):
        latest = g[g["issued_at"] == g["issued_at"].max()]
        hourly = latest[latest["step_hours"] == 1]
        report["locations"][loc] = {
            "forecasts": int(g["issued_at"].nunique()),
            "first_issue": g["issued_at"].min().isoformat(),
            "last_issue": g["issued_at"].max().isoformat(),
            "latest_hourly_steps": len(hourly),
            "latest_until": latest["target_time"].max().isoformat(),
            "latest_fog_steps": int(latest["fog_pct"].notna().sum()),
        }
    return report


def build(raw_dir: Path) -> tuple[dict[str, pd.DataFrame], dict]:
    df, meta = weather_forecast(raw_dir)
    return {"weather_forecast": df}, {"weather_forecast": forecast_quality(df, meta) if len(df) else meta}
