-- AuroraCast : entrepôt de données PostgreSQL, modèle en étoile.
-- Rejouable sans risque (CREATE ... IF NOT EXISTS, CREATE OR REPLACE VIEW).
-- Temps : toujours en UTC (timestamptz), heure de début de la période.
--
--   Dimensions : dim_location (lieux cibles), dim_time (une ligne par heure), dim_source
--   Faits      : fact_kp, fact_kp_forecast, fact_solar_daily, fact_solar_wind     (globaux)
--                fact_weather_observed, fact_weather_daily, fact_weather_forecast,
--                fact_astronomy                                                  (par lieu)
--   Vue        : v_aurora_conditions (activité, ciel, obscurité, par lieu et par heure)
--   Suivi      : load_run (une ligne par chargement)

-- ---------------------------------------------------------------- dimensions
CREATE TABLE IF NOT EXISTS dim_location (
    location_id         smallint PRIMARY KEY,
    code                text NOT NULL UNIQUE,          -- tromso, kiruna, rovaniemi
    name                text NOT NULL,
    lat                 double precision NOT NULL,
    lon                 double precision NOT NULL,
    station_id          text,                          -- station météo GHCNh retenue
    station_name        text,
    station_distance_km double precision
);

CREATE TABLE IF NOT EXISTS dim_time (
    time_utc       timestamptz PRIMARY KEY,            -- heure pile UTC
    date_utc       date NOT NULL,
    year           smallint NOT NULL,
    month          smallint NOT NULL,
    day            smallint NOT NULL,
    hour           smallint NOT NULL,
    day_of_year    smallint NOT NULL,
    iso_week       smallint NOT NULL,
    aurora_season  boolean NOT NULL                    -- septembre à mars
);

CREATE TABLE IF NOT EXISTS dim_source (
    source_id  smallint PRIMARY KEY,
    code       text NOT NULL UNIQUE,
    name       text NOT NULL,
    technique  text NOT NULL,                          -- api, scraping, crawling, sql, fichier, calcul
    licence    text NOT NULL,
    url        text
);

-- ---------------------------------------------------------------- faits globaux
CREATE TABLE IF NOT EXISTS fact_kp (                   -- indice Kp par tranche de 3 h
    time_utc   timestamptz PRIMARY KEY REFERENCES dim_time,
    kp         real CHECK (kp BETWEEN 0 AND 9),
    ap         real,
    status     text NOT NULL,                          -- definitive, preliminary, noaa_estimated
    source_id  smallint NOT NULL REFERENCES dim_source
);

CREATE TABLE IF NOT EXISTS fact_kp_forecast (          -- prévisions Kp NOAA, toutes conservées
    issued_at    timestamptz NOT NULL,
    target_time  timestamptz NOT NULL REFERENCES dim_time,
    kp           real,
    noaa_scale   text,
    lead_hours   real,
    source_id    smallint NOT NULL REFERENCES dim_source,
    PRIMARY KEY (issued_at, target_time)
);

CREATE TABLE IF NOT EXISTS fact_solar_daily (          -- activité solaire du jour (00:00 UTC)
    time_utc        timestamptz PRIMARY KEY REFERENCES dim_time,
    ap_daily        real,
    sunspot_number  real,                              -- WDC-SILSO : CC BY-NC 4.0
    f107_obs        real,
    f107_adj        real,
    sunspot_status  text,
    source_id       smallint NOT NULL REFERENCES dim_source
);

CREATE TABLE IF NOT EXISTS fact_solar_wind (           -- vent solaire et champ interplanétaire, par heure
    time_utc      timestamptz PRIMARY KEY REFERENCES dim_time,
    bt            real,
    by_gsm        real,
    bz_gsm        real,
    bz_min        real,
    speed         real,
    speed_max     real,
    density       real,
    interpolated  boolean NOT NULL,
    source_id     smallint NOT NULL REFERENCES dim_source
);

-- ---------------------------------------------------------------- faits par lieu
CREATE TABLE IF NOT EXISTS fact_weather_observed (     -- météo observée par heure (GHCNh) : nuages + météo
    location_id        smallint NOT NULL REFERENCES dim_location,
    time_utc           timestamptz NOT NULL REFERENCES dim_time,
    oktas              real CHECK (oktas BETWEEN 0 AND 8),
    cloud_cover_pct    real,
    obscured           boolean,
    temp_c             real,
    dewpoint_c         real,
    rel_humidity_pct   real,
    pressure_hpa       real,
    wind_speed_ms      real,
    wind_dir_deg       real,
    visibility_km      real,
    visibility_capped  boolean,
    fog                boolean,
    mist               boolean,
    present_weather    text,
    n_obs              smallint NOT NULL,
    source_id          smallint NOT NULL REFERENCES dim_source,
    PRIMARY KEY (location_id, time_utc)
);

