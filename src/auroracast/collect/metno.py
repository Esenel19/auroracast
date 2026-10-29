"""Prévisions météo locales : MET Norway (Institut météorologique norvégien), API
Locationforecast 2.0, format « complete ».

Pour chaque lieu cible : nébulosité totale et par étage (basse, moyenne, haute),
brouillard (part de la zone où la visibilité passe sous 1 km), température, point de
rosée, humidité, pression, vent, rafales, précipitations ; pas horaire sur environ 60 h,
puis pas de 6 h jusqu'à 10 jours. L'API ne donne pas de visibilité en km : le brouillard
en % en tient lieu.

Conditions d'utilisation (https://api.met.no/doc/TermsOfService, lues le 29/10/2026) :
  - User-Agent identifiable avec contact (sinon 403) ;
  - ne pas redemander avant l'heure de l'en-tête Expires ; utiliser If-Modified-Since
    (réponse 304 sans contenu si la prévision n'a pas changé) ;
  - coordonnées tronquées à 4 décimales au plus (5 décimales -> 403) ;
  - moins de 20 requêtes par seconde (ici 3 requêtes toutes les 3 h) ;
  - licence CC BY 4.0 : citer MET Norway, lien vers la licence, signaler les modifications.
robots.txt : seuls les robots de Google sont exclus de /weatherapi/ ; « User-agent: * »
n'interdit rien.

Chaque réponse est archivée telle quelle (JSON compressé) avec, au manifeste, la date de
mise à jour du modèle (meta.updated_at), Last-Modified et Expires. Le passage suivant lit
ces en-têtes dans les manifestes (--state-dir) : il saute un lieu dont l'Expires n'est
pas encore passé et envoie If-Modified-Since sinon.

Utilisation :
    python -m auroracast.collect.metno --out data/raw --state-dir data/raw
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from auroracast.collect.common import stamp, write_run_manifest
from auroracast.collect.noaa import DEFAULT_OUT, USER_AGENT, utc_now
from auroracast.collect.web import PoliteSession
from auroracast.locations import LOCATIONS, Location

log = logging.getLogger("auroracast.collect.metno")

BASE = "https://api.met.no/weatherapi/locationforecast/2.0/complete"
NAMESPACE = "metno"


def forecast_url(loc: Location) -> str:
    """URL de la prévision ; coordonnées tronquées (pas arrondies) à 4 décimales."""
    def trunc(x: float) -> str:
        return f"{int(x * 10_000) / 10_000:.4f}"
    return f"{BASE}?lat={trunc(loc.lat)}&lon={trunc(loc.lon)}"


def _http_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def previous_headers(state_dir: Path | None) -> dict[str, dict]:
    """Derniers en-têtes Last-Modified et Expires reçus pour chaque lieu."""
    found: dict[str, dict] = {}
    if state_dir is None:
        return found
    for path in sorted((state_dir / NAMESPACE / "manifests").glob("**/run_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("ok") and rec.get("location") and rec.get("last_modified"):
                found[rec["location"]] = {"last_modified": rec["last_modified"],
                                          "expires": rec.get("expires")}
    return found


def summarize(content: bytes) -> dict:
    """Contrôle d'exactitude minimal et repères pour le manifeste."""
    data = json.loads(content)
    series = data["properties"]["timeseries"]
    if not series:
        raise ValueError("prévision vide")
    hourly = [s for s in series if "next_1_hours" in s.get("data", {})]
    return {"updated_at": data["properties"]["meta"].get("updated_at"),
            "steps": len(series), "hourly_steps": len(hourly),
            "first_time": series[0]["time"], "last_hourly_time": hourly[-1]["time"] if hourly else None,
            "last_time": series[-1]["time"]}


def collect(
    out_dir: Path = DEFAULT_OUT,
    state_dir: Path | None = None,
    locations: tuple[Location, ...] = LOCATIONS,
    now: Callable[[], datetime] = utc_now,
    session_factory: Callable[[], PoliteSession] = lambda: PoliteSession(USER_AGENT),
) -> list[dict]:
    started = now()
    state = previous_headers(state_dir)
    day_dir = out_dir / NAMESPACE / "locationforecast" / started.strftime("%Y/%m/%d")
    records: list[dict] = []
    with session_factory() as session:
        for loc in locations:
            url = forecast_url(loc)
            rec = {"source": f"forecast_{loc.name}", "technique": "api", "url": url,
                   "location": loc.name, "collected_at": now().isoformat(), "ok": False,
                   "robots": session.robots_info(url)}
            prev = state.get(loc.name, {})
            expires = _http_date(prev.get("expires"))
            if expires and started < expires:
                rec.update(ok=True, skipped="Expires pas encore passé", file=None,
                           expires=prev["expires"], last_modified=prev["last_modified"])
                records.append(rec)
                continue
            headers = {"If-Modified-Since": prev["last_modified"]} if prev.get("last_modified") else None
            try:
                resp = session.get(url, timeout=60, headers=headers)
                rec["http_status"] = resp.status_code
                rec["expires"] = resp.headers.get("Expires")
                rec["last_modified"] = resp.headers.get("Last-Modified") or prev.get("last_modified")
                if resp.status_code == 304:
                    rec.update(ok=True, not_modified=True, file=None)
                elif resp.status_code in (200, 203):
                    info = summarize(resp.content)
                    path = day_dir / loc.name / f"complete_{stamp(started)}.json.gz"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(gzip.compress(resp.content, mtime=0))
                    rec.update(ok=True, file=path.relative_to(out_dir).as_posix(),
                               bytes=len(resp.content), bytes_gz=path.stat().st_size,
                               sha256=hashlib.sha256(resp.content).hexdigest(), **info)
                    if resp.status_code == 203:
                        rec["warning"] = "version de l'API dépréciée (HTTP 203)"
                else:
                    rec["error"] = f"HTTP {resp.status_code}"
            except Exception as exc:  # noqa: BLE001 - consigné dans le manifeste
                rec["error"] = str(exc)
                log.error("%s : %s", loc.name, exc)
            records.append(rec)
    write_run_manifest(out_dir, NAMESPACE, records, started)
    return records


def to_markdown(records: list[dict]) -> str:
    lines = ["## Prévisions MET Norway (Locationforecast 2.0)", "",
             "| Lieu | Résultat | Mise à jour du modèle | Pas horaires | Jusqu'au | Expires |",
             "| --- | --- | --- | --- | --- | --- |"]
    for r in records:
        if r.get("skipped"):
            result = "sauté (Expires)"
        elif r.get("not_modified"):
            result = "inchangée (304)"
        elif r["ok"]:
            result = f"ok, {r['bytes_gz'] // 1024} Ko"
        else:
            result = f"échec : {r.get('error')}"
        lines.append(f"| {r['location']} | {result} | {r.get('updated_at') or '—'} | "
                     f"{r.get('hourly_steps', '—')} | {(r.get('last_time') or '—')[:16]} | "
                     f"{r.get('expires') or '—'} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prévisions MET Norway (Locationforecast)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--state-dir", type=Path, help="dossier contenant les manifestes précédents")
    parser.add_argument("--summary", type=Path, help="fichier Markdown où écrire le résumé")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    records = collect(args.out, args.state_dir)
    md = to_markdown(records)
    print(md)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    return 0 if all(r["ok"] for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())

