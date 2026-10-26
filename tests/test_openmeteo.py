import json
from datetime import UTC, date, datetime

import pytest

from auroracast.collect import openmeteo as om

FIXED = datetime(2026, 10, 26, 21, 0, 0, tzinfo=UTC)
LOC = om.Location("tromso", 69.6496, 18.956)


def payload(days, values=None):
    hours = [f"2025-01-{d:02d}T{h:02d}:00" for d in range(1, days + 1) for h in range(24)]
    vals = values if values is not None else [50] * len(hours)
    return {"hourly": {"time": hours, **{v: vals for v in om.VARIABLES}}}


class OfflineSession:
    def robots_info(self, url):
        return {"status": "absent (test)", "allowed": True, "crawl_delay": None}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


class Resp:
    def __init__(self, data, status=200):
        self.content, self.status_code = json.dumps(data).encode(), status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_location_parse():
    assert om.Location.parse("Kiruna:67.8558:20.2253") == om.Location("Kiruna", 67.8558, 20.2253)


def test_year_range_stops_before_data_delay():
    assert om.year_range(2025, date(2026, 10, 26)) == (date(2025, 1, 1), date(2025, 12, 31))
    assert om.year_range(2026, date(2026, 10, 26)) == (date(2026, 1, 1), date(2026, 10, 20))
    assert om.year_range(2026, date(2026, 1, 3)) is None


def test_build_url_requests_all_cloud_layers_in_utc():
    url = om.build_url(LOC, date(2025, 1, 1), date(2025, 12, 31))
    assert "hourly=cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high" in url
    assert "timezone=GMT" in url and "start_date=2025-01-01" in url


def test_quality_report_flags_missing_nulls_and_range():
    data = payload(1, values=[None] + [120] + [40] * 22)
    q = om.quality_report(data, date(2025, 1, 1), date(2025, 1, 2))
    assert (q["expected_hours"], q["missing_hours"]) == (48, 24)
    assert q["null_share"]["cloud_cover"] == round(1 / 24, 4)
    assert q["out_of_range"]["cloud_cover"] == 1 and len(q["warnings"]) == 2


def test_quality_report_rejects_inconsistent_payload():
    data = payload(1)
    data["hourly"]["cloud_cover_low"] = [1, 2]
    with pytest.raises(ValueError, match="cloud_cover_low"):
        om.quality_report(data, date(2025, 1, 1), date(2025, 1, 1))


def test_collect_one_location_two_years(tmp_path):
    records = om.collect(tmp_path, start_year=2025, end_year=2026, locations=(LOC,),
                         now=lambda: FIXED, session_factory=OfflineSession,
                         fetcher=lambda url, session, timeout=30: Resp(payload(2)))
    assert [r["source"] for r in records] == ["cloud_cover_tromso_2025", "cloud_cover_tromso_2026"]
    assert all(r["ok"] for r in records)
    assert (tmp_path / records[0]["file"]).exists()
    assert (tmp_path / "openmeteo/manifests/2026/10/26/run_20261026T210000Z.jsonl").exists()
