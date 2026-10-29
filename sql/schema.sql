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
