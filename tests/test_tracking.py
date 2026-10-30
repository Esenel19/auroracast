import pytest

from auroracast.model import tracking
from auroracast.model import train as tm

mlflow = pytest.importorskip("mlflow")


@pytest.fixture
def local_mlflow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                                    # artefacts dans le dossier temporaire
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path / 'mlflow.db'}")
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    return tmp_path


def test_disabled_without_uri(monkeypatch, training_df):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    model, res = tm.train(training_df, 24)
    assert tracking.log_training(model, res, training_df[tm.FEATURES].tail(3), accepted=True) is None


def test_flat_metrics(training_df):
    _, res = tm.train(training_df, 24)
    m = tracking.flat_metrics(res)
    assert {"modele_test_roc_auc", "persistance_test_f1", "climatologie_validation_brier"} <= set(m)
    assert m["donnees_apprises"] == len(training_df) - 2


def test_main_logs_runs_and_registers(local_mlflow, training_df, monkeypatch):
    monkeypatch.setattr(tm, "read_training", lambda url: training_df)
    out = local_mlflow / "models"
    assert tm.main(["--out", str(out), "--database-url", "x", "--horizons", "24"]) == 0
    assert tm.main(["--out", str(out), "--database-url", "x", "--horizons", "24"]) == 0   # 2e jour
    client = mlflow.MlflowClient()
    mv = client.get_model_version_by_alias("auroracast-kp4-24h", "production")
    assert str(mv.version) == "2" and float(mv.tags["seuil_alerte"]) == pytest.approx(tm.json.loads(
        (out / "metrics_24h.json").read_text())["seuil_alerte"])
    runs = mlflow.search_runs(experiment_names=[tracking.EXPERIMENT])
    assert len(runs) == 2 and runs["metrics.modele_test_roc_auc"].gt(0.5).all()
    loaded = mlflow.sklearn.load_model("models:/auroracast-kp4-24h@production")
    assert loaded.predict_proba(training_df[tm.FEATURES].tail(2)).shape == (2, 2)


def test_refused_model_is_traced_not_registered(local_mlflow, training_df, monkeypatch):
    monkeypatch.setattr(tm, "read_training", lambda url: training_df)
    assert tm.main(["--out", str(local_mlflow / "m"), "--database-url", "x", "--horizons", "24",
                    "--min-auc", "0.999"]) == 1
    runs = mlflow.search_runs(experiment_names=[tracking.EXPERIMENT])
    assert list(runs["tags.statut"]) == ["refusé"]
    assert mlflow.MlflowClient().search_registered_models() == []
