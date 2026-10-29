import os

import pandas as pd
import pytest

from auroracast.warehouse import load as wh

T0 = pd.Timestamp("2024-05-10 21:00", tz="UTC")
HOURS = pd.date_range(T0, periods=3, freq="1h")


@pytest.fixture
def clean_dir(tmp_path):
    """Jeu minimal de tables propres : 1 lieu, 3 heures."""
    loc = "tromso"
    tables = {
        "locations": pd.DataFrame({"location": [loc], "lat": [69.6496], "lon": [18.956], "station_id": ["NOM00001026"],
                                   "station_name": ["TROMSO"], "station_distance_km": [0.9]}),
        "kp_3h": pd.DataFrame({"time": [T0], "kp": [8.667], "ap": [300.0], "status": ["definitive"], "source": ["gfz"]}),
        "kp_forecast": pd.DataFrame({"issued_at": [T0], "target_time": [HOURS[2]], "kp": [5.0], "noaa_scale": ["G1"],
                                     "lead_hours": [2.0], "source": ["noaa"]}),
        "solar_daily": pd.DataFrame({"date": [T0.floor("D")], "Ap": [200.0], "sn": [150.0], "f107_obs": [200.0],
                                     "f107_adj": [201.0], "sn_status": ["definitive"], "source": ["gfz"]}),
        "solar_wind_hourly": pd.DataFrame({"time": HOURS, "bt": [40.0] * 3, "by_gsm": [1.0] * 3, "bz_gsm": [-35.0, None, -20.0],
                                           "density": [10.0] * 3, "speed": [750.0] * 3, "bz_min": [None] * 3,
                                           "speed_max": [None] * 3, "source": ["omni", "absent", "omni"],
                                           "interpolated": [False] * 3}),
        "cloud_hourly": pd.DataFrame({"location": loc, "time": HOURS, "oktas": [3.0, None, 6.0],
                                      "cloud_cover_pct": [37.5, None, 75.0],
                                      "obscured": pd.array([False, None, False], dtype="boolean"),
                                      "n_obs": [1, 0, 1], "offset_min": [0.0, None, 0.0], "station_id": "NOM00001026",
                                      "source": ["ghcnh", "absent", "ghcnh"]}),
        "weather_hourly": pd.DataFrame({"location": loc, "time": HOURS[[0, 2]], "temp_c": [5.0, 4.0],
                                        "dewpoint_c": [1.0, 1.0], "rel_humidity_pct": [75.0, 80.0],
                                        "pressure_hpa": [1010.0, 1011.0], "wind_speed_ms": [3.0, 2.0],
                                        "wind_dir_deg": [180.0, None], "visibility_km": [40.0, 30.0],
                                        "visibility_capped": pd.array([False, False], dtype="boolean"),
                                        "fog": pd.array([False, False], dtype="boolean"),
                                        "mist": pd.array([False, True], dtype="boolean"),
                                        "present_weather": [None, "BR:10"], "n_obs": [1, 1],
                                        "offset_min": pd.array([0, 0], dtype="Int64"), "station_id": "NOM00001026",
                                        "source": "ghcnh"}),
        "weather_daily": pd.DataFrame({"location": [loc], "day": [T0.date()], "station_id": ["010260"],
                                       **{c: [1.0] for c in ("temp_c", "temp_max_c", "temp_min_c", "dewpoint_c",
                                                             "rel_humidity_pct", "visibility_km", "wind_mean_ms",
                                                             "wind_max_ms", "precip_mm")},
                                       "precip_flag": ["G"],
                                       **{c: pd.array([False], dtype="boolean") for c in
                                          ("precip_suspect", "fog", "rain", "snow", "hail", "thunder")},
                                       "source": ["gsod"]}),
        "weather_forecast": pd.DataFrame({"location": [loc], "issued_at": [T0], "target_time": [HOURS[1]],
                                          "lead_hours": [1.0], "step_hours": [1],
                                          **{c: [10.0] for c in ("cloud_cover_pct", "cloud_low_pct", "cloud_medium_pct",
                                                                 "cloud_high_pct", "fog_pct", "temp_c", "dewpoint_c",
                                                                 "rel_humidity_pct", "pressure_hpa", "wind_speed_ms",
                                                                 "wind_dir_deg", "wind_gust_ms", "precip_mm",
                                                                 "precip_probability_pct")},
                                          "symbol_code": ["clearsky_night"]}),
        "astronomy_hourly": pd.DataFrame({"location": loc, "time": HOURS, "sun_alt_deg": [-0.6, -2.1, -2.3],
                                          "night_phase": ["day", "civil", "civil"], "moon_alt_deg": [5.0] * 3,
                                          "moon_illum_pct": [10.0] * 3, "moon_up": [True] * 3, "polar_night": [False] * 3,
                                          "midnight_sun": [False] * 3, "source": "calcul"}),
    }
    for name, df in tables.items():
        df.to_parquet(tmp_path / f"{name}.parquet", index=False)
    return tmp_path


def test_build_frames_star_schema_is_consistent(clean_dir):
    f = wh.build_frames(clean_dir)
    times = set(f["dim_time"]["time_utc"])
    for name, df in f.items():
        for col in ("time_utc", "target_time"):
            if col in df:
                assert set(df[col]) <= times, f"{name}.{col} hors de dim_time"
        if "source_id" in df:
            assert df["source_id"].notna().all() and set(df["source_id"]) <= set(f["dim_source"]["source_id"])
    obs = f["fact_weather_observed"].set_index("time_utc")
    assert len(obs) == 3                                        # nuages et météo fusionnés
    assert obs.loc[HOURS[0], "oktas"] == 3.0 and obs.loc[HOURS[0], "temp_c"] == 5.0
    assert obs.loc[HOURS[1], "source_id"] == wh.SOURCE_ID["absent"] and obs.loc[HOURS[1], "n_obs"] == 0
    assert bool(f["dim_time"].set_index("time_utc").loc[T0, "aurora_season"]) is False   # mai


def test_unknown_source_is_refused(clean_dir):
    df = pd.read_parquet(clean_dir / "kp_3h.parquet").assign(source="inconnue")
    df.to_parquet(clean_dir / "kp_3h.parquet", index=False)
    with pytest.raises(ValueError, match="source inconnue"):
        wh.build_frames(clean_dir)


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="pas de base PostgreSQL de test (TEST_DATABASE_URL)")
def test_load_into_postgres_and_view(clean_dir):
    import psycopg

    url = os.environ["TEST_DATABASE_URL"]
    result = wh.load(clean_dir, url)
    assert result["rows"]["fact_astronomy"] == 3 and result["database_mb"] > 0
    wh.load(clean_dir, url)                                     # rejouable : même contenu, 2 chargements tracés
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT count(*) FROM fact_weather_observed").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM load_run").fetchone()[0] >= 2
        rows = conn.execute("SELECT time_utc, kp, kp_ge_4, oktas, dark_sky, clear_sky FROM v_aurora_conditions "
                            "WHERE location = 'tromso' ORDER BY time_utc").fetchall()
    assert [r[1] for r in rows] == [pytest.approx(8.667)] * 3          # Kp de la tranche de 3 h, à chaque heure
    assert rows[0][2] is True and rows[0][4] is False and rows[0][5] is False   # Kp >= 4, mais jour et 3 octas
    assert rows[1][3] is None                                   # heure sans observation
