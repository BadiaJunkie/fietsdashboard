"""Rekent de opgehaalde ritten om naar site/data.json voor het dashboard."""
import json
import math
import re
import statistics as st
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = json.loads((ROOT / "config.json").read_text())
FTP = CONFIG["ftp"]
KG = CONFIG["weight"]
DURATIONS = ["5", "15", "30", "60", "300", "1200", "3600"]
RACE_RE = re.compile(r"\b(race|zracing|tt:)", re.I)


def kind(a):
    if a["sport_type"] == "VirtualRide" or a.get("trainer"):
        return "binnen"
    if a["sport_type"] == "GravelRide" or re.search(r"gravel", a["name"] or "", re.I):
        return "gravel"
    return "weg"


def tss(a):
    """Trainingsbelasting: uit genormaliseerd vermogen, anders uit kJ."""
    sec = a.get("moving_time") or 0
    if not sec:
        return 0.0
    np_ = a.get("weighted_average_watts")
    if not np_ and a.get("kilojoules"):
        np_ = a["kilojoules"] * 1000 / sec
    if not np_:
        return 0.0
    i = min(np_ / FTP, 1.15)
    return sec * i * i / 36


def load_rides():
    acts = json.loads((DATA / "activities.json").read_text())
    rides = []
    for a in acts:
        d = date.fromisoformat(a["start_date_local"][:10])
        km = (a.get("distance") or 0) / 1000
        mins = (a.get("moving_time") or 0) / 60
        if km < 4 and mins < 15:  # losse warming-ups en testjes
            continue
        p = DATA / "power" / f"{a['id']}.json"
        power = json.loads(p.read_text()) if p.exists() else {}
        avg_w = a.get("average_watts") or ((a.get("kilojoules") or 0) * 1000 / (mins * 60) if mins else 0)
        rides.append(dict(
            id=a["id"], d=d, name=(a.get("name") or "").strip(), k=kind(a), km=km, min=mins,
            el=a.get("total_elevation_gain") or 0, kj=a.get("kilojoules") or 0,
            avg=avg_w or 0, np=power.get("np") or a.get("weighted_average_watts"),
            meter=bool(a.get("device_watts")), hr=a.get("average_heartrate"), hrm=a.get("max_heartrate"),
            tss=tss(a), best=power,
        ))
    rides.sort(key=lambda r: r["d"])
    return rides


def agg(sel):
    return dict(
        n=len(sel), km=round(sum(r["km"] for r in sel)), h=round(sum(r["min"] for r in sel) / 60),
        el=round(sum(r["el"] for r in sel)),
        weg_km=round(sum(r["km"] for r in sel if r["k"] == "weg")),
        gravel_km=round(sum(r["km"] for r in sel if r["k"] == "gravel")),
        binnen_km=round(sum(r["km"] for r in sel if r["k"] == "binnen")),
        k100=sum(1 for r in sel if r["km"] >= 100), tss=round(sum(r["tss"] for r in sel)),
    )


def best_of(sel):
    out = {}
    for d in DURATIONS:
        vals = [(r["best"][d], r) for r in sel if r["best"].get(d)]
        if vals:
            v, r = max(vals, key=lambda x: x[0])
            out[d] = dict(w=round(v), date=r["d"].isoformat(), name=r["name"])
    return out


