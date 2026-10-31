## Tables propres (météo de l'espace)

| Table | Lignes | Début | Fin | Périodes manquantes | Sources |
| --- | --- | --- | --- | --- | --- |
| kp_3h | 277101 | 1932-01-01T00:00:00+00:00 | 2026-10-31T12:00:00+00:00 | 0 | gfz 277064, noaa 37 |
| solar_daily | 34633 | 1932-01-01T00:00:00+00:00 | 2026-10-26T00:00:00+00:00 | 0 | gfz 34633 |
| solar_wind_hourly | 147546 | 2010-01-01T00:00:00+00:00 | 2026-10-31T17:00:00+00:00 | 0 | omni 146493, absent 915, noaa_rtsw 122, interpolated 16 |
| kp_forecast | 70 | 2026-10-26T19:45:15+00:00 | 2026-10-31T00:49:04+00:00 | — |  |

Plus longs trous du vent solaire :

- 2026-10-13T01:00:00+00:00 → 2026-10-26T15:00:00+00:00 (327 h)
- 2026-07-24T17:00:00+00:00 → 2026-07-26T15:00:00+00:00 (47 h)
- 2026-08-03T03:00:00+00:00 → 2026-08-04T20:00:00+00:00 (42 h)
- 2021-06-26T17:00:00+00:00 → 2021-06-27T20:00:00+00:00 (28 h)
- 2026-08-05T22:00:00+00:00 → 2026-08-06T22:00:00+00:00 (25 h)

## Nébulosité observée (NOAA GHCNh)

| Lieu | Station | Début | Fin | Heures avec nuages | Ciel dégagé (0-2 octas) | Ciel invisible (h) |
| --- | --- | --- | --- | --- | --- | --- |
| kiruna | SWI0000ESNQ | 2010-01-01 | 2026-10-28 | 88% | 33% | 5012 |
| rovaniemi | FIM00002847 | 2010-01-01 | 2026-10-28 | 82% | 33% | 2094 |
| tromso | NOM00001026 | 2010-01-01 | 2026-10-28 | 31% | 32% | 1371 |

## Météo quotidienne observée (NOAA GSOD, requêtes SQL BigQuery)

| Lieu | Station | Début | Fin | Jours manquants | Visibilité absente | Précipitations absentes | Jours de brouillard |
| --- | --- | --- | --- | --- | --- | --- | --- |
| kiruna | KIRUNA | 2010-01-01 | 2025-08-24 | 24 | 0.4% | 100.0% | 569 |
| rovaniemi | ROVANIEMI RAUTATIEASEMA | 2010-01-01 | 2025-08-24 | 111 | 3.4% | 99.6% | 493 |
| tromso | TROMSO | 2010-01-01 | 2025-08-24 | 178 | 4.1% | 7.7% | 155 |

Valeurs hors bornes écartées : aucune ; jours min/moy/max incohérents : 0.

## Météo horaire observée (NOAA GHCNh)

| Lieu | Station | Début | Fin | Température | Visibilité | Heures de brouillard | Valeurs écartées (qualité) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| kiruna | SWI0000ESNQ | 2010-01-01 | 2026-10-28 | 99% | 97% | 966 | 215 |
| rovaniemi | FIM00002847 | 2010-01-01 | 2026-10-28 | 95% | 87% | 941 | 32 |
| tromso | NOM00001026 | 2010-01-01 | 2026-10-28 | 99% | 31% | 365 | 0 |

Contrôle croisé GHCNh → GSOD (température moyenne du jour, jours avec au moins 18 heures de température) :

| Lieu | Jours comparés | Écart moyen (°C) | Jours à ±0,5 °C | Accord jours de brouillard |
| --- | --- | --- | --- | --- |
| kiruna | 5663 | 0.086 | 99.7% | 95.3% |
| rovaniemi | 5287 | 0.026 | 99.8% | 98.6% |
| tromso | 5533 | 0.04 | 99.3% | 99.8% |

## Prévisions météo (MET Norway)

| Lieu | Prévisions collectées | Dernière émission | Pas horaires | Jusqu'au | Pas avec brouillard |
| --- | --- | --- | --- | --- | --- |
| kiruna | 9 | 2026-10-31T16:29 | 56 | 2026-11-10T06:00 | 57 |
| rovaniemi | 9 | 2026-10-31T16:29 | 55 | 2026-11-10T06:00 | 56 |
| tromso | 9 | 2026-10-31T16:29 | 56 | 2026-11-10T06:00 | 57 |

## Table astronomique (calcul local, PyEphem 4.2.1)

| Lieu | Début | Fin | Heures de nuit noire (Soleil < -18°) | Jours de nuit polaire | Jours de soleil de minuit |
| --- | --- | --- | --- | --- | --- |
| kiruna | 2010-01-01 | 2026-11-10 | 22% | 349 | 853 |
| rovaniemi | 2010-01-01 | 2026-11-10 | 22% | 0 | 508 |
| tromso | 2010-01-01 | 2026-11-10 | 21% | 791 | 1168 |
