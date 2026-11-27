# Modèle Kp ≥ 4 dans les 6 h

Entraîné le 2026-11-27 19:28 UTC. Seuil d'alerte (choisi sur la validation) : **0.21**. Modèle en service appris sur 49364 tranches (jusqu'au 2026-11-27).

| Période | Méthode | ROC AUC | PR AUC | Brier | Précision | Rappel | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| validation | modele | 0.867 | 0.576 | 0.075 | 0.489 | 0.628 | 0.550 |
| validation | persistance | 0.726 | 0.241 | 0.237 | 0.297 | 0.678 | 0.413 |
| validation | climatologie | 0.552 | 0.145 | 0.110 | 0.142 | 0.386 | 0.208 |
| test | modele | 0.906 | 0.727 | 0.076 | 0.545 | 0.768 | 0.637 |
| test | persistance | 0.759 | 0.323 | 0.239 | 0.376 | 0.756 | 0.502 |
| test | climatologie | 0.534 | 0.171 | 0.140 | 0.183 | 0.397 | 0.250 |

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

Dernière tranche (2026-11-27 09:00 UTC) : probabilité **0%** → pas d’alerte.

# Modèle Kp ≥ 4 dans les 24 h

Entraîné le 2026-11-27 19:28 UTC. Seuil d'alerte (choisi sur la validation) : **0.30**. Modèle en service appris sur 49358 tranches (jusqu'au 2026-11-26).

| Période | Méthode | ROC AUC | PR AUC | Brier | Précision | Rappel | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| validation | modele | 0.761 | 0.619 | 0.159 | 0.566 | 0.582 | 0.574 |
| validation | persistance | 0.642 | 0.379 | 0.289 | 0.483 | 0.485 | 0.484 |
| validation | climatologie | 0.546 | 0.315 | 0.211 | 0.309 | 0.638 | 0.416 |
| test | modele | 0.825 | 0.724 | 0.153 | 0.610 | 0.697 | 0.650 |
| test | persistance | 0.680 | 0.459 | 0.279 | 0.565 | 0.565 | 0.565 |
| test | climatologie | 0.541 | 0.344 | 0.238 | 0.340 | 0.627 | 0.441 |

Importance des variables dans la forêt aléatoire (somme = 1) :

| Variable | Importance |
| --- | --- |
| kp | 0.241 |
| bt_mean_3h | 0.149 |
| kp_mean_24h | 0.110 |
| kp_max_24h | 0.094 |
| bz_min_6h | 0.079 |
| density_mean_6h | 0.064 |
| speed_mean_6h | 0.062 |
| bz_mean_3h | 0.050 |
| doy_sin | 0.041 |
| f107 | 0.040 |
| doy_cos | 0.038 |
| kp_max_24h_27d | 0.034 |

Dernière tranche (2026-11-27 09:00 UTC) : probabilité **19%** → pas d’alerte.

# Modèle Kp ≥ 4 dans les 48 h

Entraîné le 2026-11-27 19:29 UTC. Seuil d'alerte (choisi sur la validation) : **0.27**. Modèle en service appris sur 49350 tranches (jusqu'au 2026-11-25).

| Période | Méthode | ROC AUC | PR AUC | Brier | Précision | Rappel | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| validation | modele | 0.690 | 0.656 | 0.220 | 0.523 | 0.743 | 0.614 |
| validation | persistance | 0.609 | 0.503 | 0.360 | 0.615 | 0.407 | 0.490 |
| validation | climatologie | 0.551 | 0.470 | 0.267 | 0.427 | 0.833 | 0.564 |
| test | modele | 0.754 | 0.746 | 0.205 | 0.567 | 0.836 | 0.676 |
| test | persistance | 0.626 | 0.549 | 0.361 | 0.654 | 0.457 | 0.538 |
| test | climatologie | 0.532 | 0.479 | 0.283 | 0.469 | 0.870 | 0.609 |

Importance des variables dans la forêt aléatoire (somme = 1) :

| Variable | Importance |
| --- | --- |
| kp | 0.236 |
| bt_mean_3h | 0.141 |
| kp_mean_24h | 0.105 |
| kp_max_24h | 0.102 |
| density_mean_6h | 0.069 |
| f107 | 0.065 |
| bz_min_6h | 0.064 |
| doy_cos | 0.046 |
| doy_sin | 0.046 |
| speed_mean_6h | 0.045 |
| kp_max_24h_27d | 0.043 |
| bz_mean_3h | 0.039 |

Dernière tranche (2026-11-27 09:00 UTC) : probabilité **28%** → alerte.
