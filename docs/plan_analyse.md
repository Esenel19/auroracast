# Plan d'analyse : quand et où voir les aurores (bloc 2)

Compétences : C2.1.1 (besoin), C2.1.2 (axes et métriques), C2.1.3 (requêtes et tableau de bord),
C2.1.4 (tests d'hypothèses), C2.2.1 (visualisation), C2.2.2 (recommandations).

Livrables : requêtes `sql/analyse_saisonniere.sql`, calcul `src/auroracast/app/analysis.py`,
page **« Quand et où voir les aurores »** (`web/analyse.html`, servie par l'API Azure et GitHub
Pages), recalculée à chaque transformation.

## 1. Besoin métier (C2.1.1)

Le directeur d'une agence de voyages vend des séjours « aurores boréales » à Tromsø (Norvège),
Kiruna (Suède) et Rovaniemi (Finlande). Ses décisions et ses questions :

| Décision | Question posée aux données | Horizon |
| --- | --- | --- |
| Construire le catalogue de l'année | Quels mois et quels lieux donnent le plus de chances de voir une aurore ? | saison |
| Fixer la durée des séjours | Combien de nuits faut-il sur place pour avoir de bonnes chances ? | saison |
| Choisir la destination à mettre en avant | Le ciel est-il plus souvent dégagé dans un lieu que dans les autres ? | saison |
| Anticiper les années à venir | L'activité solaire va-t-elle monter ou baisser ? | plusieurs années |
| Organiser les sorties de la semaine | Cette nuit, l'aurore sera-t-elle visible ? | 6 à 48 h (application de prévision) |

Les quatre premières lignes relèvent de cette analyse ; la dernière est servie par les modèles
(bloc 5) et l'application de prévision.

## 2. Traduction en problème numérique, axes et métriques (C2.1.2)

Une aurore est visible quand **trois conditions sont réunies en même temps** : activité
géomagnétique, ciel noir et ciel dégagé. On mesure donc la fréquence de leur conjonction.

| Métrique | Définition | Source (entrepôt) |
| --- | --- | --- |
| Heure noire | Soleil à −12° ou plus bas (fin du crépuscule nautique) | `fact_astronomy` |
| Heure dégagée | nuages ≤ 2 octas et pas de brouillard | `fact_weather_observed` (GHCNh) |
| Belle aurore | Kp ≥ 4 sur la tranche de 3 h | `fact_kp` (GFZ, NOAA) |
| **Heure favorable** | heure noire **et** dégagée **et** Kp ≥ 4 | vue `v_aurora_conditions` |
| **Nuit favorable** | nuit (midi à midi UTC) avec au moins une heure favorable | idem |
| Part de Kp ≥ 4 | heures noires avec Kp ≥ 4 / heures noires au Kp connu | idem |
| Part de ciel dégagé | heures noires dégagées / heures noires au ciel observé | idem |
| Couverture du ciel | heures noires au ciel observé / heures noires (qualité de la mesure) | idem |
| Gêne de la Lune | heures favorables avec Lune couchée ou éclairée à moins de 50 % | `fact_astronomy` |

Axes d'analyse :

- **temps** : mois calendaire (saison), année (cycle solaire de 11 ans) ;
- **lieu** : Tromsø, Kiruna, Rovaniemi ;
- **condition** : activité, obscurité, ciel, Lune.

Période : années complètes 2010 → année précédente, pour les métriques par lieu (les
observations de nuages commencent en 2010) ; 1932 → année précédente pour le Kp seul (GFZ).
Les valeurs sont des **moyennes par an** : « 6,1 nuits favorables en janvier à Kiruna »
veut dire 6,1 nuits par an en moyenne sur les années observées.

Limite connue : une heure sans observation de nuages ne peut pas être favorable. Les nuits
favorables sont donc un minimum ; la colonne « ciel observé » permet de comparer les lieux à
qualité de mesure égale.

## 3. Requêtes et tableau de bord (C2.1.3)

`sql/analyse_saisonniere.sql`, une requête nommée par question, exécutable dans psql :

| Requête | Question |
| --- | --- |
| `mois_lieu` | indicateurs par lieu et par mois (agrégats conditionnels `count(*) FILTER`, nuits distinctes) |
| `kp_mois` | fréquence des belles aurores par mois, depuis 1932 |
| `kp_annee` | fréquence par année : le cycle solaire |
| `kp_jour` | Kp maximal de chaque jour, pour le test de Kruskal-Wallis |

Le module `auroracast.app.analysis` exécute ces requêtes, calcule les indicateurs, les intervalles
de confiance (Wilson, 95 %), les tests et les recommandations, et écrit `analyse.json`. Sur Azure,
la tâche `caj-auroracast-transform` le recalcule toutes les 3 h ; la page est servie par l'API
(`/analyse.html`). Un test d'intégration exécute les quatre requêtes sur une vraie base PostgreSQL
dans la CI.

## 4. Tests d'hypothèses (C2.1.4)

Seuil de décision : 5 %. Taille d'effet : V de Cramér.

| Hypothèse | Test | Pourquoi ce test |
| --- | --- | --- |
| **H1** : les belles aurores sont plus fréquentes aux équinoxes (mars-avril, septembre-octobre) qu'aux solstices (juin-juillet, décembre-janvier), effet Russell-McPherron | khi² d'indépendance sur le tableau 2 × 2 (période × Kp ≥ 4), tranches de 3 h depuis 1932 ; confirmé par Kruskal-Wallis sur le Kp maximal du jour entre les 4 saisons | Kp ≥ 4 est un événement binaire (khi²) ; le Kp maximal n'est pas normal, d'où un test non paramétrique entre plus de deux groupes |
| **H2** : la part d'heures noires au ciel dégagé (septembre à mars) n'est pas la même dans les 3 lieux | khi² d'indépendance sur le tableau 3 × 2 (lieu × dégagé) | comparaison de proportions entre trois groupes |

Précaution : les tranches de 3 h successives ne sont pas indépendantes (un orage dure souvent
plusieurs tranches). Avec des dizaines de milliers d'observations, la p-valeur est presque
toujours très petite : la décision s'appuie donc aussi sur la **taille de l'effet** (rapport des
fréquences, V de Cramér) et sur Kruskal-Wallis, calculé au jour.

## 5. Visualisation (C2.2.1)

| Question | Graphique | Choix |
| --- | --- | --- |
| Meilleurs mois et lieux | carte de chaleur mois × lieu des nuits favorables | une seule teinte (bleu) du clair au foncé ; valeur écrite dans chaque case (la couleur n'est jamais seule) ; case grise et tiret pour l'absence de nuit noire |
| Saisons de l'activité | courbe mensuelle de Kp ≥ 4 depuis 1932, intervalle à 95 %, colonnes des équinoxes | une série, pas de légende ; l'incertitude est montrée |
| Ciel selon le lieu | 3 courbes mensuelles | 3 couleurs d'une palette validée pour les daltonismes (script de contrôle : écarts ΔE ≥ 9 en mode clair et sombre), légende **et** étiquette en bout de courbe |
| Cycle solaire | courbe annuelle depuis 1932 et moyenne en pointillé | un seul axe |
| Tests | chiffres et verdict en clair | p-valeur, taille d'effet, conclusion |

Accessibilité : chaque graphique a son tableau de valeurs ou des valeurs écrites ; info-bulle au
survol et au clavier (cases de la carte de chaleur) ; mode sombre choisi (rampe inversée, couleurs
revalidées sur le fond sombre) ; lisible sur téléphone (défilement horizontal des graphiques).

## 6. Recommandations (C2.2.2)

Calculées sur les données à chaque mise à jour, et affichées en bas de la page :

1. les trois meilleures combinaisons lieu × mois (nuits favorables par an) ;
2. le lieu qui offre le plus de nuits favorables sur une saison (septembre à mars) ;
3. les mois à ne pas programmer (pas de nuit noire) ;
4. la phase du cycle solaire (années récentes au-dessus de la moyenne : maximum solaire) ;
5. la durée de séjour : le ciel décide plus que le Soleil, prévoir 3 à 4 nuits au moins.
