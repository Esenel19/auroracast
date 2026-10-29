import gzip
from datetime import date

import pandas as pd
import pytest

from auroracast.transform import weather_hourly as wh

HEADER = ("STATION|DATE|temperature|temperature_Quality_Code|dew_point_temperature|relative_humidity|"
          "sea_level_pressure|wind_speed|wind_direction|visibility|pres_wx_MW1|pres_wx_AW1\n")


def write_gz(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(text.encode()))


def test_wx_flags_reads_manual_automatic_and_abbreviations():
    df = pd.DataFrame({"pres_wx_MW1": ["FG:48", "45", "BR:10", "SN:71", None],
                       "pres_wx_AW1": [None, None, None, "33", "10"]})
    fog, mist, codes = wh.wx_flags(df)
    assert list(fog) == [True, True, False, True, False]           # AW 33 = brouillard
    assert list(mist) == [False, False, True, False, True]
    assert codes.iloc[3] == "SN:71;33" and codes.iloc[4] == "10"


def test_read_observations_quality_codes_and_calm_wind():
    text = HEADER + ("S|2025-01-01T00:00:00|-5.0|1|-6.0|90|1010.0|0.0|999|9.999|FG:48|\n"
                     "S|2025-01-01T01:00:00|80.0|2|-6.0|90|1010.0|3.0|180||SN:71|\n")
    obs, rejected = wh.read_observations(text)
    assert rejected == {"temp_c": 1, "wind_dir_calm_or_variable": 1}
    assert pd.isna(obs.loc[1, "temp_c"]) and pd.isna(obs.loc[0, "wind_dir_deg"])
    assert bool(obs.loc[0, "fog"])
    assert pd.isna(obs.loc[1, "fog"])                               # pas de visibilité : inconnu


def test_weather_hourly_grid_nearest_obs_updates_and_bounds(tmp_path):
    raw = tmp_path / "raw"
    base = raw / "ghcnh/hourly/2026/10/27/kiruna/GHCNh_SWI0000ESNQ_2025.psv.gz"
    write_gz(base, HEADER
             + "S|2025-01-01T00:20:00|-5.0|1|-6.0|90|1010.0|2.0|180|9.999||\n"
             + "S|2025-01-01T00:50:00|-4.0|1|-6.0|90|1200.0|2.0|180|4.0|BR:10|\n"   # pression hors bornes
             + "S|2025-01-01T03:00:00|-3.0|1|-6.0|90|1010.0|2.0|180|8.0||\n")
    # mise à jour plus récente : corrige l'observation de 03:00
    write_gz(raw / "ghcnh/hourly/2026/10/29/kiruna/GHCNh_SWI0000ESNQ_2025_maj_20261029T053700Z.psv.gz",
             HEADER + "S|2025-01-01T03:00:00|-2.5|1|-6.0|90|1010.0|2.0|180|8.0||\n")
    df, meta = wh.weather_hourly(raw)
    h = df.set_index("time")
    t = pd.Timestamp("2025-01-01 00:00", tz="UTC")
    assert len(df) == 4 and list(df["source"]) == ["ghcnh", "ghcnh", "absent", "ghcnh"]
    assert h.loc[t, "temp_c"] == -5.0 and h.loc[t, "visibility_capped"]      # 00:20 -> 00 h
    assert h.loc[t + pd.Timedelta("1h"), "temp_c"] == -4.0                  # 00:50 -> 01 h
    assert h.loc[t + pd.Timedelta("1h"), "mist"] and pd.isna(h.loc[t + pd.Timedelta("1h"), "pressure_hpa"])
    assert h.loc[t + pd.Timedelta("3h"), "temp_c"] == -2.5                  # la mise à jour l'emporte
    q = meta["locations"]["kiruna"]
    assert q["out_of_bounds"] == {"pressure_hpa": 1} and q["duplicate_observations"] == 1
    report = wh.hourly_quality(df, meta)["locations"]["kiruna"]
    assert report["hours_without_obs"] == 1 and report["visibility_capped_share"] == pytest.approx(1 / 3, abs=1e-3)


def test_crosscheck_gsod_compares_daily_means_and_fog_days():
    hours = pd.date_range("2025-01-01", periods=48, freq="1h", tz="UTC")
    hourly = pd.DataFrame({"location": "tromso", "time": hours, "temp_c": [1.0] * 24 + [3.0] * 24,
                           "fog": pd.array([False] * 47 + [True], dtype="boolean")})
    daily = pd.DataFrame({"location": "tromso", "day": [date(2025, 1, 1), date(2025, 1, 2)],
                          "temp_c": [1.2, 3.0], "fog": pd.array([False, True], dtype="boolean")})
    r = wh.crosscheck_gsod(hourly, daily)["locations"]["tromso"]
    assert r["days_compared"] == 2 and r["temp_mean_abs_diff_c"] == 0.1
    assert r["temp_days_within_0_5c"] == 1.0 and r["fog_day_agreement"] == 1.0
