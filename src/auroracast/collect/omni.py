"""Backfill du vent solaire horaire depuis OMNIWeb (NASA SPDF), par crawling.

Le collecteur parcourt la page d'index du répertoire public low_res_omni, y découvre
les fichiers annuels omni2_AAAA.dat (une ligne par heure, 55 champs), puis les
télécharge un par un en respectant le robots.txt et une pause entre deux fichiers.
Chaque fichier est archivé TEL QUEL avec un rapport de qualité (heures manquantes
ou en double, part de valeurs absentes pour Bz, Bt, vitesse et densité).

À citer : Papitashvili, N. E. et King, J. H. (2020), OMNI Hourly Data, NASA Space
Physics Data Facility, https://doi.org/10.48322/1shr-ht18 (avec la date d'accès).

Utilisation :
    python -m auroracast.collect.omni --start-year 2010
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests

from auroracast.collect.common import write_run_manifest
from auroracast.collect.noaa import DEFAULT_OUT, USER_AGENT, fetch, utc_now
from auroracast.collect.web import PoliteSession

log = logging.getLogger("auroracast.collect.omni")

INDEX_URL = "https://spdf.gsfc.nasa.gov/pub/data/omni/low_res_omni/"
FILE_RE = re.compile(r"omni2_(\d{4})\.dat$", re.IGNORECASE)
N_FIELDS = 55

# Champs utiles (numéro de mot OMNI, à partir de 1) et valeur « absente » associée.
FIELDS = {
    "bt": (9, 999.9),          # intensité moyenne du champ |B| (nT)
    "by_gsm": (16, 999.9),     # By GSM (nT)
    "bz_gsm": (17, 999.9),     # Bz GSM (nT)
    "density": (24, 999.9),    # densité des protons (n/cm3)
    "speed": (25, 9999.0),     # vitesse du vent solaire (km/s)
    "kp10": (39, 99.0),        # Kp x 10
}


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


def crawl_index(html: str, base_url: str = INDEX_URL) -> dict[int, str]:
    """Liens des fichiers annuels trouvés dans la page d'index : {année: URL absolue}."""
    parser = _LinkParser()
    parser.feed(html)
    found: dict[int, str] = {}
    for href in parser.links:
        url = urljoin(base_url, href)
        match = FILE_RE.search(url.split("?")[0])
        if match:
            found[int(match.group(1))] = url
    return dict(sorted(found.items()))


def parse(text: str) -> list[dict]:
    rows = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        f = line.split()
        if len(f) != N_FIELDS:
            raise ValueError(f"ligne {lineno} : {len(f)} champs au lieu de {N_FIELDS}")
        year, doy, hour = int(f[0]), int(f[1]), int(f[2])
        when = datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=doy - 1, hours=hour)
        row = {"time": when}
        for name, (word, fill) in FIELDS.items():
            value = float(f[word - 1])
            row[name] = None if value >= fill else value
        rows.append(row)
    if not rows:
        raise ValueError("fichier vide")
    return rows


def quality_report(rows: list[dict], year: int) -> dict:
    times = [r["time"] for r in rows]
    seen, duplicates = set(), 0
    for t in times:
        duplicates += t in seen
        seen.add(t)
    first, last = min(times), max(times)
    expected = int((last - first).total_seconds() // 3600) + 1
    missing = expected - len(seen)
    report = {
        "year": year,
        "first_hour": first.isoformat(),
        "last_hour": last.isoformat(),
        "hours": len(rows),
        "expected_hours": expected,
        "missing_hours": missing,
        "duplicate_hours": duplicates,
        "fill_share": {},
        "last_valid": {},
    }
    for name in FIELDS:
        valid = [r["time"] for r in rows if r[name] is not None]
        report["fill_share"][name] = round(1 - len(valid) / len(rows), 4)
        report["last_valid"][name] = max(valid).isoformat() if valid else None
    report["warnings"] = [w for w in (
        f"{missing} heure(s) manquante(s)" if missing else "",
        f"{duplicates} heure(s) en double" if duplicates else "",
        "aucune valeur de Bz" if report["last_valid"]["bz_gsm"] is None else "",
    ) if w]
    return report


def collect(
    out_dir: Path = DEFAULT_OUT,
    start_year: int = 2010,
    end_year: int | None = None,
    now: Callable[[], datetime] = utc_now,
    fetcher: Callable[..., requests.Response] = fetch,
    session_factory: Callable[[], PoliteSession] = lambda: PoliteSession(USER_AGENT),
) -> list[dict]:
    started = now()
    day_dir = out_dir / "omni" / "omni2" / started.strftime("%Y/%m/%d")
    records: list[dict] = []
    with session_factory() as session:
        index = {"source": "omni_index", "technique": "crawling", "url": INDEX_URL,
                 "collected_at": started.isoformat(), "ok": False}
        records.append(index)
        try:
            index["robots"] = session.robots_info(INDEX_URL)
            resp = fetcher(INDEX_URL, session)
            index["http_status"] = resp.status_code
            resp.raise_for_status()
            links = crawl_index(resp.text)
            day_dir.mkdir(parents=True, exist_ok=True)
            (day_dir / "index.html").write_bytes(resp.content)
            index.update(ok=True, files_found=len(links),
                         years_found=[min(links), max(links)] if links else None)
        except Exception as exc:  # noqa: BLE001 - consigné dans le manifeste
            index["error"] = str(exc)
            log.error("index OMNI : %s", exc)
            links = {}
        last = end_year or started.year
        wanted = {y: u for y, u in links.items() if start_year <= y <= last}
        for year in range(start_year, last + 1):
            if links and year not in wanted:
                records.append({"source": f"omni2_{year}", "technique": "crawling",
                                "ok": False, "error": "fichier absent de l'index"})
        for year, url in wanted.items():
            rec = {"source": f"omni2_{year}", "technique": "crawling", "url": url,
                   "collected_at": now().isoformat(), "ok": False}
            try:
                resp = fetcher(url, session, timeout=120)
                rec["http_status"] = resp.status_code
                resp.raise_for_status()
                report = quality_report(parse(resp.content.decode("ascii")), year)
                path = day_dir / f"omni2_{year}.dat"
                path.write_bytes(resp.content)
                path.with_name(f"omni2_{year}_qualite.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                rec.update(ok=True, file=path.relative_to(out_dir).as_posix(),
                           bytes=len(resp.content),
                           sha256=hashlib.sha256(resp.content).hexdigest(), quality=report)
                for w in report["warnings"]:
                    log.warning("%s : %s", year, w)
            except Exception as exc:  # noqa: BLE001 - consigné dans le manifeste
                rec["error"] = str(exc)
                log.error("omni2_%s : %s", year, exc)
            records.append(rec)
    write_run_manifest(out_dir, "omni", records, started)
    ok = sum(r["ok"] for r in records[1:])
    log.info("%d/%d fichiers annuels OMNI collectés", ok, len(records) - 1)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill du vent solaire horaire (OMNIWeb)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end-year", type=int, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    records = collect(args.out, args.start_year, args.end_year)
    return 0 if all(r["ok"] for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
