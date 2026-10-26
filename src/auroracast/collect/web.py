"""Session HTTP « polie » partagée par tous les collecteurs.

Avant le premier téléchargement sur un site, la session lit son robots.txt
(norme RFC 9309) et :
  - refuse de télécharger une URL interdite au robot AuroraCast (RobotsDisallowed) ;
  - respecte le Crawl-delay demandé, sinon une pause minimale entre deux requêtes ;
  - expose le résultat (robots_info) pour qu'il soit noté dans le manifeste.

Règles RFC 9309 appliquées : robots.txt absent ou en erreur 4xx = tout est permis ;
robots.txt injoignable (erreur 5xx ou réseau) = tout est interdit.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Self
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

ROBOT_NAME = "AuroraCast"
MIN_DELAY_S = 1.0  # pause minimale entre deux requêtes vers le même site


class RobotsDisallowed(RuntimeError):
    """L'URL est interdite par le robots.txt du site."""


class PoliteSession:
    def __init__(
        self,
        user_agent: str,
        session: requests.Session | None = None,
        min_delay: float = MIN_DELAY_S,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.min_delay = min_delay
        self._sleep, self._clock = sleep, clock
        self._robots: dict[str, tuple[dict, RobotFileParser | None]] = {}
        self._last_request: dict[str, float] = {}

    @staticmethod
    def _origin(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def _load_robots(self, origin: str) -> tuple[dict, RobotFileParser | None]:
        if origin in self._robots:
            return self._robots[origin]
        robots_url = origin + "/robots.txt"
        info: dict = {"robots_url": robots_url}
        parser: RobotFileParser | None = None
        try:
            resp = self.session.get(robots_url, timeout=15)
            if resp.status_code == 200:
                parser = RobotFileParser()
                parser.parse(resp.text.splitlines())
                info["status"] = "lu"
            elif 400 <= resp.status_code < 500:
                info["status"] = f"absent (HTTP {resp.status_code})"
            else:
                info["status"] = f"injoignable (HTTP {resp.status_code})"
        except requests.RequestException as exc:
            info["status"] = f"injoignable ({type(exc).__name__})"
        self._last_request[origin] = self._clock()
        self._robots[origin] = (info, parser)
        return info, parser

    def robots_info(self, url: str) -> dict:
        """Résultat du robots.txt pour cette URL : statut, autorisation, Crawl-delay."""
        info, parser = self._load_robots(self._origin(url))
        if parser is not None:
            allowed = parser.can_fetch(ROBOT_NAME, url)
            delay = parser.crawl_delay(ROBOT_NAME)
        else:
            allowed = not info["status"].startswith("injoignable")
            delay = None
        return {**info, "allowed": allowed, "crawl_delay": float(delay) if delay else None}

    def get(self, url: str, timeout: float = 30) -> requests.Response:
        robots = self.robots_info(url)
        if not robots["allowed"]:
            raise RobotsDisallowed(f"interdit par {robots['robots_url']} ({robots['status']})")
        origin = self._origin(url)
        delay = max(robots["crawl_delay"] or 0.0, self.min_delay)
        waited = self._clock() - self._last_request.get(origin, float("-inf"))
        if waited < delay:
            self._sleep(delay - waited)
        try:
            return self.session.get(url, timeout=timeout)
        finally:
            self._last_request[origin] = self._clock()

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
