"""Analyse saisonnière pour le directeur : quels mois et quels lieux pour voir une aurore ?

    python -m auroracast.app.analysis --out site          # DATABASE_URL lu dans .env

Lit l'entrepôt avec les requêtes de sql/analyse_saisonniere.sql et écrit analyse.json, lu par la
page web/analyse.html :

  mois_lieu     par lieu et par mois : heures noires, part de Kp >= 4, part de ciel dégagé,
                heures et nuits favorables par an (années complètes depuis 2010)
  kp_mois       fréquence des belles aurores par mois depuis 1932 (avec intervalle à 95 %)
  kp_annee      fréquence par année : le cycle solaire
  tests         H1 « plus d'activité aux équinoxes » ; H2 « le ciel n'est pas aussi dégagé partout »
  recommandations   meilleures périodes et meilleur lieu, calculées sur les données

Méthode et définitions : docs/plan_analyse.md.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy import stats

SQL_FILE = Path(__file__).resolve().parents[3] / "sql" / "analyse_saisonniere.sql"
FIRST_YEAR = 2010                                  # début des observations météo GHCNh
EQUINOX, SOLSTICE = (3, 4, 9, 10), (6, 7, 12, 1)
SEASONS = {"hiver": (12, 1, 2), "printemps": (3, 4, 5), "ete": (6, 7, 8), "automne": (9, 10, 11)}
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
          "octobre", "novembre", "décembre"]
NAMES = {"tromso": "Tromsø", "kiruna": "Kiruna", "rovaniemi": "Rovaniemi"}
ALPHA = 0.05


def queries(path: Path = SQL_FILE) -> dict[str, str]:
    """Requêtes nommées (« -- name: x ») du fichier SQL."""
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"^-- name: (\w+)\s*$", text, flags=re.MULTILINE)
    return {parts[i]: parts[i + 1].strip().rstrip(";") for i in range(1, len(parts), 2)}


def read_aggregates(url: str, debut: str, fin: str) -> dict[str, list[dict]]:
    import psycopg
    from psycopg.rows import dict_row

    out = {}
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        for name, sql in queries().items():
            out[name] = [{k: (float(v) if hasattr(v, "is_finite") else v) for k, v in r.items()}
                         for r in conn.execute(sql, {"debut": debut, "fin": fin}).fetchall()]
    return out


# ------------------------------------------------------------------------- indicateurs
def _ratio(a, b):
    return None if not b else a / b


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """Intervalle de confiance à 95 % d'une proportion (Wilson)."""
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def month_location(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        n = r["n_annees"] or 1
        out.append({
            "lieu": r["location"], "mois": int(r["mois"]), "n_annees": int(r["n_annees"]),
            "heures_noires_an": r["heures_noires"] / n,
            "part_kp4": _ratio(r["noires_kp4"], r["noires_kp_connu"]),
            "part_degage": _ratio(r["noires_degagees"], r["noires_ciel_connu"]),
            "couverture_ciel": _ratio(r["noires_ciel_connu"], r["heures_noires"]),
            "heures_favorables_an": r["heures_favorables"] / n,
            "nuits_favorables_an": r["nuits_favorables"] / n,
            "part_sans_lune": _ratio(r["favorables_sans_lune"], r["heures_favorables"]),
        })
    return out


def kp_frequency(rows: list[dict], key: str) -> list[dict]:
    out = []
    for r in rows:
        lo, hi = wilson(int(r["tranches_kp4"]), int(r["tranches"]))
        out.append({key: int(r[key]), "tranches": int(r["tranches"]),
                    "frequence_kp4": _ratio(r["tranches_kp4"], r["tranches"]), "ic95": [lo, hi]})
    return out


# ------------------------------------------------------------------------- tests d'hypothèses
def _cramers_v(table: np.ndarray, chi2: float) -> float:
    n = table.sum()
    return math.sqrt(chi2 / (n * (min(table.shape) - 1)))


def test_equinox(kp_mois: list[dict], kp_jour: list[dict]) -> dict:
    """H1 : l'activité géomagnétique est plus forte aux équinoxes (effet Russell-McPherron)."""
    by = {int(r["mois"]): r for r in kp_mois}
    k_e = sum(int(by[m]["tranches_kp4"]) for m in EQUINOX if m in by)
    n_e = sum(int(by[m]["tranches"]) for m in EQUINOX if m in by)
    k_s = sum(int(by[m]["tranches_kp4"]) for m in SOLSTICE if m in by)
    n_s = sum(int(by[m]["tranches"]) for m in SOLSTICE if m in by)
    table = np.array([[k_e, n_e - k_e], [k_s, n_s - k_s]])
    chi2, p_chi2, _, _ = stats.chi2_contingency(table)
    groups = {s: [float(r["kp_max"]) for r in kp_jour if int(r["mois"]) in months and r["kp_max"] is not None]
              for s, months in SEASONS.items()}
    h, p_kw = stats.kruskal(*groups.values())
    medians = {s: float(np.median(v)) for s, v in groups.items()}
    return {
        "hypothese": "Les belles aurores (Kp >= 4) sont plus fréquentes aux équinoxes (mars-avril, septembre-octobre) "
                     "qu'aux solstices (juin-juillet, décembre-janvier).",
        "khi2": {"frequence_equinoxes": k_e / n_e if n_e else None, "frequence_solstices": k_s / n_s if n_s else None,
                 "rapport": (k_e / n_e) / (k_s / n_s) if n_e and n_s and k_s else None,
                 "statistique": float(chi2), "p_valeur": float(p_chi2), "v_cramer": _cramers_v(table, chi2)},
        "kruskal_wallis": {"statistique": float(h), "p_valeur": float(p_kw), "mediane_kp_max_jour": medians,
                           "jours": {s: len(v) for s, v in groups.items()}},
        "conclusion": ("H1 confirmée" if p_chi2 < ALPHA and k_e / n_e > k_s / n_s else "H1 non confirmée")
                      + f" au seuil de {ALPHA:.0%}.",
    }


def test_clear_sky(rows: list[dict]) -> dict:
    """H2 : la part de ciel dégagé la nuit (septembre à mars) n'est pas la même dans les 3 lieux."""
    season = (9, 10, 11, 12, 1, 2, 3)
    acc: dict[str, list[int]] = {}
    for r in rows:
        if int(r["mois"]) in season:
            a = acc.setdefault(r["location"], [0, 0])
            a[0] += int(r["noires_degagees"])
            a[1] += int(r["noires_ciel_connu"])
    places = sorted(acc)
    table = np.array([[acc[p][0], acc[p][1] - acc[p][0]] for p in places])
    chi2, p, _, _ = stats.chi2_contingency(table)
    shares = {p: acc[p][0] / acc[p][1] if acc[p][1] else None for p in places}
    best = max(shares, key=lambda k: shares[k] or 0)
    return {
        "hypothese": "La part d'heures noires au ciel dégagé (septembre à mars) diffère selon le lieu.",
        "part_degage": shares, "heures_observees": {p: acc[p][1] for p in places},
        "statistique": float(chi2), "p_valeur": float(p), "v_cramer": _cramers_v(table, chi2),
        "conclusion": (f"H2 confirmée au seuil de {ALPHA:.0%} : {NAMES.get(best, best)} a le ciel le plus souvent dégagé."
                       if p < ALPHA else f"H2 non confirmée au seuil de {ALPHA:.0%}."),
    }


# ------------------------------------------------------------------------- recommandations
def recommendations(ml: list[dict], kp_annee: list[dict]) -> list[str]:
    ranked = sorted(ml, key=lambda r: -r["nuits_favorables_an"])
    top = ranked[:3]
    out = ["Meilleures périodes (nuits favorables par an, moyenne) : "
           + " ; ".join(f"{NAMES.get(r['lieu'], r['lieu'])} en {MONTHS[r['mois'] - 1]} "
                        f"({r['nuits_favorables_an']:.1f})" for r in top) + "."]
    per_place = {}
    for r in ml:
        if r["mois"] in (9, 10, 11, 12, 1, 2, 3):
            per_place[r["lieu"]] = per_place.get(r["lieu"], 0) + r["nuits_favorables_an"]
    if per_place:
        best = max(per_place, key=per_place.get)
        out.append(f"Sur une saison (septembre à mars), {NAMES.get(best, best)} offre le plus de nuits favorables : "
                   f"{per_place[best]:.0f} par an en moyenne"
                   + "".join(f", contre {per_place[p]:.0f} à {NAMES.get(p, p)}" for p in per_place if p != best) + ".")
    dead = sorted({r["mois"] for r in ml if r["heures_noires_an"] < 1})
    if dead:
        out.append("À ne pas programmer : " + ", ".join(MONTHS[m - 1] for m in dead)
                   + " (pas de nuit noire : soleil de minuit ou crépuscule permanent).")
    if kp_annee:
        last = [r for r in kp_annee if r["tranches"] > 2000][-3:]
        mean = np.mean([r["frequence_kp4"] for r in kp_annee if r["tranches"] > 2000])
        if last and all(r["frequence_kp4"] > mean for r in last):
            out.append(f"{last[0]['annee']}-{last[-1]['annee']} : années de forte activité (maximum du cycle solaire), "
                       "au-dessus de la moyenne depuis 1932 : bonne période pour vendre des séjours, "
                       "mais l'activité va décroître dans les années qui suivent.")
    out.append("Le ciel décide plus que le Soleil : à activité égale, une nuit couverte annule tout. "
               "Prévoir au moins 3 à 4 nuits sur place et des déplacements vers les zones dégagées.")
    return out


def build(agg: dict[str, list[dict]], debut: str, fin: str) -> dict:
    ml = month_location(agg["mois_lieu"])
    kp_annee = kp_frequency(agg["kp_annee"], "annee")
    return {
        "genere_utc": datetime.now(UTC).isoformat(timespec="minutes").replace("+00:00", "Z"),
        "periode": {"debut": debut[:10], "fin_exclue": fin[:10]},
        "lieux": [{"code": c, "nom": n} for c, n in NAMES.items()],
        "mois_lieu": ml,
        "kp_mois": kp_frequency(agg["kp_mois"], "mois"),
        "kp_annee": kp_annee,
        "tests": {"equinoxes": test_equinox(agg["kp_mois"], agg["kp_jour"]),
                  "ciel_degage": test_clear_sky(agg["mois_lieu"])},
        "recommandations": recommendations(ml, kp_annee),
        "definitions": {
            "heure_noire": "Soleil à -12° ou plus bas",
            "heure_degagee": "nuages <= 2 octas, sans brouillard",
            "heure_favorable": "heure noire et dégagée, avec Kp >= 4",
            "nuit_favorable": "nuit comptant au moins une heure favorable",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="site")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv()
    url = args.database_url or os.environ.get("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL manquant (fichier .env ou --database-url)")
    year = datetime.now(UTC).year
    debut, fin = f"{FIRST_YEAR}-01-01", f"{year}-01-01"           # années complètes seulement
    data = build(read_aggregates(url, debut, fin), debut, fin)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "analyse.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for line in data["recommandations"]:
        print("-", line)
    for t in data["tests"].values():
        print("-", t["conclusion"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
