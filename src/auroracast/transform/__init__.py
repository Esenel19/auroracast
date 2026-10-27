"""Transformation des données brutes (branche raw-data) en tables propres.

Règles communes à toutes les tables :
  - temps en UTC, horodatage du DÉBUT de la période (heure ou tranche de 3 h) ;
  - valeur absente = NaN explicite (les codes -1, 999.9, 9999. … sont convertis) ;
  - valeur hors des bornes physiques = NaN, et comptée dans le rapport de qualité ;
  - une ligne par période (doublons retirés, comptés dans le rapport) ;
  - colonne « source » : d'où vient chaque ligne ;
  - le Kp n'est jamais interpolé ; le vent solaire peut l'être sur 2 h au plus,
    avec un drapeau « interpolated ».
"""
