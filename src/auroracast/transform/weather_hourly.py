"""Table propre de météo horaire observée (NOAA GHCNh) et contrôle croisé avec GSOD.

  weather_hourly  une ligne par lieu et par heure UTC : température, point de rosée,
                  humidité, pression, vent, visibilité, brouillard et brume

Mêmes fichiers bruts que la nébulosité (cloud_hourly) : fichiers annuels du backfill et
mises à jour quotidiennes (lignes nouvelles seulement). GHCNh continue d'être publié,
contrairement à GSOD (figé au 24/08/2025) : cette table prolonge la météo jusqu'à hier.

Règles de nettoyage :
  - chaque valeur est rattachée à l'heure pile la plus proche (observation de 01:50 ->
    02:00) ; pour chaque variable, on garde l'observation la plus proche de l'heure pile
    qui renseigne cette variable ; en cas de doublon d'observation, la collecte la plus
    récente l'emporte ;
  - code qualité de la source (colonne <variable>_Quality_Code, convention NOAA ISD) :
    2, 3, 6 et 7 (suspect ou erroné) et les lettres de contrôle GHCNh -> valeur écartée
    (NaN) et comptée ; 0, 1, 4, 5, 9 ou vide -> valeur gardée ;
  - bornes physiques (HOURLY_BOUNDS) : hors bornes -> NaN, compté ;
  - temps présent (codes WMO, colonnes pres_wx_MW* manuelles et pres_wx_AW*
    automatiques) : brouillard = abréviation contenant « FG » ou code manuel 40 à 49 ou
    automatique 30 à 35 ; brume = « BR » ou code 10. Une observation avec visibilité mais
    sans phénomène signalé vaut « pas de brouillard » ; sans visibilité, c'est inconnu ;
  - visibilité plafonnée par certaines stations : les messages aéronautiques (METAR,
    aéroport de Kiruna) s'arrêtent à 10 km (« 10 km ou plus ») ; Tromsø et Rovaniemi
    vont jusqu'à 50-75 km. Colonne visibility_capped = vrai quand la valeur est un plafond ;
  - direction du vent 999 = vent calme ou variable (code NOAA, vent faible) : direction
    mise à NaN et comptée à part, pas comme une erreur ;
  - précipitations non retenues : absentes à Kiruna et Rovaniemi, cumuls sur des durées
    variables à Tromsø ;
  - grille horaire continue par lieu, rien n'est interpolé.
"""

from __future__ import annotations

import gzip
import io
from pathlib import Path

import numpy as np
import pandas as pd

from auroracast.transform.common import latest_collection
from auroracast.transform.weather import FILE_RE

VARIABLES = {  # colonne GHCNh -> colonne de la table propre
    "temperature": "temp_c",
    "dew_point_temperature": "dewpoint_c",
    "relative_humidity": "rel_humidity_pct",
    "sea_level_pressure": "pressure_hpa",
    "wind_speed": "wind_speed_ms",
    "wind_direction": "wind_dir_deg",
    "visibility": "visibility_km",
}
WX_COLUMNS = [f"pres_wx_{kind}{i}" for kind in ("MW", "AW") for i in (1, 2, 3)]
BAD_QUALITY = {"2", "3", "6", "7"}
HOURLY_BOUNDS = {
    "temp_c": (-60, 40), "dewpoint_c": (-70, 35), "rel_humidity_pct": (0, 100),
    "pressure_hpa": (870, 1085), "wind_speed_ms": (0, 75), "wind_dir_deg": (0, 360),
    "visibility_km": (0, 100),
}
METAR_CAP_KM = 9.999  # « 10 km ou plus » dans un message METAR

COLUMNS = ["location", "time", "temp_c", "dewpoint_c", "rel_humidity_pct", "pressure_hpa",
           "wind_speed_ms", "wind_dir_deg", "visibility_km", "visibility_capped", "fog", "mist",
           "present_weather", "n_obs", "offset_min", "station_id", "source"]


