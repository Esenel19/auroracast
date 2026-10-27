import gzip
import json

import pandas as pd
import pytest

from auroracast.transform import gsod

RAW = pd.DataFrame([
    # jour normal à Kiruna (°F, miles, nœuds, pouces)
    {"stn": "20440", "wban": "99999", "station_name": "KIRUNA", "day": "2025-01-01",
     "temp": 32.0, "count_temp": 24, "max": 41.0, "min": 23.0, "dewp": 32.0, "visib": 10.0,
     "count_visib": 24, "wdsp": 10.0, "mxpsd": 20.0, "gust": 999.9, "prcp": 0.0,
     "flag_prcp": "I", "sndp": 999.9, "fog": 1, "rain_drizzle": 0, "snow_ice_pellets": 0,
     "hail": 0, "thunder": 0},
    # 2025-01-02 absent ; 2025-01-03 : valeurs absentes, hors bornes, pluie suspecte
    {"stn": "020440", "wban": "99999", "station_name": "KIRUNA", "day": "2025-01-03",
     "temp": 9999.9, "count_temp": 0, "max": 9999.9, "min": 9999.9, "dewp": 9999.9,
     "visib": 999.9, "count_visib": 0, "wdsp": 200.0, "mxpsd": 999.9, "gust": 999.9,
     "prcp": 3.0, "flag_prcp": "G", "sndp": 999.9, "fog": 0, "rain_drizzle": 0,
     "snow_ice_pellets": 0, "hail": 0, "thunder": 0},
    # station inconnue : écartée
    {"stn": "999999", "wban": "99999", "station_name": "X", "day": "2025-01-01",
     "temp": 50.0, "count_temp": 24, "max": 50.0, "min": 50.0, "dewp": 40.0, "visib": 5.0,
     "count_visib": 24, "wdsp": 1.0, "mxpsd": 1.0, "gust": 999.9, "prcp": 0.0,
     "flag_prcp": "G", "sndp": 999.9, "fog": 0, "rain_drizzle": 0, "snow_ice_pellets": 0,
     "hail": 0, "thunder": 0},
])


@pytest.fixture
def cleaned():
    return gsod.clean(RAW, {"kiruna": "020440"})


def test_units_are_converted_to_si(cleaned):
    row = cleaned[0].iloc[0]
    assert row["temp_c"] == 0.0 and row["temp_max_c"] == 5.0 and row["temp_min_c"] == -5.0
    assert row["visibility_km"] == pytest.approx(16.09, abs=0.01)
    assert row["wind_mean_ms"] == pytest.approx(5.14, abs=0.01)
    assert row["rel_humidity_pct"] == 100.0 and row["fog"]


def test_missing_values_bounds_and_continuous_grid(cleaned):
    df, meta = cleaned
    assert list(df["day"].astype(str)) == ["2025-01-01", "2025-01-02", "2025-01-03"]
    assert list(df["source"]) == ["gsod", "absent", "absent"]
    last = df.iloc[2]
    assert pd.isna(last["temp_c"]) and pd.isna(last["visibility_km"])
    assert pd.isna(last["wind_mean_ms"]) and meta["out_of_bounds"] == {"wind_mean_ms": 1}
    assert meta["unknown_station_rows"] == 1 and set(df["location"]) == {"kiruna"}


def test_precipitation_rules(cleaned):
    df, meta = cleaned
    assert pd.isna(df.iloc[0]["precip_mm"])           # lettre I : pas une mesure
    assert meta["precip_not_measured_days"] == 1
    assert df.iloc[2]["precip_mm"] == pytest.approx(76.2)
    assert df.iloc[2]["precip_suspect"]                # > 50 mm sans pluie signalée
    assert meta["precip_suspect_days"] == 1


def test_build_uses_latest_successful_manifest(tmp_path):
    day = tmp_path / "gsod/2026/10/27"
    day.mkdir(parents=True)
    (day / "d.csv.gz").write_bytes(gzip.compress(RAW.to_csv(index=False).encode()))
    man = tmp_path / "gsod/manifests/2026/10/27"
    man.mkdir(parents=True)
    (man / "run_20261027T100000Z.jsonl").write_text(json.dumps(
        {"source": "gsod_daily", "ok": False}) + "\n")
    (man / "run_20261027T110000Z.jsonl").write_text(json.dumps(
        {"source": "gsod_daily", "ok": True, "file": "gsod/2026/10/27/d.csv.gz",
         "stations": {"kiruna": "020440"}, "job_id": "j1"}) + "\n")
    tables, report = gsod.build(tmp_path)
    assert len(tables["weather_daily"]) == 3
    q = report["weather_daily"]
    assert q["bigquery_job_id"] == "j1" and q["locations"]["kiruna"]["missing_days"] == 2


def test_build_without_collection(tmp_path):
    tables, report = gsod.build(tmp_path)
    assert tables == {} and "aucune" in report["weather_daily"]["error"]
