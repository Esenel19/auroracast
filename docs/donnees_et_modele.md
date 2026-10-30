# AuroraCast : données et modèle

Toutes les heures sont en UTC. Tables propres publiées chaque jour (branche `clean-data`) et chargées dans PostgreSQL.

## Données disponibles

`kp_3h` = Indice Kp, ap 

`kp_forecast` = Kp prévu par la NOAA 

`solar_daily` = Ap du jour, taches solaires, flux

`solar_wind_hourly` = Vent solaire : Bz, By, Bt, vitesse, densité 

`cloud_hourly` = Nébulosité observée (octas)

`weather_hourly` = Température, rosée, humidité, pression, vent, visibilité, brouillard

`weather_daily` = Météo quotidienne

`weather_forecast` = Nuages (total, bas, moyens, hauts), brouillard, température, vent, pluie prévus

`astronomy_hourly` Hauteur du Soleil, phase de la nuit, hauteur et éclairement de la Lune, nuit polaire

`locations` = Tromsø, Kiruna, Rovaniemi et leur station | lieu |

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

y = b1 + b2 × 0,67 + b3 × 2,60 + b4 × 349,74 + b5 × 2,64 + b6 × 90,80

y = 1,8 environ

b1       = constance valeur de y quand toutes variables = 0
y        = Kp maximal des 24 h suivantes   (kp_3h) (Kp max des 24 h suivantes)
kp       = Kp actuel                       (kp_3h) (valeur de la tranche)
bz       = Bz du vent solaire              (solar_wind_hourly) (moyenne de 15 h, 16 h et 17 h)
vitesse  = vitesse du vent solaire         (solar_wind_hourly) (moyenne des 3 heures)
densite  = densité du vent solaire         (solar_wind_hourly) (moyenne des 3 heures)
f107     = flux solaire du jour            (solar_daily) (valeur du jour)
```

Belle aurore si y ≥ 4 pour chaque lieu selectioné, elle n'est visible que si le ciel est noir (`astronomy_hourly`) et dégagé (`weather_forecast`) : vert = probable, orange = incertain, rouge = pas visible.

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

## Suivi des modèles dans le temps

Page `suivi.html` de l'application (lien en bas de la page des prévisions), recalculée toutes les 3 h (`python -m auroracast.model.history`) :

1. **Progression rejouée depuis 2016** : au début de chaque mois, le modèle est réappris avec les seules données connues à cette date, puis noté (ROC AUC) sur le mois suivant. Constat : la note varie surtout avec le cycle solaire (années calmes ou agitées) et ne monte pas avec la quantité de données (18 859 tranches en 2016, 47 832 en 2026). Les progrès viendront de nouvelles variables ou d'un autre type de modèle, pas de plus d'historique.
2. **Versions publiées** : une ligne par version quotidienne de la branche `models` (note sur 2024 → jour J, tranches apprises).
3. **Prévisions réelles** : ce que la version en service a annoncé, comparé à ce qui s'est passé une fois l'horizon écoulé.
