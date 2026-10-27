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

Le workflow `.github/workflows/collect-noaa.yml` lance la collecte **toutes les heures sur
GitHub Actions**, PC éteint. Stockage provisoire et gratuit : chaque exécution commite ses
fichiers bruts dans la branche **`raw-data`** du dépôt (le code reste seul sur `main`).

Mise en route :

1. Sur GitHub, dans *Settings > Secrets and variables > Actions > Variables* :
   `AURORACAST_CONTACT` = ton adresse de contact.
2. Onglet *Actions* > « Collecte NOAA horaire » > **Run workflow** pour tester tout de suite.

Chaque exécution écrit son propre manifeste dans
`noaa/manifests/AAAA/MM/JJ/run_<horodatage>.jsonl`. Si une source échoue, l'exécution est
marquée en échec et GitHub t'envoie un e-mail.

Pour récupérer les données sur ton PC : `git fetch origin raw-data` puis
`git worktree add ../auroracast-data raw-data`.

**Passage à Azure plus tard** : ajouter le secret `AZURE_STORAGE_CONNECTION_STRING` ; les
fichiers partiront aussi dans Azure Blob Storage (conteneur `raw`), sans autre changement.

**Fiabilité** : l'ordonnanceur de GitHub peut retarder ou supprimer des passages (incident
INC-003 : 2 passages en 13 h la première nuit). La collecte est donc prévue pour se rattraper :
deux passages par heure (h17 et h47), et le vent solaire temps réel est collecté de façon
**incrémentale** (`--state-dir`) : chaque passage lit dans les manifestes précédents l'heure
de la dernière mesure archivée (`last_time_tag`) et reprend à partir de là, avec 10 min de
recouvrement. Si le fichier NOAA (environ 2 jours de profondeur) ne remonte plus assez loin,
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

Règles : temps en UTC (début de période) ; codes d'absence convertis en NaN ; valeurs hors
bornes physiques mises à NaN et comptées ; une ligne par période ; grille horaire continue
pour le vent solaire (un trou est une ligne `source = absent`) ; Kp jamais interpolé ; vent
solaire interpolé linéairement sur 2 h au plus (`interpolated = True`).

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

Bonnes pratiques de collecte : User-Agent identifiable, nouvelles tentatives espacées,
au plus une collecte par heure, aucune image ni graphique collecté.
