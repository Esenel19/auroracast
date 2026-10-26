"""Backfill de l'historique du Kp depuis le GFZ Potsdam (fichier quotidien depuis 1932).

Le fichier Kp_ap_Ap_SN_F107_since_1932.txt contient une ligne par jour UT :
date, 8 Kp (tranches de 3 h), 8 ap, Ap, nombre de taches solaires (SN),
flux F10.7 observé et ajusté, et un indicateur définitif / provisoire (D).
Licence CC BY 4.0 (taches solaires : CC BY-NC 4.0) ; source à citer :
Matzka et al. (2021), Space Weather, https://doi.org/10.1029/2020SW002641

Le fichier est archivé TEL QUEL, accompagné d'un rapport de qualité (exactitude et
exhaustivité : jours manquants, doublons, valeurs hors bornes, part de valeurs
définitives), lui aussi enregistré dans le manifeste.

Utilisation :
    python -m auroracast.collect.gfz --out data/raw
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from auroracast.collect.noaa import DEFAULT_OUT, USER_AGENT, fetch, utc_now
from auroracast.collect.web import PoliteSession

log = logging.getLogger("auroracast.collect.gfz")

URL = "https://kp.gfz.de/fileadmin/files_for_gfz_cms/Kp_ap_Ap_SN_F107_since_1932.txt"
NAME = "kp_ap_ap_sn_f107"
N_FIELDS = 28  # 7 champs de date + 8 Kp + 8 ap + Ap, SN, F10.7obs, F10.7adj, D
MISSING = -1


def parse(text: str) -> list[dict]:
    """Lit les lignes de données (les lignes d'en-tête commencent par #)."""
    rows = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split()
        if len(f) != N_FIELDS:
            raise ValueError(f"ligne {lineno} : {len(f)} champs au lieu de {N_FIELDS}")
        rows.append({
            "date": date(int(f[0]), int(f[1]), int(f[2])),
            "kp": [float(v) for v in f[7:15]],
            "ap": [int(v) for v in f[15:23]],
            "Ap": int(f[23]),
            "SN": int(f[24]),
            "f107_obs": float(f[25]),
            "f107_adj": float(f[26]),
            "D": int(f[27]),
        })
    if not rows:
        raise ValueError("aucune ligne de données")
    return rows


def quality_report(rows: list[dict], max_listed: int = 20) -> dict:
    """Contrôles d'exhaustivité et d'exactitude du fichier GFZ."""
    dates = [r["date"] for r in rows]
    first, last = min(dates), max(dates)
    seen, duplicates = set(), []
    for d in dates:
        if d in seen:
            duplicates.append(d)
        seen.add(d)
    expected = (last - first).days + 1
    missing = [first + timedelta(days=i) for i in range(expected)
               if first + timedelta(days=i) not in seen]
    kp_slots = [k for r in rows for k in r["kp"]]
    kp_missing = sum(k == MISSING for k in kp_slots)
    kp_out_of_range = sum(k != MISSING and not 0 <= k <= 9 for k in kp_slots)
    not_definitive = [r["date"] for r in rows if r["D"] == 0]
    warnings = []
    if missing:
        warnings.append(f"{len(missing)} jour(s) manquant(s)")
    if duplicates:
        warnings.append(f"{len(duplicates)} jour(s) en double")
    if kp_out_of_range:
        warnings.append(f"{kp_out_of_range} Kp hors de [0, 9]")
    return {
        "first_date": first.isoformat(),
        "last_date": last.isoformat(),
        "days": len(rows),
        "expected_days": expected,
        "missing_days": len(missing),
        "missing_sample": [d.isoformat() for d in missing[:max_listed]],
        "duplicate_days": len(duplicates),
        "kp_slots": len(kp_slots),
        "kp_missing_slots": kp_missing,
        "kp_out_of_range": kp_out_of_range,
        "kp_preliminary_since": min(not_definitive).isoformat() if not_definitive else None,
        "warnings": warnings,
    }


def target_path(out_dir: Path, when: datetime) -> Path:
    stamp = when.strftime("%Y%m%dT%H%M%SZ")
    return out_dir / "gfz" / NAME / when.strftime("%Y/%m/%d") / f"{NAME}_{stamp}.txt"


def collect(
    out_dir: Path = DEFAULT_OUT,
    now: Callable[[], datetime] = utc_now,
    fetcher: Callable[..., requests.Response] = fetch,
    session_factory: Callable[[], PoliteSession] = lambda: PoliteSession(USER_AGENT),
) -> dict:
    """Télécharge le fichier GFZ, l'archive, écrit le rapport et le manifeste."""
    started = now()
    record = {"source": NAME, "technique": "api", "url": URL,
              "collected_at": started.isoformat(), "ok": False}
    try:
        with session_factory() as session:
            record["robots"] = session.robots_info(URL)
            resp = fetcher(URL, session, timeout=120)
        record["http_status"] = resp.status_code
        resp.raise_for_status()
        content = resp.content
        report = quality_report(parse(content.decode("utf-8")))
        path = target_path(out_dir, started)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.with_name(path.stem + "_qualite.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        record.update(ok=True, file=path.relative_to(out_dir).as_posix(), bytes=len(content),
                      sha256=hashlib.sha256(content).hexdigest(), quality=report)
        for w in report["warnings"]:
            log.warning("qualité : %s", w)
    except Exception as exc:  # noqa: BLE001 - l'échec est consigné dans le manifeste
        record["error"] = str(exc)
        log.error("%s : %s", NAME, exc)
    manifest = (out_dir / "gfz" / "manifests" / started.strftime("%Y/%m/%d")
                / f"run_{started.strftime('%Y%m%dT%H%M%SZ')}.jsonl")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill du Kp depuis le GFZ (1932 à aujourd'hui)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="dossier des données brutes")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    record = collect(args.out)
    if record["ok"]:
        q = record["quality"]
        log.info("%d jours du %s au %s, %d manquant(s), Kp provisoire depuis %s",
                 q["days"], q["first_date"], q["last_date"], q["missing_days"],
                 q["kp_preliminary_since"])
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
