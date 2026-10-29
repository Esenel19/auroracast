# AuroraCast

Prévision de la visibilité des aurores boréales pour un directeur d'agence : collecte des
données d'activité solaire et géomagnétique, entrepôt de données, modèle de prévision,
API et tableau de bord.

Projet de certification **RNCP 39586** (Ingénieur en science des données), spécialité 5
(apprentissage automatique), bloc 4 visé en complément.

## Chaîne de données

1. **Collecte** : API NOAA SWPC et GFZ, scraping des bulletins texte, crawling des archives OMNIWeb, fichiers NOAA GHCNh, requêtes SQL
2. **Données brutes** : archivées telles quelles, horodatées, avec un manifeste
3. **Nettoyage** : alignement à l'heure UTC, valeurs manquantes, agrégations
4. **Entrepôt** : PostgreSQL, schéma en étoile
5. **Modèle** : entraînement, registre MLflow
6. **API** : FastAPI
7. **Application** : tableau de bord du directeur

## Structure

```
auroracast/
├── src/auroracast/collect/   # collecteurs (NOAA, GFZ, OMNIWeb, GHCNh)
├── src/auroracast/transform/ # nettoyage : données brutes -> tables propres
├── dags/                     # DAGs Airflow (à venir)
├── tests/                    # tests pytest
├── notebooks/                # analyses exploratoires
├── docs/                     # dictionnaire de données
├── scripts/                  # lanceurs (Planificateur de tâches Windows)
├── data/raw/                 # données brutes (jamais commitées)
└── .github/workflows/ci.yml  # CI : lint + tests à chaque push
```

## Installation (Windows, PowerShell)

```powershell
cd auroracast
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pip install -e .
copy .env.example .env        # puis renseigner AURORACAST_CONTACT
```

> Si PowerShell refuse d'activer l'environnement :
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Collecte NOAA

```powershell
python -m auroracast.collect.noaa                          # toutes les sources
python -m auroracast.collect.noaa --only kp_observed alerts
pytest                                                     # tests
```

Fichiers produits :

- `data/raw/noaa/<source>/AAAA/MM/JJ/<source>_AAAAMMJJTHHMMSSZ.json` : réponse brute
- `data/raw/noaa/manifest.jsonl` : une ligne par téléchargement (statut, taille, SHA-256, erreur éventuelle)

Le code de sortie vaut 1 si au moins une source a échoué.

### Collecte automatique dans le cloud (recommandé)

Le workflow `.github/workflows/collect-noaa.yml` lance la collecte **toutes les 3 heures sur
GitHub Actions**, PC éteint. Stockage provisoire et gratuit : chaque exécution commite ses
fichiers bruts dans la branche **`raw-data`** du dépôt (le code reste seul sur `main`).

Mise en route :

1. Sur GitHub, dans *Settings > Secrets and variables > Actions > Variables* :
   `AURORACAST_CONTACT` = ton adresse de contact.
2. Onglet *Actions* > « Collecte NOAA (toutes les 3 h) » > **Run workflow** pour tester tout de suite.

Chaque exécution écrit son propre manifeste dans
`noaa/manifests/AAAA/MM/JJ/run_<horodatage>.jsonl`. Si une source échoue, l'exécution est
marquée en échec et GitHub t'envoie un e-mail.

Pour récupérer les données sur ton PC : `git fetch origin raw-data` puis
`git worktree add ../auroracast-data raw-data`.

**Passage à Azure plus tard** : ajouter le secret `AZURE_STORAGE_CONNECTION_STRING` ; les
fichiers partiront aussi dans Azure Blob Storage (conteneur `raw`), sans autre changement.

**Fiabilité** : l'ordonnanceur de GitHub peut retarder ou supprimer des passages (incident
INC-003 : 2 passages en 13 h la première nuit). La collecte est donc prévue pour se rattraper :
un passage toutes les 3 heures (cadence choisie d'après la profondeur mesurée des flux :
environ 20 h pour le vent solaire), et le vent solaire temps réel est collecté de façon
**incrémentale** (`--state-dir`) : chaque passage lit dans les manifestes précédents l'heure
de la dernière mesure archivée (`last_time_tag`) et reprend à partir de là, avec 10 min de
recouvrement. Si le fichier NOAA (environ 20 h de profondeur) ne remonte plus assez loin,
la perte est notée au manifeste (`lost_from`, `lost_to`). GitHub suspend aussi les
planifications d'un dépôt public resté 60 jours sans activité.

