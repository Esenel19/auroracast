# DAGs Airflow

Prochaine étape (C1.1.3, C1.3.3, C4.2.2) : un DAG par cadence qui appelle
`auroracast.collect.noaa.run()` :

- `noaa_horaire` : toutes les heures, toutes les sources NOAA
- `meteo_quotidien` : une fois par jour, Open-Meteo
- `backfill` : déclenché à la main, historique GFZ / OMNIWeb
