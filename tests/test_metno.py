import gzip
import json
from datetime import UTC, datetime

from auroracast.collect import metno
from auroracast.locations import Location

NOW = datetime(2026, 10, 29, 9, 17, tzinfo=UTC)
TROMSO = Location("tromso", 69.64969, 18.95609)


def body(n_hourly=2):
    series = [{"time": f"2026-10-29T{10 + i:02d}:00:00Z",
               "data": {"instant": {"details": {"cloud_area_fraction": 50.0}},
                        "next_1_hours": {"details": {"precipitation_amount": 0.0}}}}
              for i in range(n_hourly)]
    series.append({"time": "2026-11-07T00:00:00Z",
                   "data": {"instant": {"details": {"cloud_area_fraction": 10.0}},
                            "next_6_hours": {"details": {"precipitation_amount": 0.0}}}})
    return json.dumps({"properties": {"meta": {"updated_at": "2026-10-29T08:40:12Z"},
                                      "timeseries": series}}).encode()


class Resp:
    def __init__(self, status, content=b"", headers=None):
        self.status_code, self.content, self.headers = status, content, headers or {}


class FakeSession:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def robots_info(self, url):
        return {"status": "lu", "allowed": True, "crawl_delay": None}

    def get(self, url, timeout=30, headers=None):
        self.calls.append((url, headers))
        return self.resp

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def run(tmp_path, session, state=None):
    return metno.collect(tmp_path, state, (TROMSO,), now=lambda: NOW, session_factory=lambda: session)


def write_state(tmp_path, expires):
    d = tmp_path / "state/metno/manifests/2026/10/29"
    d.mkdir(parents=True)
    (d / "run_20261029T061700Z.jsonl").write_text(json.dumps(
        {"location": "tromso", "ok": True, "last_modified": "Thu, 29 Oct 2026 05:40:00 GMT",
         "expires": expires}) + "\n")
    return tmp_path / "state"


def test_url_truncates_coordinates_to_four_decimals():
    assert metno.forecast_url(TROMSO).endswith("?lat=69.6496&lon=18.9560")
    rovaniemi = Location("rovaniemi", 66.5039, 25.7294)           # piège de la virgule flottante
    assert metno.forecast_url(rovaniemi).endswith("?lat=66.5039&lon=25.7294")


def test_collect_archives_response_and_headers(tmp_path):
    session = FakeSession(Resp(200, body(), {"Expires": "Thu, 29 Oct 2026 09:47:00 GMT",
                                             "Last-Modified": "Thu, 29 Oct 2026 08:40:12 GMT"}))
    rec = run(tmp_path, session)[0]
    assert rec["ok"] and rec["hourly_steps"] == 2 and rec["steps"] == 3
    assert rec["updated_at"] == "2026-10-29T08:40:12Z" and rec["last_hourly_time"] == "2026-10-29T11:00:00Z"
    assert json.loads(gzip.decompress((tmp_path / rec["file"]).read_bytes())) == json.loads(body())
    assert session.calls[0][1] is None                       # premier passage : pas d'If-Modified-Since
    assert (tmp_path / "metno/manifests/2026/10/29/run_20261029T091700Z.jsonl").exists()


def test_collect_skips_until_expires_then_sends_if_modified_since(tmp_path):
    session = FakeSession(Resp(304, headers={"Expires": "Thu, 29 Oct 2026 10:00:00 GMT"}))
    rec = run(tmp_path, session, write_state(tmp_path, "Thu, 29 Oct 2026 10:00:00 GMT"))[0]
    assert rec["ok"] and rec["skipped"] and session.calls == []
    rec = run(tmp_path / "b", session, write_state(tmp_path / "b", "Thu, 29 Oct 2026 07:00:00 GMT"))[0]
    assert rec["ok"] and rec["not_modified"] and rec["file"] is None
    assert session.calls[0][1] == {"If-Modified-Since": "Thu, 29 Oct 2026 05:40:00 GMT"}
    assert rec["last_modified"] == "Thu, 29 Oct 2026 05:40:00 GMT"


def test_collect_records_errors_and_markdown(tmp_path):
    rec = run(tmp_path, FakeSession(Resp(403)))[0]
    assert rec["ok"] is False and rec["error"] == "HTTP 403"
    assert "échec : HTTP 403" in metno.to_markdown([rec])
