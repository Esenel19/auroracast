#!/usr/bin/env bash
# AuroraCast · déploiement Azure (TP3), à lancer dans Azure Cloud Shell (Bash), depuis une copie
# du dépôt :
#
#   git clone https://github.com/Esenel19/auroracast && cd auroracast
#   bash azure/deploiement_azure.sh                  # SIMULATION (par défaut) : rien n'est créé
#   DRY_RUN=0 bash azure/deploiement_azure.sh        # déploiement réel, après confirmation « OUI »
#   DRY_RUN=0 bash azure/deploiement_azure.sh --supprimer   # tout supprimer en fin de projet (P6)
#
# Le déploiement applique azure/main.bicep dans un seul groupe de ressources en France Central :
# stockage privé sans clé partagée (raw, clean, models, site, rapports), une identité gérée par
# usage, Key Vault (RBAC) avec l'adresse de la base saisie au clavier, Log Analytics (30 jours,
# 0,1 Go/jour), 4 tâches Container Apps planifiées (900 s au plus, sans relance), l'API, 2 alertes.
# Il copie ensuite une fois l'historique brut (branche raw-data) et les modèles (branche models),
# lance une première transformation et teste la suppression sur un groupe jetable (preuve P6).
#
# Prérequis : l'image ghcr.io/esenel19/auroracast existe et est publique (workflow « Déploiement
# Azure »). Relançable sans risque : le modèle Bicep est déclaratif, rien n'est recréé inutilement.
# Aucun secret n'est écrit dans ce fichier, sur le disque ni à l'écran.
set -euo pipefail

# ----------------------------------------------------------------------------- réglages
DRY_RUN="${DRY_RUN:-1}"
LOC="${LOC:-francecentral}"
RG="${RG:-rg-auroracast-tp3}"
GITHUB_REPO=Esenel19/auroracast
EXPIRE_LE="${EXPIRE_LE:-2027-10-30}"      # fin du crédit Azure for Students, relevée sur le portail (P2)
DEPLOY_POSTGRES="${DEPLOY_POSTGRES:-0}"  # 1 = PostgreSQL Azure B1ms, seulement si les 750 h gratuites sont confirmées
HERE="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$HERE/main.bicep"
SUFFIX=$(az account show --query id -o tsv | tr -d '-' | cut -c1-6)   # mêmes noms que main.bicep
ST="staurora${SUFFIX}"
KV="kv-aurora-${SUFFIX}"
TAGS=(projet=auroracast tp=tp3 proprietaire=elodie "expire-le=${EXPIRE_LE}")

etape() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
existe() { "$@" > /dev/null 2>&1; }
vide() { [ "$(az storage blob list --account-name "$ST" -c "$1" ${2:+--prefix "$2"} --auth-mode login \
                --num-results 1 --query 'length(@)' -o tsv 2> /dev/null || echo 0)" = "0" ]; }

# ----------------------------------------------------------------------------- 0. garde-fous
etape "0. Abonnement utilisé (preuve P1)"
az account show --query "{abonnement:name, etat:state, id:id}" -o table
OFFRE=$(az account show --query name -o tsv)
echo
echo "Région : $LOC · groupe : $RG · stockage : $ST · Key Vault : $KV · expiration : $EXPIRE_LE"
echo "Mode : $([ "$DRY_RUN" = "0" ] && echo "RÉEL" || echo "simulation (DRY_RUN=0 pour déployer)")"

# ----------------------------------------------------------------------------- suppression
if [ "${1:-}" = "--supprimer" ]; then
  [ "$DRY_RUN" = "0" ] || { echo "Simulation : az group delete -n $RG --yes ; az keyvault purge -n $KV"; exit 0; }
  read -r -p "Taper OUI pour SUPPRIMER le groupe $RG et toutes ses données : " OK
  [ "$OK" = "OUI" ] || { echo "Abandon, rien n'a été supprimé."; exit 1; }
  az group delete -n "$RG" --yes
  # Key Vault reste 7 jours en corbeille : purge pour libérer le nom
  az keyvault purge -n "$KV" -l "$LOC" 2> /dev/null || true
  az group show -n "$RG" -o none 2>&1 | grep -o "ResourceGroupNotFound" || echo "À vérifier : le groupe existe encore."
  exit 0
