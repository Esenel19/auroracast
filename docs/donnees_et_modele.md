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
