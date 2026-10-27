"""Collecte par requêtes SQL : météo quotidienne NOAA GSOD dans Google BigQuery.

Base existante interrogée : `bigquery-public-data.noaa_gsod` (une table par année
`gsodAAAA` + une table `stations`). Elle n'est plus alimentée après le 24/08/2025 (son
jeu source NOAA « Global Hourly » est figé) : elle sert de source HISTORIQUE (visibilité,
brouillard, précipitations, neige, température, vent), jour par jour.

Deux requêtes paramétrées (jamais de valeur collée dans le texte SQL) :
  1. STATIONS : pour chaque lieu cible, les stations les plus proches, calculées dans
     BigQuery avec les fonctions géographiques (ST_GEOGPOINT, ST_DISTANCE, ST_DWITHIN) ;
  2. DONNÉES : les relevés quotidiens des stations retenues, sur plusieurs années d'un
     coup grâce à une table générique (gsod* + _TABLE_SUFFIX), avec jointure sur stations.

Bonnes pratiques de coût : chaque requête est d'abord estimée « à blanc » (dry run,
gratuit), puis exécutée avec un plafond d'octets facturés ; les octets traités, l'identifiant
du job et le texte SQL sont notés dans le manifeste.

Authentification : variable GOOGLE_APPLICATION_CREDENTIALS (fichier de clé du compte de
service) et GCP_PROJECT_ID (projet qui exécute les requêtes, en mode sandbox gratuit).

Utilisation :
    python -m auroracast.collect.gsod --start-year 2010 --end-year 2025
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import logging
import os
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from auroracast.collect.common import stamp, write_run_manifest
from auroracast.collect.noaa import DEFAULT_OUT, utc_now
from auroracast.locations import LOCATIONS, Location

log = logging.getLogger("auroracast.collect.gsod")

DATASET = "bigquery-public-data.noaa_gsod"
MAX_BYTES_BILLED = 5 * 10**9      # plafond par requête : 5 Go (quota gratuit : 1 To / mois)
MAX_KM = 30.0
LAST_YEAR = 2025                  # dernière table annuelle publiée

SQL_STATIONS = f"""
-- Stations GSOD les plus proches d'un lieu (distance géodésique calculée par BigQuery)
SELECT
  usaf,
  wban,
  name,
  country,
  lat,
  lon,
  `begin` AS first_day,
  `end`   AS last_day,
  ROUND(ST_DISTANCE(ST_GEOGPOINT(lon, lat), ST_GEOGPOINT(@lon, @lat)) / 1000, 1) AS distance_km
FROM `{DATASET}.stations`
WHERE lat IS NOT NULL
  AND lon IS NOT NULL
  AND ST_DWITHIN(ST_GEOGPOINT(lon, lat), ST_GEOGPOINT(@lon, @lat), @max_m)
ORDER BY distance_km
LIMIT 10
"""

SQL_DAILY = f"""
-- Relevés quotidiens des stations retenues, plusieurs années en une requête
SELECT
  g.stn,
  g.wban,
  s.name AS station_name,
  DATE(CAST(g.year AS INT64), CAST(g.mo AS INT64), CAST(g.da AS INT64)) AS day,
  g.temp, g.count_temp, g.max, g.min, g.dewp,
  g.visib, g.count_visib,
  g.wdsp, g.mxpsd, g.gust,
  g.prcp, g.flag_prcp, g.sndp,
  g.fog, g.rain_drizzle, g.snow_ice_pellets, g.hail, g.thunder
FROM `{DATASET}.gsod*` AS g
JOIN `{DATASET}.stations` AS s
  ON s.usaf = g.stn AND s.wban = g.wban
WHERE _TABLE_SUFFIX BETWEEN @first_year AND @last_year
  AND g.stn IN UNNEST(@stations)
