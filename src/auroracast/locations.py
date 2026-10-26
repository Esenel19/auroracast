"""Lieux cibles d'AuroraCast (zone aurorale), partagés par tous les modules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Location:
    name: str
    lat: float
    lon: float

    @classmethod
    def parse(cls, text: str) -> Location:
        """Lit « nom:latitude:longitude »."""
        name, lat, lon = text.rsplit(":", 2)
        return cls(name.strip(), float(lat), float(lon))


# Lieux cibles provisoires, à confirmer avec le directeur. Kiruna remplace Abisko :
# aucune station ne relève les nuages à moins de 30 km d'Abisko (recherche du 26/10/2026).
LOCATIONS = (
    Location("tromso", 69.6496, 18.9560),
    Location("kiruna", 67.8558, 20.2253),
    Location("rovaniemi", 66.5039, 25.7294),
)
