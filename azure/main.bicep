// AuroraCast sur Azure (TP3) : tout le périmètre dans un seul groupe de ressources.
//
//   stockage privé (raw, clean, models, site, rapports)  sans clé partagée, TLS 1.2, corbeille 7 jours
//   une identité gérée par usage, chacune limitée aux conteneurs dont elle a besoin :
//     id-auroracast-collecte    écrit raw                                  (tâches collect, ghcnh)
//     id-auroracast-transform   lit raw et models ; écrit clean, site, rapports ; lit le secret
//     id-auroracast-train       écrit models et rapports ; lit le secret
//     id-auroracast-api         lit site et models                         (API, application)
//     id-auroracast-github      fédération OIDC (main) : met à jour l'image des apps, rien d'autre
//   secrets                      Key Vault (RBAC) : database-url
//   calcul                       Container Apps Jobs CPU, 900 s au plus, sans relance ; API à l'échelle zéro
//   journaux et alertes          Log Analytics (30 j, 0,1 Go/j), 2 alertes e-mail
//   budget                       désactivé par défaut : Cost Management ne gère pas Azure for Students
//   base PostgreSQL (option)     Flexible Server B1ms, si la gratuité 750 h est confirmée
//
// Déploiement : azure/deploiement_azure.sh. Suppression : supprimer le groupe.

targetScope = 'resourceGroup'

@description('Région (France Central : à vérifier dans la politique « Allowed locations » de l\'abonnement)')
param location string = resourceGroup().location

@description('Fin prévue (fin du crédit Azure for Students), écrite en étiquette sur chaque ressource')
param expireLe string = '2027-11-20'

@description('Propriétaire, écrit en étiquette')
param proprietaire string = 'elodie'

@description('Image publiée sur GitHub Container Registry (paquet public)')
param image string = 'ghcr.io/esenel19/auroracast:latest'

@description('Sujet OIDC exact envoyé par GitHub (branche main). GitHub y inclut les identifiants numériques du compte et du dépôt : le recopier depuis le journal de azure/login en cas d'erreur AADSTS700213')
param githubSubject string = 'repo:Esenel19@79450710/auroracast@1434834270:ref:refs/heads/main'

@description('Adresse e-mail des alertes')
param alertEmail string

@description('Contact envoyé dans le User-Agent des collectes (règles MET Norway et NOAA)')
param contact string

@description('objectId de la personne qui déploie : droits sur le stockage (amorçage) et Key Vault')
param deployerPrincipalId string

@description('Adresse PostgreSQL (Neon) à ranger dans Key Vault ; vide = garder le secret existant')
@secure()
param databaseUrl string = ''

@description('Le secret database-url existe déjà dans Key Vault (relance du script)')
param useExistingDbSecret bool = false

@description('Créer un PostgreSQL Flexible Server B1ms (seulement si les 750 h gratuites sont confirmées)')
param deployPostgres bool = false

@description('Mot de passe administrateur PostgreSQL (hexadécimal, généré par le script)')
@secure()
param postgresPassword string = ''

@description('Créer un budget Cost Management (indisponible avec Azure for Students)')
param deployBudget bool = false

@description('Budget mensuel, dans la devise de facturation')
param budgetAmount int = 10

@description('Premier jour du mois de départ du budget (AAAA-MM-01)')
param budgetStart string = '${utcNow('yyyy-MM')}-01'

@description('Durée maximale d\'une exécution de tâche, en secondes (TP3 : 900)')
@maxValue(1800)
param jobTimeout int = 900

// Suffixe stable : 6 premiers caractères de l'abonnement (comme le plan du TP3)
var suffix = substring(replace(subscription().subscriptionId, '-', ''), 0, 6)
var tags = { projet: 'auroracast', tp: 'tp3', proprietaire: proprietaire, 'expire-le': expireLe }
var roles = {
  blobContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'   // Storage Blob Data Contributor
  blobReader: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'        // Storage Blob Data Reader
  kvSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
  kvSecretsOfficer: 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
  containerAppsContributor: '358470bc-b998-42bd-ab17-a7e34c199c0f'
}
var containerNames = [ 'raw', 'clean', 'models', 'site', 'rapports' ]   // indices 0 à 4

// ------------------------------------------------------------------ journaux
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-auroracast'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: json('0.1') }   // au-delà, les journaux du jour sont perdus
  }
}

// ------------------------------------------------------------------ identités (une par usage)
resource idCollect 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-auroracast-collecte'
  location: location
  tags: tags
}

resource idTransform 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-auroracast-transform'
  location: location
  tags: tags
}

resource idTrain 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-auroracast-train'
  location: location
  tags: tags
}

resource idApi 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-auroracast-api'
  location: location
  tags: tags
}

resource idGithub 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-auroracast-github'
  location: location
  tags: tags
}

// GitHub Actions (branche main seulement) obtient un jeton Azure sans secret stocké
resource githubFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: idGithub
  name: 'github-main'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: githubSubject
    audiences: [ 'api://AzureADTokenExchange' ]
  }
}

