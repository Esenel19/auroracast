"""Chargement des tables propres (Parquet, branche clean-data) dans l'entrepôt PostgreSQL.

Base cible : variable d'environnement DATABASE_URL, par exemple
    postgresql://auroracast:<mot de passe>@localhost:5432/auroracast     (Docker, sur le PC)
    postgresql://<utilisateur>:<mot de passe>@<hôte>.neon.tech/auroracast?sslmode=require  (cloud)

Principe : rechargement complet et atomique. Dans une seule transaction, le schéma est
appliqué (sql/schema.sql, rejouable), toutes les tables sont vidées puis remplies par COPY
(le moyen le plus rapide d'insérer dans PostgreSQL). Si une étape échoue, rien n'est
modifié : la base garde le chargement précédent. Les tables propres étant recalculées en
entier chaque jour, c'est plus simple et plus sûr qu'un chargement incrémental.

Utilisation :
    python -m auroracast.warehouse.load --clean data/clean
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from auroracast.data import load as load_table

log = logging.getLogger("auroracast.warehouse")

SCHEMA = Path(__file__).resolve().parents[3] / "sql" / "schema.sql"
NAMES = {"tromso": "Tromsø", "kiruna": "Kiruna", "rovaniemi": "Rovaniemi"}

SOURCES = [  # (code, nom, technique, licence, url)
    ("gfz", "GFZ Potsdam, indice Kp depuis 1932", "fichier", "CC BY 4.0 (taches solaires : CC BY-NC 4.0)",
     "https://kp.gfz.de/en/data"),
    ("noaa", "NOAA SWPC, Kp et prévisions", "api", "Domaine public (citer NOAA SWPC)",
     "https://services.swpc.noaa.gov/"),
    ("noaa_rtsw", "NOAA SWPC, vent solaire temps réel (RTSW)", "api", "Domaine public (citer NOAA SWPC)",
     "https://services.swpc.noaa.gov/json/rtsw/"),
    ("omni", "OMNIWeb (NASA SPDF), vent solaire horaire", "crawling", "Citer OMNI, DOI 10.48322/1shr-ht18",
     "https://spdf.gsfc.nasa.gov/pub/data/omni/low_res_omni/"),
    ("ghcnh", "NOAA GHCNh, observations horaires des stations", "api", "CC0 (DOI 10.25921/jp3d-3v19)",
     "https://www.ncei.noaa.gov/products/global-historical-climatology-network-hourly"),
    ("gsod", "NOAA GSOD via BigQuery, météo quotidienne", "sql", "Domaine public, sans garantie (citer NCEI)",
     "https://console.cloud.google.com/marketplace/product/noaa-public/gsod"),
    ("metno", "MET Norway, Locationforecast 2.0", "api", "CC BY 4.0 (citer MET Norway)",
     "https://api.met.no/weatherapi/locationforecast/2.0/documentation"),
    ("calcul", "Calcul AuroraCast (PyEphem)", "calcul", "Sans objet (PyEphem : MIT)",
     "https://rhodesmill.org/pyephem/"),
    ("absent", "Aucune observation pour cette période", "aucune", "Sans objet", None),
    ("interpolated", "Valeur interpolée (trou de 2 h au plus)", "calcul", "Sans objet", None),
]
SOURCE_ID = {code: i + 1 for i, (code, *_) in enumerate(SOURCES)}

# Ordre de vidage et de remplissage (faits avant dimensions pour vider, l'inverse pour remplir)
DIMENSIONS = ["dim_location", "dim_time", "dim_source"]
FACTS = ["fact_kp", "fact_kp_forecast", "fact_solar_daily", "fact_solar_wind", "fact_weather_observed",
         "fact_weather_daily", "fact_weather_forecast", "fact_astronomy"]


def _utc(series: pd.Series) -> pd.Series:
    s = pd.to_datetime(series, utc=True)
    return s.dt.tz_convert("UTC")


def _source(series: pd.Series) -> pd.Series:
    unknown = set(series.dropna().unique()) - set(SOURCE_ID)
    if unknown:
        raise ValueError(f"source inconnue de dim_source : {sorted(unknown)}")
    return series.map(SOURCE_ID).astype("Int16")


def build_frames(clean_dir: Path) -> dict[str, pd.DataFrame]:
    """Les tables de l'entrepôt, prêtes à charger, à partir des tables propres."""
    t = {name: load_table(name, clean_dir) for name in
         ("locations", "kp_3h", "kp_forecast", "solar_daily", "solar_wind_hourly", "cloud_hourly",
          "weather_hourly", "weather_daily", "weather_forecast", "astronomy_hourly")}
    loc = t["locations"].reset_index(drop=True)
    loc_id = {code: i + 1 for i, code in enumerate(loc["location"])}
    frames: dict[str, pd.DataFrame] = {}
    frames["dim_location"] = pd.DataFrame({
        "location_id": loc["location"].map(loc_id), "code": loc["location"],
        "name": loc["location"].map(NAMES).fillna(loc["location"]), "lat": loc["lat"], "lon": loc["lon"],
        "station_id": loc["station_id"], "station_name": loc["station_name"],
        "station_distance_km": loc["station_distance_km"]})
    frames["dim_source"] = pd.DataFrame(SOURCES, columns=["code", "name", "technique", "licence", "url"]) \
        .assign(source_id=lambda d: d["code"].map(SOURCE_ID))[["source_id", "code", "name", "technique", "licence", "url"]]

    kp = t["kp_3h"]
    frames["fact_kp"] = pd.DataFrame({"time_utc": _utc(kp["time"]), "kp": kp["kp"], "ap": kp["ap"],
                                      "status": kp["status"], "source_id": _source(kp["source"])})
    kf = t["kp_forecast"]
    frames["fact_kp_forecast"] = pd.DataFrame({
        "issued_at": _utc(kf["issued_at"]), "target_time": _utc(kf["target_time"]), "kp": kf["kp"],
        "noaa_scale": kf.get("noaa_scale"), "lead_hours": kf["lead_hours"],
        "source_id": _source(kf["source"].fillna("noaa"))})
    sd = t["solar_daily"]
    frames["fact_solar_daily"] = pd.DataFrame({
        "time_utc": _utc(sd["date"]).dt.floor("D"), "ap_daily": sd["Ap"], "sunspot_number": sd["sn"],
        "f107_obs": sd["f107_obs"], "f107_adj": sd["f107_adj"], "sunspot_status": sd["sn_status"],
        "source_id": _source(sd["source"])})
    sw = t["solar_wind_hourly"]
    frames["fact_solar_wind"] = pd.DataFrame({
        "time_utc": _utc(sw["time"]), **{c: sw[c] for c in ("bt", "by_gsm", "bz_gsm", "bz_min", "speed",
                                                               "speed_max", "density")},
        "interpolated": sw["interpolated"].astype(bool), "source_id": _source(sw["source"])})

    clouds = t["cloud_hourly"][["location", "time", "oktas", "cloud_cover_pct", "obscured", "n_obs", "source"]]
    wh = t["weather_hourly"].drop(columns=["station_id", "offset_min"])
    obs = wh.merge(clouds, on=["location", "time"], how="outer", suffixes=("", "_cloud"))
    obs["n_obs"] = obs[["n_obs", "n_obs_cloud"]].max(axis=1).fillna(0).astype(int)
    has_obs = (obs["source"] == "ghcnh") | (obs["source_cloud"] == "ghcnh")
    frames["fact_weather_observed"] = pd.DataFrame({
        "location_id": obs["location"].map(loc_id).astype("Int16"), "time_utc": _utc(obs["time"]),
        **{c: obs[c] for c in ("oktas", "cloud_cover_pct", "obscured", "temp_c", "dewpoint_c",
                               "rel_humidity_pct", "pressure_hpa", "wind_speed_ms", "wind_dir_deg",
                               "visibility_km", "visibility_capped", "fog", "mist", "present_weather")},
        "n_obs": obs["n_obs"], "source_id": _source(has_obs.map({True: "ghcnh", False: "absent"}))})

    wd = t["weather_daily"]
    frames["fact_weather_daily"] = pd.DataFrame({
        "location_id": wd["location"].map(loc_id).astype("Int16"), "time_utc": _utc(wd["day"]),
        **{c: wd[c] for c in ("station_id", "temp_c", "temp_max_c", "temp_min_c", "dewpoint_c",
                              "rel_humidity_pct", "visibility_km", "wind_mean_ms", "wind_max_ms",
                              "precip_mm", "precip_flag", "precip_suspect", "fog", "rain", "snow",
                              "hail", "thunder")},
        "source_id": _source(wd["source"])})
    wf = t["weather_forecast"]
    frames["fact_weather_forecast"] = pd.DataFrame({
        "location_id": wf["location"].map(loc_id).astype("Int16"), "issued_at": _utc(wf["issued_at"]),
        "target_time": _utc(wf["target_time"]),
        **{c: wf[c] for c in ("lead_hours", "step_hours", "cloud_cover_pct", "cloud_low_pct",
                              "cloud_medium_pct", "cloud_high_pct", "fog_pct", "temp_c", "dewpoint_c",
                              "rel_humidity_pct", "pressure_hpa", "wind_speed_ms", "wind_dir_deg",
                              "wind_gust_ms", "precip_mm", "precip_probability_pct", "symbol_code")},
        "source_id": SOURCE_ID["metno"]})
    ah = t["astronomy_hourly"]
    frames["fact_astronomy"] = pd.DataFrame({
        "location_id": ah["location"].map(loc_id).astype("Int16"), "time_utc": _utc(ah["time"]),
        **{c: ah[c] for c in ("sun_alt_deg", "night_phase", "moon_alt_deg", "moon_illum_pct", "moon_up",
                              "polar_night", "midnight_sun")},
        "source_id": _source(ah["source"])})

    # Dimension temps : toutes les heures entre la plus ancienne et la plus récente utilisée
    times = pd.concat([f[c] for f in frames.values() for c in ("time_utc", "target_time") if c in f])
    hours = pd.date_range(times.min().floor("h"), times.max().ceil("h"), freq="1h", tz="UTC")
    frames["dim_time"] = pd.DataFrame({
        "time_utc": hours, "date_utc": hours.date, "year": hours.year, "month": hours.month,
        "day": hours.day, "hour": hours.hour, "day_of_year": hours.dayofyear,
        "iso_week": hours.isocalendar().week.to_numpy(), "aurora_season": hours.month.isin([9, 10, 11, 12, 1, 2, 3])})
    return frames