fi

etape "0b. Politiques de l'abonnement (régions autorisées : preuve P3)"
az policy assignment list --query "[].{politique:displayName}" -o table || true

etape "0c. Compilation du modèle Bicep"
az bicep build --file "$TEMPLATE" --stdout > /dev/null && echo "azure/main.bicep : OK"

[ -n "${ALERT_EMAIL:-}" ] || read -r -p "Adresse e-mail des alertes : " ALERT_EMAIL
[ -n "${CONTACT:-}" ] || read -r -p "Contact du User-Agent des collectes (e-mail) : " CONTACT
ME=$(az ad signed-in-user show --query id -o tsv)
PARAMS=(location="$LOC" expireLe="$EXPIRE_LE" alertEmail="$ALERT_EMAIL" contact="$CONTACT"
        deployerPrincipalId="$ME")

if [ "$DRY_RUN" != "0" ]; then
  if existe az group show -n "$RG"; then
    etape "Changements prévus (what-if), rien n'est appliqué"
    az deployment group what-if -g "$RG" -f "$TEMPLATE" -p "${PARAMS[@]}" useExistingDbSecret=true \
      --result-format ResourceIdOnly
  else
    etape "Le groupe $RG n'existe pas encore : tout serait créé (voir azure/plan_tp3.json)"
  fi
  echo; echo "Simulation terminée, rien n'a été créé. Déploiement réel : DRY_RUN=0 bash azure/deploiement_azure.sh"
  exit 0
fi

read -r -p "Taper OUI pour créer ces ressources dans l'abonnement « $OFFRE » : " OK
[ "$OK" = "OUI" ] || { echo "Abandon, rien n'a été créé."; exit 1; }

etape "0d. Fournisseurs de ressources"
for p in Microsoft.Storage Microsoft.ManagedIdentity Microsoft.KeyVault Microsoft.OperationalInsights \
         Microsoft.App Microsoft.Insights Microsoft.DBforPostgreSQL; do
  az provider register -n "$p" --wait --only-show-errors
done
az extension add --name containerapp --upgrade --only-show-errors -y

# ----------------------------------------------------------------------------- 1. groupe
etape "1. Groupe de ressources"
az group create -n "$RG" -l "$LOC" --tags "${TAGS[@]}" -o none

# ----------------------------------------------------------------------------- 2. adresse de la base
etape "2. Adresse de la base (Key Vault $KV)"
if [ "$DEPLOY_POSTGRES" = "1" ]; then
  PG_PASSWORD="$(openssl rand -hex 24)"          # hexadécimal : rien à échapper dans l'URL
  PARAMS+=(deployPostgres=true postgresPassword="$PG_PASSWORD")
  echo "PostgreSQL Flexible Server B1ms : mot de passe généré en mémoire, envoyé seulement à Key Vault."
elif existe az keyvault secret show --vault-name "$KV" -n database-url; then
  echo "Secret database-url déjà présent : conservé."
  PARAMS+=(useExistingDbSecret=true)
else
  read -r -s -p "Colle l'adresse de la base Neon (postgresql://…), rien ne s'affiche : " NEON; echo
  [ -n "$NEON" ] && PARAMS+=(databaseUrl="$NEON")
fi

# ----------------------------------------------------------------------------- 3. déploiement
# Un environnement créé en mode « Express » refuse les tâches planifiées : on le recrée en standard.
MODE=$(az resource show -g "$RG" -n cae-auroracast --resource-type Microsoft.App/managedEnvironments          --api-version 2025-10-02-preview --query properties.environmentMode -o tsv 2> /dev/null || true)
if [ "$MODE" = "Express" ]; then
  etape "3a. Environnement Container Apps en mode Express : suppression avant recréation en mode standard"
  az containerapp delete -n ca-auroracast-api -g "$RG" --yes -o none 2> /dev/null || true
  az containerapp env delete -n cae-auroracast -g "$RG" --yes -o none
