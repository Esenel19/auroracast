"""Stockage (mode local), tâches Azure et versions des modèles rangées dans un dossier."""

import json

import joblib
import pytest

from auroracast.cloud import jobs, storage
from auroracast.model import history


@pytest.fixture(autouse=True)
def no_azure(monkeypatch):
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT", raising=False)


def test_store_is_local_without_account(tmp_path):
    s = storage.store("raw", "noaa", local_root=tmp_path / "raw")
    assert isinstance(s, storage.LocalStore)
    assert s.root == tmp_path / "raw" / "noaa"


def test_store_is_blob_with_account(monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT", "stexemple")
    s = storage.store("models", "/latest/")
    assert isinstance(s, storage.BlobStore) and (s.container, s.prefix) == ("models", "latest")


def test_local_upload_keeps_raw_files(tmp_path):
    src = tmp_path / "src"
    (src / "a").mkdir(parents=True)
    (src / "a" / "f.json").write_text("nouveau")
    s = storage.LocalStore(tmp_path / "store")
    (tmp_path / "store" / "a").mkdir(parents=True)
    (tmp_path / "store" / "a" / "f.json").write_text("ancien")
    assert s.upload(src, overwrite=False) == 0
    assert (tmp_path / "store" / "a" / "f.json").read_text() == "ancien"   # brut jamais réécrit
    assert s.upload(src) == 1 and s.read_bytes("a/f.json") == b"nouveau"
    assert s.download(tmp_path / "copie", "a") == 1
    assert (tmp_path / "copie" / "f.json").read_text() == "nouveau"
    assert s.read_bytes("absent.json") is None


class FakeBlob:
    def __init__(self, name, size):
        self.name, self.size = name, size


class FakeContainer:
    """Imite ContainerClient : list_blobs, download_blob, upload_blob."""

    def __init__(self, blobs):
        self.blobs = dict(blobs)

    def list_blobs(self, name_starts_with=None):
        return [FakeBlob(n, len(b)) for n, b in self.blobs.items()
                if name_starts_with is None or n.startswith(name_starts_with)]

    def download_blob(self, name):
        from azure.core.exceptions import ResourceNotFoundError
        if name not in self.blobs:
            raise ResourceNotFoundError("absent")
        data = self.blobs[name]
        return type("D", (), {"readall": lambda self: data})()

    def upload_blob(self, name, fh, overwrite=False):
        from azure.core.exceptions import ResourceExistsError
        if name in self.blobs and not overwrite:
            raise ResourceExistsError("existe")
        self.blobs[name] = fh.read()


class FakeService:
    def __init__(self, container):
        self.container = container

    def get_container_client(self, name):
        return self.container


def test_blob_store_paths(tmp_path):
    pytest.importorskip("azure.storage.blob")
    c = FakeContainer({"noaa/manifests/r1.jsonl": b"1", "noaa/data/x.json": b"22",
                       "metno/manifests/r.jsonl": b"3"})
    s = storage.BlobStore("raw", service=FakeService(c))
    assert s.download(tmp_path / "state", "noaa/manifests") == 1
    assert (tmp_path / "state" / "r1.jsonl").read_bytes() == b"1"
    assert s.download(tmp_path / "state", "noaa/manifests") == 0           # déjà là
    src = tmp_path / "new"
    (src / "noaa" / "manifests").mkdir(parents=True)
    (src / "noaa" / "manifests" / "r1.jsonl").write_bytes(b"autre")
    (src / "noaa" / "manifests" / "r2.jsonl").write_bytes(b"2")
    assert s.upload(src, overwrite=False) == 1                               # r1 gardé
    assert c.blobs["noaa/manifests/r1.jsonl"] == b"1" and c.blobs["noaa/manifests/r2.jsonl"] == b"2"
    latest = storage.BlobStore("models", "latest", service=FakeService(c))
    assert latest.read_bytes("absent") is None


def _metrics(auc, date):
    t = {"roc_auc": auc, "brier": 0.1, "precision": 0.5, "rappel": 0.6}
    return {"entraine_le": date, "seuil_alerte": 0.2, "modele": {"test": t},
            "production": {"n": 100, "jusqu_au": date}}


def test_versions_from_dir(tmp_path):
    for stamp, auc in (("20261121T1013Z", 0.91), ("20261120T1013Z", 0.90)):
        d = tmp_path / stamp
        d.mkdir()
        (d / "metrics_6h.json").write_text(json.dumps(_metrics(auc, stamp)))
    vers = history.versions_from_dir(tmp_path)
    assert [v["sha"] for v in vers] == ["20261120T1013Z", "20261121T1013Z"]
    assert vers[0]["horizon_h"] == 6 and vers[1]["roc_auc_test"] == 0.91


def test_train_job_publishes_latest_and_version(tmp_path, monkeypatch):
    monkeypatch.setenv(jobs.LOCAL_ROOT_ENV, str(tmp_path / "store"))
    from auroracast.model import train as tm

    def fake_train(argv):
        out = tmp_path / "work" / "trained"
        out.mkdir(parents=True)
        joblib.dump({"threshold": 0.2}, out / "kp4_6h.joblib")
        (out / "metrics_6h.json").write_text("{}")
        (out / "rapport_modele.md").write_text("# rapport")
        return 0

    monkeypatch.setattr(tm, "main", fake_train)
    monkeypatch.setattr(history, "main", lambda argv: 0)
    assert jobs.main(["train", "--work", str(tmp_path / "work")]) == 0
    models = tmp_path / "store" / "models"
    assert (models / "latest" / "kp4_6h.joblib").is_file()
    assert len(list((models / "versions").glob("*/metrics_6h.json"))) == 1
    assert len(list((tmp_path / "store" / "rapports" / "modele").glob("*/rapport_modele.md"))) == 1


def test_train_job_keeps_old_model_when_quality_fails(tmp_path, monkeypatch):
    monkeypatch.setenv(jobs.LOCAL_ROOT_ENV, str(tmp_path / "store"))
    from auroracast.model import train as tm

    monkeypatch.setattr(tm, "main", lambda argv: 1)        # ROC AUC sous le seuil
    assert jobs.main(["train", "--work", str(tmp_path / "work")]) == 1
    assert not (tmp_path / "store" / "models").exists()


def test_collect_job_archives_even_on_partial_failure(tmp_path, monkeypatch):
    monkeypatch.setenv(jobs.LOCAL_ROOT_ENV, str(tmp_path / "store"))
    old = tmp_path / "store" / "raw" / "noaa" / "manifests" / "run_1.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text("{}")
    from auroracast.collect import metno, noaa

    seen = {}

    def fake_noaa(argv):
        out, state = argv[argv.index("--out") + 1], argv[argv.index("--state-dir") + 1]
        seen["state"] = sorted(p.name for p in (tmp_path / "work" / "state").rglob("*.jsonl"))
        f = tmp_path / "work" / "raw" / "noaa" / "kp" / "kp_1.json"
        f.parent.mkdir(parents=True)
        f.write_text("[]")
        assert out.endswith("raw") and state.endswith("state")
        return 0

    def broken_metno(argv):
        raise RuntimeError("API indisponible")

    monkeypatch.setattr(noaa, "main", fake_noaa)
    monkeypatch.setattr(metno, "main", broken_metno)
    assert jobs.main(["collect", "--work", str(tmp_path / "work")]) == 1   # échec signalé
    assert seen["state"] == ["run_1.jsonl"]                                 # état relu
    assert (tmp_path / "store" / "raw" / "noaa" / "kp" / "kp_1.json").is_file()  # archivé quand même
