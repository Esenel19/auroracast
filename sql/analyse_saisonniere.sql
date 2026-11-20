-- AuroraCast · analyse saisonnière : quels mois et quels lieux pour voir une aurore ?
-- Requêtes lues par auroracast.app.analysis (une requête par bloc « -- name: »), exécutables
-- telles quelles dans psql en remplaçant les paramètres %(debut)s et %(fin)s par des dates.
--
-- Définitions (voir docs/plan_analyse.md) :
--   heure noire      Soleil à -12° ou plus bas (fin du crépuscule nautique)
--   heure dégagée    nuages <= 2 octas et pas de brouillard (observation GHCNh de l'heure)
--   heure favorable  heure noire ET dégagée ET Kp >= 4 sur la tranche de 3 h
--   nuit favorable   nuit (de midi à midi UTC) comptant au moins une heure favorable

-- name: mois_lieu
-- Une ligne par lieu et par mois calendaire, sur les années complètes demandées.
SELECT location,
       extract(month FROM time_utc)::int                                          AS mois,
       count(DISTINCT extract(year FROM time_utc))::int                           AS n_annees,
       count(*) FILTER (WHERE dark_sky)                                           AS heures_noires,
       count(*) FILTER (WHERE dark_sky AND kp IS NOT NULL)                        AS noires_kp_connu,
       count(*) FILTER (WHERE dark_sky AND kp_ge_4)                               AS noires_kp4,
       count(*) FILTER (WHERE dark_sky AND oktas IS NOT NULL)                     AS noires_ciel_connu,
       count(*) FILTER (WHERE dark_sky AND clear_sky)                             AS noires_degagees,
       count(*) FILTER (WHERE dark_sky AND clear_sky AND kp_ge_4)                 AS heures_favorables,
       count(DISTINCT (time_utc - interval '12 hours')::date)
             FILTER (WHERE dark_sky AND clear_sky AND kp_ge_4)                    AS nuits_favorables,
       count(*) FILTER (WHERE dark_sky AND clear_sky AND kp_ge_4
                          AND (moon_alt_deg < 0 OR moon_illum_pct < 50))          AS favorables_sans_lune
FROM v_aurora_conditions
WHERE time_utc >= %(debut)s AND time_utc < %(fin)s
GROUP BY 1, 2
ORDER BY 1, 2;

-- name: kp_mois
-- Fréquence des belles aurores (Kp >= 4) par mois calendaire, toutes années (GFZ depuis 1932).
SELECT extract(month FROM time_utc)::int AS mois,
       count(*)                          AS tranches,
       count(*) FILTER (WHERE kp >= 4)   AS tranches_kp4
FROM fact_kp
WHERE kp IS NOT NULL AND time_utc < %(fin)s
GROUP BY 1
ORDER BY 1;

-- name: kp_annee
-- Fréquence des belles aurores par année : le cycle solaire de 11 ans.
SELECT extract(year FROM time_utc)::int AS annee,
       count(*)                         AS tranches,
       count(*) FILTER (WHERE kp >= 4)  AS tranches_kp4
FROM fact_kp
WHERE kp IS NOT NULL AND time_utc < %(fin)s
GROUP BY 1
ORDER BY 1;

-- name: kp_jour
-- Kp maximal de chaque jour, pour le test d'hypothèse sur les saisons (Kruskal-Wallis).
SELECT extract(month FROM time_utc)::int AS mois,
       max(kp)                          AS kp_max
FROM fact_kp
WHERE kp IS NOT NULL AND time_utc < %(fin)s
GROUP BY time_utc::date, 1;
