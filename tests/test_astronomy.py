from datetime import UTC, datetime

import numpy as np
import pandas as pd

from auroracast.locations import Location
from auroracast.transform import astronomy

TROMSO = Location("tromso", 69.6496, 18.9560)


def test_night_phase_thresholds():
    alts = np.array([10.0, -0.5, -0.9, -6.5, -12.5, -18.5])
    assert list(astronomy.night_phase(alts)) == ["day", "day", "civil", "nautical", "astronomical", "night"]


def test_tromso_twilight_matches_reference_29_october_2026():
    # Référence sunrise-sunset.org (UTC) : fin du crépuscule nautique 16:19:15, astronomique 17:28:20
    df = astronomy.hourly(TROMSO, datetime(2026, 10, 29, 16, tzinfo=UTC), datetime(2026, 10, 29, 18, tzinfo=UTC))
    assert list(df["night_phase"]) == ["nautical", "astronomical", "night"]
    assert df["sun_alt_deg"].is_monotonic_decreasing


def test_polar_night_and_midnight_sun_in_tromso():
    # Référence : pas de lever du 28/11/2026 au 14/01/2027
    assert astronomy.sun_events(TROMSO, datetime(2026, 11, 27, tzinfo=UTC)) == (False, False)
    assert astronomy.sun_events(TROMSO, datetime(2026, 11, 28, tzinfo=UTC)) == (True, False)
    assert astronomy.sun_events(TROMSO, datetime(2027, 1, 15, tzinfo=UTC)) == (False, False)
    assert astronomy.sun_events(TROMSO, datetime(2026, 6, 21, tzinfo=UTC)) == (False, True)


def test_moon_illumination_near_full_moon():
    # Pleine Lune le 26/10/2026 : disque éclairé à près de 100 %
    df = astronomy.hourly(TROMSO, datetime(2026, 10, 26, 12, tzinfo=UTC), datetime(2026, 10, 26, 12, tzinfo=UTC))
    assert df["moon_illum_pct"].iloc[0] > 97


def test_astronomy_table_covers_future_and_quality():
    df = astronomy.astronomy_hourly(now=datetime(2026, 10, 29, 9, 30, tzinfo=UTC),
                                    start=datetime(2026, 10, 1, tzinfo=UTC), locations=(TROMSO,))
    assert df["time"].min() == pd.Timestamp("2026-10-01", tz="UTC")
    assert df["time"].max() == pd.Timestamp("2026-11-08 09:00", tz="UTC")   # + 10 jours
    assert df["time"].diff().dropna().eq(pd.Timedelta("1h")).all()
    q = astronomy.astronomy_quality(df)["locations"]["tromso"]
    assert q["polar_night_days"] == 0 and q["hours"] == len(df)