def main():
    rides = load_rides()
    today = date.today()
    start = date.fromisoformat(CONFIG["since"])
    days = (today - start).days + 1

    # fitheid / vermoeidheid / vorm
    daily = [0.0] * days
    for r in rides:
        i = (r["d"] - start).days
        if 0 <= i < days:
            daily[i] += r["tss"]
    kc, ka = 1 - math.exp(-1 / 42), 1 - math.exp(-1 / 7)
    ctl = atl = 0.0
    pmc = []
    for i, v in enumerate(daily):
        ctl += (v - ctl) * kc
        atl += (v - atl) * ka
        pmc.append([(start + timedelta(i)).isoformat(), round(ctl, 1), round(atl, 1), round(ctl - atl, 1)])
    peaks = {}
    for d, c, _, _ in pmc:
        if d[:4] not in peaks or c > peaks[d[:4]][1]:
            peaks[d[:4]] = [d, c]

    # maanden
    months = []
    y, m = start.year, start.month
    while (y, m) <= (today.year, today.month):
        sel = [r for r in rides if r["d"].year == y and r["d"].month == m]
        months.append(dict(
            m=f"{y}-{m:02d}", weg=round(sum(r["km"] for r in sel if r["k"] == "weg")),
            gravel=round(sum(r["km"] for r in sel if r["k"] == "gravel")),
            binnen=round(sum(r["km"] for r in sel if r["k"] == "binnen")),
            h=round(sum(r["min"] for r in sel) / 60, 1), n=len(sel),
        ))
        m += 1
        if m > 12:
            y, m = y + 1, 1

    years = {}
    for yy in range(start.year, today.year + 1):
        try:
            cutoff = date(yy, today.month, today.day)
        except ValueError:  # 29 februari
            cutoff = date(yy, 2, 28)
        years[yy] = dict(full=agg([r for r in rides if r["d"].year == yy]),
                         ytd=agg([r for r in rides if r["d"].year == yy and r["d"] <= cutoff]))

    def window(a, b):
        return [r for r in rides if a <= r["d"] <= b]

    l12 = window(today - timedelta(364), today)
    p12 = window(today - timedelta(729), today - timedelta(365))

    # vermogensprofiel
    power = dict(
        last90=best_of(window(today - timedelta(89), today)),
        prev90=best_of(window(today - timedelta(89 + 365), today - timedelta(365))),
        last365=best_of(l12),
        years={str(yy): best_of([r for r in rides if r["d"].year == yy]) for yy in range(start.year, today.year + 1)},
        coverage=dict(with_meter=sum(1 for r in rides if r["meter"]),
                      analysed=sum(1 for r in rides if r["best"])),
    )

    # vermogen binnen per kwartaal (ritten >= 50 min)
    q = defaultdict(list)
    for r in rides:
        if r["k"] == "binnen" and r["min"] >= 50 and r["avg"]:
            q[f"{r['d'].year}-K{(r['d'].month - 1) // 3 + 1}"].append(r["avg"])
    qtrend = [dict(q=k, med=round(st.median(v)), top=round(sorted(v)[-max(1, len(v) // 10)]), n=len(v))
              for k, v in sorted(q.items())]

    # races
    races = []
    for r in reversed(rides):
        if r["k"] == "binnen" and RACE_RE.search(r["name"]):
            cat = re.search(r"\(([A-E])\)", r["name"])
            name = re.sub(r"^Zwift\s*-\s*", "", r["name"])
            name = re.sub(r"^(Race|TT):\s*", "", name)
            name = re.sub(r"\s*\([A-E]\)", "", name).strip()
            races.append(dict(d=r["d"].isoformat(), cat=cat.group(1) if cat else None, name=name,
                              avg=round(r["avg"]), np=round(r["np"]) if r["np"] else None,
                              best={k: r["best"].get(k) for k in ["1200", "300", "60", "30", "15"]},
                              hr=round(r["hr"]) if r["hr"] else None, hrm=round(r["hrm"]) if r["hrm"] else None,
                              km=round(r["km"], 1), min=round(r["min"])))
        if len(races) >= 25:
            break

    # lange ritten buiten en terugkerende routes
    long_ = [dict(d=r["d"].isoformat(), name=r["name"], t=r["k"], km=round(r["km"], 1), el=round(r["el"]),
                  min=round(r["min"]), kmh=round(r["km"] / (r["min"] / 60), 1) if r["min"] else 0,
                  pw=round(r["avg"]) if r["meter"] else None, tss=round(r["tss"]))
             for r in rides if r["k"] != "binnen" and r["km"] >= 100]

    def norm(n):
        n = n.lower()
        n = re.sub(r"[’'`]", "", n)
        n = re.sub(r"\b(19|20)\d\d\b|\b\d\d\b|redux|#\d+|@\S+|endurance builder", "", n)
        return re.sub(r"[^a-z]+", " ", n).strip()

    groups = defaultdict(list)
    for e in long_:
        if norm(e["name"]):
            groups[norm(e["name"])].append(e)
    classics = [dict(name=v[-1]["name"], rides=v) for v in groups.values()
                if len({e["d"][:4] for e in v}) >= 2]
    classics.sort(key=lambda c: -len(c["rides"]))

    athlete = json.loads((DATA / "athlete.json").read_text()) if (DATA / "athlete.json").exists() else {}
    out = dict(
        generated=datetime.now().isoformat(timespec="minutes"), today=today.isoformat(),
        name=CONFIG["name"], ftp=FTP, weight=KG, manual=CONFIG.get("manual", {}), strava_athlete=athlete,
        nrides=len(rides), pmc=pmc, peaks=peaks, months=months, years=years,
        l12m=agg(l12), p12m=agg(p12), power=power, qtrend=qtrend, races=races,
        events=long_, classics=classics[:8],
        last_ride=rides[-1]["d"].isoformat() if rides else None,
    )
    site = ROOT / "site"
    site.mkdir(exist_ok=True)
    (site / "data.json").write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"data.json geschreven: {len(rides)} ritten, {len(races)} races, "
          f"{power['coverage']['analysed']}/{power['coverage']['with_meter']} ritten met vermogensanalyse")


if __name__ == "__main__":
    main()
