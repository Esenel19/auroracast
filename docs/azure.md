# AuroraCast sur Azure (TP3)

Toute la chaîne tourne sur Azure (offre Azure for Students), dans un seul groupe de ressources
`rg-auroracast-tp3` en France Central. La chaîne gratuite GitHub Actions + Neon reste en service
à côté : si l'abonnement s'arrête, le projet continue de fonctionner.

Livrables du TP3, dans le dossier `azure/` :

| Fichier | Rôle |
| --- | --- |
| `main.bicep` | Infrastructure as code : toutes les ressources, leurs réglages et leurs droits |
| `deploiement_azure.sh` | Déploiement depuis Azure Cloud Shell : simulation par défaut, confirmation « OUI », amorçage, test de suppression |
| `plan_tp3.json` | Plan : offre, région, ressources, limites, coût, suppression |
| `preflight_tp3.py` | Prévol local (14 contrôles, dont la cohérence plan ↔ Bicep), lancé aussi par la CI |
| `rapport_prevol.json` | Dernier rapport du prévol |

## Architecture

```
            GitHub (main)
              │  push ─► workflow « Déploiement Azure » : image Docker testée ─► ghcr.io (gratuit)
              │  OIDC, sans secret (id-auroracast-github : Container Apps Contributor sur le groupe)
              ▼
┌──────────────────────────── rg-auroracast-tp3 ────────────────────────────┐
│ Container Apps (cae-auroracast, consommation)        identité            │
│   caj-auroracast-collect    toutes les 3 h   ─► raw   id-auroracast-collecte
│   caj-auroracast-ghcnh      05:37 UTC        ─► raw   id-auroracast-collecte
│   caj-auroracast-transform  3 h (+30 min)  raw ─► clean, PostgreSQL,     │
│                             forecast.json ─► site, rapport ─► rapports   │
│                                                       id-auroracast-transform
│   caj-auroracast-train      10:13 UTC  PostgreSQL ─► models, rapports    │
│                                                       id-auroracast-train│
│   ca-auroracast-api         HTTPS, 0 à 1 réplique ◄─ site, models        │
│                                                       id-auroracast-api  │
│ Stockage staurora<6> (privé, sans clé partagée, TLS 1.2, corbeille 7 j)   │
│   raw · clean · models (latest/, versions/) · site · rapports            │
│ Key Vault kv-aurora<6> (RBAC) : database-url                             │
│ Log Analytics (30 j, 0,1 Go/j) ─► 2 alertes e-mail                       │
│ [option] PostgreSQL Flexible Server B1ms                                 │
└──────────────────────────────────────────────────────────────────────────┘
       sources : NOAA SWPC, GFZ, OMNIWeb, GHCNh, MET Norway ; base : Neon (par défaut)
```

### Droits (moindre privilège, conteneur par conteneur)

| Identité | raw | clean | models | site | rapports | Key Vault |
| --- | --- | --- | --- | --- | --- | --- |
| id-auroracast-collecte | écriture | | | | | |
| id-auroracast-transform | lecture | écriture | lecture | écriture | écriture | lecture du secret |
| id-auroracast-train | | | écriture | | écriture | lecture du secret |
| id-auroracast-api | | | lecture | lecture | | |
| id-auroracast-github | aucun accès aux données : changer l'image des apps du groupe | | | | | |
| personne qui déploie | écriture (amorçage) | | | | | gestion des secrets |

Aucune identité n'a le rôle Owner ou Contributor, ni de droit sur l'abonnement.

| Avant (GitHub) | Sur Azure | Code |
| --- | --- | --- |
| Branches `raw-data`, `clean-data`, `models` | Conteneurs Blob `raw`, `clean`, `models/latest` + `models/versions/<horodatage>` | `auroracast.cloud.storage` |
| Workflows planifiés | Container Apps Jobs planifiés (même code) | `auroracast.cloud.jobs` |
| GitHub Pages | Container App : l'API sert aussi la page du directeur | `auroracast.api.main` |
| Secrets GitHub | Key Vault, lu par identité gérée | `azure/main.bicep` |
| E-mail d'échec GitHub | Alertes Azure Monitor (tâche en échec, prévisions de plus de 12 h) | `azure/main.bicep` |

