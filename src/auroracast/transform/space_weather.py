"""Tables propres de météo de l'espace : Kp, indices solaires, vent solaire, prévisions.

  kp_3h             une ligne par tranche de 3 h : Kp (GFZ, complété par la NOAA pour
                    les jours que le GFZ n'a pas encore publiés)
  solar_daily       une ligne par jour : Ap, taches solaires, flux F10.7 (GFZ)
  solar_wind_hourly une ligne par heure : Bt, By, Bz, vitesse, densité (OMNIWeb, complété
                    par les mesures temps réel NOAA RTSW agrégées à l'heure)
  kp_forecast       une ligne par prévision émise : heure visée, Kp prévu, date d'émission
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from auroracast.collect import gfz as gfz_raw
from auroracast.collect import omni as omni_raw
from auroracast.transform.common import apply_bounds, collected_at, latest_collection, quality

MAX_INTERP_HOURS = 2      # trou de vent solaire comblé par interpolation au plus
MIN_MINUTES_PER_HOUR = 15  # minutes de mesure RTSW nécessaires pour une moyenne horaire
WIND_COLS = ("bt", "by_gsm", "bz_gsm", "density", "speed")


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)
    return data.decode("utf-8", errors="replace")


# --- GFZ : Kp tri-horaire et indices quotidiens ---------------------------------------

def gfz_tables(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    files = sorted((raw_dir / "gfz").glob("**/kp_ap_ap_sn_f107_*Z.txt"))
    if not files:
        raise FileNotFoundError("aucun fichier GFZ dans les données brutes")
    path = files[-1]  # la collecte la plus récente contient tout l'historique
    rows = gfz_raw.parse(_read_text(path))
    kp_rows, daily_rows = [], []
    for r in rows:
        day = pd.Timestamp(r["date"]).tz_localize("UTC")
        status = "definitive" if r["D"] >= 1 else "preliminary"
        for i, (kp, ap) in enumerate(zip(r["kp"], r["ap"], strict=True)):
            kp_rows.append((day + pd.Timedelta(hours=3 * i), kp, ap, status))
        daily_rows.append((day, r["Ap"], r["SN"], r["f107_obs"], r["f107_adj"],
                           "definitive" if r["D"] == 2 else "preliminary"))
    kp = pd.DataFrame(kp_rows, columns=["time", "kp", "ap", "status"])
    daily = pd.DataFrame(daily_rows, columns=["date", "Ap", "sn", "f107_obs", "f107_adj",
                                              "sn_status"])
    for df, cols in ((kp, ["kp", "ap"]), (daily, ["Ap", "sn", "f107_obs", "f107_adj"])):
        df[cols] = df[cols].astype(float).replace(-1.0, np.nan)
    kp["source"] = "gfz"
    daily["source"] = "gfz"
    return kp, daily, {"gfz_file": path.relative_to(raw_dir).as_posix()}


# --- NOAA : Kp observé récent et prévisions -----------------------------------------

def _noaa_json(raw_dir: Path, source: str) -> list[tuple[Path, list]]:
    out = []
    for p in sorted((raw_dir / "noaa" / source).glob("**/*.json")):
        try:
            out.append((p, json.loads(p.read_text(encoding="utf-8"))))
        except json.JSONDecodeError:
            continue
    return out


def noaa_kp(raw_dir: Path) -> pd.DataFrame:
    """Kp observé NOAA (fichier des 7 derniers jours) : la collecte la plus récente gagne."""
    rows = []
    for path, data in _noaa_json(raw_dir, "kp_observed"):
        at = collected_at(path)
        for r in data:
            if isinstance(r, dict) and "time_tag" in r:
                rows.append((pd.Timestamp(r["time_tag"]).tz_localize("UTC"),
                             r.get("Kp"), at))
    df = pd.DataFrame(rows, columns=["time", "kp", "collected_at"])
    if df.empty:
        return df
    df = df.sort_values("collected_at").drop_duplicates("time", keep="last")
    df["kp"] = df["kp"].astype(float)
    df["status"] = "noaa_estimated"
    df["source"] = "noaa"
    return df.drop(columns="collected_at")


def kp_3h(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    gfz_kp, daily, meta = gfz_tables(raw_dir)
    gfz_kp = gfz_kp[gfz_kp["kp"].notna()]
    noaa = noaa_kp(raw_dir)
    last_gfz = gfz_kp["time"].max()
    recent = noaa[noaa["time"] > last_gfz] if not noaa.empty else noaa
    kp = pd.concat([gfz_kp, recent], ignore_index=True).sort_values("time")
    before = len(kp)
    kp = kp.drop_duplicates("time", keep="first").reset_index(drop=True)
    removed = apply_bounds(kp)
    removed_daily = apply_bounds(daily)
    meta.update(kp_duplicates_removed=before - len(kp), kp_out_of_bounds=removed,
                daily_out_of_bounds=removed_daily, noaa_rows_added=len(recent))
    return kp[["time", "kp", "ap", "status", "source"]], daily, meta


def kp_forecast(raw_dir: Path) -> tuple[pd.DataFrame, dict]:
    """Prévisions NOAA : chaque valeur « predicted » avec sa date d'émission (la première
    collecte où elle apparaît). Une prévision inchangée d'une collecte à l'autre n'est
    gardée qu'une fois."""
    rows = []
    for path, data in _noaa_json(raw_dir, "kp_forecast"):
        at = collected_at(path)
        for r in data:
            if isinstance(r, dict) and r.get("observed") == "predicted":
                target = pd.Timestamp(r["time_tag"]).tz_localize("UTC")
                rows.append((at, target, float(r["kp"]), r.get("noaa_scale")))
    df = pd.DataFrame(rows, columns=["issued_at", "target_time", "kp", "noaa_scale"])
    if df.empty:
        return df, {"rows": 0}
    collections = df["issued_at"].nunique()
    df = (df.sort_values("issued_at")
            .drop_duplicates(["target_time", "kp"], keep="first")
            .reset_index(drop=True))
    df["lead_hours"] = ((df["target_time"] - df["issued_at"]).dt.total_seconds() / 3600).round(1)
    df["source"] = "noaa"
    return df, {"collections_read": collections, "distinct_forecasts": len(df)}


