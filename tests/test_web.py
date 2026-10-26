import pytest
import requests

from auroracast.collect.web import PoliteSession, RobotsDisallowed


class Resp:
    def __init__(self, status=200, text=""):
        self.status_code, self.text = status, text


class FakeSession:
    """Remplace requests.Session : renvoie des réponses préparées par URL."""

    def __init__(self, responses):
        self.responses, self.calls, self.headers = responses, [], {}

    def get(self, url, timeout=None):
        self.calls.append(url)
        r = self.responses.get(url, Resp(200, "ok"))
        if isinstance(r, Exception):
            raise r
        return r

    def close(self):
        pass


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


def polite(responses, min_delay=1.0):
    clock = Clock()
    fake = FakeSession(responses)
    s = PoliteSession("AuroraCast/test", session=fake, min_delay=min_delay,
                      sleep=clock.sleep, clock=clock)
    return s, fake, clock


ROBOTS = "https://ex.org/robots.txt"


def test_absent_robots_allows_and_is_read_once():
    s, fake, _ = polite({ROBOTS: Resp(404)})
    s.get("https://ex.org/a.json")
    s.get("https://ex.org/b.json")
    assert fake.calls.count(ROBOTS) == 1
    assert s.robots_info("https://ex.org/a.json")["status"] == "absent (HTTP 404)"


def test_disallowed_path_is_refused_without_download():
    s, fake, _ = polite({ROBOTS: Resp(200, "User-agent: *\nDisallow: /private/\n")})
    with pytest.raises(RobotsDisallowed):
        s.get("https://ex.org/private/x.json")
    assert "https://ex.org/private/x.json" not in fake.calls
    assert s.robots_info("https://ex.org/public.json")["allowed"] is True


def test_rule_for_our_robot_name_applies():
    s, _, _ = polite({ROBOTS: Resp(200, "User-agent: AuroraCast\nDisallow: /\n")})
    assert s.robots_info("https://ex.org/a.json")["allowed"] is False


def test_unreachable_robots_means_disallow():
    s, _, _ = polite({ROBOTS: requests.ConnectionError("down")})
    info = s.robots_info("https://ex.org/a.json")
    assert info["allowed"] is False and info["status"].startswith("injoignable")


def test_crawl_delay_is_respected_between_requests():
    s, _, clock = polite({ROBOTS: Resp(200, "User-agent: *\nCrawl-delay: 5\n")})
    s.get("https://ex.org/a.json")
    s.get("https://ex.org/b.json")
    assert s.robots_info("https://ex.org/a.json")["crawl_delay"] == 5.0
    assert clock.slept == [5.0, 5.0]  # après le robots.txt, puis entre les deux fichiers
