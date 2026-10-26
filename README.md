# AuroraCast

Prévision de la visibilité des aurores boréales pour un directeur d'agence : collecte des
données d'activité solaire et géomagnétique, entrepôt de données, modèle de prévision,
API et tableau de bord.

Projet de certification **RNCP 39586** (Ingénieur en science des données), spécialité 5
(apprentissage automatique), bloc 4 visé en complément.

## Chaîne de données

1. **Collecte** : API NOAA SWPC et Open-Meteo, scraping des bulletins texte, crawling des archives, requêtes SQL
2. **Données brutes** : archivées telles quelles, horodatées, avec un manifeste
3. **Nettoyage** : alignement à l'heure UTC, valeurs manquantes, agrégations
4. **Entrepôt** : PostgreSQL, schéma en étoile
5. **Modèle** : entraînement, registre MLflow
6. **API** : FastAPI
7. **Application** : tableau de bord du directeur

## Structure

```
auroracast/
├── src/auroracast/collect/   # collecteurs (NOAA pour l'instant)
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

Points de vigilance : GitHub peut retarder ou sauter une exécution planifiée (chaque collecte du
vent solaire garde les 3 dernières heures pour rattraper ces trous) et suspend les planifications
d'un dépôt public resté 60 jours sans activité.

## Backfill de l'historique

### Kp depuis 1932 (GFZ Potsdam)

```powershell
python -m auroracast.collect.gfz        # en local
```

Dans le cloud : onglet *Actions* > « Backfill historique Kp (GFZ) » > **Run workflow**.
Le fichier brut est archivé dans `gfz/kp_ap_ap_sn_f107/AAAA/MM/JJ/` de la branche `raw-data`,
avec un rapport `*_qualite.json` : période couverte, jours manquants ou en double, Kp hors de
[0, 9], date à partir de laquelle les valeurs sont encore provisoires.

### Collecte locale toutes les heures (option)

Si le PC reste allumé, dans un terminal, depuis le dossier du projet :

```powershell
schtasks /Create /SC HOURLY /TN "AuroraCast collecte NOAA" /TR "\"$PWD\scripts\collect_noaa.bat\""
```

Le journal s'écrit dans `data\raw\collect.log`. Pour arrêter :
`schtasks /Delete /TN "AuroraCast collecte NOAA"`. Cette tâche est provisoire : elle sera
remplacée par un DAG Airflow.

## Sources et licences

| Source | Données | Licence / conditions |
| --- | --- | --- |
| [NOAA SWPC](https://services.swpc.noaa.gov/) | Kp, prévisions, vent solaire, IMF, alertes | Données publiques, à citer |
| [GFZ Potsdam](https://kp.gfz.de/) | Historique du Kp depuis 1932 | CC BY 4.0 (taches solaires : CC BY-NC 4.0) |
| [Open-Meteo](https://open-meteo.com/) | Nébulosité | CC BY 4.0, usage non commercial |

Bonnes pratiques de collecte : User-Agent identifiable, nouvelles tentatives espacées,
au plus une collecte par heure, aucune image ni graphique collecté.
