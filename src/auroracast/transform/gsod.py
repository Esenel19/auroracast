"""Table propre de météo quotidienne observée (NOAA GSOD, collectée par SQL dans BigQuery).

  weather_daily  une ligne par lieu et par jour (UTC) : températures, point de rosée,
                 humidité relative, visibilité, vent, précipitations, phénomènes du jour

Règles de nettoyage :
  - source : le dernier passage réussi de la collecte (manifeste gsod_daily) ; chaque
    station est rattachée à son lieu grâce au manifeste ;
  - valeurs « absentes » de GSOD (9999.9, 999.9, 99.99) remplacées par NaN ;
  - unités converties en système international : °F -> °C, miles -> km, nœuds -> m/s,
    pouces -> mm ;
  - bornes physiques (WEATHER_BOUNDS) : une valeur hors bornes devient NaN et est comptée ;
  - une ligne par lieu et par jour, grille continue : un jour sans relevé reste vide
    (source = « absent »), rien n'est interpolé ;
  - colonnes écartées car trop peu renseignées (voir rapport de qualité) : rafales (gust,
    44 à 89 % absentes) et hauteur de neige (sndp, 60 à 100 % absentes) ;
  - précipitations : la lettre de qualité NOAA (flag_prcp) est gardée ; la lettre « I »
    signifie « aucun relevé de précipitation ce jour-là » (valeur 0 déduite, pas mesurée) :
    ces jours passent à NaN. Kiruna n'a QUE des « I » : ses précipitations sont inutilisables ;
  - précipitation suspecte (precip_suspect) : plus de 50 mm sans pluie, neige ni grêle
    signalée ce jour-là (cumul de plusieurs jours probable) ; signalée, non supprimée.
"""

from __future__ import annotations

import gzip
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd

# Valeurs « absentes » de GSOD (documentation NOAA GSOD)
MISSING = {"temp": 9999.9, "max": 9999.9, "min": 9999.9, "dewp": 9999.9, "visib": 999.9,
           "wdsp": 999.9, "mxpsd": 999.9, "gust": 999.9, "prcp": 99.99, "sndp": 999.9}



def F_TO_C(fahrenheit):
    return (fahrenheit - 32) * 5 / 9


MILE_KM, KNOT_MS, INCH_MM = 1.609344, 0.514444, 25.4

WEATHER_BOUNDS: dict[str, tuple[float, float]] = {
    "temp_c": (-60, 40), "temp_max_c": (-60, 40), "temp_min_c": (-60, 40),
    "dewpoint_c": (-70, 35), "visibility_km": (0, 100),
    "wind_mean_ms": (0, 75), "wind_max_ms": (0, 75), "precip_mm": (0, 300),
}

SUSPECT_PRECIP_MM = 50.0

FLAGS = {"fog": "fog", "rain_drizzle": "rain", "snow_ice_pellets": "snow", "hail": "hail",
         "thunder": "thunder"}

COLUMNS = ["location", "day", "station_id", "station_name", "temp_c", "temp_max_c",
           "temp_min_c", "dewpoint_c", "rel_humidity_pct", "visibility_km", "wind_mean_ms",
           "wind_max_ms", "precip_mm", "precip_flag", "precip_suspect", "fog", "rain", "snow", "hail", "thunder",
           "n_obs_temp", "n_obs_visib", "source"]


