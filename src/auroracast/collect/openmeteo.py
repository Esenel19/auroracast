"""Backfill de la nébulosité horaire depuis l'API d'archive Open-Meteo.

Pour chaque lieu cible et chaque année, une requête à l'API d'archive renvoie la
couverture nuageuse horaire (totale, basse, moyenne, haute, en %, heures UTC).
La réponse JSON est archivée TELLE QUELLE avec un rapport de qualité (heures
manquantes, valeurs nulles, valeurs hors de [0, 100]).

Usage gratuit réservé au non commercial (usage éducatif inclus), données CC BY 4.0 :
afficher « Weather data by Open-Meteo.com » à côté des données. Limites : 600 appels
par minute, 5 000 par heure, 10 000 par jour ; une requête d'un an compte pour
plusieurs appels, d'où la pause de 5 s entre deux requêtes.

Utilisation :
    python -m auroracast.collect.openmeteo --start-year 2010
    python -m auroracast.collect.openmeteo --location "Kiruna:67.8558:20.2253"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from auroracast.collect.common import write_run_manifest
from auroracast.collect.noaa import DEFAULT_OUT, USER_AGENT, fetch, utc_now
from auroracast.collect.web import PoliteSession

log = logging.getLogger("auroracast.collect.openmeteo")

API_URL = "https://archive-api.open-meteo.com/v1/archive"
VARIABLES = ("cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high")
DATA_DELAY_DAYS = 6   # l'archive ERA5 a environ 5 jours de retard
PAUSE_S = 5.0


@dataclass(frozen=True)
class Location:
    name: str
    lat: float
    lon: float

    @classmethod
    def parse(cls, text: str) -> Location:
        name, lat, lon = text.rsplit(":", 2)
        return cls(name.strip(), float(lat), float(lon))


# Lieux cibles provisoires (zone aurorale, à confirmer avec le directeur).
LOCATIONS = (
    Location("tromso", 69.6496, 18.9560),
    Location("abisko", 68.3495, 18.8312),
    Location("rovaniemi", 66.5039, 25.7294),
)


def year_range(year: int, today: date) -> tuple[date, date] | None:
    """Période à demander pour une année, sans dépasser les données disponibles."""
    start = date(year, 1, 1)
    end = min(date(year, 12, 31), today - timedelta(days=DATA_DELAY_DAYS))
    return (start, end) if start <= end else None


def build_url(loc: Location, start: date, end: date) -> str:
    return (f"{API_URL}?latitude={loc.lat}&longitude={loc.lon}"
            f"&start_date={start.isoformat()}&end_date={end.isoformat()}"
            f"&hourly={','.join(VARIABLES)}&timezone=GMT")


def quality_report(payload: dict, start: date, end: date) -> dict:
    hourly = payload.get("hourly")
    if not hourly or "time" not in hourly:
        raise ValueError("réponse sans bloc « hourly »")
    times = hourly["time"]
    expected = ((end - start).days + 1) * 24
    report = {"first_hour": times[0] if times else None,
              "last_hour": times[-1] if times else None,
              "hours": len(times), "expected_hours": expected,
              "missing_hours": expected - len(set(times)),
              "null_share": {}, "out_of_range": {}}
    for var in VARIABLES:
        values = hourly.get(var)
        if values is None or len(values) != len(times):
            raise ValueError(f"variable {var} absente ou de longueur incohérente")
        nulls = sum(v is None for v in values)
        report["null_share"][var] = round(nulls / len(values), 4) if values else 1.0
        report["out_of_range"][var] = sum(v is not None and not 0 <= v <= 100 for v in values)
    report["warnings"] = [w for w in (
        f"{report['missing_hours']} heure(s) manquante(s)" if report["missing_hours"] else "",
        "valeurs hors de [0, 100]" if any(report["out_of_range"].values()) else "",
    ) if w]
    return report


def collect(
    out_dir: Path = DEFAULT_OUT,
    start_year: int = 2010,
    end_year: int | None = None,
    locations: tuple[Location, ...] = LOCATIONS,
    now: Callable[[], datetime] = utc_now,
    fetcher: Callable[..., requests.Response] = fetch,
    session_factory: Callable[[], PoliteSession] = lambda: PoliteSession(USER_AGENT,
                                                                         min_delay=PAUSE_S),
) -> list[dict]:
    started = now()
    day_dir = out_dir / "openmeteo" / "cloud_cover" / started.strftime("%Y/%m/%d")
    records: list[dict] = []
    with session_factory() as session:
        robots = session.robots_info(API_URL)
        for loc in locations:
            for year in range(start_year, (end_year or started.year) + 1):
                period = year_range(year, started.date())
                if period is None:
                    continue
                url = build_url(loc, *period)
                rec = {"source": f"cloud_cover_{loc.name}_{year}", "technique": "api",
                       "url": url, "location": loc.name, "robots": robots,
                       "collected_at": now().isoformat(), "ok": False}
                try:
                    resp = fetcher(url, session, timeout=120)
                    rec["http_status"] = resp.status_code
                    resp.raise_for_status()
                    report = quality_report(json.loads(resp.content), *period)
                    path = day_dir / loc.name / f"{loc.name}_{year}.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(resp.content)
                    rec.update(ok=True, file=path.relative_to(out_dir).as_posix(),
                               bytes=len(resp.content),
                               sha256=hashlib.sha256(resp.content).hexdigest(), quality=report)
                    for w in report["warnings"]:
                        log.warning("%s %s : %s", loc.name, year, w)
                except Exception as exc:  # noqa: BLE001 - consigné dans le manifeste
                    rec["error"] = str(exc)
                    log.error("%s %s : %s", loc.name, year, exc)
                records.append(rec)
    write_run_manifest(out_dir, "openmeteo", records, started)
    log.info("%d/%d séries lieu-année collectées", sum(r["ok"] for r in records), len(records))
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill de la nébulosité (Open-Meteo)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end-year", type=int, default=None)
    parser.add_argument("--location", action="append", metavar="NOM:LAT:LON",
                        help="lieu cible (répétable) ; par défaut Tromsø, Abisko, Rovaniemi")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    locations = tuple(Location.parse(t) for t in args.location) if args.location else LOCATIONS
    records = collect(args.out, args.start_year, args.end_year, locations)
    return 0 if records and all(r["ok"] for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