fi

etape "3. Déploiement du modèle Bicep (quelques minutes)"
deployer() { az deployment group create -g "$RG" -n auroracast -f "$TEMPLATE" -p "${PARAMS[@]}" -o none; }
if ! deployer; then
  # Les droits fraîchement attribués (Key Vault, stockage) mettent jusqu'à quelques minutes à se propager
  echo "Échec probablement dû à la propagation des droits : nouvel essai dans 90 s…"; sleep 90
  deployer
fi
unset NEON PG_PASSWORD
out() { az deployment group show -g "$RG" -n auroracast --query "properties.outputs.$1.value" -o tsv; }

# ----------------------------------------------------------------------------- 4. amorçage
etape "4. Copie initiale de l'historique brut et des modèles"
echo "Attente de la propagation des droits sur le stockage (60 s)…"; sleep 60
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
if vide raw; then
  git clone --quiet --depth 1 --branch raw-data --single-branch "https://github.com/${GITHUB_REPO}" "$TMP/raw"
  rm -rf "$TMP/raw/.git"
  az storage blob upload-batch --account-name "$ST" --auth-mode login -d raw -s "$TMP/raw" --only-show-errors -o none
else
  echo "Le conteneur raw contient déjà des fichiers : copie initiale déjà faite."
fi
echo "Fichiers dans raw : $(az storage blob list --account-name "$ST" -c raw --auth-mode login --num-results 5000 --query 'length(@)' -o tsv)"
if vide models latest/; then
  git clone --quiet --depth 1 --branch models --single-branch "https://github.com/${GITHUB_REPO}" "$TMP/models"
  rm -rf "$TMP/models/.git"
  STAMP=$(date -u +%Y%m%dT%H%MZ)
  az storage blob upload-batch --account-name "$ST" --auth-mode login -d models --destination-path latest -s "$TMP/models" --only-show-errors -o none
  az storage blob upload-batch --account-name "$ST" --auth-mode login -d models --destination-path "versions/$STAMP" -s "$TMP/models" --only-show-errors -o none
  echo "Modèles copiés : latest/ et versions/$STAMP/"
else
  echo "Des modèles sont déjà en service : conservés."
fi
az containerapp job start -n caj-auroracast-transform -g "$RG" -o none \
  && echo "Première transformation lancée (portail : caj-auroracast-transform > Historique d'exécution)."

# ----------------------------------------------------------------------------- 5. preuve P6
etape "5. Test de la procédure de suppression sur un groupe jetable (preuve P6)"
az group create -n rg-auroracast-test -l "$LOC" --tags projet=auroracast usage=test-suppression -o none
az group delete -n rg-auroracast-test --yes
az group show -n rg-auroracast-test -o none 2>&1 | grep -o "ResourceGroupNotFound" || echo "À vérifier : le groupe existe encore."

# ----------------------------------------------------------------------------- 6. résumé
etape "6. Preuve P5 : ressources créées et stockage privé"
az resource list -g "$RG" --query "[].{nom:name, type:type}" -o table
az storage account show -n "$ST" -g "$RG" \
  --query "{acces_public:allowBlobPublicAccess, cle_partagee:allowSharedKeyAccess, tls_min:minimumTlsVersion}" -o table

etape "À recopier dans GitHub : Settings > Secrets and variables > Actions > onglet Variables"
echo "(ce ne sont pas des secrets : des identifiants publics, sans mot de passe)"
echo "  AZURE_CLIENT_ID       = $(out githubClientId)"
echo "  AZURE_TENANT_ID       = $(out tenantId)"
echo "  AZURE_SUBSCRIPTION_ID = $(out subscriptionId)"
echo "  AZURE_RESOURCE_GROUP  = $RG"
echo
echo "Application : $(out apiUrl)   ·   documentation de l'API : $(out apiUrl)/api/docs"
echo "Terminé. Lancer une tâche à la main : az containerapp job start -n caj-auroracast-train -g $RG"
