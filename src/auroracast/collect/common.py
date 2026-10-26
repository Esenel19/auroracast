"""Outils partagés par les collecteurs de backfill (manifeste par exécution)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def stamp(when: datetime) -> str:
    return when.strftime("%Y%m%dT%H%M%SZ")


def write_run_manifest(out_dir: Path, namespace: str, records: list[dict], started: datetime) -> Path:
    """Un manifeste JSONL par exécution : <out>/<namespace>/manifests/AAAA/MM/JJ/run_<stamp>.jsonl"""
    path = (out_dir / namespace / "manifests" / started.strftime("%Y/%m/%d")
            / f"run_{stamp(started)}.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path