ORDER BY g.stn, day
"""


def make_client(project: str | None = None):
    """Client BigQuery authentifié (import différé : les tests n'en ont pas besoin)."""
    from google.cloud import bigquery

    return bigquery.Client(project=project or os.environ["GCP_PROJECT_ID"])


def _job_config(params: dict, dry_run: bool):
    from google.cloud import bigquery

    query_params = []
    for name, value in params.items():
        if isinstance(value, list):
            query_params.append(bigquery.ArrayQueryParameter(name, "STRING", value))
        elif isinstance(value, float):
            query_params.append(bigquery.ScalarQueryParameter(name, "FLOAT64", value))
        elif isinstance(value, int):
            query_params.append(bigquery.ScalarQueryParameter(name, "INT64", value))
        else:
            query_params.append(bigquery.ScalarQueryParameter(name, "STRING", value))
    return bigquery.QueryJobConfig(
        query_parameters=query_params, dry_run=dry_run, use_query_cache=False,
        maximum_bytes_billed=None if dry_run else MAX_BYTES_BILLED,
        labels={"application": "auroracast"})


def run_query(client, sql: str, params: dict, job_config: Callable = _job_config) -> tuple[list[dict], dict]:
    """Estimation à blanc, puis exécution avec plafond. Renvoie (lignes, trace)."""
    estimate = client.query(sql, job_config=job_config(params, True))
    trace = {"bytes_estimated": estimate.total_bytes_processed}
    if trace["bytes_estimated"] and trace["bytes_estimated"] > MAX_BYTES_BILLED:
        raise RuntimeError(f"requête trop coûteuse : {trace['bytes_estimated']} octets estimés")
    job = client.query(sql, job_config=job_config(params, False))
    rows = [dict(r.items()) for r in job.result()]
    trace.update(job_id=job.job_id, bytes_processed=job.total_bytes_processed,
                 bytes_billed=job.total_bytes_billed, rows=len(rows))
    return rows, trace


def select_station(candidates: list[dict], end_year: int, max_km: float = MAX_KM) -> dict | None:
    """La station la plus proche, à moins de max_km, encore active la dernière année demandée."""
    ok = [c for c in candidates
          if c["distance_km"] <= max_km and str(c.get("last_day") or "") >= f"{end_year}0101"]
    return min(ok, key=lambda c: c["distance_km"]) if ok else None


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def to_csv_gz(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _json_default(v) if isinstance(v, (date, datetime)) else v
                             for k, v in r.items()})
    return gzip.compress(buf.getvalue().encode("utf-8"), mtime=0)


# Valeurs « absentes » de GSOD (documentation NOAA GSOD)
MISSING = {"temp": 9999.9, "max": 9999.9, "min": 9999.9, "dewp": 9999.9, "visib": 999.9,
           "wdsp": 999.9, "mxpsd": 999.9, "gust": 999.9, "prcp": 99.99, "sndp": 999.9}


def quality(rows: list[dict], start_year: int, end_year: int) -> dict:
    """Exhaustivité (jours manquants) et part de valeurs absentes, par station."""
    report: dict = {}
    by_station: dict[str, list[dict]] = {}
    for r in rows:
        by_station.setdefault(r["stn"], []).append(r)
    for stn, rs in by_station.items():
        days = sorted({r["day"] for r in rs})
        first, last = days[0], days[-1]
        expected = (last - first).days + 1
        report[stn] = {
            "station_name": rs[0].get("station_name"),
            "first_day": first.isoformat(), "last_day": last.isoformat(),
            "days": len(days), "expected_days": expected, "missing_days": expected - len(days),
            "duplicate_rows": len(rs) - len(days),
            "missing_share": {k: round(sum(1 for r in rs if r.get(k) is None
                                           or float(r[k]) >= v) / len(rs), 4)
                              for k, v in MISSING.items()},
            "period_requested": [start_year, end_year],
        }
    return report


