import gzip
from pathlib import Path

import numpy as np
import pandas as pd

from auroracast.transform import weather

HEADER = "STATION|DATE|sky_condition|sky_cover_layer_1|sky_cover_layer_2\n"


def write_gz(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(text.encode()))


def test_to_hourly_keeps_observation_closest_to_the_hour():
    obs = pd.DataFrame({
        "obs_time": pd.to_datetime(["2025-01-01T00:20", "2025-01-01T00:50", "2025-01-01T01:00",
                                    "2025-01-01T03:10"], utc=True),
        "oktas": [1.0, 7.0, 3.0, 8.0], "obscured": [False, False, False, True]})
    h = weather.to_hourly(obs).set_index("time")

    def t(s):
        return pd.Timestamp(s, tz="UTC")

    assert h.loc[t("2025-01-01 00:00"), "oktas"] == 1.0      # 00:20 -> 00 h
    assert h.loc[t("2025-01-01 01:00"), "oktas"] == 3.0      # 01:00 bat 00:50
    assert h.loc[t("2025-01-01 01:00"), "n_obs"] == 2
    assert h.loc[t("2025-01-01 03:00"), "offset_min"] == 10


def test_cloud_hourly_builds_continuous_grid_without_interpolation(tmp_path):
    raw = tmp_path / "raw"
    base = raw / "ghcnh/hourly/2026/10/26"
    write_gz(base / "tromso/GHCNh_NOM00001026_2025.psv.gz", HEADER
             + "S|2025-01-01T00:00:00|07;;06|BKN:05|OVC:08\n"
             + "S|2025-01-01T03:00:00|||VV:09|\n"
             + "S|2025-01-01T03:00:00|||VV:09|\n")        # doublon
    # collecte plus ancienne du même fichier : ignorée
    write_gz(raw / "ghcnh/hourly/2026/10/25/tromso/GHCNh_NOM00001026_2025.psv.gz",
             HEADER + "S|2025-01-01T00:00:00|00;;||\n")
    df, meta = weather.cloud_hourly(raw)
    assert len(df) == 4                                      # 00, 01, 02, 03 h
    assert df["oktas"].tolist()[0] == 8.0 and np.isnan(df["oktas"].iloc[1])
    last = df.iloc[-1]
    assert last["obscured"] and last["cloud_cover_pct"] == 100.0
    assert df["source"].tolist() == ["ghcnh", "absent", "absent", "ghcnh"]
    assert meta["tromso"]["duplicate_observations"] == 1
    q = weather.cloud_quality(df, meta)["locations"]["tromso"]
    assert q["hours_with_cloud"] == 2 and q["cloud_share"] == 0.5 and q["obscured_hours"] == 1


def test_locations_table_uses_station_config():
    cfg = {"tromso": {"station_id": "X1", "name": "TROMSO", "distance_km": 0.9}}
    loc = weather.locations_table(cfg).set_index("location")
    assert loc.loc["tromso", "station_id"] == "X1"
    assert pd.isna(loc.loc["kiruna", "station_id"])