def latest_daily_run(raw_dir: Path) -> dict | None:
    """Dernier enregistrement « gsod_daily » réussi parmi les manifestes."""
    best = None
    for path in sorted((raw_dir / "gsod" / "manifests").glob("**/run_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("source") == "gsod_daily" and rec.get("ok") and rec.get("file"):
                best = rec
    return best


def relative_humidity(temp_c: pd.Series, dewpoint_c: pd.Series) -> pd.Series:
    """Humidité relative (%) par la formule de Magnus, à partir des moyennes du jour
    (approximation : l'humidité moyenne réelle n'est pas publiée par GSOD)."""
    a, b = 17.625, 243.04
    rh = 100 * np.exp(a * dewpoint_c / (b + dewpoint_c)) / np.exp(a * temp_c / (b + temp_c))
    return rh.clip(upper=100).round(1)


def clean(raw: pd.DataFrame, stations: dict[str, str]) -> tuple[pd.DataFrame, dict]:
    """Nettoie les relevés bruts. stations : {lieu: identifiant USAF}."""
    df = raw.copy()
    df["stn"] = df["stn"].astype(str).str.zfill(6)
    for col, fill in MISSING.items():
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce").mask(lambda s, f=fill: s >= f)
    out = pd.DataFrame({
        "station_id": df["stn"],
        "station_name": df.get("station_name"),
        "day": pd.to_datetime(df["day"]).dt.date,
        "temp_c": F_TO_C(df["temp"]),
        "temp_max_c": F_TO_C(df["max"]),
        "temp_min_c": F_TO_C(df["min"]),
        "dewpoint_c": F_TO_C(df["dewp"]),
        "visibility_km": df["visib"] * MILE_KM,
        "wind_mean_ms": df["wdsp"] * KNOT_MS,
        "wind_max_ms": df["mxpsd"] * KNOT_MS,
        "precip_mm": df["prcp"] * INCH_MM,
        "precip_flag": df["flag_prcp"].where(df["prcp"].notna()) if "flag_prcp" in df else None,
        "n_obs_temp": df.get("count_temp"),
        "n_obs_visib": df.get("count_visib"),
    })
    for raw_col, col in FLAGS.items():
        out[col] = df[raw_col].astype("boolean") if raw_col in df else pd.NA
    not_measured = out["precip_flag"] == "I"
    precip_not_measured = int(not_measured.sum())
    out.loc[not_measured, "precip_mm"] = np.nan
    removed = {}
    for col, (lo, hi) in WEATHER_BOUNDS.items():
        bad = out[col].notna() & ~out[col].between(lo, hi)
        if bad.any():
            removed[col] = int(bad.sum())
            out.loc[bad, col] = np.nan
    # cohérence : min <= moyenne <= max (tolérance 0,5 °C), sinon signalée (non corrigée)
    incoherent = int(((out["temp_min_c"] > out["temp_c"] + 0.5)
                      | (out["temp_c"] > out["temp_max_c"] + 0.5)).sum())
    wet = out["rain"].fillna(False) | out["snow"].fillna(False) | out["hail"].fillna(False)
    out["precip_suspect"] = (out["precip_mm"] > SUSPECT_PRECIP_MM) & ~wet
    out["rel_humidity_pct"] = relative_humidity(out["temp_c"], out["dewpoint_c"])
    for col in ["temp_c", "temp_max_c", "temp_min_c", "dewpoint_c", "visibility_km",
                "wind_mean_ms", "wind_max_ms", "precip_mm"]:
        out[col] = out[col].round(2)

    by_station = {v: k for k, v in stations.items()}
    out["location"] = out["station_id"].map(by_station)
    unknown = int(out["location"].isna().sum())
    out = out.dropna(subset=["location"])
    before = len(out)
    out = out.sort_values(["location", "day"]).drop_duplicates(["location", "day"])
    duplicates = before - len(out)

    frames = []
    for loc, g in out.groupby("location"):
        days = pd.date_range(min(g["day"]), max(g["day"]), freq="D").date
        grid = pd.DataFrame({"day": days})
        g = grid.merge(g, on="day", how="left")
        g["location"] = loc
        g["station_id"] = stations[loc]
        g["station_name"] = g["station_name"].dropna().iloc[0] if g["station_name"].notna().any() else None
        frames.append(g)
    table = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    table["source"] = np.where(table["temp_c"].notna() | table["visibility_km"].notna(),
                               "gsod", "absent")
    for col in ["n_obs_temp", "n_obs_visib"]:
        table[col] = table[col].astype("Int64")
    for col in [*FLAGS.values(), "precip_suspect"]:
        table[col] = table[col].astype("boolean")
    meta = {"out_of_bounds": removed, "temp_incoherent_days": incoherent,
            "precip_not_measured_days": precip_not_measured,
            "precip_suspect_days": int(out["precip_suspect"].sum()),
            "unknown_station_rows": unknown, "duplicate_rows": duplicates}
    return table[COLUMNS], meta


def weather_quality(df: pd.DataFrame, meta: dict) -> dict:
    report = {**meta, "locations": {}}
    values = ["temp_c", "dewpoint_c", "rel_humidity_pct", "visibility_km", "wind_mean_ms",
              "precip_mm"]
    for loc, g in df.groupby("location"):
        report["locations"][loc] = {
            "station_id": g["station_id"].iloc[0],
            "station_name": g["station_name"].iloc[0],
            "start": str(g["day"].min()), "end": str(g["day"].max()),
            "days": len(g), "missing_days": int((g["source"] == "absent").sum()),
            "missing_share": {c: round(float(g[c].isna().mean()), 4) for c in values},
            "fog_days": int(g["fog"].fillna(False).sum()),
        }
    return report


def build(raw_dir: Path) -> tuple[dict[str, pd.DataFrame], dict]:
    run = latest_daily_run(raw_dir)
    if run is None:
        return {}, {"weather_daily": {"rows": 0, "error": "aucune collecte GSOD réussie"}}
    data = (raw_dir / run["file"]).read_bytes()
    raw = pd.read_csv(io.BytesIO(gzip.decompress(data)), dtype={"stn": str, "wban": str})
    table, meta = clean(raw, run["stations"])
    meta.update(source_file=run["file"], collected_at=run.get("collected_at"),
                bigquery_job_id=run.get("job_id"), rows_raw=len(raw))
    return {"weather_daily": table}, {"weather_daily": weather_quality(table, meta)}
