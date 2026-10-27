import gzip
import json
from datetime import UTC, date, datetime

import pytest

from auroracast.collect import gsod
from auroracast.locations import Location

FIXED = datetime(2026, 10, 27, 10, 0, 0, tzinfo=UTC)
KIRUNA = Location("kiruna", 67.8558, 20.2253)


class Row(dict):
    def items(self):  # comme google.cloud.bigquery.Row
        return super().items()


class FakeJob:
    def __init__(self, rows, dry):
        self._rows = rows
        self.total_bytes_processed = 1000 if dry else 990
        self.total_bytes_billed = 0 if dry else 10_485_760
        self.job_id = None if dry else "job_123"

    def result(self):
        return [Row(r) for r in self._rows]


class FakeClient:
    def __init__(self, stations, daily):
        self.stations, self.daily, self.calls = stations, daily, []

    def query(self, sql, job_config):
        self.calls.append((sql, job_config))
        rows = self.stations if "ST_DISTANCE" in sql else self.daily
        return FakeJob(rows, job_config["dry"])


def fake_config(params, dry):
    return {"params": params, "dry": dry}


STATIONS = [
    {"usaf": "020450", "wban": "99999", "name": "KIRUNA-SOL", "distance_km": 3.0,
     "last_day": "20101231", "last_obs": None, "days_last_year": 0},                                   # fermée avant la période
    {"usaf": "020440", "wban": "99999", "name": "KIRUNA", "distance_km": 6.0,
     "last_day": "20250824", "last_obs": date(2025, 8, 24), "days_last_year": 236},
]
DAILY = [
    {"stn": "020440", "station_name": "KIRUNA", "day": date(2025, 1, 1), "temp": 5.0,
     "visib": 999.9, "wdsp": 3.0, "prcp": 0.0, "sndp": 999.9, "max": 9.0, "min": 1.0,
     "dewp": 2.0, "mxpsd": 5.0, "gust": 999.9},
    {"stn": "020440", "station_name": "KIRUNA", "day": date(2025, 1, 3), "temp": 4.0,
     "visib": 12.4, "wdsp": 2.0, "prcp": 99.99, "sndp": 10.0, "max": 8.0, "min": 0.0,
     "dewp": 1.0, "mxpsd": 4.0, "gust": 999.9},
]


def test_select_station_uses_real_activity_over_stale_metadata():
    stale = [  # table stations figée au 20/09/2021, mais relevés réels en 2025
        {"usaf": "010260", "distance_km": 0.9, "last_day": "20210920", "days_last_year": 236},
        {"usaf": "010250", "distance_km": 4.0, "last_day": "20210920", "days_last_year": 5},
    ]
    assert gsod.select_station(stale, 2025)["usaf"] == "010260"
    stale[0]["days_last_year"] = 0
    assert gsod.select_station(stale, 2025) is None


def test_select_station_skips_closed_and_far_stations():
    assert gsod.select_station(STATIONS, 2025)["usaf"] == "020440"
    assert gsod.select_station(STATIONS[:1], 2025) is None


def test_run_query_estimates_then_runs_with_parameters():
    client = FakeClient(STATIONS, DAILY)
    _, trace = gsod.run_query(client, gsod.SQL_STATIONS, {"lat": 1.0}, fake_config)
    assert [c[1]["dry"] for c in client.calls] == [True, False]      # à blanc puis réel
    assert trace == {"bytes_estimated": 1000, "job_id": "job_123", "bytes_processed": 990,
                     "bytes_billed": 10_485_760, "rows": 2}
    assert "@lat" in gsod.SQL_STATIONS and "1.0" not in gsod.SQL_STATIONS  # paramètre, pas texte


def test_run_query_refuses_too_expensive_query(monkeypatch):
    monkeypatch.setattr(gsod, "MAX_BYTES_BILLED", 500)
    with pytest.raises(RuntimeError, match="trop coûteuse"):
        gsod.run_query(FakeClient(STATIONS, DAILY), gsod.SQL_DAILY, {}, fake_config)


def test_quality_counts_missing_days_and_values():
    q = gsod.quality(DAILY, 2010, 2025)["020440"]
    assert (q["expected_days"], q["missing_days"]) == (3, 1)
    assert q["missing_share"]["visib"] == 0.5 and q["missing_share"]["prcp"] == 0.5


def test_collect_archives_sql_results_and_manifest(tmp_path):
    client = FakeClient(STATIONS, DAILY)
    records = gsod.collect(tmp_path, 2010, 2025, (KIRUNA,), client_factory=lambda: client,
                           now=lambda: FIXED, job_config=fake_config)
    by = {r["source"]: r for r in records}
    assert by["gsod_stations_kiruna"]["station"]["usaf"] == "020440"
    daily = by["gsod_daily"]
    assert daily["ok"] and daily["rows"] == 2 and daily["job_id"] == "job_123"
    sql_call = [c for c in client.calls if "gsod*" in c[0]][-1][1]
    assert sql_call["params"] == {"first_year": "2010", "last_year": "2025",
                                  "stations": ["020440"]}
    station_call = [c for c in client.calls if "ST_DISTANCE" in c[0]][-1][1]
    assert station_call["params"]["last_year"] == "2025"
    text = gzip.decompress((tmp_path / daily["file"]).read_bytes()).decode()
    assert text.splitlines()[0].startswith("stn,station_name,day") and "2025-01-03" in text
    assert (tmp_path / "gsod/2026/10/27/requetes.sql").read_text(encoding="utf-8").count("-- 2. Relevés") == 1
    manifest = tmp_path / "gsod/manifests/2026/10/27/run_20261027T100000Z.jsonl"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["station"]["last_obs"] == "2025-08-24"   # date -> texte ISO
    assert json.loads((tmp_path / by["gsod_stations_kiruna"]["file"]).read_text(encoding="utf-8"))[1]["usaf"] == "020440"
    assert "KIRUNA (USAF 020440, 6.0 km)" in gsod.to_markdown(records)


def test_resolve_project_order(monkeypatch, tmp_path):
    for var in ("GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "CLOUDSDK_CORE_PROJECT",
                "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(var, raising=False)
    key = tmp_path / "cle.json"
    key.write_text(json.dumps({"project_id": "depuis-la-cle"}))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key))
    assert gsod.resolve_project() == "depuis-la-cle"
    monkeypatch.setenv("GCP_PROJECT_ID", "   ")                  # variable vide : ignorée
    assert gsod.resolve_project() == "depuis-la-cle"
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "auth-github")
    assert gsod.resolve_project() == "auth-github"
    monkeypatch.setenv("GCP_PROJECT_ID", "auroracast-512909")
    assert gsod.resolve_project() == "auroracast-512909"
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS")
    for var in ("GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(var)
    with pytest.raises(RuntimeError, match="Variables"):
        gsod.resolve_project()


def test_collect_records_client_failure(tmp_path):
    def broken():
        raise RuntimeError("ID du projet Google Cloud introuvable")

    records = gsod.collect(tmp_path, client_factory=broken, now=lambda: FIXED,
                           job_config=fake_config)
    assert records[0]["source"] == "gsod_client" and "introuvable" in records[0]["error"]


def test_real_job_config_always_sets_a_numeric_cap():
    pytest.importorskip("google.cloud.bigquery")
    for dry in (True, False):
        cfg = gsod._job_config({"lat": 1.0, "stations": ["020440"]}, dry)
        assert cfg.maximum_bytes_billed == gsod.MAX_BYTES_BILLED
        assert cfg.to_api_repr()["query"]["maximumBytesBilled"] == str(gsod.MAX_BYTES_BILLED)
