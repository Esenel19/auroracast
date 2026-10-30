"""Prévision des prochaines 24 h par lieu, avec le code couleur, pour l'interface web.

    python -m auroracast.app.predict --out site              # DATABASE_URL lu dans .env
    python -m auroracast.app.predict --now 2026-10-29T09:00Z  # rejouer une date passée

Pour chaque lieu et chaque heure, trois conditions doivent être réunies :

  activité  probabilité de Kp >= 4 dans les 24 h (modèle)   forte / possible / faible
  ciel noir hauteur du Soleil <= -12° (astronomy)          oui / non
  ciel      nébulosité et brouillard prévus (MET Norway)    dégagé / partiel / couvert

  vert   = activité forte  ET ciel noir ET dégagé           aurore probable
  rouge  = activité faible OU pas noir  OU couvert          pas visible
  orange = tous les autres cas                              incertain

Écrit forecast.json, lu par la page web/index.html.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import joblib
import pandas as pd

from auroracast.model.train import FEATURES, MODEL_FILE

HOURS = 24
DARK_SUN_ALT = -12.0            # fin du crépuscule nautique : le ciel est assez noir
CLEAR_MAX, COVERED_MIN = 40.0, 80.0   # nébulosité totale (%)
FOG_COVERED = 50.0              # brouillard (%) : ciel bouché
SOURCES = [
    "Kp : GFZ Potsdam (CC BY 4.0) et NOAA SWPC",
    "Vent solaire : NASA OMNIWeb et NOAA SWPC",
    "Prévisions météo : MET Norway (CC BY 4.0)",
    "Position du Soleil et de la Lune : calcul PyEphem",
]


# ------------------------------------------------------------------------- règles
def activity(p: float, threshold: float) -> str:
    if p >= threshold:
        return "forte"
    return "possible" if p >= threshold / 2 else "faible"


def sky(cloud: float | None, fog: float | None) -> str:
    if cloud is None or pd.isna(cloud):
        return "inconnu"
    if cloud >= COVERED_MIN or (fog is not None and not pd.isna(fog) and fog >= FOG_COVERED):
        return "couvert"
    return "degage" if cloud < CLEAR_MAX else "partiel"


def color(act: str, dark: bool, sky_state: str) -> str:
    if act == "faible" or not dark or sky_state == "couvert":
        return "rouge"
    if act == "forte" and sky_state == "degage":
        return "vert"
    return "orange"


COLOR_RANK = {"rouge": 0, "orange": 1, "vert": 2}


# ------------------------------------------------------------------------- données
def _query(conn, sql: str, params=None) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])


def read_inputs(database_url: str, now: datetime) -> dict:
    import psycopg

    end = now + timedelta(hours=HOURS)
    with psycopg.connect(database_url) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        latest = _query(conn, f"SELECT time_utc, {', '.join(FEATURES)} FROM v_training_kp "
                              "WHERE time_utc + INTERVAL '3 hours' <= %s ORDER BY time_utc DESC LIMIT 1", (now,))
        locations = _query(conn, "SELECT location_id, code, name, lat, lon FROM dim_location ORDER BY lat DESC")
        astro = _query(conn, "SELECT location_id, time_utc, sun_alt_deg, night_phase, moon_alt_deg, moon_illum_pct "
                             "FROM fact_astronomy WHERE time_utc >= %s AND time_utc < %s", (now, end))
        weather = _query(conn, """
            SELECT DISTINCT ON (location_id, target_time) location_id, target_time AS time_utc, issued_at,
                   cloud_cover_pct, cloud_low_pct, fog_pct, temp_c, wind_speed_ms, symbol_code
            FROM fact_weather_forecast
            WHERE target_time >= %s AND target_time < %s AND issued_at <= %s
            ORDER BY location_id, target_time, issued_at DESC""", (now, end, now))
        kp_fc = _query(conn, """
            SELECT DISTINCT ON (target_time) target_time AS time_utc, kp, noaa_scale
            FROM fact_kp_forecast WHERE target_time >= %s - INTERVAL '3 hours' AND target_time < %s
              AND issued_at <= %s ORDER BY target_time, issued_at DESC""", (now, end, now))
    return {"latest": latest, "locations": locations, "astro": astro, "weather": weather, "kp_fc": kp_fc}


# ------------------------------------------------------------------------- prévision
def _num(v, nd=1):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def build_forecast(inputs: dict, bundle: dict, now: datetime, metrics: dict | None = None) -> dict:
    latest = inputs["latest"]
    if latest.empty:
        raise ValueError("aucune tranche de Kp connue avant cette date")
    row = latest.iloc[[0]]
    x = row[FEATURES].apply(pd.to_numeric, errors="coerce").astype(float)
    p = float(bundle["model"].predict_proba(x)[0, 1])
    threshold = float(bundle["threshold"])
    act = activity(p, threshold)

    kp_fc = inputs["kp_fc"].copy()
    kp_fc["time_utc"] = pd.to_datetime(kp_fc["time_utc"], utc=True)
    hours = pd.date_range(pd.Timestamp(now).floor("h"), periods=HOURS, freq="h")
    out_locations = []
    for loc in inputs["locations"].itertuples():
        a = inputs["astro"][inputs["astro"]["location_id"] == loc.location_id].copy()
        w = inputs["weather"][inputs["weather"]["location_id"] == loc.location_id].copy()
        for df in (a, w):
            df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)
        a, w = a.set_index("time_utc"), w.set_index("time_utc")
        rows = []
        for h in hours:
            ar = a.loc[h] if h in a.index else None
            wr = w.loc[h] if h in w.index else None
            sun = None if ar is None else float(ar["sun_alt_deg"])
            dark = sun is not None and sun <= DARK_SUN_ALT
            cloud = None if wr is None else wr["cloud_cover_pct"]
            fog = None if wr is None else wr["fog_pct"]
            s = sky(cloud, fog)
            slot = kp_fc[(kp_fc["time_utc"] <= h) & (kp_fc["time_utc"] + pd.Timedelta(hours=3) > h)]
            rows.append({
                "time_utc": h.isoformat().replace("+00:00", "Z"),
                "couleur": color(act, dark, s), "ciel_noir": dark, "ciel": s,
                "soleil_deg": _num(sun), "phase": None if ar is None else ar["night_phase"],
                "nuages_pct": _num(cloud, 0), "nuages_bas_pct": None if wr is None else _num(wr["cloud_low_pct"], 0),
                "brouillard_pct": _num(fog, 0),
                "temp_c": None if wr is None else _num(wr["temp_c"]),
                "vent_ms": None if wr is None else _num(wr["wind_speed_ms"]),
                "lune_pct": None if ar is None else _num(ar["moon_illum_pct"], 0),
                "lune_haute": ar is not None and float(ar["moon_alt_deg"]) > 0,
                "kp_noaa": None if slot.empty else _num(slot.iloc[-1]["kp"], 2),
            })
        best = max(rows, key=lambda r: COLOR_RANK[r["couleur"]])
        n_dark = sum(r["ciel_noir"] for r in rows)
        issued = None if w.empty else pd.Timestamp(w["issued_at"].max()).isoformat().replace("+00:00", "Z")
        out_locations.append({
            "code": loc.code, "nom": loc.name, "lat": round(float(loc.lat), 4), "lon": round(float(loc.lon), 4),
            "couleur": best["couleur"],
            "meilleure_heure_utc": best["time_utc"] if best["couleur"] != "rouge" else None,
            "heures_noires": n_dark, "heures_vertes": sum(r["couleur"] == "vert" for r in rows),
            "heures_orange": sum(r["couleur"] == "orange" for r in rows),
            "meteo_emise_utc": issued, "heures": rows,
        })

    t = pd.Timestamp(row.index[0] if "time_utc" not in row else row["time_utc"].iloc[0])
    return {
        "genere_utc": now.isoformat().replace("+00:00", "Z"),
        "activite": {"probabilite_kp4_24h": round(p, 4), "seuil_alerte": threshold, "niveau": act,
                     "kp_actuel": _num(row["kp"].iloc[0], 2), "kp_max_24h": _num(row["kp_max_24h"].iloc[0], 2),
                     "bz_nt": _num(row["bz_mean_3h"].iloc[0]), "vitesse_kms": _num(row["speed_mean_6h"].iloc[0], 0),
                     "tranche_kp_utc": pd.Timestamp(t).tz_convert("UTC").isoformat().replace("+00:00", "Z")},
        "regles": {"ciel_noir_soleil_deg": DARK_SUN_ALT, "degage_max_pct": CLEAR_MAX,
                   "couvert_min_pct": COVERED_MIN, "brouillard_couvert_pct": FOG_COVERED},
        "modele": metrics or {},
        "lieux": out_locations,
        "sources": SOURCES,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="site")
    parser.add_argument("--models", default="models")
    parser.add_argument("--now", default=None, help="date de référence UTC (par défaut : maintenant)")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv()
    url = args.database_url or os.environ.get("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL manquant (fichier .env ou --database-url)")
    now = (pd.Timestamp(args.now).tz_convert("UTC") if args.now else pd.Timestamp(datetime.now(UTC))).floor("h")
    now = now.to_pydatetime()

    bundle = joblib.load(Path(args.models) / MODEL_FILE)
    metrics_file = Path(args.models) / "metrics.json"
    metrics = None
    if metrics_file.exists():
        m = json.loads(metrics_file.read_text(encoding="utf-8"))
        t = m["modele"]["test"]
        metrics = {"entraine_utc": m.get("entraine_le"), "roc_auc_test": t["roc_auc"],
                   "precision_test": t["precision"], "rappel_test": t["rappel"]}
    data = build_forecast(read_inputs(url, now), bundle, now, metrics)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "forecast.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    a = data["activite"]
    print(f"Probabilité Kp >= 4 dans les 24 h : {a['probabilite_kp4_24h']:.0%} ({a['niveau']})")
    for loc in data["lieux"]:
        print(f"  {loc['nom']:10} {loc['couleur']:7} heures noires {loc['heures_noires']:2}  "
              f"vertes {loc['heures_vertes']:2}  orange {loc['heures_orange']:2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