# --- Vent solaire : OMNIWeb (historique) + NOAA RTSW (temps réel) ------------------

def omni_hourly(raw_dir: Path) -> tuple[pd.DataFrame, dict]:
    files = latest_collection(list((raw_dir / "omni").glob("**/omni2_*.dat")))
    frames = []
    for path in files:
        rows = omni_raw.parse(_read_text(path))
        df = pd.DataFrame(rows)
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["time", *WIND_COLS, "source"]), {"omni_files": 0}
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df[["time", *WIND_COLS]].astype({c: float for c in WIND_COLS})
    # OMNI publie l'année entière : les heures futures sont entièrement vides
    df = df[df[list(WIND_COLS)].notna().any(axis=1)]
    df["source"] = "omni"
    return df, {"omni_files": len(files),
                "omni_last_valid": df["time"].max().isoformat() if len(df) else None}


def rtsw_hourly(raw_dir: Path) -> tuple[pd.DataFrame, dict]:
    """Mesures à la minute NOAA RTSW : satellite actif seulement, doublons retirés,
    moyenne horaire (minimum de Bz et maximum de vitesse en plus)."""
    def minutes(source: str, fields: dict[str, str]) -> pd.DataFrame:
        rows = []
        for _, data in _noaa_json(raw_dir, source):
            for r in data:
                if isinstance(r, dict) and r.get("active") is True:
                    rows.append({"time": r["time_tag"], **{k: r.get(v) for k, v in fields.items()}})
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["time"] = pd.to_datetime(df["time"], utc=True)
        return df.drop_duplicates("time").set_index("time").astype(float)

    mag = minutes("solar_wind_mag", {"bt": "bt", "by_gsm": "by_gsm", "bz_gsm": "bz_gsm"})
    plasma = minutes("solar_wind_plasma", {"speed": "proton_speed", "density": "proton_density"})
    if mag.empty and plasma.empty:
        return pd.DataFrame(columns=["time", *WIND_COLS, "source"]), {"rtsw_minutes": 0}
    parts = []
    if not mag.empty:
        g = mag.resample("1h")
        m = g[["bt", "by_gsm", "bz_gsm"]].mean()
        m["bz_min"] = g["bz_gsm"].min()
        m["n_mag"] = g["bz_gsm"].count()
        m.loc[m["n_mag"] < MIN_MINUTES_PER_HOUR, ["bt", "by_gsm", "bz_gsm", "bz_min"]] = np.nan
        parts.append(m)
    if not plasma.empty:
        g = plasma.resample("1h")
        p = g[["speed", "density"]].mean()
        p["speed_max"] = g["speed"].max()
        p["n_plasma"] = g["speed"].count()
        p.loc[p["n_plasma"] < MIN_MINUTES_PER_HOUR, ["speed", "density", "speed_max"]] = np.nan
        parts.append(p)
    df = pd.concat(parts, axis=1).reset_index()
    df["source"] = "noaa_rtsw"
    return df, {"rtsw_minutes_mag": len(mag), "rtsw_minutes_plasma": len(plasma)}