CREATE TABLE IF NOT EXISTS fact_weather_daily (        -- météo quotidienne GSOD (collectée en SQL)
    location_id       smallint NOT NULL REFERENCES dim_location,
    time_utc          timestamptz NOT NULL REFERENCES dim_time,   -- jour, à 00:00 UTC
    station_id        text,
    temp_c            real,
    temp_max_c        real,
    temp_min_c        real,
    dewpoint_c        real,
    rel_humidity_pct  real,
    visibility_km     real,
    wind_mean_ms      real,
    wind_max_ms       real,
    precip_mm         real,
    precip_flag       text,
    precip_suspect    boolean,
    fog               boolean,
    rain              boolean,
    snow              boolean,
    hail              boolean,
    thunder           boolean,
    source_id         smallint NOT NULL REFERENCES dim_source,
    PRIMARY KEY (location_id, time_utc)
);

CREATE TABLE IF NOT EXISTS fact_weather_forecast (     -- prévisions MET Norway, toutes conservées
    location_id             smallint NOT NULL REFERENCES dim_location,
    issued_at               timestamptz NOT NULL,
    target_time             timestamptz NOT NULL REFERENCES dim_time,
    lead_hours              real NOT NULL,
    step_hours              smallint NOT NULL,
    cloud_cover_pct         real,
    cloud_low_pct           real,
    cloud_medium_pct        real,
    cloud_high_pct          real,
    fog_pct                 real,
    temp_c                  real,
    dewpoint_c              real,
    rel_humidity_pct        real,
    pressure_hpa            real,
    wind_speed_ms           real,
    wind_dir_deg            real,
    wind_gust_ms            real,
    precip_mm               real,
    precip_probability_pct  real,
    symbol_code             text,
    source_id               smallint NOT NULL REFERENCES dim_source,
    PRIMARY KEY (location_id, issued_at, target_time)
);

CREATE TABLE IF NOT EXISTS fact_astronomy (            -- obscurité du ciel (calcul PyEphem)
    location_id     smallint NOT NULL REFERENCES dim_location,
    time_utc        timestamptz NOT NULL REFERENCES dim_time,
    sun_alt_deg     real NOT NULL,
    night_phase     text NOT NULL,                     -- day, civil, nautical, astronomical, night
    moon_alt_deg    real NOT NULL,
    moon_illum_pct  real NOT NULL,
    moon_up         boolean NOT NULL,
    polar_night     boolean NOT NULL,
    midnight_sun    boolean NOT NULL,
    source_id       smallint NOT NULL REFERENCES dim_source,
    PRIMARY KEY (location_id, time_utc)
);

CREATE INDEX IF NOT EXISTS fact_weather_forecast_target ON fact_weather_forecast (location_id, target_time);
CREATE INDEX IF NOT EXISTS fact_kp_forecast_target ON fact_kp_forecast (target_time);

-- ---------------------------------------------------------------- suivi des chargements
CREATE TABLE IF NOT EXISTS load_run (
    run_id        bigserial PRIMARY KEY,
    started_at    timestamptz NOT NULL,
    finished_at   timestamptz NOT NULL,
    rows_loaded   jsonb NOT NULL,                      -- nombre de lignes par table
    database_mb   real
);

-- ---------------------------------------------------------------- vue d'analyse
-- Une ligne par lieu et par heure : activité aurorale (Kp de la tranche de 3 h), ciel
-- observé, obscurité. Base des requêtes du directeur et, plus tard, de l'étiquette.
CREATE OR REPLACE VIEW v_aurora_conditions AS
SELECT l.code                                  AS location,
       a.time_utc,
       k.kp,
       k.kp >= 4                               AS kp_ge_4,
       w.oktas,
       w.fog,
       a.sun_alt_deg,
       a.night_phase,
       a.moon_alt_deg,
       a.moon_illum_pct,
       a.sun_alt_deg < -12                     AS dark_sky,
       w.oktas <= 2 AND NOT coalesce(w.fog, false) AS clear_sky
