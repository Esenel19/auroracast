from datetime import UTC, datetime

from auroracast.collect import ghcnh
from auroracast.collect.openmeteo import Location

FIXED = datetime(2026, 10, 26, 21, 0, 0, tzinfo=UTC)
TROMSO = Location("tromso", 69.6496, 18.9560)
STATION_CSV = (
    "GHCN_ID,LATITUDE,LONGITUDE,ELEVATION,STATE,NAME\n"
    "NOE00000001,69.683,18.919,9.0,,TROMSO LANGNES\n"
    "NOE00000002,69.500,19.500,100.0,,TOWARD SOUTHEAST\n"
    "FIE00000003,60.170,24.940,10.0,,HELSINKI\n"
    "BAD,not-a-number,0,0,,BROKEN\n"
)
PSV = (
    "Station_ID|Station_name|DATE|Latitude|Longitude|sky_cover_1|sky_cover_baseht_1|sky_cover_2\n"
    "NOE00000001|TROMSO|2025-01-01T00:00:00|69.68|18.91|BKN:07|600|\n"
    "NOE00000001|TROMSO|2025-01-01T01:00:00|69.68|18.91||| \n"
    "NOE00000001|TROMSO|2025-01-01T02:00:00|69.68|18.91|BKN:07|700|OVC:08\n"
)


def test_parse_station_list_with_header_skips_bad_rows():
    st = ghcnh.parse_station_list(STATION_CSV)
    assert [s["id"] for s in st] == ["NOE00000001", "NOE00000002", "FIE00000003"]
    assert st[0]["name"] == "TROMSO LANGNES"


def test_parse_station_list_without_header():
    st = ghcnh.parse_station_list("NOE00000001,69.683,18.919,9.0,,TROMSO\n")
    assert st[0]["lat"] == 69.683 and st[0]["name"] == "TROMSO"


def test_distance_and_nearest_filters_far_stations():
    assert round(ghcnh.distance_km(0, 0, 0, 1)) == 111
    near = ghcnh.nearest(ghcnh.parse_station_list(STATION_CSV), TROMSO, n=5, max_km=150)
    assert [s["id"] for s in near] == ["NOE00000001", "NOE00000002"]
    assert near[0]["distance_km"] < 5


def test_inspect_year_file_measures_cloud_filling():
    info = ghcnh.inspect_year_file(PSV)
    assert info["rows"] == 3 and info["sky_cover_1_filled"] == 2
    assert info["sky_cover_1_samples"] == [("BKN:07", 2)]
    assert info["sky_cover_columns"] == ["sky_cover_1", "sky_cover_baseht_1", "sky_cover_2"]
    assert info["first_date"] == "2025-01-01T00:00:00" and info["last_date"].endswith("02:00:00")


class Resp:
    def __init__(self, body, status=200):
        self.content, self.status_code = body.encode(), status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class OfflineSession:
    def robots_info(self, url):
        return {"status": "absent (test)", "allowed": True, "crawl_delay": None}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def test_find_stations_builds_report_and_markdown(tmp_path):
    def fetcher(url, session, timeout=30):
        if url == ghcnh.STATION_LIST_URL:
            return Resp(STATION_CSV)
        if "NOE00000001" in url:
            return Resp(PSV)
        return Resp("", 404)

    report = ghcnh.find_stations(tmp_path, 2025, (TROMSO,), now=lambda: FIXED,
                                 fetcher=fetcher, session_factory=OfflineSession)
    cands = report["locations"][0]["candidates"]
    assert cands[0]["sky_cover_1_share"] == 0.667 and cands[1]["http_status"] == 404
    assert (tmp_path / "ghcnh/stations/recherche_stations_20261026T210000Z.json").exists()
    md = ghcnh.to_markdown(report)
    assert "TROMSO LANGNES" in md and "`BKN:07` (2)" in md