### Collecte locale toutes les heures (option)

Si le PC reste allumé, dans un terminal, depuis le dossier du projet :

```powershell
schtasks /Create /SC HOURLY /TN "AuroraCast collecte NOAA" /TR "\"$PWD\scripts\collect_noaa.bat\""
```

Le journal s'écrit dans `data\raw\collect.log`. Pour arrêter :
`schtasks /Delete /TN "AuroraCast collecte NOAA"`. Cette tâche est provisoire : elle sera
remplacée par un DAG Airflow.

## Backfill de l'historique

Onglet *Actions* > « Backfill historique » > **Run workflow**, en choisissant la source
(`tout`, `gfz`, `omni`, `ghcnh`) et la première année. En local :

```powershell
python -m auroracast.collect.gfz                         # Kp, ap, SN, F10.7 depuis 1932
python -m auroracast.collect.omni --start-year 2010      # vent solaire horaire (crawling)
python -m auroracast.collect.ghcnh backfill --start-year 2010   # nébulosité observée
```

| Source | Technique | Archivé dans `raw-data` | Rapport de qualité |
| --- | --- | --- | --- |
| GFZ Potsdam | API (fichier HTTPS) | `gfz/kp_ap_ap_sn_f107/AAAA/MM/JJ/` | jours manquants ou en double, Kp hors [0, 9], valeurs provisoires |
| OMNIWeb (NASA SPDF) | crawling du répertoire `low_res_omni` | `omni/omni2/AAAA/MM/JJ/` (+ page d'index) | heures manquantes ou en double, part de valeurs absentes pour Bz, Bt, vitesse, densité |

### Nébulosité observée (NOAA GHCNh)

Open-Meteo est écarté : le robots.txt de son API d'archive interdit l'accès (incident INC-002
du journal des incidents). La nébulosité viendra des observations horaires des stations
NOAA GHCNh (CC0). Première étape, le choix des stations :

```powershell
python -m auroracast.collect.ghcnh stations --year 2025
```

Dans le cloud : *Actions* > « Recherche des stations météo (GHCNh) » > **Run workflow**. Pour
chaque lieu cible (Tromsø, Kiruna, Rovaniemi ; option `--location NOM:LAT:LON`), le rapport
liste les stations les plus proches, leur distance et la part d'heures où les nuages sont
renseignés.

Les stations retenues sont fixées dans `src/auroracast/data/stations.json`. Un lieu à `null`
reçoit une station choisie automatiquement au backfill, selon une règle écrite : la plus
proche à moins de 30 km dont les nuages sont renseignés au moins 30 % des heures.

Le backfill (*Backfill historique*, source `ghcnh`) archive chaque fichier annuel compressé
(`ghcnh/hourly/AAAA/MM/JJ/<lieu>/GHCNh_<station>_<année>.psv.gz`, contenu inchangé) avec un
rapport de qualité : heures couvertes par une observation de nuages, répartition des octas,
plus long trou entre deux observations. Décodage : la couche la plus couverte parmi
`sky_cover_layer_1` à `4` (`CLR:00` dégagé … `OVC:08` couvert, `VV:09` ciel invisible compté
8 octas), sinon la couverture totale de `sky_condition`.

**Mise à jour quotidienne** (*Actions* > « Collecte météo GHCNh (quotidienne) », chaque jour
vers 05:37 UTC) : le fichier de l'année en cours est re-téléchargé, mais seules les lignes
postérieures à la dernière observation archivée (lue dans les manifestes, 6 h de
recouvrement) sont gardées, dans `GHCNh_<station>_<année>_maj_<horodatage>.psv.gz`. Quelques
Ko par jour au lieu de près de 1 Mo (environ 340 Mo par an évités dans `raw-data`). En local :
`python -m auroracast.collect.ghcnh update --state-dir data/raw`.

## Transformation : tables propres

```powershell
python -m auroracast.transform --raw data/raw --out data/clean --csv
```

Dans le cloud : *Actions* > « Transformation des données » (aussi chaque jour vers 06:23 UTC).
Toutes les données brutes de `raw-data` sont relues et les tables sont publiées dans la branche
**`clean-data`** (Parquet, un seul commit remplacé à chaque fois) avec `rapport_qualite.json`.

| Table | Grain | Colonnes | Sources |
| --- | --- | --- | --- |
| `kp_3h` | 3 h | `time`, `kp`, `ap`, `status` (definitive, preliminary, noaa_estimated), `source` | GFZ, complété par la NOAA pour les tranches que le GFZ n'a pas encore publiées |
| `solar_daily` | jour | `date`, `Ap`, `sn`, `f107_obs`, `f107_adj`, `sn_status` | GFZ |
| `solar_wind_hourly` | 1 h | `time`, `bt`, `by_gsm`, `bz_gsm`, `density`, `speed`, `bz_min`, `speed_max`, `source`, `interpolated` | OMNIWeb, puis NOAA RTSW (satellite actif, moyenne horaire si au moins 15 min de mesures) |
| `kp_forecast` | prévision | `issued_at`, `target_time`, `kp`, `lead_hours` | NOAA (valeurs « predicted », date d'émission = collecte où elles apparaissent) |
| `locations` | lieu | `location`, `lat`, `lon`, `station_id`, `station_name`, `station_distance_km` | `locations.py` + `data/stations.json` |
| `cloud_hourly` | lieu × 1 h | `location`, `time`, `oktas`, `cloud_cover_pct`, `obscured`, `n_obs`, `offset_min`, `station_id`, `source` | NOAA GHCNh : observation la plus proche de l'heure pile, jamais interpolée |
| `weather_daily` | lieu × jour | `location`, `day`, `temp_c`, `temp_max_c`, `temp_min_c`, `dewpoint_c`, `rel_humidity_pct`, `visibility_km`, `wind_mean_ms`, `wind_max_ms`, `precip_mm`, `precip_flag`, `precip_suspect`, `fog`, `rain`, `snow`, `hail`, `thunder`, … | NOAA GSOD par SQL (BigQuery), 2010 → 24/08/2025 ; unités SI ; lettre « I » = pas de relevé de pluie |
| `weather_forecast` | lieu × émission × heure visée | `location`, `issued_at`, `collected_at`, `target_time`, `lead_hours`, `step_hours`, `cloud_cover_pct`, `cloud_low_pct`, `cloud_medium_pct`, `cloud_high_pct`, `fog_pct`, `temp_c`, `dewpoint_c`, `rel_humidity_pct`, `pressure_hpa`, `wind_speed_ms`, `wind_dir_deg`, `wind_gust_ms`, `precip_mm`, `precip_probability_pct`, `symbol_code`, `source_file` | MET Norway : toutes les prévisions collectées ; `issued_at` = mise à jour du modèle ; doublons de collecte : la plus récente l'emporte |
| `astronomy_hourly` | lieu × 1 h | `location`, `time`, `sun_alt_deg`, `night_phase` (day, civil, nautical, astronomical, night), `moon_alt_deg`, `moon_illum_pct`, `moon_up`, `polar_night`, `midnight_sun`, `source` | Calcul local PyEphem (aucune collecte), 2010 → J+10 ; hauteurs géométriques ; vérifié contre sunrise-sunset.org (écart < 5 s) |
| `weather_hourly` | lieu × 1 h | `location`, `time`, `temp_c`, `dewpoint_c`, `rel_humidity_pct`, `pressure_hpa`, `wind_speed_ms`, `wind_dir_deg`, `visibility_km`, `visibility_capped`, `fog`, `mist`, `present_weather`, `n_obs`, `offset_min`, `station_id`, `source` | NOAA GHCNh, 2010 → hier : par variable, observation la plus proche de l'heure pile ; codes qualité 2, 3, 6, 7 écartés ; vent 999 = calme ou variable ; visibilité METAR plafonnée à 10 km (Kiruna) |

Contrôle croisé (rapport de qualité, `crosscheck_gsod_ghcnh`) : la température moyenne du
jour recalculée depuis `weather_hourly` est comparée à celle publiée par GSOD. Écart moyen
de 0,03 à 0,09 °C selon le lieu, plus de 99 % des jours à ±0,5 °C, et 95 à 99,8 % d'accord
sur les jours de brouillard : les deux sources (mêmes stations) se confirment.

Règles : temps en UTC (début de période) ; codes d'absence convertis en NaN ; valeurs hors
bornes physiques mises à NaN et comptées ; une ligne par période ; grille horaire continue
pour le vent solaire (un trou est une ligne `source = absent`) ; Kp jamais interpolé ; vent
solaire interpolé linéairement sur 2 h au plus (`interpolated = True`).

### Visualiser les tables (VS Code)

Récupérer les tables propres une fois, depuis le dossier du projet :

```powershell
git fetch origin clean-data
git worktree add data/clean clean-data
```

Puis ouvrir `notebooks/01_exploration_meteo_espace.ipynb` (météo de l'espace),
`notebooks/02_exploration_nuages.ipynb` (nébulosité) ou `notebooks/03_exploration_meteo_sol.ipynb`
(vue d'ensemble des tables propres, météo au sol GSOD et GHCNh, contrôle croisé),
`notebooks/04_previsions_meteo.ipynb` (prévisions MET Norway) ou
`notebooks/05_exploration_astronomie.ipynb` (ciel noir, Lune, nuit polaire, biais des
observations de nuit) dans VS Code (extensions *Python* et
*Jupyter*) et cliquer sur *Run All*. Pour parcourir une table comme dans un tableur, clic
droit sur un fichier `.parquet` de `data/clean` > *Open in Data Wrangler* (extension Microsoft
*Data Wrangler*). Mise à jour des tables : `git -C data/clean fetch origin clean-data` puis `git -C data/clean reset --hard origin/clean-data` (pas de `pull` : la branche est republiée à chaque transformation).

### Prévisions météo (MET Norway)

Le workflow de collecte (toutes les 3 h) interroge aussi l'API Locationforecast 2.0 de MET
Norway (`collect/metno.py`, format *complete*) pour les 3 lieux : nébulosité totale et par
étage, brouillard (part de la zone où la visibilité passe sous 1 km : l'API ne donne pas de
visibilité en km), température, rosée, humidité, pression, vent, rafales, pluie. Pas horaire
sur environ 50 h, puis pas de 6 h jusqu'à 10 jours. Règles de l'API respectées : User-Agent
avec contact, coordonnées tronquées à 4 décimales, pas de nouvelle requête avant l'en-tête
`Expires`, `If-Modified-Since` (réponse 304 sans contenu si rien n'a changé). Chaque réponse
est archivée telle quelle (`metno/locationforecast/AAAA/MM/JJ/<lieu>/complete_<horodatage>.json.gz`,
environ 6 Ko) et la table propre `weather_forecast` garde **toutes** les prévisions émises, pour
pouvoir comparer plus tard le prévu à l'observé selon l'échéance. Visualisation :
`notebooks/04_previsions_meteo.ipynb`.

### Collecte par requêtes SQL (Google BigQuery, NOAA GSOD)

Base existante interrogée en SQL : `bigquery-public-data.noaa_gsod` (relevés quotidiens :
visibilité, brouillard, précipitations, neige, température, vent). Elle s'arrête au
24/08/2025 et sert donc d'historique ; `weather_hourly` (GHCNh, mêmes stations) prend le
relais jusqu'à hier et sert de contrôle croisé. Deux requêtes paramétrées (`collect/gsod.py`) :
stations les plus proches de chaque lieu (`ST_GEOGPOINT`, `ST_DISTANCE`, `ST_DWITHIN`),
puis relevés 2010-2025 en une requête sur la table générique `gsod*` (`_TABLE_SUFFIX`) avec
jointure sur `stations`. Chaque requête est estimée à blanc (gratuit) puis exécutée avec un
plafond de 15 Go facturés (la plus grosse lit 8,7 Go, sur 1 To gratuit par mois) ; le texte SQL, les octets traités et l'identifiant du job sont
archivés avec les résultats (`gsod/AAAA/MM/JJ/`).

Mise en route : projet Google Cloud en mode sandbox (gratuit, sans carte bancaire), compte
de service avec le seul rôle *Utilisateur de job BigQuery*, puis sur GitHub le secret
`GCP_SA_KEY` (contenu de la clé JSON) et la variable `GCP_PROJECT_ID`. Lancement :
*Backfill historique*, source `gsod`. Ne jamais commiter la clé JSON.

## Entrepôt de données PostgreSQL (modèle en étoile)

Les tables propres sont chargées dans une base PostgreSQL (`sql/schema.sql`) :

- **dimensions** : `dim_location` (3 lieux et leur station), `dim_time` (une ligne par heure
  UTC depuis 1932, avec année, mois, saison des aurores…), `dim_source` (source, technique de
  collecte, licence) ;
- **faits globaux** : `fact_kp` (3 h), `fact_kp_forecast`, `fact_solar_daily`, `fact_solar_wind` ;
- **faits par lieu** : `fact_weather_observed` (nuages et météo GHCNh fusionnés, par heure),
  `fact_weather_daily` (GSOD), `fact_weather_forecast` (MET Norway, toutes les émissions),
  `fact_astronomy` ;
- **vue** `v_aurora_conditions` : par lieu et par heure, Kp de la tranche de 3 h, nuages,
  brouillard, obscurité, et les indicateurs `kp_ge_4`, `dark_sky`, `clear_sky` ;
- **suivi** : `load_run` (une ligne par chargement : durée, lignes, taille de la base).

Chargement (`python -m auroracast.warehouse.load --clean data/clean`) : rechargement complet
dans une seule transaction (schéma rejouable, `TRUNCATE`, puis `COPY`) ; en cas d'erreur, la
base garde le chargement précédent. Environ 1,8 million de lignes, 213 Mo, 1 min en local.
Requêtes d'exemple : `sql/requetes_exemples.sql`.

Sur le PC (Docker Desktop démarré, fichier `.env` rempli d'après `.env.example`) :

```powershell
docker compose up -d                          # base PostgreSQL 16 locale
python -m auroracast.warehouse.load           # lit DATABASE_URL dans .env
docker exec -it auroracast-db psql -U auroracast -d auroracast   # console SQL
```

## Respect des sites (robots.txt)

Tous les collecteurs passent par `auroracast.collect.web.PoliteSession`, qui lit le robots.txt
de chaque site avant le premier téléchargement (RFC 9309) : une URL interdite n'est pas
téléchargée, le Crawl-delay est respecté (pause d'au moins 1 s entre deux requêtes, 5 s pour
les API qui comptent les requêtes), et le résultat est noté dans le manifeste (champ `robots`).

## Sources et licences

| Source | Données | Licence / conditions |
| --- | --- | --- |
| [NOAA SWPC](https://services.swpc.noaa.gov/) | Kp, prévisions, vent solaire, IMF, alertes | Données publiques, à citer |
| [GFZ Potsdam](https://kp.gfz.de/) | Historique du Kp depuis 1932 | CC BY 4.0 (taches solaires WDC-SILSO : CC BY-NC 4.0) ; citer Matzka et al. (2021), DOI 10.5880/Kp.0001 |
| [OMNIWeb, NASA SPDF](https://omniweb.gsfc.nasa.gov/) | Vent solaire et IMF horaires | Citer OMNI Hourly Data, DOI 10.48322/1shr-ht18, avec la date d'accès |
| [NOAA GHCNh](https://www.ncei.noaa.gov/products/global-historical-climatology-network-hourly) | Nébulosité observée par les stations | CC0 ; citer NOAA NCEI, GHCNh Version 1, DOI 10.25921/jp3d-3v19 |
| NOAA GSOD sur BigQuery (`bigquery-public-data.noaa_gsod`) | Relevés météo quotidiens (2010 → 24/08/2025) | Données NOAA du domaine public, hébergées dans les jeux publics Google BigQuery |
| [MET Norway, Locationforecast 2.0](https://api.met.no/weatherapi/locationforecast/2.0/documentation) | Prévisions locales : nuages par étage, brouillard, température, humidité, vent, pluie | CC BY 4.0 ; citer MET Norway, lien vers la licence, signaler les modifications ([conditions](https://api.met.no/doc/TermsOfService)) |

Bonnes pratiques de collecte : User-Agent identifiable, nouvelles tentatives espacées,
au plus une collecte par heure, aucune image ni graphique collecté.
