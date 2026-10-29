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

**Cible :** $y_t = 1$ si $\max \mathrm{Kp}$ sur $]t,\ t+24\,\mathrm{h}]$ $\geq 4$, sinon $0$ (table `kp_3h`). Fréquence depuis 2010 : 21,6 % des jours.

**Variables d'entrée, connues à l'instant $t$ :**

| Variable | Définition | Table |
| --- | --- | --- |
| $\mathrm{Kp}_t$ | Kp de la dernière tranche de 3 h | `kp_3h` |
| $\overline{\mathrm{Kp}}_{24h}$ | Kp moyen des 24 h passées | `kp_3h` |
| $\mathrm{Kp}^{max}_{t-27j}$ | Kp max 27 jours avant (rotation solaire) | `kp_3h` |
| $B_z^{min}$ | Bz le plus négatif des 6 h passées (nT) | `solar_wind_hourly` |
| $\overline{V}$ | Vitesse moyenne du vent solaire, 6 h passées (km/s) | `solar_wind_hourly` |
| $\overline{n}$ | Densité moyenne, 6 h passées (p/cm³) | `solar_wind_hourly` |
| $F_{10.7}$ | Flux solaire du jour | `solar_daily` |
| $s_t,\ c_t$ | Saison : $\sin$ et $\cos$ de $2\pi \cdot \text{jour}/365{,}25$ | calcul |

Découpage dans le temps : entraînement 2010-2021, validation 2022-2023, test 2024-2026. Référence à battre : la prévision Kp de la NOAA (`kp_forecast`).

## Formule de régression

**Régression logistique** (probabilité d'une belle aurore dans les 24 h) :

$$
P(y_t = 1) = \sigma(z_t) = \frac{1}{1 + e^{-z_t}}
$$

$$
z_t = \beta_0 + \beta_1\,\mathrm{Kp}_t + \beta_2\,\overline{\mathrm{Kp}}_{24h} + \beta_3\,\mathrm{Kp}^{max}_{t-27j} + \beta_4\,B_z^{min} + \beta_5\,\overline{V} + \beta_6\,\overline{n} + \beta_7\,F_{10.7} + \beta_8\,s_t + \beta_9\,c_t
$$

Les coefficients $\beta$ sont estimés par maximum de vraisemblance. On attend $\beta_4 < 0$ : plus Bz est négatif, plus l'aurore est probable.

**Visibilité par lieu** (affichage pour le directeur), pour chaque heure $h$ des 24 prochaines heures :

$$
P(\text{visible}) = P(y_t = 1) \times \mathbb{1}[\text{Soleil} < -12°] \times \left(1 - \frac{\text{nuages}_h}{100}\right) \times \left(1 - \frac{\text{brouillard}_h}{100}\right)
$$

Les nuages et le brouillard viennent de `weather_forecast` et le Soleil de `astronomy_hourly`.

Couleurs (seuils provisoires) : **vert** si $P \geq 0{,}6$ (probable), **orange** si $0{,}3 \leq P < 0{,}6$ (incertain), **rouge** si $P < 0{,}3$ (pas visible).
