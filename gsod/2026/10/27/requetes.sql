-- 1. Stations
-- Stations GSOD les plus proches d'un lieu (distance géodésique calculée par BigQuery).
-- La colonne `end` de la table stations n'est plus mise à jour (figée au 20/09/2021) :
-- l'activité réelle est lue dans les relevés de la dernière année (jointure externe).
WITH recent AS (
  SELECT stn, wban,
         MAX(DATE(CAST(year AS INT64), CAST(mo AS INT64), CAST(da AS INT64))) AS last_obs,
         COUNT(*) AS days_last_year
  FROM `bigquery-public-data.noaa_gsod.gsod*`
  WHERE _TABLE_SUFFIX = @last_year
  GROUP BY stn, wban
)
SELECT
  s.usaf,
  s.wban,
  s.name,
  s.country,
  s.lat,
  s.lon,
  s.`begin` AS first_day,
  s.`end`   AS last_day,
  r.last_obs,
  IFNULL(r.days_last_year, 0) AS days_last_year,
  ROUND(ST_DISTANCE(ST_GEOGPOINT(s.lon, s.lat), ST_GEOGPOINT(@lon, @lat)) / 1000, 1) AS distance_km
FROM `bigquery-public-data.noaa_gsod.stations` AS s
LEFT JOIN recent AS r
  ON r.stn = s.usaf AND r.wban = s.wban
WHERE s.lat IS NOT NULL
  AND s.lon IS NOT NULL
  AND ST_DWITHIN(ST_GEOGPOINT(s.lon, s.lat), ST_GEOGPOINT(@lon, @lat), @max_m)
ORDER BY distance_km
LIMIT 10

-- 2. Relevés quotidiens
-- Relevés quotidiens des stations retenues, plusieurs années en une requête
SELECT
  g.stn,
  g.wban,
  s.name AS station_name,
  DATE(CAST(g.year AS INT64), CAST(g.mo AS INT64), CAST(g.da AS INT64)) AS day,
  g.temp, g.count_temp, g.max, g.min, g.dewp,
  g.visib, g.count_visib,
  g.wdsp, g.mxpsd, g.gust,
  g.prcp, g.flag_prcp, g.sndp,
  g.fog, g.rain_drizzle, g.snow_ice_pellets, g.hail, g.thunder
FROM `bigquery-public-data.noaa_gsod.gsod*` AS g
JOIN `bigquery-public-data.noaa_gsod.stations` AS s
  ON s.usaf = g.stn AND s.wban = g.wban
WHERE _TABLE_SUFFIX BETWEEN @first_year AND @last_year
  AND g.stn IN UNNEST(@stations)
ORDER BY g.stn, day
