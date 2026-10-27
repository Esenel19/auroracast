import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from auroracast.transform import space_weather as sw
from auroracast.transform.common import apply_bounds, collected_at, latest_collection

HEADER = "\n".join(f"# en-tête {i}" for i in range(40))


def gfz_line(y, m, d, kps, flag=2, sn=50):
    k = " ".join(f"{v:6.3f}" for v in kps)
    a = " ".join(f"{5:3d}" for _ in range(8))
    return f"{y:4d} {m:02d} {d:02d} 34000 34000.5 2600 01 {k} {a}   5 {sn:3d}    150.2    148.9 {flag}"


def omni_line(year, doy, hour, bz="-3.0", speed="450.", density="4.0"):
    w = ["0"] * 55
    w[0:3] = [str(year), str(doy), str(hour)]
    w[8], w[15], w[16], w[23], w[24], w[38] = "6.0", "1.0", bz, density, speed, "20"
    return " ".join(w)


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def raw(tmp_path):
    r = tmp_path / "raw"
    write(r / "gfz/kp_ap_ap_sn_f107/2026/10/26/kp_ap_ap_sn_f107_20261026T210000Z.txt",
          HEADER + "\n" + gfz_line(2026, 10, 24, [1.0] * 8) + "\n"
          + gfz_line(2026, 10, 25, [2.0] * 7 + [-1.0], flag=0, sn=-1) + "\n")
    # deux collectes OMNI de la même année : la plus récente doit gagner
    write(r / "omni/omni2/2026/10/25/omni2_2026.dat", omni_line(2026, 298, 0, bz="99.0"))
    write(r / "omni/omni2/2026/10/26/omni2_2026.dat",
          "\n".join([omni_line(2026, 298, 0), omni_line(2026, 298, 1, bz="999.9"),
                     omni_line(2026, 298, 2, bz="-5.0"), omni_line(2026, 298, 3, bz="999.9",
                                                                     speed="9999.", density="999.9")]))
    write(r / "noaa/kp_observed/2026/10/27/kp_observed_20261027T080000Z.json", json.dumps(
        [{"time_tag": "2026-10-25T21:00:00", "Kp": 5.0},       # déjà couvert par le GFZ
         {"time_tag": "2026-10-26T00:00:00", "Kp": 3.33}]))
    for stamp in ("20261027T070000Z", "20261027T080000Z"):
        write(r / f"noaa/kp_forecast/2026/10/27/kp_forecast_{stamp}.json", json.dumps(
            [{"time_tag": "2026-10-26T00:00:00", "kp": 3.33, "observed": "observed"},
             {"time_tag": "2026-10-28T00:00:00", "kp": 4.0, "observed": "predicted"},
             {"time_tag": "2026-10-28T03:00:00", "kp": 3.0 if stamp.endswith("70000Z") else 5.0,
              "observed": "predicted"}]))
    mag = [{"time_tag": f"2026-10-26T05:{m:02d}:00", "active": True, "bt": 5.0, "by_gsm": 1.0,
            "bz_gsm": -2.0 - m / 10} for m in range(20)]
    mag += [{"time_tag": "2026-10-26T05:00:00", "active": False, "bt": 99, "by_gsm": 0,
             "bz_gsm": 50}]                                     # satellite inactif : ignoré
    mag += [{"time_tag": "2026-10-26T06:00:00", "active": True, "bt": 5, "by_gsm": 1,
             "bz_gsm": -1}]                                     # 1 minute seulement : NaN
    write(r / "noaa/solar_wind_mag/2026/10/26/solar_wind_mag_20261026T060000Z.json",
          json.dumps(mag + mag[:3]))                            # doublons
    plasma = [{"time_tag": f"2026-10-26T05:{m:02d}:00", "active": True, "proton_speed": 400 + m,
               "proton_density": 3.0} for m in range(20)]
    write(r / "noaa/solar_wind_plasma/2026/10/26/solar_wind_plasma_20261026T060000Z.json",
          json.dumps(plasma))
    return r


def test_collected_at_and_latest_collection(tmp_path):
    assert collected_at(Path("x_20261027T080000Z.json")) == pd.Timestamp("2026-10-27 08:00", tz="UTC")
    a, b = Path("omni/2026/10/25/omni2_2026.dat"), Path("omni/2026/10/26/omni2_2026.dat")
    assert latest_collection([b, a]) == [b]