def _copy(cur, table: str, df: pd.DataFrame) -> None:
    """COPY d'un DataFrame (format CSV ; les valeurs vides deviennent NULL)."""
    buf = io.StringIO()
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S+00")
    out.to_csv(buf, index=False, header=False, na_rep="")
    buf.seek(0)
    cols = ", ".join(df.columns)
    with cur.copy(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, NULL '')") as cp:
        while chunk := buf.read(1 << 20):
            cp.write(chunk)


def load(clean_dir: Path, database_url: str, connect=None) -> dict:
    import psycopg  # import différé : les autres modules n'en ont pas besoin

    started = datetime.now(UTC)
    t0 = time.monotonic()
    frames = build_frames(clean_dir)
    connect = connect or psycopg.connect
    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'UTC'")
            cur.execute(SCHEMA.read_text(encoding="utf-8"))
            cur.execute("TRUNCATE " + ", ".join(FACTS + DIMENSIONS))
            for table in DIMENSIONS + FACTS:
                _copy(cur, table, frames[table])
                log.info("%s : %d lignes", table, len(frames[table]))
            # taille des tables du schéma (pg_database_size compterait aussi, pendant la
            # transaction, l'ancienne version des tables vidées : environ le double)
            cur.execute("SELECT coalesce(sum(pg_total_relation_size(c.oid)), 0) / 1048576.0 FROM pg_class c "
                        "WHERE c.relkind = 'r' AND c.relnamespace = 'public'::regnamespace")
            size_mb = round(float(cur.fetchone()[0]), 1)
            rows = {k: len(v) for k, v in frames.items()}
            cur.execute("INSERT INTO load_run (started_at, finished_at, rows_loaded, database_mb) "
                        "VALUES (%s, now(), %s, %s)", (started, json.dumps(rows), size_mb))
        conn.commit()
    return {"rows": rows, "database_mb": size_mb, "seconds": round(time.monotonic() - t0, 1)}


def to_markdown(result: dict) -> str:
    lines = ["## Chargement de l'entrepôt PostgreSQL", "",
             f"Taille de la base : {result['database_mb']} Mo ; durée : {result['seconds']} s.", "",
             "| Table | Lignes |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in result["rows"].items()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chargement de l'entrepôt PostgreSQL")
    parser.add_argument("--clean", type=Path, default=Path("data/clean"), help="dossier des tables propres")
    parser.add_argument("--summary", type=Path, help="fichier Markdown où écrire le résumé")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from dotenv import load_dotenv

    load_dotenv()  # lit DATABASE_URL dans le fichier .env s'il existe (sur le PC)
    url = os.getenv("DATABASE_URL")
    if not url:
        print("Variable DATABASE_URL absente (voir .env.example)", file=sys.stderr)
        return 2
    result = load(args.clean, url)
    md = to_markdown(result)
    print(md)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
