"""NOAA GHCNh (Global Historical Climatology Network hourly) : observations horaires
de stations météo, dont la couverture nuageuse (sky_cover_*, en octas).

Licence CC0 (domaine public) ; NOAA demande d'être citée et de ne pas laisser croire
à son soutien. Fichiers servis par l'espace public NOAA Open Data sur AWS :
    https://noaa-ghcnh-pds.s3.amazonaws.com/hourly/...

Étape 1 (ce module, commande « stations ») : choisir les stations. Pour chaque lieu
cible, on cherche les stations les plus proches dans la liste officielle, puis on
examine un fichier annuel de chacune pour vérifier qu'elle relève bien les nuages.

Étape 2 (commande « backfill ») : télécharger l'historique des stations retenues
(src/auroracast/data/stations.json), décoder la couverture nuageuse en octas et
produire un rapport de qualité par fichier annuel.

Utilisation :
    python -m auroracast.collect.ghcnh stations --year 2025
    python -m auroracast.collect.ghcnh backfill --start-year 2010
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import itertools
import json
import logging
import math
import sys
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import requests

from auroracast.collect.common import stamp, write_run_manifest
from auroracast.collect.noaa import DEFAULT_OUT, USER_AGENT, fetch, utc_now
from auroracast.collect.web import PoliteSession
from auroracast.locations import LOCATIONS, Location

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


def nearest(stations: list[dict], loc: Location, n: int = 10, max_km: float = 150) -> list[dict]:
    scored = [{**s, "distance_km": round(distance_km(loc.lat, loc.lon, s["lat"], s["lon"]), 1)}
              for s in stations]
    scored = [s for s in scored if s["distance_km"] <= max_km]
    return sorted(scored, key=lambda s: s["distance_km"])[:n]


SUFFIXES = ("_measurement_code", "_quality_code", "_report_type", "_source_code",
            "_source_station_id")


def sky_columns(header: list[str]) -> list[int]:
    """Colonnes de valeurs de nuages (sky_condition, sky_cover_layer_*, sky_cover_summation_*),
    sans leurs colonnes de codes associées."""
    return [i for i, h in enumerate(header)
            if h.strip().lower().startswith("sky_") and "baseht" not in h.lower()
            and not h.strip().lower().endswith(SUFFIXES)]


def inspect_year_file(text: str, max_samples: int = 8) -> dict:
    """Examine un fichier annuel PSV : période et remplissage de chaque colonne de nuages."""
    reader = csv.reader(io.StringIO(text), delimiter="|")
    header = next(reader)
    i_date = _pick(header, "date")
    cols = sky_columns(header)
    filled = Counter()
    samples = {i: Counter() for i in cols}
    rows = 0
    first = last = None
    for r in reader:
        if not r:
            continue
        rows += 1
        if i_date is not None and i_date < len(r):
            first = first or r[i_date]
            last = r[i_date]
        for i in cols:
            if i < len(r) and r[i].strip():
                filled[i] += 1
                samples[i][r[i].strip()] += 1
    sky = {header[i]: {"share": round(filled[i] / rows, 3) if rows else 0.0,
                       "samples": samples[i].most_common(max_samples)} for i in cols}
    best = max(sky, key=lambda k: sky[k]["share"]) if sky else None
    return {
        "columns": len(header),
        "rows": rows,
        "first_date": first,
        "last_date": last,
        "sky": sky,
        "best_sky_column": best,
        "best_sky_share": sky[best]["share"] if best else 0.0,
        "header": header,
    }


def examine_candidates(stations: list[dict], loc: Location, year: int, session, fetcher,
                       per_location: int = 10) -> list[dict]:
    """Stations les plus proches d'un lieu, chacune examinée sur son fichier annuel."""
    candidates = []
    for st in nearest(stations, loc, n=per_location):
        cand = {k: st[k] for k in ("id", "name", "lat", "lon", "distance_km")}
        try:
            r = fetcher(year_file_url(st["id"], year), session, timeout=120)
            cand["http_status"] = r.status_code
            if r.status_code == 200:
                cand.update(inspect_year_file(r.content.decode("utf-8", errors="replace")))
        except Exception as exc:  # noqa: BLE001 - consigné dans le rapport
            cand["error"] = str(exc)
        candidates.append(cand)
    return candidates


