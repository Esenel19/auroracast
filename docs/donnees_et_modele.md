# AuroraCast : données et modèle

Toutes les heures sont en UTC. Tables propres publiées chaque jour (branche `clean-data`) et chargées dans PostgreSQL.

## Données disponibles

| Table | Contenu | Grain | Période | Lignes | Source (technique) |
| --- | --- | --- | --- | --- | --- |
| `kp_3h` | Indice Kp, ap | 3 h | 1932 → aujourd'hui | 277 083 | GFZ (fichier), NOAA SWPC (API) |
| `kp_forecast` | Kp prévu par la NOAA | 3 h, 3 jours | depuis 10/2026 | 54 | NOAA SWPC (API) |
| `solar_daily` | Ap du jour, taches solaires, flux F10.7 | jour | 1932 → aujourd'hui | 34 633 | GFZ (fichier) |
| `solar_wind_hourly` | Vent solaire : Bz, By, Bt, vitesse, densité | 1 h | 2010 → aujourd'hui | 147 490 | OMNIWeb (crawling), NOAA RTSW (API) |
| `cloud_hourly` | Nébulosité observée (octas) | lieu × 1 h | 2010 → J-3 | 442 261 | NOAA GHCNh (API) |
| `weather_hourly` | Température, rosée, humidité, pression, vent, visibilité, brouillard | lieu × 1 h | 2010 → J-3 | 442 262 | NOAA GHCNh (API) |
| `weather_daily` | Météo quotidienne (contrôle croisé) | lieu × jour | 2010 → 24/08/2025 | 17 145 | NOAA GSOD (SQL BigQuery) |
| `weather_forecast` | Nuages (total, bas, moyens, hauts), brouillard, température, vent, pluie prévus | lieu × 1 h, 50 h | depuis 10/2026 | toutes les émissions | MET Norway (API) |
| `astronomy_hourly` | Hauteur du Soleil, phase de la nuit, hauteur et éclairement de la Lune, nuit polaire | lieu × 1 h | 2010 → J+10 | 443 190 | Calcul (PyEphem) |
| `locations` | Tromsø, Kiruna, Rovaniemi et leur station | lieu | — | 3 | — |

## Données utilisées pour l'entraînement

**Question :** une belle aurore (Kp ≥ 4) aura-t-elle lieu dans les 24 prochaines heures ?

| Rôle | Donnée | Table |
| --- | --- | --- |
| Cible (y) | Kp maximal des 24 h suivantes | `kp_3h` |
| Variable | Kp actuel | `kp_3h` |
| Variable | Bz du vent solaire | `solar_wind_hourly` |
| Variable | Vitesse du vent solaire | `solar_wind_hourly` |
| Variable | Densité du vent solaire | `solar_wind_hourly` |
| Variable | Flux solaire F10.7 | `solar_daily` |

Découpage dans le temps : entraînement 2010-2021, validation 2022-2023, test 2024-2026.

## Formule de régression

```
y = b1 + b2 × kp + b3 × bz + b4 × vitesse + b5 × densite + b6 × f107

y        = Kp maximal des 24 h suivantes   (kp_3h)
kp       = Kp actuel                       (kp_3h)
bz       = Bz du vent solaire              (solar_wind_hourly)
vitesse  = vitesse du vent solaire         (solar_wind_hourly)
densite  = densité du vent solaire         (solar_wind_hourly)
f107     = flux solaire du jour            (solar_daily)
```

**Les coefficients b** sont les nombres que le modèle apprend sur l'historique (2010-2021) :

- `b1` = la constante : la valeur de y quand toutes les variables valent 0 ;
- `b2` à `b6` = le poids de chaque variable : de combien y change quand la variable augmente de 1, les autres restant fixes. Exemple : si `b3 = -0,05`, chaque nT de Bz en moins (Bz plus négatif) ajoute 0,05 au Kp prévu. On s'attend à `b3 < 0` (Bz vers le sud = aurores plus fortes) et à `b4 > 0` (vent plus rapide = aurores plus fortes).

## Valeurs réelles des variables

Valeurs du 29/10/2026 et statistiques depuis 2010. Elles sont recalculées en temps réel par la vue `v_formule_valeurs` de l'entrepôt (`SELECT * FROM v_formule_valeurs;` dans Neon).

| Variable | Dernière valeur | Heure (UTC) | Moyenne | Minimum | Maximum | Nombre de valeurs |
| --- | --- | --- | --- | --- | --- | --- |
| y (Kp max 24 h) | 2,33 | 28/10/2026 06:00 | 2,83 | 0 | 9 | 49 155 |
| kp | 0,67 | 29/10/2026 06:00 | 1,72 | 0 | 9 | 49 163 |
| bz (nT) | 2,60 | 29/10/2026 08:00 | -0,07 | -42,20 | 58,90 | 146 161 |
| vitesse (km/s) | 349,74 | 29/10/2026 08:00 | 421,25 | 240 | 1 121 | 146 189 |
| densite (p/cm³) | 2,64 | 29/10/2026 08:00 | 6,19 | 0,10 | 137,20 | 145 553 |
| f107 (sfu) | 90,80 | 26/10/2026 | 112,75 | 64 | 400,70 | 6 130 |