FROM fact_astronomy a
JOIN dim_location l USING (location_id)
LEFT JOIN fact_weather_observed w USING (location_id, time_utc)
LEFT JOIN fact_kp k                                    -- tranche de 3 h, calculée sur l'époque Unix :
       ON k.time_utc = to_timestamp(floor(extract(epoch FROM a.time_utc) / 10800) * 10800);
                                                       -- indépendant du fuseau de la session

-- ---------------------------------------------------------------- variables du modèle
-- Valeurs réelles des variables de la formule de régression, recalculées à chaque requête :
-- dernière valeur connue (avec son heure), moyenne, minimum et maximum depuis 2010.
--   y = b1 + b2 × kp + b3 × bz + b4 × vitesse + b5 × densite + b6 × f107
CREATE OR REPLACE VIEW v_formule_valeurs AS
WITH kp AS (SELECT time_utc, kp FROM fact_kp WHERE time_utc >= '2010-01-01' AND kp IS NOT NULL),
y AS (                                      -- Kp maximal des 24 h suivantes (8 tranches de 3 h)
    SELECT time_utc, max(kp) OVER (ORDER BY time_utc ROWS BETWEEN 1 FOLLOWING AND 8 FOLLOWING) AS y,
           count(*) OVER (ORDER BY time_utc ROWS BETWEEN 1 FOLLOWING AND 8 FOLLOWING) AS n
    FROM kp
),
sw AS (SELECT * FROM fact_solar_wind WHERE time_utc >= '2010-01-01'),
sd AS (SELECT * FROM fact_solar_daily WHERE time_utc >= '2010-01-01'),
series(ordre, variable, definition, table_source, time_utc, valeur) AS (
    SELECT 1, 'y', 'Kp maximal des 24 h suivantes', 'kp_3h', time_utc, y FROM y WHERE n = 8
    UNION ALL SELECT 2, 'kp', 'Kp actuel', 'kp_3h', time_utc, kp FROM kp
    UNION ALL SELECT 3, 'bz', 'Bz du vent solaire (nT)', 'solar_wind_hourly', time_utc, bz_gsm FROM sw WHERE bz_gsm IS NOT NULL
    UNION ALL SELECT 4, 'vitesse', 'Vitesse du vent solaire (km/s)', 'solar_wind_hourly', time_utc, speed FROM sw WHERE speed IS NOT NULL
    UNION ALL SELECT 5, 'densite', 'Densité du vent solaire (p/cm³)', 'solar_wind_hourly', time_utc, density FROM sw WHERE density IS NOT NULL
    UNION ALL SELECT 6, 'f107', 'Flux solaire du jour (sfu)', 'solar_daily', time_utc, f107_obs FROM sd WHERE f107_obs IS NOT NULL
)
SELECT variable, definition, table_source,
       (array_agg(valeur ORDER BY time_utc DESC))[1]::numeric(8, 2) AS derniere_valeur,
       max(time_utc)                                                 AS derniere_heure_utc,
       avg(valeur)::numeric(8, 2)                                    AS moyenne,
       min(valeur)::numeric(8, 2)                                    AS minimum,
       max(valeur)::numeric(8, 2)                                    AS maximum,
       count(*)                                                      AS nb_valeurs
FROM series
GROUP BY ordre, variable, definition, table_source
ORDER BY ordre;