def wx_flags(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(brouillard, brume, codes bruts) par observation, à partir des colonnes de temps
    présent (format « ABR:code », ex. « FG:48 », ou code seul, ex. « 10 »)."""
    fog = pd.Series(False, index=df.index)
    mist = pd.Series(False, index=df.index)
    joined = pd.Series("", index=df.index)
    for col in (c for c in WX_COLUMNS if c in df):
        raw = df[col].fillna("").str.strip()
        abbr = raw.str.extract(r"^([^:]*):", expand=False).fillna("")
        code = pd.to_numeric(raw.str.extract(r"(\d+)$", expand=False), errors="coerce")
        fog_code = code.between(40, 49) if "_MW" in col else code.between(30, 35)
        fog |= abbr.str.contains("FG") | fog_code
        mist |= abbr.str.contains("BR") | (code == 10)
        joined = joined.str.cat(raw, sep=";")
    joined = joined.str.replace(r";{2,}", ";", regex=True).str.strip(";")
    return fog, mist, joined.where(joined != "")


def read_observations(text: str) -> tuple[pd.DataFrame, dict]:
    """Observations d'un fichier GHCNh : une ligne par observation, valeurs nettoyées."""
    wanted = {"DATE", *VARIABLES, *(f"{v}_Quality_Code" for v in VARIABLES), *WX_COLUMNS}
    df = pd.read_csv(io.StringIO(text), sep="|", dtype=str, usecols=lambda c: c in wanted,
                     engine="c")
    out = pd.DataFrame({"obs_time": pd.to_datetime(df["DATE"].str[:19], utc=True, errors="coerce")})
    rejected = {}
    for src, dst in VARIABLES.items():
        values = pd.to_numeric(df[src], errors="coerce") if src in df else pd.Series(np.nan, index=df.index)
        qc_col = f"{src}_Quality_Code"
        if qc_col in df:
            qc = df[qc_col].fillna("").str.strip()
            bad = values.notna() & (qc.isin(BAD_QUALITY) | qc.str.contains(r"[A-Za-z]", regex=True))
            if bad.any():
                rejected[dst] = int(bad.sum())
                values = values.mask(bad)
        if dst == "wind_dir_deg":
            variable = values == 999
            if variable.any():
                rejected["wind_dir_calm_or_variable"] = int(variable.sum())
                values = values.mask(variable)
        out[dst] = values
    fog, mist, codes = wx_flags(df)
    known = out["visibility_km"].notna()  # bulletin complet : l'absence de phénomène compte
    out["fog"] = fog.astype("boolean").where(fog | known)
    out["mist"] = mist.astype("boolean").where(mist | known)
    out["present_weather"] = codes
    return out.dropna(subset=["obs_time"]), rejected


def to_hourly_multi(obs: pd.DataFrame) -> pd.DataFrame:
    """Pour chaque variable, l'observation la plus proche de l'heure pile qui la renseigne."""
    obs = obs.copy()
    obs["time"] = obs["obs_time"].dt.round("1h")
    obs["offset_min"] = ((obs["obs_time"] - obs["time"]).dt.total_seconds() / 60).astype(int)
    obs["abs_offset"] = obs["offset_min"].abs()
    obs = obs.sort_values(["time", "abs_offset", "obs_time"])
    base = obs.groupby("time").size().rename("n_obs").to_frame()
    first = obs.drop_duplicates("time").set_index("time")
    base["offset_min"] = first["offset_min"]
    for col in VARIABLES.values():
        sub = obs[obs[col].notna()].drop_duplicates("time").set_index("time")
        base[col] = sub[col]
        if col == "visibility_km":  # brouillard et brume : même observation que la visibilité
            for flag in ("fog", "mist", "present_weather"):
                base[flag] = sub[flag]
    return base.reset_index()


def weather_hourly(raw_dir: Path) -> tuple[pd.DataFrame, dict]:
    files = latest_collection(list((raw_dir / "ghcnh" / "hourly").glob("**/GHCNh_*.psv*")))
    frames, meta = [], {"files": len(files), "locations": {}}
    for loc in sorted({p.parent.name for p in files}):
        loc_files = [p for p in files if p.parent.name == loc]
        parts, rejected, station = [], {}, None
        for p in loc_files:  # du plus ancien au plus récent (tri par date de collecte)
            m = FILE_RE.search(p.name)
            station = m.group("station") if m else station
            data = p.read_bytes()
            text = (gzip.decompress(data) if p.suffix == ".gz" else data).decode("utf-8", errors="replace")
            part, rej = read_observations(text)
            parts.append(part)
            for k, v in rej.items():
                rejected[k] = rejected.get(k, 0) + v
        obs = pd.concat(parts, ignore_index=True)
        before = len(obs)
        obs = obs.drop_duplicates("obs_time", keep="last")
        out_of_bounds = {}
        for col, (lo, hi) in HOURLY_BOUNDS.items():
            bad = obs[col].notna() & ~obs[col].between(lo, hi)
            if bad.any():
                out_of_bounds[col] = int(bad.sum())
                obs.loc[bad, col] = np.nan
        hourly = to_hourly_multi(obs)
        grid = pd.DataFrame({"time": pd.date_range(hourly["time"].min(), hourly["time"].max(), freq="1h")})
        hourly = grid.merge(hourly, on="time", how="left")
        hourly["location"], hourly["station_id"] = loc, station
        frames.append(hourly)
        meta["locations"][loc] = {"observations": before, "duplicate_observations": before - len(obs),
                                  "wind_dir_calm_or_variable": rejected.pop("wind_dir_calm_or_variable", 0),
                                  "rejected_quality": rejected, "out_of_bounds": out_of_bounds}
    if not frames:
        return pd.DataFrame(columns=COLUMNS), meta
    df = pd.concat(frames, ignore_index=True)
    df["n_obs"] = df["n_obs"].fillna(0).astype(int)
    df["offset_min"] = df["offset_min"].astype("Int64")
    for col in ("fog", "mist"):
        df[col] = df[col].astype("boolean")
    df["visibility_capped"] = (df["visibility_km"].round(3) == METAR_CAP_KM).astype("boolean")
    df.loc[df["visibility_km"].isna(), "visibility_capped"] = pd.NA
    df["source"] = np.where(df["n_obs"] > 0, "ghcnh", "absent")
    return df[COLUMNS], meta


def hourly_quality(df: pd.DataFrame, meta: dict) -> dict:
    report = {"files": meta.get("files", 0), "locations": {}}
    values = list(VARIABLES.values())
    for loc, g in df.groupby("location"):
        vis = g["visibility_km"].notna()
        report["locations"][loc] = {
            "station_id": g["station_id"].iloc[0],
            "start": g["time"].min().isoformat(), "end": g["time"].max().isoformat(),
            "hours": len(g), "hours_without_obs": int((g["source"] == "absent").sum()),
            "present_share": {c: round(float(g[c].notna().mean()), 4) for c in values},
            "fog_hours": int(g["fog"].fillna(False).sum()),
            "visibility_capped_share": round(float(g.loc[vis, "visibility_capped"].mean()), 4) if vis.any() else None,
            **meta.get("locations", {}).get(loc, {}),
        }
    return report


def crosscheck_gsod(hourly: pd.DataFrame, daily: pd.DataFrame, min_hours: int = 18) -> dict:
    """Contrôle croisé : moyenne quotidienne de la température recalculée depuis GHCNh
    (jours avec au moins min_hours heures) comparée à la moyenne publiée par GSOD."""
    if hourly.empty or daily.empty:
        return {"error": "table manquante"}
    h = hourly[hourly["temp_c"].notna()].copy()
    h["day"] = h["time"].dt.date
    per_day = h.groupby(["location", "day"])["temp_c"].agg(["mean", "size"]).reset_index()
    per_day = per_day[per_day["size"] >= min_hours]
    d = daily[["location", "day", "temp_c", "fog"]].copy()
    d["day"] = pd.to_datetime(d["day"]).dt.date
    merged = per_day.merge(d, on=["location", "day"], how="inner").dropna(subset=["temp_c"])
    fog_h = hourly.assign(day=hourly["time"].dt.date).groupby(["location", "day"])["fog"].any()
    report = {"rule": f"jours avec au moins {min_hours} heures de température", "locations": {}}
    for loc, g in merged.groupby("location"):
        diff = g["mean"] - g["temp_c"]
        fog = g.set_index("day")["fog"].astype("boolean")
        fog_ghcnh = fog_h.loc[loc].reindex(fog.index)
        both = fog.notna() & fog_ghcnh.notna()
        report["locations"][loc] = {
            "days_compared": len(g),
            "temp_mean_abs_diff_c": round(float(diff.abs().mean()), 3),
            "temp_bias_c": round(float(diff.mean()), 3),
            "temp_days_within_0_5c": round(float((diff.abs() <= 0.5).mean()), 4),
            "fog_day_agreement": round(float((fog[both] == fog_ghcnh[both]).mean()), 4) if both.any() else None,
        }
    return report


def build(raw_dir: Path) -> tuple[dict[str, pd.DataFrame], dict]:
    df, meta = weather_hourly(raw_dir)
    return {"weather_hourly": df}, {"weather_hourly": hourly_quality(df, meta) if len(df) else meta}
