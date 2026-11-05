## Tables propres (météo de l'espace)

| Table | Lignes | Début | Fin | Périodes manquantes | Sources |
| --- | --- | --- | --- | --- | --- |
| kp_3h | 277138 | 1932-01-01T00:00:00+00:00 | 2026-11-05T03:00:00+00:00 | 0 | gfz 277064, noaa 74 |
| solar_daily | 34633 | 1932-01-01T00:00:00+00:00 | 2026-10-26T00:00:00+00:00 | 0 | gfz 34633 |
| solar_wind_hourly | 147655 | 2010-01-01T00:00:00+00:00 | 2026-11-05T06:00:00+00:00 | 0 | omni 146493, absent 915, noaa_rtsw 231, interpolated 16 |
| kp_forecast | 129 | 2026-10-26T19:45:15+00:00 | 2026-11-05T06:50:34+00:00 | — |  |

Plus longs trous du vent solaire :

- 2026-10-13T01:00:00+00:00 → 2026-10-26T15:00:00+00:00 (327 h)
- 2026-07-24T17:00:00+00:00 → 2026-07-26T15:00:00+00:00 (47 h)
- 2026-08-03T03:00:00+00:00 → 2026-08-04T20:00:00+00:00 (42 h)
- 2021-06-26T17:00:00+00:00 → 2021-06-27T20:00:00+00:00 (28 h)
- 2026-08-05T22:00:00+00:00 → 2026-08-06T22:00:00+00:00 (25 h)

## Nébulosité observée (NOAA GHCNh)

| Lieu | Station | Début | Fin | Heures avec nuages | Ciel dégagé (0-2 octas) | Ciel invisible (h) |
| --- | --- | --- | --- | --- | --- | --- |
| kiruna | SWI0000ESNQ | 2010-01-01 | 2026-11-02 | 88% | 33% | 5018 |
| rovaniemi | FIM00002847 | 2010-01-01 | 2026-11-02 | 82% | 33% | 2101 |
| tromso | NOM00001026 | 2010-01-01 | 2026-11-02 | 31% | 32% | 1371 |

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
| kiruna | SWI0000ESNQ | 2010-01-01 | 2026-11-02 | 99% | 97% | 966 | 218 |
| rovaniemi | FIM00002847 | 2010-01-01 | 2026-11-02 | 95% | 87% | 941 | 32 |
| tromso | NOM00001026 | 2010-01-01 | 2026-11-02 | 99% | 31% | 365 | 0 |

Contrôle croisé GHCNh → GSOD (température moyenne du jour, jours avec au moins 18 heures de température) :

| Lieu | Jours comparés | Écart moyen (°C) | Jours à ±0,5 °C | Accord jours de brouillard |
| --- | --- | --- | --- | --- |
| kiruna | 5663 | 0.086 | 99.7% | 95.3% |
| rovaniemi | 5287 | 0.026 | 99.8% | 98.6% |
| tromso | 5533 | 0.04 | 99.3% | 99.8% |

## Prévisions météo (MET Norway)

| Lieu | Prévisions collectées | Dernière émission | Pas horaires | Jusqu'au | Pas avec brouillard |
| --- | --- | --- | --- | --- | --- |
| kiruna | 22 | 2026-11-04T22:29 | 55 | 2026-11-14T12:00 | 56 |
| rovaniemi | 23 | 2026-11-05T06:29 | 54 | 2026-11-15T00:00 | 55 |
| tromso | 23 | 2026-11-05T06:29 | 54 | 2026-11-15T00:00 | 55 |

## Table astronomique (calcul local, PyEphem 4.2.1)

| Lieu | Début | Fin | Heures de nuit noire (Soleil < -18°) | Jours de nuit polaire | Jours de soleil de minuit |
| --- | --- | --- | --- | --- | --- |
| kiruna | 2010-01-01 | 2026-11-15 | 22% | 349 | 853 |
| rovaniemi | 2010-01-01 | 2026-11-15 | 22% | 0 | 508 |
| tromso | 2010-01-01 | 2026-11-15 | 21% | 791 | 1168 |