## API

| Route | Rôle |
| --- | --- |
| `GET /` et `/suivi.html` | Application du directeur et suivi des modèles |
| `GET /forecast.json`, `/suivi_modeles.json` | Données calculées par la tâche `transform` |
| `GET /api/health` | Modèles chargés, prévision disponible et son âge |
| `GET /api/models` | Modèles en service : seuil, date d'entraînement, notes sur le test |
| `POST /api/predict` | Probabilité de Kp ≥ 4 dans 6, 24 ou 48 h pour des mesures données |
| `GET /api/docs` | Documentation interactive (OpenAPI) |

```powershell
pip install -r requirements-azure.txt
uvicorn auroracast.api.main:app --port 8000      # lit models/ et site/ du dossier courant
```

```json
POST /api/predict
{"horizon_h": 24, "mesures": {"kp": 5, "bz_mean_3h": -8, "speed_mean_6h": 550}}
→ {"probabilite": 0.81, "seuil_alerte": 0.24, "alerte": true, "variables_absentes": ["f107", …]}
```

Une variable absente est remplacée par la médiane de l'apprentissage, comme dans le modèle.

## Garde-fous (prévol)

```bash
python azure/preflight_tp3.py . azure/plan_tp3.json azure/deploiement_azure.sh azure/rapport_prevol.json
```

| # | Garde-fou | Mise en œuvre |
| --- | --- | --- |
| C1 à C3 | Aucun secret dans le dépôt | Identifiants Azure en *variables* GitHub (publics), OIDC, `database-url` seulement dans Key Vault |
| C4 | Durée maximale de chaque job | `timeout-minutes` sur tous les workflows GitHub |
| C5 | Stockage privé | Accès public désactivé, clé partagée désactivée, TLS 1.2, conteneurs privés |
| C6 | CPU seul, durée limitée | 0,5 à 1 vCPU, pas de GPU, 900 s, aucune relance ; tâches planifiées (lançables à la main) |
| C7 | Une identité par usage, rôles précis | Voir le tableau des droits |
| C8 | Journaux bornés | Log Analytics : 30 jours, 0,1 Go par jour |
| C9 | Suivi des coûts | Budget indisponible pour Azure for Students : relevé mensuel du crédit, plafonds techniques, alertes |
| C10 | Suppression écrite et testée | Étiquette `expire-le=2027-10-30` (fin du crédit) ; test sur `rg-auroracast-test` ; `--supprimer` |
| C11 | Pas de passage au payant | Offre sans carte bancaire |
| C12 | Région et limites documentées | `azure/plan_tp3.json` |
| C13 | Script sans risque | Simulation par défaut, confirmation « OUI », secret saisi sans écho |
| C14 | Le code applique le plan | Lecture de `main.bicep` : conteneurs, stockage privé, journaux, 900 s, aucun rôle large, HTTPS, OIDC limité à main |

Évolution par rapport à la première version du TP3 : les tâches sont **planifiées** et non
plus lancées à la main uniquement. L'application doit rester à jour toutes les 3 h. Le coût
reste maîtrisé par la limite de 900 s, l'absence de relance, l'API à l'échelle zéro et la
gratuité mensuelle de Container Apps.

## Coût estimé (jusqu'à la fin du crédit)

