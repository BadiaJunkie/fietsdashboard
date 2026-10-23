"""Haalt gegevens op via de officiële Garmin Connect Personal API.

Benodigd secret: GARMIN_PERSONAL_API_KEY
(aanmaken op https://connect.garmin.com/app/settings/personalApiToken)

Schrijft:
  data/garmin/activities.json   fietsactiviteiten: trainingseffect, TSS, tijd per zone
  data/garmin/daily.json        per dag: rusthartslag, slaap, stress, stappen

Faalt dit script, dan gaat de rest van de update gewoon door; het dashboard
toont dan de laatst bekende Garmin-gegevens.
"""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "garmin"
CONFIG = json.loads((ROOT / "config.json").read_text())
SINCE = date.fromisoformat(CONFIG["since"])
CHUNK = 28            # dagen per verzoek voor slaap en dagoverzichten
MAX_BACKFILL = 12     # zoveel oudere blokken per run, tot SINCE bereikt is
RECENT_DAYS = 10      # deze dagen altijd opnieuw ophalen (late synchronisatie)

ACT_KEEP = [
    "activityId", "activityName", "startTimeLocal", "duration", "movingDuration", "distance",
    "averageHR", "maxHR", "avgPower", "normPower", "trainingStressScore", "intensityFactor",
    "aerobicTrainingEffect", "anaerobicTrainingEffect", "aerobicTrainingEffectMessage",
    "max20MinPower", "calories",
]


def as_list(x):
    """Pakt de lijst uit een antwoord, ongeacht of die direct of in een veld zit."""
    if isinstance(x, list):
        return x
    if isinstance(x, dict):
        for k in ("results", "data", "dailySleepSummaries", "activities", "items"):
            if isinstance(x.get(k), list):
                return x[k]
        for v in x.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True))


def is_cycling(a):
    t = a.get("activityType") or {}
    key = (t.get("typeKey") or "").lower()
    return t.get("parentTypeId") == 2 or t.get("typeId") == 2 or any(
        s in key for s in ("cycl", "biking", "ride", "gravel"))


def compact_activity(a):
    out = {k: a.get(k) for k in ACT_KEEP if a.get(k) is not None}
    out["type"] = (a.get("activityType") or {}).get("typeKey")
    out["hrZones"] = [round(a.get(f"hrTimeInZone_{i}") or 0) for i in range(1, 6)]
    out["powerZones"] = [round(a.get(f"powerTimeInZone_{i}") or 0) for i in range(1, 8)]
    return out


def fetch_activities(client, cache):
    by_id = {str(a["activityId"]): a for a in cache}
    start_date = SINCE if not cache else date.today() - timedelta(days=14)
    start = 0
    while True:
        batch = as_list(client.search_activities(
            start=start, limit=100, start_date=start_date.isoformat(), end_date=date.today().isoformat()))
        if not batch:
            break
        for a in batch:
            if is_cycling(a):
                by_id[str(a["activityId"])] = compact_activity(a)
        if len(batch) < 100:
            break
        start += 100
    return sorted(by_id.values(), key=lambda a: a.get("startTimeLocal") or "")


def day_from_sleep(s):
    secs = s.get("sleepTimeSeconds")
    if not secs:
        return None
    return dict(
        sleep_h=round(secs / 3600, 2),
        deep_min=round((s.get("deepSleepSeconds") or 0) / 60),
        rem_min=round((s.get("remSleepSeconds") or 0) / 60),
        light_min=round((s.get("lightSleepSeconds") or 0) / 60),
        awake_min=round((s.get("awakeSleepSeconds") or 0) / 60),
        sleep_hr=s.get("averageSpO2HRSleep") or s.get("avgHeartRate"),
    )


def day_from_summary(d):
    hr = d.get("heartRate") or {}
    stress = d.get("stress") or {}
    steps = (d.get("movement") or {}).get("steps") or {}
    out = dict(
        rhr=hr.get("restingValue") or d.get("restingHeartRate"),
        stress=stress.get("avgLevel") if isinstance(stress, dict) else None,
        steps=steps.get("value") if isinstance(steps, dict) else d.get("totalSteps"),
    )
    if out["stress"] is None:
        out["stress"] = d.get("averageStressLevel")
    return {k: v for k, v in out.items() if v not in (None, -1, -2)}


def fetch_range(client, daily, a, b):
    for s in as_list(client.get_daily_sleep_summaries(a.isoformat(), b.isoformat())):
        key = s.get("calendarDate")
        rec = day_from_sleep(s)
        if key and rec:
            daily.setdefault(key, {}).update(rec)
    for d in as_list(client.get_daily_summaries(a.isoformat(), b.isoformat())):
        key = d.get("calendarDate")
        rec = day_from_summary(d)
        if key and rec:
            daily.setdefault(key, {}).update(rec)


def fetch_daily(client, store):
    daily = store.get("days", {})
    today = date.today()
    # recente dagen altijd verversen
    fetch_range(client, daily, today - timedelta(days=RECENT_DAYS), today)
    # daarna in blokken terug in de tijd tot SINCE
    oldest = date.fromisoformat(store.get("oldest_fetched", (today - timedelta(days=RECENT_DAYS)).isoformat()))
    for _ in range(MAX_BACKFILL):
        if oldest <= SINCE:
            break
        a = max(SINCE, oldest - timedelta(days=CHUNK))
        fetch_range(client, daily, a, oldest - timedelta(days=1))
        oldest = a
    return {"days": dict(sorted(daily.items())), "oldest_fetched": oldest.isoformat()}


def main():
    key = os.environ.get("GARMIN_PERSONAL_API_KEY")
    if not key:
        print("Geen GARMIN_PERSONAL_API_KEY ingesteld; Garmin overgeslagen.")
        return 0
    try:
        from garmin_personal_api import GarminPersonalApiClient, PersonalApiAuthenticator

        with PersonalApiAuthenticator() as auth:
            token = auth.obtain_token(key)
        with GarminPersonalApiClient(token.access_token) as client:
            acts = fetch_activities(client, load(OUT / "activities.json", []))
            save(OUT / "activities.json", acts)
            print(f"Garmin-fietsactiviteiten in cache: {len(acts)}")
            daily = fetch_daily(client, load(OUT / "daily.json", {}))
            save(OUT / "daily.json", daily)
            print(f"Garmin-dagen in cache: {len(daily['days'])} (terug tot {daily['oldest_fetched']})")
    except Exception as e:  # Garmin mag de Strava-update nooit tegenhouden
        print(f"::warning::Garmin ophalen mislukt: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