// ------------------------------------------------------------------ stockage privé
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'staurora${suffix}'
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false          // identités Entra ID seulement (garde-fou C5)
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'       // Container Apps en consommation : pas d'IP fixe
    allowCrossTenantReplication: false
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 7 }           // suppression annulable 7 jours
    containerDeleteRetentionPolicy: { enabled: true, days: 7 }
  }
}

resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for c in containerNames: {
  parent: blobService
  name: c
  properties: { publicAccess: 'None' }
}]

// Droits sur les données, conteneur par conteneur (moindre privilège)
var storageGrants = [
  { who: 'collecte', c: 0, role: roles.blobContributor }
  { who: 'transform', c: 0, role: roles.blobReader }
  { who: 'transform', c: 1, role: roles.blobContributor }
  { who: 'transform', c: 2, role: roles.blobReader }
  { who: 'transform', c: 3, role: roles.blobContributor }
  { who: 'transform', c: 4, role: roles.blobContributor }
  { who: 'train', c: 2, role: roles.blobContributor }
  { who: 'train', c: 4, role: roles.blobContributor }
  { who: 'api', c: 2, role: roles.blobReader }
  { who: 'api', c: 3, role: roles.blobReader }
]

resource storageRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for g in storageGrants: {
  scope: containers[g.c]
  name: guid(storage.id, containerNames[g.c], g.who, g.role)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', g.role)
    principalId: g.who == 'collecte' ? idCollect.properties.principalId
      : g.who == 'transform' ? idTransform.properties.principalId
      : g.who == 'train' ? idTrain.properties.principalId
      : idApi.properties.principalId
    principalType: 'ServicePrincipal'
  }
}]

// La personne qui déploie : copie initiale de l'historique (pas de clé)
resource deployerBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, deployerPrincipalId, roles.blobContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobContributor)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

// ------------------------------------------------------------------ secrets
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-aurora-${suffix}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}

var pgName = 'psql-auroracast-${suffix}'
var pgUrl = format('postgresql://{0}:{1}@{2}.postgres.database.azure.com:5432/auroracast?sslmode=require',
  'auroracast', postgresPassword, pgName)
var newDbUrl = deployPostgres ? pgUrl : databaseUrl
var hasDb = deployPostgres || !empty(databaseUrl) || useExistingDbSecret

resource dbSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(newDbUrl)) {
  parent: kv
  name: 'database-url'
  properties: { value: newDbUrl, contentType: 'adresse PostgreSQL' }
}

resource kvTransform 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, idTransform.id, roles.kvSecretsUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.kvSecretsUser)
    principalId: idTransform.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource kvTrain 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, idTrain.id, roles.kvSecretsUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.kvSecretsUser)
    principalId: idTrain.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource kvDeployer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, deployerPrincipalId, roles.kvSecretsOfficer)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.kvSecretsOfficer)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

// ------------------------------------------------------------------ base PostgreSQL (option)
resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = if (deployPostgres) {
  name: pgName
  location: location
  tags: tags
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: 'auroracast'
    administratorLoginPassword: postgresPassword
    storage: { storageSizeGB: 32, autoGrow: 'Disabled' }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
  }
}

resource pgDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = if (deployPostgres) {
  parent: pg
  name: 'auroracast'
}

