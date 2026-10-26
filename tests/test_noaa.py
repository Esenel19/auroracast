import json
from datetime import UTC, datetime

import pytest

from auroracast.collect import noaa

FIXED = datetime(2026, 10, 26, 19, 0, 5, tzinfo=UTC)


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class OfflineSession:
    """Session factice : robots.txt « absent », aucun accès réseau."""

    def robots_info(self, url):
        return {"robots_url": "x/robots.txt", "status": "absent (test)", "allowed": True,
                "crawl_delay": None}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def make_fetcher(content: bytes, status: int = 200):
    return lambda url, session: FakeResponse(content, status)


def kp_source():
    return next(s for s in noaa.SOURCES if s.name == "kp_observed")


def test_target_path_is_partitioned_by_utc_date(tmp_path):
    path = noaa.target_path(tmp_path, kp_source(), FIXED)
    assert path.relative_to(tmp_path).as_posix() == (
        "noaa/kp_observed/2026/10/26/kp_observed_20261026T190005Z.json"
    )


def test_collect_one_writes_raw_file_and_manifest_record(tmp_path):
    body = json.dumps([["time_tag", "Kp"], ["2026-10-26 18:00:00", "2.33"]]).encode()
    rec = noaa.collect_one(kp_source(), tmp_path, session=None,
                           now=lambda: FIXED, fetcher=make_fetcher(body))
    assert rec["ok"] is True
    assert (tmp_path / rec["file"]).read_bytes() == body  # stocké tel quel
    assert rec["bytes"] == len(body) and len(rec["sha256"]) == 64


@pytest.mark.parametrize("body,status", [(b"[]", 200), (b"", 200), (b"oops", 404)])
def test_collect_one_records_failures_without_writing(tmp_path, body, status):
    rec = noaa.collect_one(kp_source(), tmp_path, session=None,
                           now=lambda: FIXED, fetcher=make_fetcher(body, status))
    assert rec["ok"] is False and "error" in rec
    assert not (tmp_path / "noaa" / "kp_observed").exists()


def test_run_appends_one_manifest_line_per_source(tmp_path):
    body = json.dumps([["a"], ["b"]]).encode()
    noaa.run(tmp_path, only=["kp_observed", "alerts"], now=lambda: FIXED,
             session_factory=OfflineSession,
             fetcher=make_fetcher(body))
    lines = (tmp_path / "noaa" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["source"] for line in lines] == ["kp_observed", "alerts"]


def test_run_rejects_unknown_source(tmp_path):
    with pytest.raises(SystemExit):
        noaa.run(tmp_path, only=["inexistante"], session_factory=OfflineSession)


def test_run_manifest_writes_one_file_per_execution(tmp_path):
    body = json.dumps([["a"], ["b"]]).encode()
    noaa.run(tmp_path, only=["alerts"], run_manifest=True, now=lambda: FIXED,
             session_factory=OfflineSession,
             fetcher=make_fetcher(body))
    expected = tmp_path / "noaa/manifests/2026/10/26/run_20261026T190005Z.jsonl"
    assert expected.exists()
    assert not (tmp_path / "noaa" / "manifest.jsonl").exists()


def rtsw_source():
    return next(s for s in noaa.SOURCES if s.name == "solar_wind_mag")


def test_window_keeps_only_recent_rows_unchanged(tmp_path):
    rows = [
        {"time_tag": "2026-10-26T14:00:00", "bz_gsm": 1.0},   # 5 h avant : écarté
        {"time_tag": "2026-10-26 17:30:00.000", "bz_gsm": -2.5},
        {"time_tag": "2026-10-26T19:00:00", "bz_gsm": -4.1},
    ]
    rec = noaa.collect_one(rtsw_source(), tmp_path, session=None, now=lambda: FIXED,
                           fetcher=make_fetcher(json.dumps(rows).encode()))
    assert rec["ok"] and (rec["rows_kept"], rec["rows_received"]) == (2, 3)
    assert json.loads((tmp_path / rec["file"]).read_text()) == rows[1:]


def test_window_fails_when_feed_is_stale(tmp_path):
    rows = [{"time_tag": "2026-10-24T06:26:00", "bz_gsm": 0.3}]
    rec = noaa.collect_one(rtsw_source(), tmp_path, session=None, now=lambda: FIXED,
                           fetcher=make_fetcher(json.dumps(rows).encode()))
    assert rec["ok"] is False and "aucune donnée" in rec["error"]


def test_run_records_robots_result_in_manifest(tmp_path):
    body = json.dumps([["a"], ["b"]]).encode()
    rec = noaa.run(tmp_path, only=["alerts"], now=lambda: FIXED,
                   session_factory=OfflineSession, fetcher=make_fetcher(body))[0]
    assert rec["robots"]["allowed"] is True
