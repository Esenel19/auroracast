-- AuroraCast : requêtes d'exemple sur l'entrepôt (questions du directeur et contrôles).
-- Psql : \i sql/requetes_exemples.sql   (ou une requête à la fois dans VS Code)

SET TIME ZONE 'UTC';   -- afficher les heures en UTC, comme elles sont stockées

-- 1. Combien d'heures « belle aurore possible » par lieu et par saison ?
--    Kp >= 4, ciel noir (Soleil sous -12°), ciel dégagé (0 à 2 octas, sans brouillard).
--    Attention : Tromsø n'observe les nuages que toutes les 3 h ; on compte donc les heures
--    OBSERVÉES et on compare les lieux par la part de ciel dégagé, pas par les totaux.
SELECT location,
       CASE WHEN extract(month FROM time_utc) >= 9
            THEN extract(year FROM time_utc)::int || '-' || (extract(year FROM time_utc)::int + 1)
            ELSE (extract(year FROM time_utc)::int - 1) || '-' || extract(year FROM time_utc)::int
       END                                            AS saison,
       count(*) FILTER (WHERE dark_sky AND oktas IS NOT NULL)                  AS heures_noires_observees,
       round(100.0 * count(*) FILTER (WHERE dark_sky AND clear_sky)
             / nullif(count(*) FILTER (WHERE dark_sky AND oktas IS NOT NULL), 0), 1) AS pct_degage,
       count(*) FILTER (WHERE kp_ge_4 AND dark_sky AND clear_sky)              AS heures_belle_aurore
FROM v_aurora_conditions
WHERE time_utc >= '2015-09-01' AND time_utc < '2026-04-01'
  AND extract(month FROM time_utc) IN (9, 10, 11, 12, 1, 2, 3)
GROUP BY 1, 2
ORDER BY 2, 1;

-- 2. Les 10 journées les plus actives depuis 2010 : Kp maximal et Bz le plus négatif.
SELECT k.time_utc::date              AS jour,
       max(k.kp)                     AS kp_max,
       min(w.bz_gsm)                 AS bz_min_nt,       -- moyenne horaire la plus négative
       max(w.speed)                  AS vitesse_max_km_s
FROM fact_kp k
LEFT JOIN fact_solar_wind w ON w.time_utc::date = k.time_utc::date
WHERE k.time_utc >= '2010-01-01'
GROUP BY 1
ORDER BY kp_max DESC, bz_min_nt
LIMIT 10;

-- 3. Le ciel était-il dégagé à Tromsø pendant la tempête du 10 au 11 mai 2024 ?
SELECT time_utc, kp, oktas, fog, round(sun_alt_deg::numeric, 1) AS soleil_deg, night_phase
FROM v_aurora_conditions
WHERE location = 'tromso' AND time_utc BETWEEN '2024-05-10 18:00+00' AND '2024-05-11 06:00+00'
ORDER BY time_utc;

-- 4. Dernière prévision MET Norway : heures de ciel noir et dégagé prévues dans les 24 h, par lieu.
WITH derniere AS (
    SELECT location_id, max(issued_at) AS issued_at FROM fact_weather_forecast GROUP BY location_id
)
SELECT l.code, f.issued_at,
       count(*) FILTER (WHERE f.cloud_cover_pct < 30 AND coalesce(f.fog_pct, 0) < 10
                          AND a.sun_alt_deg < -12)   AS heures_noires_degagees_24h
FROM fact_weather_forecast f
JOIN derniere d USING (location_id, issued_at)
JOIN dim_location l USING (location_id)
JOIN fact_astronomy a ON a.location_id = f.location_id AND a.time_utc = f.target_time
WHERE f.target_time < f.issued_at + interval '24 hours'
GROUP BY 1, 2
ORDER BY 1;

-- 5. Contrôle : historique des chargements de l'entrepôt.
SELECT run_id, started_at, finished_at - started_at AS duree, database_mb,
       rows_loaded ->> 'fact_weather_observed' AS lignes_meteo
FROM load_run ORDER BY run_id DESC LIMIT 5;