-- ---------------------------------------------------------------- table d'entraînement
-- Une ligne par tranche de 3 h (début de tranche = time_utc). La prévision est émise à la
-- FIN de la tranche (issued_at = time_utc + 3 h) : les variables n'utilisent que des mesures
-- antérieures à cet instant ; la cible porte sur les 24 h suivantes (8 tranches).
--   cibles      kp_max_next_6h/24h/48h et aurora_kp4_6h/24h/48h (= Kp >= 4 au moins une fois
--               dans les 6, 24 ou 48 h suivantes : 2, 8 ou 16 tranches)
--   Kp          kp, kp_mean_24h, kp_max_24h, kp_max_24h_27d (rotation solaire : 27 jours avant)
--   vent        bz_mean_3h, bz_min_6h, bt_mean_3h, speed_mean_6h, density_mean_6h
--   Soleil      f107 (valeur de la veille ou d'avant, 4 jours au plus : mesurée à 20 h UTC)
--   saison      doy_sin, doy_cos
DROP VIEW IF EXISTS v_training_kp;
CREATE VIEW v_training_kp AS
WITH kp AS (
    SELECT time_utc, kp,
           avg(kp) OVER w24  AS kp_mean_24h,
           max(kp) OVER w24  AS kp_max_24h,
           max(kp) OVER n6    AS kp_max_next_6h,   count(kp) OVER n6  AS n6,
           max(kp) OVER n24   AS kp_max_next_24h,  count(kp) OVER n24 AS n24,
           max(kp) OVER n48   AS kp_max_next_48h,  count(kp) OVER n48 AS n48
    FROM fact_kp
    WHERE time_utc >= '2009-11-01'
    WINDOW w24 AS (ORDER BY time_utc ROWS BETWEEN 7 PRECEDING AND CURRENT ROW),
           n6  AS (ORDER BY time_utc ROWS BETWEEN 1 FOLLOWING AND 2 FOLLOWING),
           n24 AS (ORDER BY time_utc ROWS BETWEEN 1 FOLLOWING AND 8 FOLLOWING),
           n48 AS (ORDER BY time_utc ROWS BETWEEN 1 FOLLOWING AND 16 FOLLOWING)
),
sw AS (                                 -- vent solaire horaire, fenêtres glissantes
    SELECT time_utc,
           avg(bz_gsm)  OVER (ORDER BY time_utc RANGE BETWEEN INTERVAL '2 hours' PRECEDING AND CURRENT ROW) AS bz_mean_3h,
           min(bz_gsm)  OVER (ORDER BY time_utc RANGE BETWEEN INTERVAL '5 hours' PRECEDING AND CURRENT ROW) AS bz_min_6h,
           avg(bt)      OVER (ORDER BY time_utc RANGE BETWEEN INTERVAL '2 hours' PRECEDING AND CURRENT ROW) AS bt_mean_3h,
           avg(speed)   OVER (ORDER BY time_utc RANGE BETWEEN INTERVAL '5 hours' PRECEDING AND CURRENT ROW) AS speed_mean_6h,
           avg(density) OVER (ORDER BY time_utc RANGE BETWEEN INTERVAL '5 hours' PRECEDING AND CURRENT ROW) AS density_mean_6h
    FROM fact_solar_wind
    WHERE time_utc >= '2009-12-31'
)
SELECT k.time_utc,
       k.time_utc + INTERVAL '3 hours'                     AS issued_at,
       CASE WHEN k.n6  = 2  THEN k.kp_max_next_6h END          AS kp_max_next_6h,
       CASE WHEN k.n24 = 8  THEN k.kp_max_next_24h END         AS kp_max_next_24h,
       CASE WHEN k.n48 = 16 THEN k.kp_max_next_48h END         AS kp_max_next_48h,
       CASE WHEN k.n6  = 2  THEN k.kp_max_next_6h  >= 4 END    AS aurora_kp4_6h,
       CASE WHEN k.n24 = 8  THEN k.kp_max_next_24h >= 4 END    AS aurora_kp4_24h,
       CASE WHEN k.n48 = 16 THEN k.kp_max_next_48h >= 4 END    AS aurora_kp4_48h,
       k.kp, k.kp_mean_24h, k.kp_max_24h,
       k27.kp_max_24h                                      AS kp_max_24h_27d,
       s.bz_mean_3h, s.bz_min_6h, s.bt_mean_3h, s.speed_mean_6h, s.density_mean_6h,
       f.f107_obs                                          AS f107,
       sin(2 * pi() * extract(doy FROM k.time_utc AT TIME ZONE 'UTC') / 365.25) AS doy_sin,
       cos(2 * pi() * extract(doy FROM k.time_utc AT TIME ZONE 'UTC') / 365.25) AS doy_cos
FROM kp k
LEFT JOIN kp k27 ON k27.time_utc = k.time_utc - INTERVAL '27 days'
LEFT JOIN sw s   ON s.time_utc = k.time_utc + INTERVAL '2 hours'      -- dernière heure de la tranche
LEFT JOIN LATERAL (
    SELECT f107_obs FROM fact_solar_daily d
    WHERE d.time_utc <= k.time_utc - INTERVAL '1 day' AND d.time_utc > k.time_utc - INTERVAL '5 days'
      AND d.f107_obs IS NOT NULL
    ORDER BY d.time_utc DESC LIMIT 1
) f ON true
WHERE k.time_utc >= '2010-01-01';
-- les dernières tranches (cible encore inconnue) sont gardées pour la prévision : cible NULL
