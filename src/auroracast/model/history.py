"""Suivi des modèles dans le temps : de la première version à la dernière.

Trois séries, écrites dans suivi_modeles.json pour la page web/suivi.html :

1. ``backtest`` (rejoué, calculé chaque jour) : « si on avait réentraîné le modèle au début
   de chaque mois depuis 2016 avec les données connues à ce moment-là, quelle note aurait-il
   eue sur le mois suivant ? ». Montre comment le modèle progresse quand l'historique grandit.
2. ``versions`` (réel) : chaque version publiée dans la branche ``models`` (un commit par
   jour), avec sa note sur 2024 → jour J et la quantité de données apprises.
3. ``reel`` (réel) : les prévisions réellement faites par la version en service à chaque
   instant, comparées à ce qui s'est passé ensuite, agrégées par jour.

    python -m auroracast.model.history backtest --out trained            # DATABASE_URL
    python -m auroracast.model.history suivi --ref origin/models --out site
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from auroracast.model.train import (
    FEATURES,
    HORIZONS,
    build_model,
    known,
    metrics,
    model_file,
    read_training,
    target,
)

BACKTEST_START = "2016-01-01"
DEFAULT_THRESHOLDS = {6: 0.21, 24: 0.24, 48: 0.17}


# ------------------------------------------------------------------------- 1. backtest
def backtest(df: pd.DataFrame, horizons=HORIZONS, start: str = BACKTEST_START,
             thresholds: dict | None = None, min_rows: int = 60) -> list[dict]:
    """Réentraînement simulé au début de chaque mois, note sur le mois qui suit.

    Pas de fuite : une tranche n'entre dans l'apprentissage que si toute sa cible (les h heures
    suivantes) est antérieure au début du mois évalué.
    """
    thresholds = thresholds or DEFAULT_THRESHOLDS
    last = df.index.max()
    months = pd.date_range(pd.Timestamp(start, tz="UTC"), last, freq="MS")
    rows = []
    for h in horizons:
        data = known(df, target(h))
        for m in months:
            end = m + pd.offsets.MonthBegin(1)
            tr = data[data.index < m - pd.Timedelta(hours=h)]
            te = data[(data.index >= m) & (data.index < end)]
            if len(te) < min_rows or tr[target(h)].nunique() < 2:
                continue
            model = build_model().fit(tr[FEATURES], tr[target(h)])
            proba = model.predict_proba(te[FEATURES])[:, 1]
            met = metrics(te[target(h)].to_numpy(), proba, thresholds.get(h, 0.5))
            rows.append({"mois": m.strftime("%Y-%m"), "horizon_h": h, "n_appris": len(tr),
                         **{k: met[k] for k in ("n", "roc_auc", "brier", "precision", "rappel", "vp", "fn", "fp")}})
    return rows


# ------------------------------------------------------------------------- 2. versions
def _git(repo: Path, *args: str, binary: bool = False):
    out = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return out.stdout if binary else out.stdout.decode()


def versions(repo: Path, ref: str, horizons=HORIZONS) -> list[dict]:
    """Une entrée par commit de la branche models et par horizon, du plus ancien au plus récent."""
    out = []
    for line in _git(repo, "log", "--reverse", "--format=%H %cI", ref).splitlines():
        sha, date = line.split()
        for h in horizons:
            try:
                m = json.loads(_git(repo, "show", f"{sha}:metrics_{h}h.json"))
            except subprocess.CalledProcessError:
                continue
            t = m["modele"]["test"]
            out.append({"sha": sha[:7], "date": m.get("entraine_le", date), "horizon_h": h,
                        "seuil": m["seuil_alerte"], "n_appris": m.get("production", {}).get("n"),
                        "jusqu_au": m.get("production", {}).get("jusqu_au"),
                        "roc_auc_test": t["roc_auc"], "brier_test": t["brier"],
                        "precision_test": t["precision"], "rappel_test": t["rappel"]})
    return out


def load_bundle(repo: Path, sha: str, h: int) -> dict:
    return joblib.load(io.BytesIO(_git(repo, "show", f"{sha}:{model_file(h)}", binary=True)))


# ------------------------------------------------------------------------- 3. réel
def live_scores(df: pd.DataFrame, vers: list[dict], loader) -> list[dict]:
    """Prévisions de la version en service à chaque tranche, comparées à la réalité, par jour.

    Une tranche est prévue à sa fin (time_utc + 3 h) par la dernière version entraînée avant.
    ``loader(sha, h)`` renvoie le modèle de ce commit.
    """
    rows = []
    for h in sorted({v["horizon_h"] for v in vers}):
        vh = sorted((v for v in vers if v["horizon_h"] == h), key=lambda v: v["date"])
        data = known(df, target(h))
        issued = data.index + pd.Timedelta(hours=3)
        for i, v in enumerate(vh):
            start = pd.Timestamp(v["date"])
            stop = pd.Timestamp(vh[i + 1]["date"]) if i + 1 < len(vh) else None
            mask = (issued >= start) & ((issued < stop) if stop is not None else True)
            part = data[mask]
            if part.empty:
                continue
            b = loader(v["sha"], h)
            proba = b["model"].predict_proba(part[FEATURES])[:, 1]
            frame = pd.DataFrame({"y": part[target(h)].to_numpy(), "p": proba,
                                  "alerte": proba >= b["threshold"]}, index=part.index)
            for day, d in frame.groupby(frame.index.floor("D")):
                y, a = d["y"].to_numpy(bool), d["alerte"].to_numpy(bool)
                rows.append({"jour": day.strftime("%Y-%m-%d"), "horizon_h": h, "version": v["sha"],
                             "n": len(d), "orages": int(y.sum()), "vus": int((y & a).sum()),
                             "alertes": int(a.sum()), "alertes_justes": int((y & a).sum()),
                             "brier": float(np.mean((d["p"] - y) ** 2))})
    return sorted(rows, key=lambda r: (r["jour"], r["horizon_h"]))


# ------------------------------------------------------------------------- commandes
def _url(arg):
    from dotenv import load_dotenv
    load_dotenv()
    url = arg or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL manquant (fichier .env ou --database-url)")
    return url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest", help="réentraînement simulé mois par mois")
    b.add_argument("--out", default="models")
    b.add_argument("--database-url")
    s = sub.add_parser("suivi", help="assemble suivi_modeles.json pour la page web")
    s.add_argument("--repo", default=".")
    s.add_argument("--ref", default="origin/models")
    s.add_argument("--models", default="models", help="dossier contenant backtest.json")
    s.add_argument("--out", default="site")
    s.add_argument("--database-url")
    args = parser.parse_args(argv)

    df = read_training(_url(args.database_url))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.cmd == "backtest":
        rows = backtest(df)
        (out / "backtest.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        print(f"Backtest : {len(rows)} mois × horizon")
        return 0

    repo = Path(args.repo)
    vers = versions(repo, args.ref)
    cache: dict = {}

    def loader(sha, h):
        full = next(v["sha"] for v in vers if v["sha"] == sha)
        if (full, h) not in cache:
            cache[(full, h)] = load_bundle(repo, _git(repo, "rev-parse", full).strip(), h)
        return cache[(full, h)]

    bt_file = Path(args.models) / "backtest.json"
    data = {"genere_utc": datetime.now(UTC).isoformat(timespec="minutes").replace("+00:00", "Z"),
            "backtest": json.loads(bt_file.read_text(encoding="utf-8")) if bt_file.exists() else [],
            "versions": vers, "reel": live_scores(df, vers, loader)}
    (out / "suivi_modeles.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"Suivi : {len(vers)} versions, {len(data['backtest'])} points rejoués, {len(data['reel'])} jours réels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
