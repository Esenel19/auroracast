import gzip
import json
from pathlib import Path

import pandas as pd

from auroracast.transform import forecast

# Extrait d'une vraie réponse MET Norway (Tromsø, émise le 29/10/2026 à 08:30 UTC) :
# 2 pas horaires, 1 pas de 6 h, et le dernier pas, sans période.
SAMPLE = json.loads((Path(__file__).parent / "data/metno_complete_extrait.json").read_text())


def write(raw, loc, stamp, data):
    path = raw / f"metno/locationforecast/2026/10/29/{loc}/complete_{stamp}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(data).encode()))


def test_parse_forecast_reads_hourly_and_six_hourly_steps():
    rows = forecast.parse_forecast(SAMPLE)
    assert [r["step_hours"] for r in rows] == [1, 1, 6]            # dernier pas ignoré
    first = rows[0]
    assert first["cloud_cover_pct"] == 100.0 and first["cloud_low_pct"] == 81.0
    assert first["fog_pct"] == 0.0 and first["temp_c"] == 9.3 and first["precip_mm"] == 0.0
    assert first["precip_probability_pct"] == 28.4 and first["symbol_code"] == "cloudy"


def test_weather_forecast_table_lead_time_bounds_and_duplicates(tmp_path):
    raw = tmp_path / "raw"
    write(raw, "tromso", "20261029T091457Z", SAMPLE)
    bad = json.loads(json.dumps(SAMPLE))
    bad["properties"]["timeseries"][0]["data"]["instant"]["details"]["fog_area_fraction"] = 140.0
    write(raw, "tromso", "20261029T121700Z", bad)                  # même émission, recollectée
    df, meta = forecast.weather_forecast(raw)
    assert len(df) == 3 and meta["duplicate_rows"] == 3
    assert meta["out_of_bounds"] == {"fog_pct": 1}
    first = df.iloc[0]
    assert first["collected_at"] == pd.Timestamp("2026-10-29 12:17", tz="UTC")   # la plus récente
    assert pd.isna(first["fog_pct"])
    assert first["lead_hours"] == 0.49                             # 09:00 - 08:30:21
    assert list(df["step_hours"]) == [1, 1, 6]
    q = forecast.forecast_quality(df, meta)["locations"]["tromso"]
    assert q["forecasts"] == 1 and q["latest_hourly_steps"] == 2


def test_build_without_files(tmp_path):
    tables, report = forecast.build(tmp_path)
    assert tables["weather_forecast"].empty and report["weather_forecast"]["files"] == 0
