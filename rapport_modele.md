# Modèle Kp ≥ 4 dans les 6 h

Entraîné le 2026-11-02 20:22 UTC. Seuil d'alerte (choisi sur la validation) : **0.21**. Modèle en service appris sur 49193 tranches (jusqu'au 2026-11-02).

| Période | Méthode | ROC AUC | PR AUC | Brier | Précision | Rappel | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| validation | modele | 0.867 | 0.576 | 0.075 | 0.489 | 0.628 | 0.550 |
| validation | persistance | 0.726 | 0.241 | 0.237 | 0.297 | 0.678 | 0.413 |
| validation | climatologie | 0.552 | 0.145 | 0.110 | 0.142 | 0.386 | 0.208 |
| test | modele | 0.905 | 0.728 | 0.077 | 0.545 | 0.769 | 0.638 |
| test | persistance | 0.757 | 0.325 | 0.243 | 0.377 | 0.757 | 0.503 |
| test | climatologie | 0.533 | 0.173 | 0.143 | 0.183 | 0.397 | 0.250 |

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

Dernière tranche (2026-11-02 06:00 UTC) : probabilité **7%** → pas d’alerte.

# Modèle Kp ≥ 4 dans les 24 h

Entraîné le 2026-11-02 20:22 UTC. Seuil d'alerte (choisi sur la validation) : **0.24**. Modèle en service appris sur 49187 tranches (jusqu'au 2026-11-01).

| Période | Méthode | ROC AUC | PR AUC | Brier | Précision | Rappel | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| validation | modele | 0.750 | 0.598 | 0.163 | 0.496 | 0.666 | 0.569 |
| validation | persistance | 0.642 | 0.379 | 0.289 | 0.483 | 0.485 | 0.484 |
| validation | climatologie | 0.546 | 0.315 | 0.211 | 0.309 | 0.638 | 0.416 |
| test | modele | 0.813 | 0.717 | 0.155 | 0.539 | 0.780 | 0.637 |
| test | persistance | 0.679 | 0.464 | 0.282 | 0.568 | 0.567 | 0.568 |
| test | climatologie | 0.540 | 0.348 | 0.242 | 0.350 | 0.626 | 0.449 |

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

Dernière tranche (2026-11-02 06:00 UTC) : probabilité **22%** → pas d’alerte.

# Modèle Kp ≥ 4 dans les 48 h

Entraîné le 2026-11-02 20:22 UTC. Seuil d'alerte (choisi sur la validation) : **0.17**. Modèle en service appris sur 49179 tranches (jusqu'au 2026-10-31).

| Période | Méthode | ROC AUC | PR AUC | Brier | Précision | Rappel | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| validation | modele | 0.661 | 0.624 | 0.226 | 0.440 | 0.929 | 0.597 |
| validation | persistance | 0.609 | 0.503 | 0.360 | 0.615 | 0.407 | 0.490 |
| validation | climatologie | 0.551 | 0.470 | 0.267 | 0.427 | 0.833 | 0.564 |
| test | modele | 0.745 | 0.738 | 0.204 | 0.488 | 0.967 | 0.649 |
| test | persistance | 0.625 | 0.554 | 0.364 | 0.657 | 0.458 | 0.540 |
| test | climatologie | 0.530 | 0.482 | 0.286 | 0.477 | 0.870 | 0.616 |

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

Dernière tranche (2026-11-02 06:00 UTC) : probabilité **36%** → alerte.
