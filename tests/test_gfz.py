import json
from datetime import UTC, datetime

import pytest

from auroracast.collect import gfz

FIXED = datetime(2026, 10, 26, 20, 0, 0, tzinfo=UTC)
HEADER = "\n".join(f"# ligne d'en-tête {i}" for i in range(40))


def line(y, m, d, kp=(1.333,) * 8, flag=2):
    kps = " ".join(f"{k:6.3f}" for k in kp)
    aps = " ".join(f"{5:3d}" for _ in range(8))
    return (f"{y:4d} {m:02d} {d:02d} 34000 34000.5 2600 01 {kps} {aps}"
            f"   5  97    150.2    148.9 {flag}")


def sample(*lines):
    return (HEADER + "\n" + "\n".join(lines) + "\n").encode()


class FakeResponse:
    def __init__(self, content, status=200):
        self.content, self.status_code = content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def run(tmp_path, content, status=200):
    return gfz.collect(tmp_path, now=lambda: FIXED,
                       fetcher=lambda url, session, timeout: FakeResponse(content, status))


def test_parse_reads_the_28_fields():
    rows = gfz.parse(sample(line(2024, 5, 10, kp=(9.0,) * 8, flag=0)).decode())
    assert rows[0]["date"].isoformat() == "2024-05-10"
    assert rows[0]["kp"] == [9.0] * 8 and rows[0]["SN"] == 97 and rows[0]["D"] == 0


def test_parse_rejects_malformed_line():
    with pytest.raises(ValueError, match="champs"):
        gfz.parse(HEADER + "\n2024 05 10 12\n")


def test_quality_report_flags_gaps_duplicates_and_range():
    rows = gfz.parse(sample(
        line(2024, 5, 1), line(2024, 5, 2), line(2024, 5, 2),          # doublon
        line(2024, 5, 4, kp=(12.0,) + (-1,) * 7, flag=0),              # 3 mai manquant
    ).decode())
    q = gfz.quality_report(rows)
    assert (q["expected_days"], q["missing_days"], q["duplicate_days"]) == (4, 1, 1)
    assert q["missing_sample"] == ["2024-05-03"]
    assert (q["kp_out_of_range"], q["kp_missing_slots"]) == (1, 7)
    assert q["kp_preliminary_since"] == "2024-05-04" and len(q["warnings"]) == 3


def test_collect_archives_raw_file_report_and_manifest(tmp_path):
    content = sample(line(2026, 10, 24), line(2026, 10, 25))
    rec = run(tmp_path, content)
    assert rec["ok"] and rec["quality"]["missing_days"] == 0
    raw = tmp_path / rec["file"]
    assert raw.read_bytes() == content
    assert json.loads(raw.with_name(raw.stem + "_qualite.json").read_text())["days"] == 2
    manifest = tmp_path / "gfz/manifests/2026/10/26/run_20261026T200000Z.jsonl"
    assert json.loads(manifest.read_text())["sha256"] == rec["sha256"]


def test_collect_records_http_failure(tmp_path):
    rec = run(tmp_path, b"", status=404)
    assert rec["ok"] is False and "404" in rec["error"]
    assert not (tmp_path / "gfz" / gfz.NAME).exists()
