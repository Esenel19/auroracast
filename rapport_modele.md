# Modèle Kp ≥ 4 dans les 6 h

Entraîné le 2026-10-31 15:45 UTC. Seuil d'alerte (choisi sur la validation) : **0.21**. Modèle en service appris sur 49177 tranches (jusqu'au 2026-10-31).

| Période | Méthode | ROC AUC | PR AUC | Brier | Précision | Rappel | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| validation | modele | 0.867 | 0.576 | 0.075 | 0.489 | 0.628 | 0.550 |
| validation | persistance | 0.726 | 0.241 | 0.237 | 0.297 | 0.678 | 0.413 |
| validation | climatologie | 0.552 | 0.145 | 0.110 | 0.142 | 0.386 | 0.208 |
| test | modele | 0.905 | 0.728 | 0.077 | 0.545 | 0.768 | 0.637 |
| test | persistance | 0.757 | 0.325 | 0.242 | 0.377 | 0.756 | 0.503 |
| test | climatologie | 0.532 | 0.173 | 0.143 | 0.182 | 0.396 | 0.249 |

Coefficients du modèle évalué (variables centrées-réduites : comparables entre elles) :

| Variable | Coefficient |
| --- | --- |
| constante | -3.890 |
| kp | +0.934 |
| kp_mean_24h | +0.198 |
| kp_max_24h | +0.015 |
| kp_max_24h_27d | +0.162 |
| bz_mean_3h | -0.386 |
| bz_min_6h | +0.119 |
| bt_mean_3h | +0.590 |
| speed_mean_6h | +0.476 |
| density_mean_6h | +0.369 |
| f107 | -0.004 |
| doy_sin | -0.046 |
| doy_cos | -0.049 |

Dernière tranche (2026-10-31 06:00 UTC) : probabilité **16%** → pas d’alerte.

# Modèle Kp ≥ 4 dans les 24 h

Entraîné le 2026-10-31 15:45 UTC. Seuil d'alerte (choisi sur la validation) : **0.24**. Modèle en service appris sur 49171 tranches (jusqu'au 2026-10-30).

| Période | Méthode | ROC AUC | PR AUC | Brier | Précision | Rappel | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| validation | modele | 0.750 | 0.598 | 0.163 | 0.496 | 0.666 | 0.569 |
| validation | persistance | 0.642 | 0.379 | 0.289 | 0.483 | 0.485 | 0.484 |
| validation | climatologie | 0.546 | 0.315 | 0.211 | 0.309 | 0.638 | 0.416 |
| test | modele | 0.813 | 0.717 | 0.155 | 0.538 | 0.780 | 0.637 |
| test | persistance | 0.679 | 0.462 | 0.282 | 0.567 | 0.566 | 0.567 |
| test | climatologie | 0.539 | 0.346 | 0.241 | 0.348 | 0.624 | 0.447 |

Coefficients du modèle évalué (variables centrées-réduites : comparables entre elles) :

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

Dernière tranche (2026-10-31 06:00 UTC) : probabilité **40%** → alerte.

# Modèle Kp ≥ 4 dans les 48 h

Entraîné le 2026-10-31 15:45 UTC. Seuil d'alerte (choisi sur la validation) : **0.17**. Modèle en service appris sur 49163 tranches (jusqu'au 2026-10-29).

| Période | Méthode | ROC AUC | PR AUC | Brier | Précision | Rappel | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| validation | modele | 0.661 | 0.624 | 0.226 | 0.440 | 0.929 | 0.597 |
| validation | persistance | 0.609 | 0.503 | 0.360 | 0.615 | 0.407 | 0.490 |
| validation | climatologie | 0.551 | 0.470 | 0.267 | 0.427 | 0.833 | 0.564 |
| test | modele | 0.747 | 0.738 | 0.203 | 0.488 | 0.969 | 0.649 |
| test | persistance | 0.625 | 0.553 | 0.363 | 0.657 | 0.459 | 0.540 |
| test | climatologie | 0.529 | 0.481 | 0.285 | 0.476 | 0.869 | 0.615 |

Coefficients du modèle évalué (variables centrées-réduites : comparables entre elles) :

| Variable | Coefficient |
| --- | --- |
| constante | -1.116 |
| kp | +0.324 |
| kp_mean_24h | +0.277 |
| kp_max_24h | -0.056 |
| kp_max_24h_27d | +0.235 |
| bz_mean_3h | -0.136 |
| bz_min_6h | +0.006 |
| bt_mean_3h | +0.287 |
| speed_mean_6h | -0.013 |
| density_mean_6h | +0.255 |
| f107 | +0.098 |
| doy_sin | -0.039 |
| doy_cos | -0.054 |

Dernière tranche (2026-10-31 06:00 UTC) : probabilité **53%** → alerte.
