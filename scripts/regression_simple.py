"""Régression linéaire simple : prévoir le Kp maximal des 24 h suivantes.

    y = b1 + b2 × kp + b3 × bz + b4 × vitesse + b5 × densite + b6 × f107

Première estimation de démonstration (le vrai modèle viendra ensuite). Lit les tables
propres de data/clean ; à lancer depuis le dossier du projet :

    python scripts/regression_simple.py
"""

import numpy as np
import pandas as pd

from auroracast.data import load

# 1. Charger les tables propres ------------------------------------------------------
kp = load("kp_3h").set_index("time")["kp"]                       # une valeur toutes les 3 h
vent = load("solar_wind_hourly").set_index("time")[["bz_gsm", "speed", "density"]]  # par heure
f107 = load("solar_daily").set_index("date")["f107_obs"]          # une valeur par jour

kp = kp[kp.index >= "2010-01-01"].dropna().sort_index()          # vent solaire connu depuis 2010

# 2. Une ligne par tranche de 3 h ------------------------------------------------------
# Le vent solaire est horaire : on fait la moyenne des 3 heures de chaque tranche,
# pour qu'il ait le même rythme que le Kp.
vent_3h = vent.sort_index().resample("3h").mean()

# y = Kp maximal des 8 tranches suivantes (24 h), c'est-à-dire ce qu'on veut prévoir
y = kp[::-1].rolling(8, min_periods=8).max()[::-1].shift(-1)

table = pd.DataFrame({"y": y, "kp": kp}).join(vent_3h)
# f107 : valeur du jour de la tranche. Le GFZ la publie avec quelques jours de retard ;
# elle varie lentement, donc on reprend la dernière valeur connue pendant 3 jours au plus.
jours = pd.date_range(f107.index.min(), table.index.max().floor("D"), freq="D")
f107 = f107.reindex(jours).ffill(limit=3)
table["f107"] = f107.reindex(table.index.floor("D")).to_numpy()
table = table.rename(columns={"bz_gsm": "bz", "speed": "vitesse", "density": "densite"})
VARIABLES = ["kp", "bz", "vitesse", "densite", "f107"]

# 3. Séparer le passé en deux : apprendre sur 2010-2021, vérifier sur 2024-2026 ----------
complet = table.dropna()
apprentissage = complet[complet.index < "2022-01-01"]
test = complet[complet.index >= "2024-01-01"]


def matrice(t: pd.DataFrame) -> np.ndarray:
    """Colonne de 1 (pour b1) puis une colonne par variable."""
    return np.column_stack([np.ones(len(t))] + [t[v].to_numpy() for v in VARIABLES])


# 4. Calculer les b (moindres carrés : les b qui collent le mieux aux vrais y) ----------
b, *_ = np.linalg.lstsq(matrice(apprentissage), apprentissage["y"].to_numpy(), rcond=None)

print(f"Apprentissage : {len(apprentissage)} tranches de 3 h (2010-2021)\n")
print("Coefficients :")
for nom, valeur in zip(["b1 (constante)"] + [f"b{i + 2} ({v})" for i, v in enumerate(VARIABLES)], b):
    print(f"  {nom:16} = {valeur:+.4f}")

# 5. Vérifier sur des données jamais vues (2024-2026) -----------------------------------
prevu = matrice(test) @ b
erreur = np.abs(test["y"] - prevu).mean()
erreur_naive = np.abs(test["y"] - test["kp"]).mean()            # « demain = aujourd'hui »
r2 = 1 - ((test["y"] - prevu) ** 2).sum() / ((test["y"] - test["y"].mean()) ** 2).sum()
vraies = test["y"] >= 4
annoncees = prevu >= 4
print(f"\nTest sur {len(test)} tranches (2024-2026) :")
print(f"  erreur moyenne       = {erreur:.2f} (contre {erreur_naive:.2f} pour « demain = aujourd'hui »)")
print(f"  R²                   = {r2:.2f}")
print(f"  belles aurores vues  = {(annoncees & vraies).sum()} sur {vraies.sum()}")
print(f"  annonces justes      = {(annoncees & vraies).sum()} sur {annoncees.sum()}")

# 6. Prédire avec les dernières valeurs connues -----------------------------------------
derniere = table[VARIABLES].dropna().iloc[-1]
y_prevu = b @ np.r_[1, derniere.to_numpy()]
print(f"\nDernière tranche complète : {derniere.name:%d/%m/%Y %H:%M} UTC")
for v in VARIABLES:
    print(f"  {v:8} = {derniere[v]:.2f}")
print(f"\nPrédiction : Kp maximal des 24 h suivantes ≈ {y_prevu:.1f}"
      f" → {'belle aurore possible' if y_prevu >= 4 else 'pas de belle aurore'} (seuil : 4)")