La dernière valeur de y date de la veille : il faut attendre 24 h pour connaître le Kp maximal des 24 h suivantes.

**La formule avec les valeurs actuelles** (les b seront connus après l'entraînement) :

```
y = b1 + b2 × 0,67 + b3 × 2,60 + b4 × 349,74 + b5 × 2,64 + b6 × 90,80
```

Lecture : ce 29/10, toutes les variables sont sous leur moyenne (Kp faible, Bz positif, vent lent et peu dense, Soleil calme) : le modèle prévoira un Kp bas, donc pas de belle aurore (y < 4).

Belle aurore prévue si y ≥ 4. Pour chaque lieu, elle n'est visible que si le ciel est noir (`astronomy_hourly`) et dégagé (`weather_forecast`) : vert = probable, orange = incertain, rouge = pas visible.

## Modèle entraîné (régression logistique)

La table d'entraînement est la vue `v_training_kp` de l'entrepôt : une ligne par tranche de 3 h depuis 2010 (49 163 tranches). La prévision est émise à la fin de la tranche et n'utilise que des valeurs déjà connues à ce moment-là.

Le modèle donne une **probabilité** de belle aurore (Kp ≥ 4) dans les 24 h :

```
p = 1 / (1 + e^-(b1 + b2 × kp + b3 × kp_moy_24h + ... + b13 × saison))
```

| Variable | Contenu | Table |
| --- | --- | --- |
| `kp`, `kp_mean_24h`, `kp_max_24h` | Kp actuel, moyenne et maximum des 24 dernières heures | `kp_3h` |
| `kp_max_24h_27d` | Kp maximal il y a 27 jours (le Soleil tourne en 27 jours) | `kp_3h` |
| `bz_mean_3h`, `bz_min_6h`, `bt_mean_3h` | Champ magnétique du vent solaire | `solar_wind_hourly` |
| `speed_mean_6h`, `density_mean_6h` | Vitesse et densité du vent solaire | `solar_wind_hourly` |
| `f107` | Flux solaire de la veille | `solar_daily` |
| `doy_sin`, `doy_cos` | Saison (jour de l'année) | calcul |

**Résultats sur le test (2024-2026, jamais vu pendant l'apprentissage) :**

| Méthode | ROC AUC | Précision | Rappel | F1 |
| --- | --- | --- | --- | --- |
| Régression logistique | 0,81 | 0,54 | 0,78 | 0,64 |
| Persistance (« demain = aujourd'hui ») | 0,68 | 0,57 | 0,57 | 0,57 |
| Climatologie (fréquence du mois) | 0,54 | 0,35 | 0,62 | 0,45 |

Le modèle détecte 78 % des belles aurores ; quand il donne l'alerte, il a raison une fois sur deux. Seuil d'alerte : probabilité ≥ 0,24, choisi sur la validation 2022-2023.

**Trois horizons** (même formule, même vue, cible différente : `aurora_kp4_6h`, `aurora_kp4_24h`, `aurora_kp4_48h`) :

| Horizon | ROC AUC test | Précision | Rappel | Seuil | Persistance (ROC AUC) |
| --- | --- | --- | --- | --- | --- |
| 6 h | 0,91 | 0,55 | 0,77 | 0,21 | 0,76 |
| 24 h | 0,81 | 0,54 | 0,78 | 0,24 | 0,68 |
| 48 h | 0,75 | 0,49 | 0,97 | 0,17 | 0,63 |

Plus on prévoit loin, moins c'est précis : le modèle 48 h sert surtout à prévenir qu'une nuit agitée est possible.

**Renouvellement quotidien** (workflow « Entraînement des modèles (quotidien) », dans le cloud) : chaque jour les trois modèles sont réévalués sur 2024 → aujourd'hui, contrôlés (ROC AUC ≥ 0,70, sinon l'ancien modèle reste en service), puis réappris sur toutes les données jusqu'à la veille et publiés dans la branche `models` (un commit par jour). Le détail est dans `rapport_modele.md`.

## Application du directeur

Page web (`web/index.html`) publiée sur GitHub Pages, mise à jour toutes les 3 h après la collecte et le chargement de l'entrepôt. Le calcul `python -m auroracast.app.predict` écrit `forecast.json` : pour chaque lieu et chaque heure des 48 prochaines heures, trois conditions. Chaque heure utilise le modèle le plus court qui la couvre : 6 h pour h+0 à h+5, 24 h jusqu'à h+23, 48 h ensuite.

| Condition | Règle | Table |
| --- | --- | --- |
| Activité | probabilité du modèle ≥ son seuil : forte ; ≥ la moitié du seuil : possible ; sinon faible | `v_training_kp` |
| Ciel noir | Soleil à −12° ou plus bas | `fact_astronomy` |
| Ciel | nuages < 40 % : dégagé ; ≥ 80 % ou brouillard ≥ 50 % : couvert ; sinon partiel | `fact_weather_forecast` |

**Vert** = activité forte, ciel noir et dégagé. **Rouge** = activité faible, jour/crépuscule ou ciel couvert. **Orange** = les autres cas.