def interpolate_short_gaps(df: pd.DataFrame, cols: tuple[str, ...],
                           max_hours: int = MAX_INTERP_HOURS) -> pd.DataFrame:
    """Comble par interpolation linéaire les trous d'au plus max_hours heures consécutives
    (jamais au bord de la série) et marque les lignes concernées."""
    df = df.set_index("time").sort_index()
    before = df[list(cols)].isna()
    for c in cols:
        s = df[c]
        gap_id = s.notna().cumsum()
        gap_len = s.isna().groupby(gap_id).transform("sum")
        short = s.isna() & (gap_len <= max_hours)
        filled = s.interpolate(method="time", limit_area="inside")
        df[c] = s.where(~short, filled)
    df["interpolated"] = (before & df[list(cols)].notna()).any(axis=1)
    return df.reset_index()


def solar_wind_hourly(raw_dir: Path) -> tuple[pd.DataFrame, dict]:
    omni, m1 = omni_hourly(raw_dir)
    rtsw, m2 = rtsw_hourly(raw_dir)
    last_omni = omni["time"].max() if len(omni) else None
    recent = rtsw[rtsw["time"] > last_omni] if last_omni is not None and len(rtsw) else rtsw
    df = pd.concat([omni, recent], ignore_index=True).sort_values("time")
    before = len(df)
    df = df.drop_duplicates("time", keep="first")
    duplicates = before - len(df)
    # grille horaire continue : un trou devient une ligne de NaN, visible et comptée
    grid = pd.DataFrame({"time": pd.date_range(df["time"].min(), df["time"].max(), freq="1h")})
    df = grid.merge(df, on="time", how="left")
    df["source"] = df["source"].fillna("absent")
    removed = apply_bounds(df)
    df = interpolate_short_gaps(df, WIND_COLS)
    df.loc[df["interpolated"] & (df["source"] == "absent"), "source"] = "interpolated"
    cols = ["time", *WIND_COLS, "bz_min", "speed_max", "source", "interpolated"]
    for c in ("bz_min", "speed_max"):
        if c not in df:
            df[c] = np.nan
    meta = {**m1, **m2, "duplicates_removed": duplicates,
            "out_of_bounds": removed, "interpolated_rows": int(df["interpolated"].sum()),
            "rtsw_rows_added": len(recent)}
    return df[cols], meta


# --- Construction de toutes les tables -------------------------------------------

def build(raw_dir: Path) -> tuple[dict[str, pd.DataFrame], dict]:
    kp, daily, m_kp = kp_3h(raw_dir)
    forecast, m_fc = kp_forecast(raw_dir)
    wind, m_wind = solar_wind_hourly(raw_dir)
    tables = {"kp_3h": kp, "solar_daily": daily, "solar_wind_hourly": wind,
              "kp_forecast": forecast}
    report = {
        "kp_3h": quality(kp, "time", "3h", {k: v for k, v in m_kp.items()
                                            if not k.startswith("daily")}),
        "solar_daily": quality(daily, "date", "1D",
                               {"out_of_bounds": m_kp.get("daily_out_of_bounds", {})}),
        "solar_wind_hourly": quality(wind, "time", "1h", m_wind),
        "kp_forecast": {**m_fc, "rows": len(forecast),
                        "first_issue": forecast["issued_at"].min().isoformat() if len(forecast) else None,
                        "last_issue": forecast["issued_at"].max().isoformat() if len(forecast) else None},
    }
    if len(wind):
        absent = wind["source"] == "absent"
        runs = (absent != absent.shift()).cumsum()[absent]
        gaps = wind.loc[absent].groupby(runs)["time"].agg(["min", "max", "count"])
        report["solar_wind_hourly"]["longest_gaps"] = [
            {"start": r["min"].isoformat(), "end": r["max"].isoformat(), "hours": int(r["count"])}
            for _, r in gaps.sort_values("count", ascending=False).head(5).iterrows()]
    return tables, report
