"""Haalt fietsactiviteiten en vermogensdata op uit de Strava API.

Benodigde omgevingsvariabelen (in GitHub: Settings > Secrets and variables > Actions):
  STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_REFRESH_TOKEN

Schrijft:
  data/activities.json   compacte lijst van ritten (geen kaarten of locaties)
  data/power/<id>.json   beste vermogens per duur voor ritten met een vermogensmeter
  data/athlete.json      gewicht en FTP zoals Strava ze kent

Strava staat 100 verzoeken per 15 minuten en 1000 per dag toe. Het script haalt
per run hooguit MAX_STREAMS vermogensreeksen op; oudere ritten komen er bij de
volgende runs vanzelf bij.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
POWER = DATA / "power"
CONFIG = json.loads((ROOT / "config.json").read_text())

API = "https://www.strava.com/api/v3"
MAX_STREAMS = int(os.environ.get("MAX_STREAMS", "40"))
RIDE_TYPES = {"Ride", "VirtualRide", "GravelRide", "MountainBikeRide", "EBikeRide"}
DURATIONS = [5, 15, 30, 60, 300, 1200, 3600]

# Velden die we bewaren. Bewust zonder kaart, polyline of start/eind-coördinaten.
KEEP = [
    "id", "name", "sport_type", "start_date_local", "distance", "moving_time",
    "elapsed_time", "total_elevation_gain", "kilojoules", "average_watts",
    "weighted_average_watts", "max_watts", "device_watts", "average_heartrate",
    "max_heartrate", "average_cadence", "trainer", "suffer_score",
]


def token():
    r = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": os.environ["STRAVA_CLIENT_ID"],
            "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
            "grant_type": "refresh_token",
            "refresh_token": os.environ["STRAVA_REFRESH_TOKEN"],
        },
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("refresh_token") and body["refresh_token"] != os.environ["STRAVA_REFRESH_TOKEN"]:
        print("::warning::Strava gaf een nieuw refresh token terug. Werk het secret "
              "STRAVA_REFRESH_TOKEN bij als volgende runs mislukken.")
    return body["access_token"]


class Client:
    def __init__(self, access):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {access}"
        self.calls = 0

    def get(self, path, **params):
        for attempt in range(3):
            r = self.s.get(f"{API}{path}", params=params, timeout=60)
            self.calls += 1
            if r.status_code == 429:
                print("Strava-limiet bereikt, stoppen voor deze run.")
                raise RateLimited()
            if r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()


class RateLimited(Exception):
    pass


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def fetch_activities(c, existing):
    """Haalt alle ritten op vanaf de laatst bekende datum (min. 2 dagen overlap)."""
    since = datetime.fromisoformat(CONFIG["since"]).replace(tzinfo=timezone.utc)
    if existing:
        last = max(a["start_date_local"] for a in existing)
        since = max(since, datetime.fromisoformat(last.replace("Z", "")).replace(tzinfo=timezone.utc))
        since = datetime.fromtimestamp(since.timestamp() - 2 * 86400, tz=timezone.utc)
    by_id = {a["id"]: a for a in existing}
    page = 1
    while True:
        batch = c.get("/athlete/activities", after=int(since.timestamp()), per_page=200, page=page)
        if not batch:
            break
        for a in batch:
            if a.get("sport_type") not in RIDE_TYPES:
                continue
            by_id[a["id"]] = {k: a.get(k) for k in KEEP}
        page += 1
    return sorted(by_id.values(), key=lambda a: a["start_date_local"])


def best_efforts(time_s, watts):
    """Beste gemiddelde vermogen per duur, op een 1-secondenraster."""
    if not watts or not time_s:
        return {}
    n = int(time_s[-1]) + 1
    grid = [0.0] * n
    for t, w in zip(time_s, watts):
        grid[int(t)] = float(w or 0)
    # gaten (pauzes) blijven 0, zoals bij de meeste analysetools
    prefix = [0.0]
    for w in grid:
        prefix.append(prefix[-1] + w)
    out = {}
    for d in DURATIONS:
        if d > n:
            continue
        best = max(prefix[i + d] - prefix[i] for i in range(n - d + 1))
        out[str(d)] = round(best / d, 1)
    # genormaliseerd vermogen (30 s voortschrijdend gemiddelde, 4e macht)
    if n >= 30:
        roll = [(prefix[i + 30] - prefix[i]) / 30 for i in range(n - 29)]
        out["np"] = round((sum(x ** 4 for x in roll) / len(roll)) ** 0.25, 1)
    return out


def fetch_power(c, activities):
    done = 0
    for a in reversed(activities):  # nieuwste eerst
        if not a.get("device_watts"):
            continue
        path = POWER / f"{a['id']}.json"
        if path.exists():
            continue
        if done >= MAX_STREAMS:
            break
        s = c.get(f"/activities/{a['id']}/streams", keys="time,watts", key_by_type="true")
        t = (s.get("time") or {}).get("data")
        w = (s.get("watts") or {}).get("data")
        save(path, best_efforts(t, w))
        done += 1
    left = sum(1 for a in activities if a.get("device_watts") and not (POWER / f"{a['id']}.json").exists())
    print(f"Vermogensreeksen opgehaald: {done}, nog te gaan: {left}")


def main():
    c = Client(token())
    acts = load(DATA / "activities.json", [])
    try:
        athlete = c.get("/athlete")
        save(DATA / "athlete.json", {"weight": athlete.get("weight"), "ftp": athlete.get("ftp")})
        acts = fetch_activities(c, acts)
        save(DATA / "activities.json", acts)
        print(f"Ritten in cache: {len(acts)}")
        fetch_power(c, acts)
    except RateLimited:
        save(DATA / "activities.json", acts)
    print(f"API-verzoeken deze run: {c.calls}")


if __name__ == "__main__":
    sys.exit(main())