def collect(
    out_dir: Path = DEFAULT_OUT,
    start_year: int = 2010,
    end_year: int = LAST_YEAR,
    locations: tuple[Location, ...] = LOCATIONS,
    client_factory: Callable = make_client,
    now: Callable[[], datetime] = utc_now,
    job_config: Callable = _job_config,
) -> list[dict]:
    started = now()
    day_dir = out_dir / "gsod" / started.strftime("%Y/%m/%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "requetes.sql").write_text(
        "-- 1. Stations\n" + SQL_STATIONS.strip() + "\n\n-- 2. Relevés quotidiens\n"
        + SQL_DAILY.strip() + "\n", encoding="utf-8")
    records: list[dict] = []
    client = client_factory()
    chosen: dict[str, dict] = {}
    for loc in locations:
        rec = {"source": f"gsod_stations_{loc.name}", "technique": "sql", "location": loc.name,
               "collected_at": now().isoformat(), "ok": False}
        try:
            cands, trace = run_query(client, SQL_STATIONS,
                                     {"lat": loc.lat, "lon": loc.lon, "max_m": int(MAX_KM * 1000)},
                                     job_config)
            rec.update(trace)
            path = day_dir / f"stations_{loc.name}.json"
            path.write_text(json.dumps(cands, ensure_ascii=False, indent=2,
                                       default=_json_default), encoding="utf-8")
            best = select_station(cands, end_year)
            rec.update(ok=best is not None, file=path.relative_to(out_dir).as_posix(),
                       candidates=len(cands), station=best)
            if best:
                chosen[loc.name] = best
            else:
                rec["error"] = f"aucune station active à moins de {MAX_KM:.0f} km"
        except Exception as exc:  # noqa: BLE001 - consigné dans le manifeste
            rec["error"] = str(exc)
            log.error("stations %s : %s", loc.name, exc)
        records.append(rec)
    if chosen:
        rec = {"source": "gsod_daily", "technique": "sql", "collected_at": now().isoformat(),
               "stations": {k: v["usaf"] for k, v in chosen.items()}, "ok": False}
        try:
            params = {"first_year": str(start_year), "last_year": str(end_year),
                      "stations": sorted({v["usaf"] for v in chosen.values()})}
            rows, trace = run_query(client, SQL_DAILY, params, job_config)
            rec.update(trace)
            content = to_csv_gz(rows)
            path = day_dir / f"gsod_daily_{start_year}_{end_year}_{stamp(started)}.csv.gz"
            path.write_bytes(content)
            rec.update(ok=bool(rows), file=path.relative_to(out_dir).as_posix(),
                       bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
                       quality=quality(rows, start_year, end_year))
            if not rows:
                rec["error"] = "aucune ligne renvoyée"
        except Exception as exc:  # noqa: BLE001 - consigné dans le manifeste
            rec["error"] = str(exc)
            log.error("relevés quotidiens : %s", exc)
        records.append(rec)
    write_run_manifest(out_dir, "gsod", records, started)
    return records


def to_markdown(records: list[dict]) -> str:
    lines = ["## Collecte SQL BigQuery (NOAA GSOD)", ""]
    for r in records:
        if r["source"].startswith("gsod_stations_"):
            st = r.get("station")
            lines.append(f"- **{r['location']}** : " + (
                f"{st['name']} (USAF {st['usaf']}, {st['distance_km']} km), "
                f"{r.get('candidates')} candidates, {r.get('bytes_processed')} octets traités"
                if st else f"échec ({r.get('error')})"))
    daily = next((r for r in records if r["source"] == "gsod_daily"), None)
    if daily:
        summary = (f"Relevés quotidiens : {daily.get('rows')} lignes, "
                   f"{daily.get('bytes_processed')} octets traités "
                   f"(estimation {daily.get('bytes_estimated')}).")
        lines += ["", summary, ""]
        if daily.get("quality"):
            lines += ["| Station | Du | Au | Jours | Manquants | Visibilité absente |",
                      "| --- | --- | --- | --- | --- | --- |"]
            for stn, q in daily["quality"].items():
                lines.append(f"| {q['station_name']} ({stn}) | {q['first_day']} | {q['last_day']} | "
                             f"{q['days']} | {q['missing_days']} | "
                             f"{q['missing_share']['visib']:.1%} |")
        elif daily.get("error"):
            lines.append(f"Échec : {daily['error']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collecte SQL BigQuery : NOAA GSOD")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end-year", type=int, default=LAST_YEAR)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    records = collect(args.out, args.start_year, args.end_year)
    md = to_markdown(records)
    print(md)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    return 0 if records and all(r["ok"] for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
