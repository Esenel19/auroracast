"""Collecte brute des produits NOAA SWPC.

Chaque exécution télécharge les produits listés dans SOURCES et les enregistre
TELS QUELS (aucune transformation) avec l'horodatage UTC de la collecte :

    data/raw/noaa/<source>/AAAA/MM/JJ/<source>_AAAAMMJJTHHMMSSZ.<ext>

Une ligne par téléchargement est ajoutée au manifeste data/raw/noaa/manifest.jsonl
(URL, statut HTTP, taille, empreinte SHA-256, durée). Le manifeste sert à prouver
l'exhaustivité de la collecte (trous, échecs) et à dédoublonner plus tard.

Utilisation :
    python -m auroracast.collect.noaa                 # toutes les sources
    python -m auroracast.collect.noaa --only kp_observed kp_forecast
    python -m auroracast.collect.noaa --out D:/auroracast/raw
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()  # lit le fichier .env du dossier courant s'il existe

log = logging.getLogger("auroracast.collect.noaa")

BASE = "https://services.swpc.noaa.gov"


@dataclass(frozen=True)
class Source:
    name: str
    path: str
    technique: str  # "api" (JSON) ou "scraping" (texte non structuré)

    @property
    def url(self) -> str:
        return BASE + self.path

    @property
    def ext(self) -> str:
        return Path(self.path).suffix.lstrip(".") or "txt"


# Cadence conseillée : toutes les heures. Les fichiers « 6 heures » du vent solaire
# rattrapent jusqu'à 5 exécutions manquées tout en restant légers ; les recouvrements
# entre exécutions seront dédoublonnés à l'étape de nettoyage.
SOURCES: tuple[Source, ...] = (
    Source("kp_observed", "/products/noaa-planetary-k-index.json", "api"),
    Source("kp_forecast", "/products/noaa-planetary-k-index-forecast.json", "api"),
    Source("kp_1m", "/json/planetary_k_index_1m.json", "api"),
    Source("solar_wind_mag", "/products/solar-wind/mag-6-hour.json", "api"),
    Source("solar_wind_plasma", "/products/solar-wind/plasma-6-hour.json", "api"),
    Source("noaa_scales", "/products/noaa-scales.json", "api"),
    Source("alerts", "/products/alerts.json", "api"),
    Source("forecast_3day_text", "/text/3-day-forecast.txt", "scraping"),
    Source("daily_geomag_text", "/text/daily-geomagnetic-indices.txt", "scraping"),
)

DEFAULT_OUT = Path(os.getenv("AURORACAST_RAW_DIR", "data/raw"))
CONTACT = os.getenv("AURORACAST_CONTACT", "contact-non-renseigne")
USER_AGENT = f"AuroraCast/0.1 (projet etudiant; contact: {CONTACT})"


def utc_now() -> datetime:
    return datetime.now(UTC)


def target_path(out_dir: Path, source: Source, when: datetime) -> Path:
    """Chemin d'archive d'un téléchargement, partitionné par date UTC."""
    stamp = when.strftime("%Y%m%dT%H%M%SZ")
    return (
        out_dir / "noaa" / source.name / when.strftime("%Y/%m/%d")
        / f"{source.name}_{stamp}.{source.ext}"
    )


def fetch(url: str, session: requests.Session, retries: int = 3, timeout: int = 30) -> requests.Response:
    """GET avec nouvelles tentatives (1 s, 2 s, 4 s) sur erreur réseau ou 5xx."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code < 500:
                return resp
            last_exc = RuntimeError(f"HTTP {resp.status_code}")
        except requests.RequestException as exc:
            last_exc = exc
        if attempt < retries - 1:
            time.sleep(2**attempt)
    raise RuntimeError(f"échec après {retries} tentatives : {last_exc}")


def validate(source: Source, content: bytes) -> None:
    """Contrôle minimal d'exactitude : le JSON se lit et n'est pas vide."""
    if not content.strip():
        raise ValueError("réponse vide")
    if source.ext == "json":
        data = json.loads(content)
        if not data:
            raise ValueError("JSON vide")


def collect_one(
    source: Source,
    out_dir: Path,
    session: requests.Session,
    now: Callable[[], datetime] = utc_now,
    fetcher: Callable[..., requests.Response] = fetch,
) -> dict:
    """Télécharge une source, l'archive et renvoie la ligne de manifeste."""
    started = now()
    record = {
        "source": source.name,
        "technique": source.technique,
        "url": source.url,
        "collected_at": started.isoformat(),
        "ok": False,
    }
    t0 = time.monotonic()
    try:
        resp = fetcher(source.url, session)
        record["http_status"] = resp.status_code
        resp.raise_for_status()
        content = resp.content
        validate(source, content)
        path = target_path(out_dir, source, started)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        record.update(
            ok=True,
            file=path.relative_to(out_dir).as_posix(),
            bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
    except Exception as exc:  # noqa: BLE001 - on journalise et on passe à la source suivante
        record["error"] = str(exc)
        log.error("%s : %s", source.name, exc)
    record["duration_s"] = round(time.monotonic() - t0, 3)
    return record


def manifest_path(out_dir: Path, per_run: datetime | None = None) -> Path:
    """Manifeste cumulé (local) ou un fichier par exécution (cloud, où chaque
    exécution part d'un dossier vide et ne doit pas écraser les précédentes)."""
    if per_run is None:
        return out_dir / "noaa" / "manifest.jsonl"
    stamp = per_run.strftime("%Y%m%dT%H%M%SZ")
    return out_dir / "noaa" / "manifests" / per_run.strftime("%Y/%m/%d") / f"run_{stamp}.jsonl"


def append_manifest(out_dir: Path, records: list[dict], per_run: datetime | None = None) -> Path:
    manifest = manifest_path(out_dir, per_run)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return manifest


def run(
    out_dir: Path = DEFAULT_OUT,
    only: list[str] | None = None,
    run_manifest: bool = False,
    **kwargs,
) -> list[dict]:
    selected = [s for s in SOURCES if not only or s.name in only]
    unknown = set(only or []) - {s.name for s in SOURCES}
    if unknown:
        raise SystemExit(f"Sources inconnues : {', '.join(sorted(unknown))}")
    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        records = [collect_one(s, out_dir, session, **kwargs) for s in selected]
    started = kwargs.get("now", utc_now)()
    append_manifest(out_dir, records, per_run=started if run_manifest else None)
    ok = sum(r["ok"] for r in records)
    log.info("%d/%d sources collectées dans %s", ok, len(records), out_dir)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collecte brute NOAA SWPC")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="dossier des données brutes")
    parser.add_argument("--only", nargs="+", metavar="SOURCE",
                        help="sources à collecter : " + ", ".join(s.name for s in SOURCES))
    parser.add_argument("--run-manifest", action="store_true",
                        help="un manifeste par exécution (mode cloud) au lieu du manifeste cumulé")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    records = run(args.out, args.only, run_manifest=args.run_manifest)
    return 0 if all(r["ok"] for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
