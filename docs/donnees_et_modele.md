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