def find_stations(
    out_dir: Path = DEFAULT_OUT,
    year: int = 2025,
    locations: tuple[Location, ...] = LOCATIONS,
    per_location: int = 10,
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
            report["locations"].append({
                "location": loc.name, "lat": loc.lat, "lon": loc.lon,
                "candidates": examine_candidates(stations, loc, year, session, fetcher,
                                                 per_location)})
    path = out_dir / "ghcnh" / "stations" / f"recherche_stations_{stamp(started)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def to_markdown(report: dict) -> str:
    lines = [f"## Stations GHCNh candidates (fichiers {report['year_inspected']})", "",
             f"{report.get('stations_in_list', '?')} stations dans la liste officielle.", ""]
    for loc in report["locations"]:
        lines += [f"### {loc['location']} ({loc['lat']}, {loc['lon']})", "",
                  "| Station | Nom | Distance (km) | Fichier | Lignes | Meilleure colonne nuages | Remplie | Exemples |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for c in loc["candidates"]:
            best = c.get("best_sky_column")
            ex = ", ".join(f"`{v}` ({n})" for v, n in c["sky"][best]["samples"][:4]) if best else ""
            share = f"{c['best_sky_share']:.0%}" if "best_sky_share" in c else "—"
            lines.append(f"| {c['id']} | {c['name']} | {c['distance_km']} | "
                         f"{c.get('http_status', c.get('error', '?'))} | {c.get('rows', '—')} | "
                         f"{best or '—'} | {share} | {ex} |")
        lines.append("")
    return "\n".join(lines)


# --- Étape 2 : backfill de la nébulosité -------------------------------------------

STATIONS_FILE = Path(__file__).resolve().parents[1] / "data" / "stations.json"
MAX_KM = 30.0
MIN_SHARE = 0.30
LAYER_COLUMNS = tuple(f"sky_cover_layer_{i}" for i in range(1, 5))


def load_stations_config(path: Path = STATIONS_FILE) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def select_station(candidates: list[dict], max_km: float = MAX_KM,
                   min_share: float = MIN_SHARE) -> dict | None:
    """Règle de choix : la station la plus proche, à moins de max_km, dont les nuages
    sont renseignés au moins min_share des heures sur l'année examinée."""
    ok = [c for c in candidates
          if c["distance_km"] <= max_km and c.get("best_sky_share", 0) >= min_share]
    return min(ok, key=lambda c: c["distance_km"]) if ok else None


def _oktas(code: str) -> int | None:
    """« BKN:07 » -> 7 ; « VV:09 » (ciel invisible, brouillard) -> 8 ; « 07 » -> 7."""
    digits = code.split(":")[-1].strip()
    if not digits.isdigit():
        return None
    value = int(digits)
    if value == 9:
        return 8
    return value if 0 <= value <= 8 else None


def total_oktas(row: dict) -> int | None:
    """Couverture nuageuse totale (0 à 8 octas) d'une observation : la couche la plus
    couverte parmi sky_cover_layer_1 à 4, sinon la couverture totale de sky_condition."""
    layers = [_oktas(row[c]) for c in LAYER_COLUMNS if (row.get(c) or "").strip()]
    layers = [v for v in layers if v is not None]
    if layers:
        return max(layers)
    cond = (row.get("sky_condition") or "").strip()
    return _oktas(cond.split(";")[0]) if cond and cond.split(";")[0] else None


def cloud_quality(text: str, year: int) -> dict:
    """Rapport de qualité d'un fichier annuel : heures couvertes par une observation
    de nuages, répartition des octas, plus long trou entre deux observations."""
    reader = csv.DictReader(io.StringIO(text), delimiter="|")
    rows = 0
    hours: set[datetime] = set()
    distribution = Counter()
    for row in reader:
        rows += 1
        oktas = total_oktas(row)
        raw_date = (row.get("DATE") or "").strip()
        if oktas is None or not raw_date:
            continue
        try:
            when = datetime.fromisoformat(raw_date[:19])
        except ValueError:
            continue
        hours.add(when.replace(minute=0, second=0))
        distribution[oktas] += 1
    ordered = sorted(hours)
    gaps = [(b - a).total_seconds() / 3600 for a, b in itertools.pairwise(ordered)]
    expected = (datetime(year + 1, 1, 1) - datetime(year, 1, 1)).days * 24
    if ordered and ordered[-1].year == year and ordered[-1] < datetime(year, 12, 31):
        expected = int((ordered[-1] - datetime(year, 1, 1)).total_seconds() // 3600) + 1
    share = round(len(hours) / expected, 3) if expected else 0.0
    report = {
        "year": year,
        "rows": rows,
        "first_obs": ordered[0].isoformat() if ordered else None,
        "last_obs": ordered[-1].isoformat() if ordered else None,
        "hours_with_cloud": len(hours),
        "expected_hours": expected,
        "cloud_share": share,
        "longest_gap_h": max(gaps) if gaps else None,
        "oktas_distribution": {str(k): distribution[k] for k in range(9)},
    }
    report["warnings"] = [w for w in (
        "aucune observation de nuages" if not hours else "",
        f"nuages renseignés seulement {share:.0%} des heures" if hours and share < 0.25 else "",
    ) if w]
    return report


def backfill(
    out_dir: Path = DEFAULT_OUT,
    start_year: int = 2010,
    end_year: int | None = None,
    locations: tuple[Location, ...] = LOCATIONS,
    config: dict | None = None,
    now: Callable[[], datetime] = utc_now,
    fetcher: Callable[..., requests.Response] = fetch,
    session_factory: Callable[[], PoliteSession] = lambda: PoliteSession(USER_AGENT),
) -> list[dict]:
    """Télécharge les fichiers annuels GHCNh des stations retenues, les archive
    compressés (.psv.gz, contenu inchangé) avec un rapport de qualité par fichier."""
    started = now()
    config = load_stations_config() if config is None else config
    last = end_year or started.year
    day_dir = out_dir / "ghcnh" / "hourly" / started.strftime("%Y/%m/%d")
    records: list[dict] = []
    with session_factory() as session:
        robots = session.robots_info(STATION_LIST_URL)
        stations_list: list[dict] | None = None
        for loc in locations:
            chosen = config.get(loc.name)
            selection = {"source": f"station_{loc.name}", "technique": "api",
                         "location": loc.name, "robots": robots, "ok": False}
            if not chosen:
                try:
                    if stations_list is None:
                        resp = fetcher(STATION_LIST_URL, session, timeout=120)
                        resp.raise_for_status()
                        stations_list = parse_station_list(
                            resp.content.decode("utf-8", errors="replace"))
                    cands = examine_candidates(stations_list, loc, last - 1, session, fetcher)
                    best = select_station(cands)
                    selection["candidates"] = [
                        {k: c.get(k) for k in ("id", "name", "distance_km", "http_status",
                                               "best_sky_column", "best_sky_share")}
                        for c in cands]
                    if best:
                        chosen = {"station_id": best["id"], "name": best["name"],
                                  "distance_km": best["distance_km"],
                                  "chosen_on": started.date().isoformat(),
                                  "reason": (f"Choix automatique : plus proche station à moins de "
                                             f"{MAX_KM:.0f} km avec nuages renseignés "
                                             f"{best['best_sky_share']:.0%} des heures en {last - 1}.")}
                except Exception as exc:  # noqa: BLE001 - consigné dans le manifeste
                    selection["error"] = str(exc)
            if not chosen:
                selection.setdefault("error", f"aucune station à moins de {MAX_KM:.0f} km "
                                              f"ne relève les nuages")
                records.append(selection)
                log.error("%s : %s", loc.name, selection["error"])
                continue
            selection.update(ok=True, station=chosen)
            records.append(selection)
            for year in range(start_year, last + 1):
                url = year_file_url(chosen["station_id"], year)
                rec = {"source": f"cloud_{loc.name}_{year}", "technique": "api", "url": url,
                       "location": loc.name, "station_id": chosen["station_id"],
                       "collected_at": now().isoformat(), "ok": False}
                try:
                    resp = fetcher(url, session, timeout=180)
                    rec["http_status"] = resp.status_code
                    if resp.status_code == 404:
                        rec.update(error="pas de fichier pour cette année", missing_year=True)
                        records.append(rec)
                        continue
                    resp.raise_for_status()
                    report = cloud_quality(resp.content.decode("utf-8", errors="replace"), year)
                    path = day_dir / loc.name / f"GHCNh_{chosen['station_id']}_{year}.psv.gz"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(gzip.compress(resp.content, mtime=0))
                    rec.update(ok=True, file=path.relative_to(out_dir).as_posix(),
                               bytes=len(resp.content), bytes_gz=path.stat().st_size,
                               sha256=hashlib.sha256(resp.content).hexdigest(), quality=report)
                    for w in report["warnings"]:
                        log.warning("%s %s : %s", loc.name, year, w)
                except Exception as exc:  # noqa: BLE001 - consigné dans le manifeste
                    rec["error"] = str(exc)
                    log.error("%s %s : %s", loc.name, year, exc)
                records.append(rec)
    write_run_manifest(out_dir, "ghcnh", records, started)
    return records


def backfill_markdown(records: list[dict]) -> str:
    lines = ["## Backfill nébulosité NOAA GHCNh", ""]
    for sel in (r for r in records if r["source"].startswith("station_")):
        st = sel.get("station")
        lines.append(f"- **{sel['location']}** : " + (
            f"{st['name']} ({st['station_id']}, {st['distance_km']} km). {st['reason']}"
            if st else f"aucune station retenue ({sel.get('error')})"))
    lines += ["", "| Lieu | Année | Fichier | Heures avec nuages | Part | Plus long trou (h) |",
              "| --- | --- | --- | --- | --- | --- |"]
    for r in records:
        if not r["source"].startswith("cloud_"):
            continue
        q = r.get("quality")
        if q:
            lines.append(f"| {r['location']} | {q['year']} | ok | {q['hours_with_cloud']} | "
                         f"{q['cloud_share']:.0%} | {q['longest_gap_h']} |")
        else:
            lines.append(f"| {r['location']} | {r['source'].rsplit('_', 1)[-1]} | "
                         f"{r.get('error')} | — | — | — |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NOAA GHCNh (observations horaires)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("stations", help="chercher les stations proches des lieux cibles")
    st.add_argument("--year", type=int, default=2025)
    bf = sub.add_parser("backfill", help="récupérer l'historique des nuages des stations retenues")
    bf.add_argument("--start-year", type=int, default=2010)
    bf.add_argument("--end-year", type=int, default=None)
    for p in (st, bf):
        p.add_argument("--out", type=Path, default=DEFAULT_OUT)
        p.add_argument("--location", action="append", metavar="NOM:LAT:LON")
        p.add_argument("--summary", type=Path, help="fichier Markdown où écrire le résumé")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    locations = tuple(Location.parse(t) for t in args.location) if args.location else LOCATIONS
    if args.cmd == "stations":
        md, code = to_markdown(find_stations(args.out, args.year, locations)), 0
    else:
        records = backfill(args.out, args.start_year, args.end_year, locations)
        md = backfill_markdown(records)
        hard_errors = [r for r in records if not r["ok"] and not r.get("missing_year")]
        code = 1 if hard_errors else 0
    print(md)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
