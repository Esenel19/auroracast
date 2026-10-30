from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from auroracast.app import predict as ap
from auroracast.model.train import FEATURES

NOW = datetime(2026, 10, 29, 15, tzinfo=UTC)


@pytest.mark.parametrize(("act", "dark", "sky", "expected"), [
    ("forte", True, "degage", "vert"),
    ("forte", True, "partiel", "orange"),
    ("possible", True, "degage", "orange"),
    ("forte", True, "inconnu", "orange"),
    ("faible", True, "degage", "rouge"),
    ("forte", False, "degage", "rouge"),
    ("forte", True, "couvert", "rouge"),
])
def test_color_rules(act, dark, sky, expected):
    assert ap.color(act, dark, sky) == expected


def test_activity_and_sky_thresholds():
    assert ap.activity(0.30, 0.24) == "forte" and ap.activity(0.15, 0.24) == "possible"
    assert ap.activity(0.05, 0.24) == "faible"
    assert ap.sky(10, 0) == "degage" and ap.sky(50, 0) == "partiel" and ap.sky(90, 0) == "couvert"
    assert ap.sky(10, 60) == "couvert" and ap.sky(None, None) == "inconnu"


class FakeModel:
    def __init__(self, p):
        self.p = p

    def predict_proba(self, x):
        assert list(x.columns) == FEATURES
        return np.array([[1 - self.p, self.p]])


def inputs():
    hours = pd.date_range(NOW, periods=24, freq="h")
    latest = pd.DataFrame([{"time_utc": pd.Timestamp("2026-10-29 09:00", tz="UTC"), **{f: 1.0 for f in FEATURES}}])
    sun = [-20.0 if 2 <= i <= 14 else 5.0 for i in range(24)]          # nuit de 17 h à 05 h
    astro = pd.DataFrame({"location_id": 1, "time_utc": hours, "sun_alt_deg": sun,
                          "night_phase": ["night" if s < 0 else "day" for s in sun],
                          "moon_alt_deg": 10.0, "moon_illum_pct": 50.0})
    cloud = [10.0] * 6 + [95.0] * 18                                    # dégagé jusqu'à 20 h, puis couvert
    weather = pd.DataFrame({"location_id": 1, "time_utc": hours, "issued_at": NOW, "cloud_cover_pct": cloud,
                            "cloud_low_pct": 0.0, "fog_pct": 0.0, "temp_c": -3.0, "wind_speed_ms": 2.0,
                            "symbol_code": "x"})
    return {"latest": latest,
            "locations": pd.DataFrame({"location_id": [1], "code": ["tromso"], "name": ["Tromsø"],
                                       "lat": [69.65], "lon": [18.96]}),
            "astro": astro, "weather": weather,
            "kp_fc": pd.DataFrame({"time_utc": [pd.Timestamp("2026-10-29 15:00", tz="UTC")], "kp": [5.0],
                                   "noaa_scale": ["G1"]})}


def test_build_forecast_storm():
    data = ap.build_forecast(inputs(), {"model": FakeModel(0.6), "threshold": 0.24}, NOW)
    loc = data["lieux"][0]
    colors = [h["couleur"] for h in loc["heures"]]
    assert colors[:2] == ["rouge", "rouge"]                     # jour
    assert colors[2:6] == ["vert"] * 4                          # nuit + dégagé + activité forte
    assert set(colors[6:]) == {"rouge"}                         # couvert puis jour
    assert loc["couleur"] == "vert" and loc["meilleure_heure_utc"] == "2026-10-29T17:00:00Z"
    assert loc["heures_noires"] == 13 and loc["heures_vertes"] == 4
    assert loc["heures"][0]["kp_noaa"] == 5.0 and loc["heures"][3]["kp_noaa"] is None
    assert data["activite"]["niveau"] == "forte"


def test_build_forecast_quiet_is_red():
    data = ap.build_forecast(inputs(), {"model": FakeModel(0.03), "threshold": 0.24}, NOW)
    assert data["lieux"][0]["couleur"] == "rouge" and data["lieux"][0]["meilleure_heure_utc"] is None
