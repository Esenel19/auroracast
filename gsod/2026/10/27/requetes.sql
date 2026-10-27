-- 1. Stations
-- Stations GSOD les plus proches d'un lieu (distance géodésique calculée par BigQuery)
SELECT
  usaf,
  wban,
  name,
  country,
  lat,
  lon,
  `begin` AS first_day,
  `end`   AS last_day,
  ROUND(ST_DISTANCE(ST_GEOGPOINT(lon, lat), ST_GEOGPOINT(@lon, @lat)) / 1000, 1) AS distance_km
FROM `bigquery-public-data.noaa_gsod.stations`
WHERE lat IS NOT NULL
  AND lon IS NOT NULL
  AND ST_DWITHIN(ST_GEOGPOINT(lon, lat), ST_GEOGPOINT(@lon, @lat), @max_m)
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
