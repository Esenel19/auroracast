"""NOAA GHCNh (Global Historical Climatology Network hourly) : observations horaires
de stations météo, dont la couverture nuageuse (sky_cover_*, en octas).

Licence CC0 (domaine public) ; NOAA demande d'être citée et de ne pas laisser croire
à son soutien. Fichiers servis par l'espace public NOAA Open Data sur AWS :
    https://noaa-ghcnh-pds.s3.amazonaws.com/hourly/...

Étape 1 (ce module, commande « stations ») : choisir les stations. Pour chaque lieu
cible, on cherche les stations les plus proches dans la liste officielle, puis on
examine un fichier annuel de chacune pour vérifier qu'elle relève bien les nuages.

Utilisation :
    python -m auroracast.collect.ghcnh stations --year 2025
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import math
import sys
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import requests

from auroracast.collect.common import stamp
from auroracast.collect.noaa import DEFAULT_OUT, USER_AGENT, fetch, utc_now
from auroracast.collect.openmeteo import LOCATIONS, Location
from auroracast.collect.web import PoliteSession

log = logging.getLogger("auroracast.collect.ghcnh")

BASE = "https://noaa-ghcnh-pds.s3.amazonaws.com/hourly"
STATION_LIST_URL = f"{BASE}/doc/ghcnh-station-list.csv"


def year_file_url(station_id: str, year: int) -> str:
    return f"{BASE}/access/by-year/{year}/psv/GHCNh_{station_id}_{year}.psv"


def _pick(header: list[str], *candidates: str) -> int | None:
    low = [h.strip().lower() for h in header]
    for cand in candidates:
        if cand in low:
            return low.index(cand)
    return None


def parse_station_list(text: str) -> list[dict]:
    """Lit la liste des stations (CSV, avec ou sans en-tête)."""
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if r and any(c.strip() for c in r)]
    header = rows[0]
    i_id = _pick(header, "ghcn_id", "station_id", "id")
    i_lat = _pick(header, "latitude", "lat")
    i_lon = _pick(header, "longitude", "lon")
    i_name = _pick(header, "name", "station_name")
    if None in (i_id, i_lat, i_lon):
        i_id, i_lat, i_lon, i_name = 0, 1, 2, 5   # ordre GHCN classique, sans en-tête
        data = rows
    else:
        data = rows[1:]
    stations = []
    for r in data:
        try:
            stations.append({
                "id": r[i_id].strip(),
                "lat": float(r[i_lat]),
                "lon": float(r[i_lon]),
                "name": r[i_name].strip() if i_name is not None and i_name < len(r) else "",
            })
        except (ValueError, IndexError):
            continue
    if not stations:
        raise ValueError("liste des stations illisible")
    return stations


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance orthodromique (formule de haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def nearest(stations: list[dict], loc: Location, n: int = 6, max_km: float = 150) -> list[dict]:
    scored = [{**s, "distance_km": round(distance_km(loc.lat, loc.lon, s["lat"], s["lon"]), 1)}
              for s in stations]
    scored = [s for s in scored if s["distance_km"] <= max_km]
    return sorted(scored, key=lambda s: s["distance_km"])[:n]


def inspect_year_file(text: str, max_samples: int = 12) -> dict:
    """Examine un fichier annuel PSV : colonnes, période, remplissage des nuages."""
    reader = csv.reader(io.StringIO(text), delimiter="|")
    header = next(reader)
    i_date = _pick(header, "date")
    sky_cols = [i for i, h in enumerate(header) if h.strip().lower().startswith("sky_cover")]
    i_sky1 = _pick(header, "sky_cover_1")
    rows = filled = 0
    first = last = None
    samples: Counter[str] = Counter()
    for r in reader:
        if not r:
            continue
        rows += 1
        if i_date is not None and i_date < len(r):
            first = first or r[i_date]
            last = r[i_date]
        if i_sky1 is not None and i_sky1 < len(r) and r[i_sky1].strip():
            filled += 1
            samples[r[i_sky1].strip()] += 1
    return {
        "columns": len(header),
        "sky_cover_columns": [header[i] for i in sky_cols],
        "rows": rows,
        "first_date": first,
        "last_date": last,
        "sky_cover_1_filled": filled,
        "sky_cover_1_share": round(filled / rows, 3) if rows else 0.0,
        "sky_cover_1_samples": samples.most_common(max_samples),
        "header": header,
    }


def find_stations(
    out_dir: Path = DEFAULT_OUT,
    year: int = 2025,
    locations: tuple[Location, ...] = LOCATIONS,
    per_location: int = 4,
    now: Callable[[], datetime] = utc_now,
    fetcher: Callable[..., requests.Response] = fetch,
    session_factory: Callable[[], PoliteSession] = lambda: PoliteSession(USER_AGENT),
) -> dict:
    started = now()
    report: dict = {"collected_at": started.isoformat(), "year_inspected": year,
                    "station_list_url": STATION_LIST_URL, "locations": []}
    with session_factory() as session:
        report["robots"] = session.robots_info(STATION_LIST_URL)
        resp = fetcher(STATION_LIST_URL, session, timeout=120)
        resp.raise_for_status()
        stations = parse_station_list(resp.content.decode("utf-8", errors="replace"))
        report["stations_in_list"] = len(stations)
        for loc in locations:
            entry = {"location": loc.name, "lat": loc.lat, "lon": loc.lon, "candidates": []}
            for st in nearest(stations, loc, n=per_location):
                cand = {k: st[k] for k in ("id", "name", "lat", "lon", "distance_km")}
                url = year_file_url(st["id"], year)
                try:
                    r = fetcher(url, session, timeout=120)
                    cand["http_status"] = r.status_code
                    if r.status_code == 200:
                        cand.update(inspect_year_file(r.content.decode("utf-8", errors="replace")))
                except Exception as exc:  # noqa: BLE001 - consigné dans le rapport
                    cand["error"] = str(exc)
                entry["candidates"].append(cand)
            report["locations"].append(entry)
    path = out_dir / "ghcnh" / "stations" / f"recherche_stations_{stamp(started)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def to_markdown(report: dict) -> str:
    lines = [f"## Stations GHCNh candidates (fichiers {report['year_inspected']})", "",
             f"{report.get('stations_in_list', '?')} stations dans la liste officielle.", ""]
    for loc in report["locations"]:
        lines += [f"### {loc['location']} ({loc['lat']}, {loc['lon']})", "",
                  "| Station | Nom | Distance (km) | Fichier | Lignes | Nuages renseignés | Exemples |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for c in loc["candidates"]:
            ex = ", ".join(f"`{v}` ({n})" for v, n in c.get("sky_cover_1_samples", [])[:5])
            share = f"{c['sky_cover_1_share']:.0%}" if "sky_cover_1_share" in c else "—"
            lines.append(f"| {c['id']} | {c['name']} | {c['distance_km']} | "
                         f"{c.get('http_status', c.get('error', '?'))} | {c.get('rows', '—')} | "
                         f"{share} | {ex} |")
        lines.append("")
    first = next((c for loc in report["locations"] for c in loc["candidates"] if "header" in c), None)
    if first:
        lines += ["Colonnes d'un fichier annuel :", "", "`" + "|".join(first["header"]) + "`", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NOAA GHCNh (observations horaires)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("stations", help="chercher les stations proches des lieux cibles")
    st.add_argument("--out", type=Path, default=DEFAULT_OUT)
    st.add_argument("--year", type=int, default=2025)
    st.add_argument("--location", action="append", metavar="NOM:LAT:LON")
    st.add_argument("--summary", type=Path, help="fichier Markdown où écrire le résumé")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    locations = tuple(Location.parse(t) for t in args.location) if args.location else LOCATIONS
    report = find_stations(args.out, args.year, locations)
    md = to_markdown(report)
    print(md)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
