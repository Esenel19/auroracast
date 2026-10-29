"""Table astronomique : obscurité du ciel pour chaque lieu et chaque heure (calcul local).

  astronomy_hourly  une ligne par lieu et par heure UTC, de 2010 à 10 jours après la date
                    du calcul (pour couvrir les prévisions) : hauteur du Soleil, phase de la
                    nuit, hauteur et éclairement de la Lune, nuit polaire, soleil de minuit

Aucune collecte : tout est calculé avec PyEphem (licence MIT, éphémérides intégrées, aucun
fichier à télécharger) à partir des coordonnées du lieu et de l'heure.

Conventions (vérifiées le 29/10/2026 contre sunrise-sunset.org pour Tromsø : lever, coucher
et crépuscules à moins de 5 s près, nuit polaire du 28/11/2026 au 14/01/2027) :
  - hauteurs géométriques, au centre de l'astre, sans réfraction (pressure = 0) : c'est la
    définition des crépuscules (Soleil à -6°, -12°, -18°) ;
  - lever et coucher : bord supérieur du Soleil à -0°34' (réfraction standard). Piège : ne
    pas combiner cet horizon avec une pression non nulle, sinon la réfraction est comptée
    deux fois (environ 11 min d'erreur à Tromsø fin octobre) ;
  - phase de la nuit selon la hauteur du Soleil : day (> -0,833°), civil (jusqu'à -6°),
    nautical (jusqu'à -12°), astronomical (jusqu'à -18°), night (sous -18°) ;
  - nuit polaire / soleil de minuit : le Soleil ne se lève / ne se couche pas de la journée
    (journée UTC) ;
  - valeurs à l'heure pile, comme les observations météo.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ephem
import numpy as np
import pandas as pd

from auroracast.locations import LOCATIONS, Location

START = datetime(2010, 1, 1, tzinfo=UTC)
HORIZON_DAYS = 10
RISE_SET_HORIZON = "-0:34"
PHASES = [(-0.833, "day"), (-6.0, "civil"), (-12.0, "nautical"), (-18.0, "astronomical")]
COLUMNS = ["location", "time", "sun_alt_deg", "night_phase", "moon_alt_deg", "moon_illum_pct",
           "moon_up", "polar_night", "midnight_sun", "source"]


def _observer(loc: Location) -> ephem.Observer:
    obs = ephem.Observer()
    obs.lat, obs.lon = str(loc.lat), str(loc.lon)
    obs.elevation = 0
    obs.pressure = 0  # hauteurs géométriques ; la réfraction passe par l'horizon de -0°34'
    return obs


def night_phase(sun_alt: np.ndarray) -> np.ndarray:
    out = np.full(sun_alt.shape, "night", dtype=object)
    for limit, name in reversed(PHASES):  # du plus sombre au plus clair
        out[sun_alt > limit] = name
    return out


def sun_events(loc: Location, day: datetime) -> tuple[bool, bool]:
    """(nuit polaire, soleil de minuit) pour la journée UTC commençant à day."""
    obs = _observer(loc)
    obs.horizon = RISE_SET_HORIZON
    obs.date = ephem.Date(day)
    try:
        obs.next_rising(ephem.Sun())
        obs.next_setting(ephem.Sun())
    except ephem.NeverUpError:
        return True, False
    except ephem.AlwaysUpError:
        return False, True
    return False, False


def hourly(loc: Location, start: datetime, end: datetime) -> pd.DataFrame:
    times = pd.date_range(start, end, freq="1h", tz="UTC")
    obs, sun, moon = _observer(loc), ephem.Sun(), ephem.Moon()
    sun_alt = np.empty(len(times))
    moon_alt = np.empty(len(times))
    moon_illum = np.empty(len(times))
    for i, t in enumerate(times):
        obs.date = ephem.Date(t.to_pydatetime())
        sun.compute(obs)
        moon.compute(obs)
        sun_alt[i] = math.degrees(sun.alt)
        moon_alt[i] = math.degrees(moon.alt)
        moon_illum[i] = moon.phase  # % du disque éclairé
    days = pd.date_range(times[0].normalize(), times[-1].normalize(), freq="D", tz="UTC")
    events = {d.date(): sun_events(loc, d.to_pydatetime()) for d in days}
    day_keys = times.date
    df = pd.DataFrame({
        "location": loc.name, "time": times,
        "sun_alt_deg": sun_alt.round(2), "night_phase": night_phase(sun_alt),
        "moon_alt_deg": moon_alt.round(2), "moon_illum_pct": moon_illum.round(1),
        "moon_up": moon_alt > 0,
        "polar_night": [events[d][0] for d in day_keys],
        "midnight_sun": [events[d][1] for d in day_keys],
        "source": "calcul",
    })
    return df[COLUMNS]


def astronomy_hourly(now: datetime | None = None, start: datetime = START,
                     locations: tuple[Location, ...] = LOCATIONS) -> pd.DataFrame:
    now = now or datetime.now(UTC)
    end = (now + timedelta(days=HORIZON_DAYS)).replace(minute=0, second=0, microsecond=0)
    return pd.concat([hourly(loc, start, end) for loc in locations], ignore_index=True)


def astronomy_quality(df: pd.DataFrame) -> dict:
    report = {"method": "PyEphem " + ephem.__version__, "locations": {}}
    for loc, g in df.groupby("location"):
        polar = g.loc[g["polar_night"], "time"].dt.date
        report["locations"][loc] = {
            "start": g["time"].min().isoformat(), "end": g["time"].max().isoformat(),
            "hours": len(g),
            "night_phase_share": {k: round(float(v), 4) for k, v in
                                  g["night_phase"].value_counts(normalize=True).items()},
            "polar_night_days": int(polar.nunique()),
            "midnight_sun_days": int(g.loc[g["midnight_sun"], "time"].dt.date.nunique()),
        }
    return report


def build(raw_dir: Path | None = None, now: datetime | None = None) -> tuple[dict[str, pd.DataFrame], dict]:
    """raw_dir n'est pas utilisé (aucune donnée brute) : même signature que les autres tables."""
    df = astronomy_hourly(now)
    return {"astronomy_hourly": df}, {"astronomy_hourly": astronomy_quality(df)}