// 0.0.0.0 = services Azure seulement (les tâches n'ont pas d'IP fixe) ; TLS imposé par le serveur
resource pgFirewall 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = if (deployPostgres) {
  parent: pg
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

// ------------------------------------------------------------------ Container Apps
// Mode standard explicite : sans lui, Azure peut créer un environnement « Express », qui refuse
// les Container Apps Jobs (erreur ExpressEnvironmentResourceNotSupported). Propriété en préversion.
#disable-next-line BCP081
resource env 'Microsoft.App/managedEnvironments@2025-10-02-preview' = {
  name: 'cae-auroracast'
  location: location
  tags: tags
  properties: {
    #disable-next-line BCP037
    environmentMode: 'WorkloadProfiles'
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [ { name: 'Consumption', workloadProfileType: 'Consumption' } ]
  }
}

var identityIds = { collecte: idCollect.id, transform: idTransform.id, train: idTrain.id }
var clientIds = {
  collecte: idCollect.properties.clientId
  transform: idTransform.properties.clientId
  train: idTrain.properties.clientId
}

// tâche, identité, besoin de la base, horaire CRON (UTC), vCPU, mémoire
var jobs = [
  { name: 'collect', who: 'collecte', db: false, cron: '17 */3 * * *', cpu: '0.5', memory: '1Gi' }    // NOAA + MET Norway
  { name: 'ghcnh', who: 'collecte', db: false, cron: '37 5 * * *', cpu: '0.5', memory: '1Gi' }        // observations
  { name: 'transform', who: 'transform', db: true, cron: '47 */3 * * *', cpu: '1.0', memory: '2Gi' }  // 30 min après la collecte
  { name: 'train', who: 'train', db: true, cron: '13 10 * * *', cpu: '1.0', memory: '2Gi' }           // chaque matin
]

resource caJobs 'Microsoft.App/jobs@2024-03-01' = [for j in jobs: {
  name: 'caj-auroracast-${j.name}'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityIds[j.who]}': {} } }
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Schedule'      // se lance aussi à la main : az containerapp job start
      scheduleTriggerConfig: { cronExpression: j.cron, parallelism: 1, replicaCompletionCount: 1 }
      replicaTimeout: jobTimeout
      replicaRetryLimit: 0         // pas de relance : la suivante rattrape (collecte incrémentale)
      secrets: (j.db && hasDb) ? [ {
        name: 'database-url'
        keyVaultUrl: '${kv.properties.vaultUri}secrets/database-url'
        identity: identityIds[j.who]
      } ] : []
    }
    template: {
      containers: [ {
        name: j.name
        image: image
        command: [ 'python', '-m', 'auroracast.cloud.jobs', j.name ]
        resources: { cpu: json(j.cpu), memory: j.memory }
        env: concat([
          { name: 'AZURE_STORAGE_ACCOUNT', value: storage.name }
          { name: 'AZURE_CLIENT_ID', value: clientIds[j.who] }    // identité utilisée par DefaultAzureCredential
          { name: 'AURORACAST_CONTACT', value: contact }
        ], (j.db && hasDb) ? [ { name: 'DATABASE_URL', secretRef: 'database-url' } ] : [])
      } ]
    }
  }
  dependsOn: [ storageRoles, kvTransform, kvTrain, dbSecret ]
}]

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-auroracast-api'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${idApi.id}': {} } }
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }   // HTTPS seulement
    }
    template: {
      containers: [ {
        name: 'api'
        image: image
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: [
          { name: 'AZURE_STORAGE_ACCOUNT', value: storage.name }
          { name: 'AZURE_CLIENT_ID', value: idApi.properties.clientId }
        ]
        probes: [ { type: 'Liveness', httpGet: { path: '/api/health', port: 8000 }, periodSeconds: 60 } ]
      } ]
      scale: { minReplicas: 0, maxReplicas: 1 }      // aucune requête = aucun coût
    }
  }
  dependsOn: [ storageRoles ]
}

// GitHub peut changer l'image des apps et des tâches de ce groupe, rien d'autre (ni données, ni secrets)
resource githubDeploy 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, idGithub.id, roles.containerAppsContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.containerAppsContributor)
    principalId: idGithub.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ------------------------------------------------------------------ alertes
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-auroracast'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'auroracast'
    enabled: true
    emailReceivers: [ { name: 'responsable', emailAddress: alertEmail, useCommonAlertSchema: true } ]
  }
}

// 1. une tâche a fini en échec (ligne écrite par auroracast.cloud.jobs)
resource alertJobFailed 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alerte-tache-en-echec'
  location: location
  tags: tags
  properties: {
    displayName: 'AuroraCast : tâche en échec'
    severity: 2
    enabled: true
    scopes: [ logs.id ]
    skipQueryValidation: true       // la table n'existe qu'après la première exécution
    evaluationFrequency: 'PT1H'
    windowSize: 'PT1H'
    criteria: {
      allOf: [ {
        query: 'ContainerAppConsoleLogs_CL | where Log_s has "en échec" and Log_s has "tâche"'
        timeAggregation: 'Count'
        operator: 'GreaterThan'
        threshold: 0
        failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
      } ]
    }
    actions: { actionGroups: [ actionGroup.id ] }
  }
  dependsOn: [ env ]
}

// 2. aucune transformation réussie depuis 12 h (couvre aussi une tâche coupée au bout de 900 s)
resource alertStale 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alerte-prevision-perimee'
  location: location
  tags: tags
  properties: {
    displayName: 'AuroraCast : prévisions non mises à jour depuis 12 h'
    severity: 2
    enabled: true
    scopes: [ logs.id ]
    skipQueryValidation: true
    evaluationFrequency: 'PT3H'
    windowSize: 'PT12H'
    criteria: {
      allOf: [ {
        query: 'ContainerAppConsoleLogs_CL | where Log_s has "tâche transform : réussie"'
        timeAggregation: 'Count'
        operator: 'LessThan'
        threshold: 1
        failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
      } ]
    }
    actions: { actionGroups: [ actionGroup.id ] }
  }
  dependsOn: [ env ]
}

// Un budget prévient, il n'arrête rien. Indisponible pour Azure for Students : relevé mensuel du crédit.
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = if (deployBudget) {
  name: 'budget-auroracast'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStart }
    notifications: {
      reel50: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 50, thresholdType: 'Actual', contactEmails: [ alertEmail ] }
      reel80: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 80, thresholdType: 'Actual', contactEmails: [ alertEmail ] }
      reel100: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 100, thresholdType: 'Actual', contactEmails: [ alertEmail ] }
    }
  }
}

output storageAccount string = storage.name
output keyVault string = kv.name
output apiUrl string = 'https://${api.properties.configuration.ingress.fqdn}'
output githubClientId string = idGithub.properties.clientId
output tenantId string = subscription().tenantId
output subscriptionId string = subscription().subscriptionId
