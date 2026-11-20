"""Analyse saisonnière : indicateurs, tests d'hypothèses, recommandations, requêtes SQL."""

import os

import pytest

from auroracast.app import analysis as an
from test_warehouse import clean_dir  # noqa: F401 - fixture partagée (jeu minimal de tables propres)


def _row(loc, mois, n=16, noires=1000, kp_connu=1000, kp4=100, ciel=900, degage=300, fav=40, nuits=8, sans_lune=20):
    return {"location": loc, "mois": mois, "n_annees": n, "heures_noires": noires, "noires_kp_connu": kp_connu,
            "noires_kp4": kp4, "noires_ciel_connu": ciel, "noires_degagees": degage, "heures_favorables": fav,
            "nuits_favorables": nuits, "favorables_sans_lune": sans_lune}


@pytest.fixture
def agg():
    rows = []
    for loc, clear in (("tromso", 200), ("kiruna", 400), ("rovaniemi", 300)):
        for m in range(1, 13):
            summer = m in (5, 6, 7)
            rows.append(_row(loc, m, noires=0 if summer else 1000, kp_connu=0 if summer else 1000,
                             kp4=0 if summer else (150 if m in an.EQUINOX else 80),
                             ciel=0 if summer else 900, degage=0 if summer else clear,
                             fav=0 if summer else clear // 5, nuits=0 if summer else clear // 20))
    kp_mois = [{"mois": m, "tranches": 10000, "tranches_kp4": 1500 if m in an.EQUINOX else 900} for m in range(1, 13)]
    kp_jour = [{"mois": m, "kp_max": (4.5 if m in an.EQUINOX else 3.0) + (i % 5) * 0.3}
               for m in range(1, 13) for i in range(60)]
    kp_annee = [{"annee": y, "tranches": 2920, "tranches_kp4": 300 + (200 if y >= 2023 else 0)} for y in range(2000, 2026)]
    return {"mois_lieu": rows, "kp_mois": kp_mois, "kp_jour": kp_jour, "kp_annee": kp_annee}


def test_queries_are_all_named():
    q = an.queries()
    assert set(q) == {"mois_lieu", "kp_mois", "kp_annee", "kp_jour"}
    assert all("%(fin)s" in s for s in q.values()) and "%(debut)s" in q["mois_lieu"]


def test_wilson_interval():
    lo, hi = an.wilson(50, 100)
    assert lo < 0.5 < hi and hi - lo == pytest.approx(0.19, abs=0.01)
    assert an.wilson(0, 0) == (None, None)


def test_build_indicators_tests_and_recommendations(agg):
    data = an.build(agg, "2010-01-01", "2026-01-01")
    kiruna_jan = next(r for r in data["mois_lieu"] if r["lieu"] == "kiruna" and r["mois"] == 1)
    assert kiruna_jan["part_degage"] == pytest.approx(400 / 900)
    assert kiruna_jan["nuits_favorables_an"] == pytest.approx(20 / 16)
    assert kiruna_jan["part_kp4"] == pytest.approx(0.08)
    june = next(r for r in data["mois_lieu"] if r["lieu"] == "tromso" and r["mois"] == 6)
    assert june["part_degage"] is None and june["heures_noires_an"] == 0     # pas de division par zéro
    h1 = data["tests"]["equinoxes"]
    assert h1["khi2"]["p_valeur"] < 0.05 and h1["khi2"]["rapport"] > 1 and h1["conclusion"].startswith("H1 confirmée")
    assert h1["kruskal_wallis"]["p_valeur"] < 0.05
    h2 = data["tests"]["ciel_degage"]
    assert h2["p_valeur"] < 0.05 and "Kiruna" in h2["conclusion"]
    recs = " ".join(data["recommandations"])
    assert "Kiruna" in data["recommandations"][0] and "mai, juin, juillet" in recs and "Cycle solaire" in recs
    assert kiruna_jan["heures_favorables_an_estimees"] == pytest.approx(80 / 16 / 0.9)    # corrigé de la couverture
    assert june["heures_favorables_an_estimees"] == 0.0
    assert data["kp_mois"][2]["ic95"][0] < data["kp_mois"][2]["frequence_kp4"] < data["kp_mois"][2]["ic95"][1]


def test_main_writes_json(tmp_path, monkeypatch, agg):
    monkeypatch.setattr(an, "read_aggregates", lambda url, debut, fin: agg)
    assert an.main(["--out", str(tmp_path), "--database-url", "x"]) == 0
    import json
    data = json.loads((tmp_path / "analyse.json").read_text(encoding="utf-8"))
    assert data["periode"]["debut"] == "2010-01-01" and len(data["mois_lieu"]) == 36


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="pas de base PostgreSQL de test (TEST_DATABASE_URL)")
def test_sql_queries_run_on_the_warehouse(clean_dir):  # noqa: F811
    from auroracast.warehouse import load as wh

    url = os.environ["TEST_DATABASE_URL"]
    wh.load(clean_dir, url)
    agg = an.read_aggregates(url, "2024-01-01", "2025-01-01")
    may = next(r for r in agg["mois_lieu"] if r["location"] == "tromso" and r["mois"] == 5)
    assert may["heures_noires"] == 0 and may["n_annees"] == 1                # soleil à -0,6 / -2,3° : pas noir
    assert agg["kp_mois"] == [{"mois": 5, "tranches": 1, "tranches_kp4": 1}]
    assert agg["kp_annee"][0]["annee"] == 2024 and agg["kp_jour"][0]["kp_max"] == pytest.approx(8.667)


def test_estimate_corrects_low_coverage_and_drops_tiny_coverage():
    rows = an.month_location([_row("tromso", 1, noires=1000, ciel=320, fav=32),
                              _row("kiruna", 1, noires=1000, ciel=900, fav=90),
                              _row("rovaniemi", 1, noires=1000, ciel=100, fav=10)])
    est = {r["lieu"]: r["heures_favorables_an_estimees"] for r in rows}
    assert est["tromso"] == pytest.approx(est["kiruna"])           # même ciel réel, station moins assidue
    assert est["rovaniemi"] is None                                  # 10 % d'heures observées : trop fragile