def test_apply_bounds_turns_impossible_values_into_nan():
    df = pd.DataFrame({"kp": [3.0, 12.0], "speed": [100.0, 500.0]})
    assert apply_bounds(df) == {"kp": 1, "speed": 1}
    assert df["kp"].isna().tolist() == [False, True]


def test_kp_3h_merges_gfz_and_recent_noaa(raw):
    kp, daily, meta = sw.kp_3h(raw)
    # 15 tranches publiées par le GFZ (la 16e vaut -1 : pas encore publiée), complétées par
    # la NOAA à partir de la première tranche que le GFZ n'a pas encore publiée
    assert len(kp) == 15 + 2
    gfz = kp[kp["source"] == "gfz"]
    assert len(gfz) == 15 and gfz.iloc[-1]["kp"] == 2.0 and gfz.iloc[-1]["status"] == "preliminary"
    noaa = kp[kp["source"] == "noaa"]
    assert noaa["time"].min() == pd.Timestamp("2026-10-25 21:00", tz="UTC")
    assert noaa.iloc[-1]["kp"] == 3.33
    assert kp["time"].is_monotonic_increasing and kp["time"].is_unique
    assert np.isnan(daily.iloc[1]["sn"]) and daily.iloc[1]["sn_status"] == "preliminary"
    assert meta["noaa_rows_added"] == 2


def test_kp_forecast_keeps_first_issue_and_changes(raw):
    fc, meta = sw.kp_forecast(raw)
    assert meta["collections_read"] == 2
    assert len(fc) == 3                 # 4.0 inchangé gardé une fois ; 3.0 puis 5.0
    first = fc[fc["kp"] == 4.0].iloc[0]
    assert first["issued_at"] == pd.Timestamp("2026-10-27 07:00", tz="UTC")
    assert first["lead_hours"] == 17.0


def test_rtsw_hourly_uses_active_satellite_only(raw):
    df, meta = sw.rtsw_hourly(raw)
    h5 = df[df["time"] == pd.Timestamp("2026-10-26 05:00", tz="UTC")].iloc[0]
    assert h5["n_mag"] == 20 and h5["bt"] == 5.0          # l'inactif (bt 99) est ignoré
    assert h5["bz_min"] == pytest.approx(-3.9) and h5["speed_max"] == 419
    h6 = df[df["time"] == pd.Timestamp("2026-10-26 06:00", tz="UTC")].iloc[0]
    assert np.isnan(h6["bz_gsm"])                          # moins de 15 minutes
    assert meta["rtsw_minutes_mag"] == 21


def test_interpolate_short_gaps_only():
    t = pd.date_range("2026-01-01", periods=8, freq="1h", tz="UTC")
    s = [1.0, np.nan, 3.0, np.nan, np.nan, np.nan, 7.0, np.nan]
    out = sw.interpolate_short_gaps(pd.DataFrame({"time": t, "bz_gsm": s}), ("bz_gsm",))
    assert out["bz_gsm"].tolist()[:3] == [1.0, 2.0, 3.0]   # trou d'1 h comblé
    assert out["bz_gsm"].iloc[3:6].isna().all()            # trou de 3 h laissé vide
    assert np.isnan(out["bz_gsm"].iloc[7])                 # jamais au bord
    assert out["interpolated"].tolist() == [False, True] + [False] * 6


def test_solar_wind_hourly_uses_latest_omni_then_continuous_grid(raw):
    df, meta = sw.solar_wind_hourly(raw)
    assert df["time"].is_unique and df["time"].diff().dropna().eq(pd.Timedelta("1h")).all()
    first = df.iloc[0]
    assert first["bz_gsm"] == -3.0 and first["source"] == "omni"   # pas la collecte du 25/10
    assert df.iloc[1]["bz_gsm"] == -4.0 and df.iloc[1]["interpolated"]  # 999.9 puis comblé
    assert meta["omni_files"] == 1


def test_build_and_report(raw):
    tables, report = sw.build(raw)
    assert set(tables) == {"kp_3h", "solar_daily", "solar_wind_hourly", "kp_forecast"}
    assert report["kp_3h"]["missing_periods"] == 0
    assert report["solar_wind_hourly"]["by_source"]["omni"] >= 1
    assert report["kp_forecast"]["distinct_forecasts"] == 3
