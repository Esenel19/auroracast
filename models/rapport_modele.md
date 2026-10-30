# Modèle Kp ≥ 4 dans les 24 h

Entraîné le 2026-10-30 09:40 UTC. Seuil d'alerte (choisi sur la validation) : **0.24**.

| Période | Méthode | ROC AUC | PR AUC | Brier | Précision | Rappel | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| validation | modele | 0.750 | 0.598 | 0.163 | 0.496 | 0.666 | 0.569 |
| validation | persistance | 0.642 | 0.379 | 0.289 | 0.483 | 0.485 | 0.484 |
| validation | climatologie | 0.546 | 0.315 | 0.211 | 0.309 | 0.638 | 0.416 |
| test | modele | 0.814 | 0.718 | 0.155 | 0.538 | 0.781 | 0.637 |
| test | persistance | 0.679 | 0.462 | 0.282 | 0.567 | 0.567 | 0.567 |
| test | climatologie | 0.539 | 0.346 | 0.241 | 0.349 | 0.624 | 0.447 |

Coefficients (variables centrées-réduites : comparables entre elles) :

| Variable | Coefficient |
| --- | --- |
| constante | -1.968 |
| kp | +0.438 |
| kp_mean_24h | +0.334 |
| kp_max_24h | -0.083 |
| kp_max_24h_27d | +0.230 |
| bz_mean_3h | -0.202 |
| bz_min_6h | +0.019 |
| bt_mean_3h | +0.455 |
| speed_mean_6h | +0.208 |
| density_mean_6h | +0.381 |
| f107 | +0.047 |
| doy_sin | -0.042 |
| doy_cos | -0.038 |

Dernière tranche (2026-10-29 06:00 UTC) : probabilité **4%** → pas d’alerte.
