import gzip
from datetime import UTC, datetime

import pytest

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
    "STATION|Station_name|DATE|sky_cover_layer_1|sky_cover_layer_1_Quality_Code|"
    "sky_cover_layer_baseht_1|sky_cover_summation_1\n"
    "NOE00000001|TROMSO|2025-01-01T00:00:00|BKN:07|1|600|\n"
    "NOE00000001|TROMSO|2025-01-01T01:00:00||||\n"
    "NOE00000001|TROMSO|2025-01-01T02:00:00|BKN:07|1|700|OVC:08\n"
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


def test_inspect_year_file_measures_each_cloud_column():
    info = ghcnh.inspect_year_file(PSV)
    assert info["rows"] == 3
    assert list(info["sky"]) == ["sky_cover_layer_1", "sky_cover_summation_1"]  # sans codes ni hauteurs
    assert info["sky"]["sky_cover_layer_1"] == {"share": 0.667, "samples": [("BKN:07", 2)]}
    assert info["best_sky_column"] == "sky_cover_layer_1" and info["best_sky_share"] == 0.667
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
    assert cands[0]["best_sky_share"] == 0.667 and cands[1]["http_status"] == 404
    assert (tmp_path / "ghcnh/stations/recherche_stations_20261026T210000Z.json").exists()
    md = ghcnh.to_markdown(report)
    assert "TROMSO LANGNES" in md and "`BKN:07` (2)" in md


# --- backfill ---------------------------------------------------------------

YEAR_PSV = (
    "STATION|DATE|sky_condition|sky_cover_layer_1|sky_cover_layer_2\n"
    "S1|2025-01-01T00:00:00|07;;06|BKN:05|OVC:08\n"      # couche la plus couverte : 8
    "S1|2025-01-01T00:50:00||FEW:01|\n"                  # même heure : compte une fois
    "S1|2025-01-01T03:00:00|02;;02||\n"                  # repli sur sky_condition : 2
    "S1|2025-01-01T06:00:00||VV:09|\n"                   # brouillard : 8
    "S1|2025-01-01T07:00:00|||\n"                        # pas de nuages
)


@pytest.mark.parametrize("row,expected", [
    ({"sky_cover_layer_1": "BKN:05", "sky_cover_layer_2": "OVC:08"}, 8),
    ({"sky_cover_layer_1": "CLR:00"}, 0),
    ({"sky_cover_layer_1": "VV:09"}, 8),
    ({"sky_condition": "03;;01"}, 3),
    ({"sky_condition": ";;08"}, None),
    ({"sky_cover_layer_1": "XX:99"}, None),
    ({}, None),
])
def test_total_oktas(row, expected):
    assert ghcnh.total_oktas(row) == expected


def test_cloud_quality_counts_distinct_hours_and_gaps():
    q = ghcnh.cloud_quality(YEAR_PSV, 2025, current_year=2025)
    assert q["rows"] == 5 and q["hours_with_cloud"] == 3
    assert q["longest_gap_h"] == 3.0
    assert q["oktas_distribution"]["8"] == 2 and q["oktas_distribution"]["2"] == 1
    assert q["expected_hours"] == 7  # année en cours : jusqu'à la dernière observation
    past = ghcnh.cloud_quality(YEAR_PSV, 2025, current_year=2026)
    assert past["expected_hours"] == 8760  # année passée : l'année entière est attendue


def test_select_station_prefers_closest_qualified():
    cands = [
        {"id": "A", "distance_km": 1.0, "best_sky_share": 0.1},   # trop peu de nuages
        {"id": "B", "distance_km": 12.0, "best_sky_share": 0.9},
        {"id": "C", "distance_km": 8.0, "best_sky_share": 0.4},
        {"id": "D", "distance_km": 45.0, "best_sky_share": 1.0},  # trop loin
    ]
    assert ghcnh.select_station(cands)["id"] == "C"
    assert ghcnh.select_station(cands[:1]) is None


def test_backfill_pinned_and_auto_selected_stations(tmp_path):
    kiruna = Location("kiruna", 67.8558, 20.2253)
    station_csv = "GHCN_ID,LATITUDE,LONGITUDE,NAME\nSWX00000001,67.82,20.34,KIRUNA AIRPORT\n"
    inspect_psv = PSV  # 67 % de nuages : station retenue

    def fetcher(url, session, timeout=30):
        if url == ghcnh.STATION_LIST_URL:
            return Resp(station_csv)
        if "SWX00000001_2025" in url:
            return Resp(inspect_psv)
        if url.endswith("_2026.psv"):
            return Resp(YEAR_PSV)
        return Resp("", 404)

    config = {"tromso": {"station_id": "NOM00001026", "name": "TROMSO", "distance_km": 0.9,
                         "reason": "fixée"}, "kiruna": None}
    records = ghcnh.backfill(tmp_path, 2025, 2026, (TROMSO, kiruna), config, now=lambda: FIXED,
                             fetcher=fetcher, session_factory=OfflineSession)
    by = {r["source"]: r for r in records}
    assert by["station_kiruna"]["station"]["station_id"] == "SWX00000001"
    assert "Choix automatique" in by["station_kiruna"]["station"]["reason"]
    assert by["cloud_tromso_2025"]["missing_year"] is True
    rec = by["cloud_kiruna_2026"]
    assert rec["ok"] and rec["quality"]["hours_with_cloud"] == 3
    assert gzip.decompress((tmp_path / rec["file"]).read_bytes()).decode() == YEAR_PSV
    md = ghcnh.backfill_markdown(records)
    assert "KIRUNA AIRPORT" in md and "| kiruna | 2026 | ok | 3 |" in md


def test_backfill_reports_location_without_station(tmp_path):
    far = Location("nulle_part", 0.0, 0.0)

    def fetcher(url, session, timeout=30):
        return Resp("GHCN_ID,LATITUDE,LONGITUDE,NAME\nX,10.0,10.0,LOIN\n")

    records = ghcnh.backfill(tmp_path, 2025, 2025, (far,), {}, now=lambda: FIXED,
                             fetcher=fetcher, session_factory=OfflineSession)
    assert records[0]["ok"] is False and "aucune station" in records[0]["error"]
