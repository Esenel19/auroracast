import json
from datetime import UTC, datetime

from auroracast.collect import omni

FIXED = datetime(2026, 10, 26, 21, 0, 0, tzinfo=UTC)
INDEX_HTML = """<html><body>
<a href="?C=N;O=D">Name</a>
<a href="https://spdf.gsfc.nasa.gov/pub/data/omni/low_res_omni/omni2_2024.dat">omni2_2024.dat</a>
<a href="omni2_2025.dat">omni2_2025.dat</a>
<a href="omni2_all_years.dat">omni2_all_years.dat</a>
<a href="omni_m2025.dat">omni_m2025.dat</a>
<a href="omni2.text">omni2.text</a>
</body></html>"""


def line(year, doy, hour, bz=-3.2, speed=450.0, fill_bz=False):
    words = ["0"] * omni.N_FIELDS
    words[0:3] = [str(year), str(doy), str(hour)]
    words[8] = "6.5"                       # Bt
    words[15] = "1.1"                      # By GSM
    words[16] = "999.9" if fill_bz else str(bz)
    words[23] = "4.2"                      # densité
    words[24] = str(speed)
    words[38] = "33"                       # Kp x 10
    return " ".join(words)


class OfflineSession:
    def robots_info(self, url):
        return {"status": "absent (test)", "allowed": True, "crawl_delay": None}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


class Resp:
    def __init__(self, body: str, status=200):
        self.text, self.content, self.status_code = body, body.encode(), status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_crawl_index_finds_only_yearly_omni2_files():
    links = omni.crawl_index(INDEX_HTML)
    assert list(links) == [2024, 2025]
    assert links[2025] == "https://spdf.gsfc.nasa.gov/pub/data/omni/low_res_omni/omni2_2025.dat"


def test_parse_converts_fill_values_to_none_and_time_to_utc():
    rows = omni.parse(line(2024, 32, 5, fill_bz=True))
    assert rows[0]["time"] == datetime(2024, 2, 1, 5, tzinfo=UTC)
    assert rows[0]["bz_gsm"] is None and rows[0]["speed"] == 450.0 and rows[0]["kp10"] == 33.0


def test_quality_report_counts_gaps_duplicates_and_fill():
    rows = omni.parse("\n".join([
        line(2024, 1, 0), line(2024, 1, 1), line(2024, 1, 1),   # doublon
        line(2024, 1, 3, fill_bz=True),                           # 02 h manquante
    ]))
    q = omni.quality_report(rows, 2024)
    assert (q["expected_hours"], q["missing_hours"], q["duplicate_hours"]) == (4, 1, 1)
    assert q["fill_share"]["bz_gsm"] == 0.25
    assert q["last_valid"]["bz_gsm"] == "2024-01-01T01:00:00+00:00"


def test_collect_crawls_downloads_and_reports(tmp_path):
    files = {
        omni.INDEX_URL: Resp(INDEX_HTML),
        omni.INDEX_URL + "omni2_2025.dat": Resp(line(2025, 1, 0) + "\n" + line(2025, 1, 1)),
    }
    records = omni.collect(tmp_path, start_year=2025, end_year=2026, now=lambda: FIXED,
                           session_factory=OfflineSession,
                           fetcher=lambda url, session, timeout=30: files[url])
    by_source = {r["source"]: r for r in records}
    assert by_source["omni_index"]["files_found"] == 2
    assert by_source["omni2_2025"]["ok"] and by_source["omni2_2025"]["quality"]["hours"] == 2
    assert by_source["omni2_2026"]["error"] == "fichier absent de l'index"
    day = tmp_path / "omni/omni2/2026/10/26"
    assert (day / "index.html").exists() and (day / "omni2_2025.dat").exists()
    manifest = tmp_path / "omni/manifests/2026/10/26/run_20261026T210000Z.jsonl"
    assert len(manifest.read_text().splitlines()) == 3
    assert json.loads((day / "omni2_2025_qualite.json").read_text())["missing_hours"] == 0