| Brique | Usage | Par mois (USD) |
| --- | --- | --- |
| Container Apps Jobs | ~240 collectes, ~240 transformations, 30 entraînements | ≈ 0 (gratuité de 180 000 vCPU-s et 360 000 Gio-s par mois) |
| API (mise à l'échelle jusqu'à zéro) | quelques consultations | ≈ 0 |
| Stockage LRS | ~0,3 Go | < 0,10 |
| Key Vault | quelques milliers de lectures | < 0,10 |
| Log Analytics | ≤ 3 Go | 0 à 1 |
| 2 alertes de journaux | évaluation toutes les 1 h et 3 h | ≈ 1 |
| **Total** | | **≈ 1 à 2, soit 11 à 22 jusqu'au 30/10/2027, pour un crédit de 90** |

À éviter, car ces services feraient fondre le crédit :
- **Azure Container Registry** (~5 par mois), remplacé par ghcr.io ;
- **PostgreSQL Flexible Server** (~15 par mois si les 750 h gratuites ne s'appliquent pas). Ne passer `DEPLOY_POSTGRES=1` qu'après avoir vu ces 750 h dans les services gratuits de l'abonnement.

Neon : donner aux tâches Azure l'adresse d'une **branche Neon dédiée** (par exemple `azure`).
Sans cela, GitHub et Azure rechargent la même base, ce qui double la consommation de calcul
Neon.

Si une transformation dépasse 900 s, l'alerte « prévisions non mises à jour » prévient.
`jobTimeout` peut alors monter jusqu'à 1800 s.

## Déploiement pas à pas

1. **Image** : pousser sur `main`. Le workflow « Déploiement Azure » publie
   `ghcr.io/esenel19/auroracast`. Rendre ensuite le paquet **public**, une seule fois
   (GitHub > Packages > auroracast > *Change visibility*).
2. **Azure Cloud Shell (Bash)**, sur portal.azure.com :
   ```bash
   git clone https://github.com/Esenel19/auroracast && cd auroracast
   bash azure/deploiement_azure.sh            # simulation : rien n'est créé
   DRY_RUN=0 bash azure/deploiement_azure.sh  # réel : demande OUI, puis l'adresse Neon (masquée)
   ```
3. **GitHub** : créer les 4 variables affichées à la fin (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
   `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`). Chaque push sur `main` met alors à jour
   l'API et les tâches.
4. **Vérifier** : ouvrir l'adresse de l'application ; dans le portail,
   *caj-auroracast-transform > Historique d'exécution* ; dans Log Analytics :
   `ContainerAppConsoleLogs_CL | where ContainerJobName_s startswith "caj-auroracast" | project TimeGenerated, Log_s`.

Lancer une tâche à la main : `az containerapp job start -n caj-auroracast-train -g rg-auroracast-tp3`.
Tout supprimer : `DRY_RUN=0 bash azure/deploiement_azure.sh --supprimer`.

## Preuves à fournir (console)

| # | Preuve | Où |
| --- | --- | --- |
| P1 | Abonnement actif, sans moyen de paiement | Portail > Abonnements ; début de sortie du script |
| P2 | Crédit restant et date d'expiration (chaque mois) | Microsoft Azure Sponsorships |
| P3 | France Central autorisée | Policy > Allowed locations ; étape 0b du script |
| P4 | Suivi des coûts | Message « offre non prise en charge » de Cost Management + relevé P2 |
| P5 | Ressources créées et privées | Étape 6 du script |
| P6 | Suppression effective | Étape 5 du script (`rg-auroracast-test` → `ResourceGroupNotFound`) |

Pour les captures, masquer l'identifiant d'abonnement et les adresses ; aucune clé ne doit être
visible. Une fois les captures faites, créer `azure/preuves_console.json`, par exemple
`{"P1": true, "P2": true, …}` : le prévol passe alors en **GO**.

## Rejouer la chaîne Azure sur le PC

Sans `AZURE_STORAGE_ACCOUNT`, les tâches utilisent le dossier `data/azure-local/` comme faux
stockage :

```powershell
python -m auroracast.cloud.jobs collect
python -m auroracast.cloud.jobs transform     # DATABASE_URL lu dans .env
```
