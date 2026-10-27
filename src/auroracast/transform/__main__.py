"""Construit les tables propres à partir des données brutes.

Utilisation :
    python -m auroracast.transform --raw data/raw --out data/clean
    python -m auroracast.transform --raw data/raw --out data/clean --csv   # + copies CSV
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from auroracast.transform import gsod, space_weather, weather

log = logging.getLogger("auroracast.transform")


def to_markdown(report: dict) -> str:
    report = dict(report)
    clouds = report.pop("cloud_hourly", None)
    daily = report.pop("weather_daily", None)
    lines = ["## Tables propres (météo de l'espace)", "",
             "| Table | Lignes | Début | Fin | Périodes manquantes | Sources |",
             "| --- | --- | --- | --- | --- | --- |"]
    for name, r in report.items():
        sources = ", ".join(f"{k} {v}" for k, v in r.get("by_source", {}).items())
        lines.append(f"| {name} | {r.get('rows')} | {r.get('start', r.get('first_issue', ''))} | "
                     f"{r.get('end', r.get('last_issue', ''))} | {r.get('missing_periods', '—')} | "
                     f"{sources} |")
    wind = report.get("solar_wind_hourly", {})
    if wind.get("longest_gaps"):
        lines += ["", "Plus longs trous du vent solaire :", ""]
        lines += [f"- {g['start']} → {g['end']} ({g['hours']} h)" for g in wind["longest_gaps"]]
    if clouds and clouds.get("locations"):
        lines += ["", "## Nébulosité observée (NOAA GHCNh)", "",
                  "| Lieu | Station | Début | Fin | Heures avec nuages | Ciel dégagé (0-2 octas) | Ciel invisible (h) |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for loc, q in clouds["locations"].items():
            lines.append(f"| {loc} | {q['station_id']} | {q['start'][:10]} | {q['end'][:10]} | "
                         f"{q['cloud_share']:.0%} | {q['clear_share']:.0%} | {q['obscured_hours']} |")
    if daily and daily.get("locations"):
        lines += ["", "## Météo quotidienne observée (NOAA GSOD, requêtes SQL BigQuery)", "",
                  ("| Lieu | Station | Début | Fin | Jours manquants | Visibilité absente | "
                   "Précipitations absentes | Jours de brouillard |"),
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for loc, q in daily["locations"].items():
            ms = q["missing_share"]
            lines.append(f"| {loc} | {q['station_name']} | {q['start']} | {q['end']} | "
                         f"{q['missing_days']} | {ms['visibility_km']:.1%} | "
                         f"{ms['precip_mm']:.1%} | {q['fog_days']} |")
        lines.append(f"\nValeurs hors bornes écartées : {daily.get('out_of_bounds') or 'aucune'} ; "
                     f"jours min/moy/max incohérents : {daily.get('temp_incoherent_days')}.")
    elif daily and daily.get("error"):
        lines += ["", f"Météo quotidienne GSOD : {daily['error']}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Transformation des données brutes")
    parser.add_argument("--raw", type=Path, default=Path("data/raw"))
    parser.add_argument("--out", type=Path, default=Path("data/clean"))
    parser.add_argument("--csv", action="store_true", help="écrire aussi des copies CSV")
    parser.add_argument("--summary", type=Path, help="fichier Markdown où écrire le résumé")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tables, report = space_weather.build(args.raw)
    weather_tables, weather_report = weather.build(args.raw)
    tables.update(weather_tables)
    report.update(weather_report)
    daily_tables, daily_report = gsod.build(args.raw)
    tables.update(daily_tables)
    report.update(daily_report)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(args.out / f"{name}.parquet", index=False)
        if args.csv:
            df.to_csv(args.out / f"{name}.csv", index=False)
        log.info("%s : %d lignes", name, len(df))
    (args.out / "rapport_qualite.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md = to_markdown(report)
    (args.out / "rapport_qualite.md").write_text(md + "\n", encoding="utf-8")
    print(md)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
