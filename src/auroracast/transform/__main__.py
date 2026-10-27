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

from auroracast.transform.space_weather import build

log = logging.getLogger("auroracast.transform")


def to_markdown(report: dict) -> str:
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
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Transformation des données brutes")
    parser.add_argument("--raw", type=Path, default=Path("data/raw"))
    parser.add_argument("--out", type=Path, default=Path("data/clean"))
    parser.add_argument("--csv", action="store_true", help="écrire aussi des copies CSV")
    parser.add_argument("--summary", type=Path, help="fichier Markdown où écrire le résumé")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tables, report = build(args.raw)
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
