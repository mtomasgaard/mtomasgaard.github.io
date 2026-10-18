"""Fishing Window's forecast job: point forecasts for the secret spots, gridded fields for the two
home regions, one public forecast.json.

  FISHING_SPOTS='[{"id": "…", "lat": 59.1234, "lon": 5.1234}, …]' python3 fishing/job/forecast.py

The spots' coordinates come only from the environment (a repository secret) and never reach the
output, a log line or an error message: forecast.json carries each spot's opaque id and nothing
that names or places it. Every request names this job and a contact in its User-Agent, uses at
most four decimals, and honours Expires and Last-Modified through the cache in common.py.

Sources: MET Norway Locationforecast 2.0 (complete), Oceanforecast 2.0, Sunrise 3.0 (CC BY 4.0);
Kartverket's tide API (CC BY 4.0); fields.py for the gridded layers.
"""

import datetime as dt
import json
import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fields                      # noqa: E402
import indicators as ind           # noqa: E402
from common import fetch, FetchError   # noqa: E402

try:
    from zoneinfo import ZoneInfo
    OSLO = ZoneInfo("Europe/Oslo")
except Exception:                   # pragma: no cover
    OSLO = None

SCHEMA = 1
DAYS = 10
MET = "https://api.met.no/weatherapi"
TIDE = "https://vannstand.kartverket.no/tideapi.php"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # …/fishing


def log(msg):
    print(msg, flush=True)


def c4(v):
    return "%.4f" % round(float(v), 4)


def iso_to_epoch(s):
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def r1(v):
    return None if v is None else round(float(v), 1)


def oslo_offset(t):
    if OSLO is None:
        return 3600
    return int(dt.datetime.fromtimestamp(t, OSLO).utcoffset().total_seconds())


def oslo_date(t):
    return time.strftime("%Y-%m-%d", time.gmtime(t + oslo_offset(t)))


# ── MET ──────────────────────────────────────────────────────────────────────

def weather(spot):
    url = "%s/locationforecast/2.0/complete?lat=%s&lon=%s" % (MET, c4(spot["lat"]), c4(spot["lon"]))
    data, changed = fetch(url, "Locationforecast " + spot["id"])
    j = json.loads(data)
    ts = j["properties"]["timeseries"]
    out = {k: [] for k in ("t", "step", "wind", "gust", "dir", "p10", "p90", "pres", "temp", "cloud", "low", "fog", "rain")}
    for k, e in enumerate(ts):
        t = iso_to_epoch(e["time"])
        nxt = iso_to_epoch(ts[k + 1]["time"]) if k + 1 < len(ts) else t + 6 * 3600
        step = max(1, int(round((nxt - t) / 3600)))
        d = e["data"]["instant"]["details"]
        n1 = e["data"].get("next_1_hours") or {}
        n6 = e["data"].get("next_6_hours") or {}
        rain = (n1.get("details") or {}).get("precipitation_amount")
        if rain is None and step > 1:
            rain = (n6.get("details") or {}).get("precipitation_amount")
        out["t"].append(t)
        out["step"].append(step)
        out["wind"].append(r1(d.get("wind_speed")))
        out["gust"].append(r1(d.get("wind_speed_of_gust")))
        out["dir"].append(None if d.get("wind_from_direction") is None else int(round(d["wind_from_direction"])) % 360)
        out["p10"].append(r1(d.get("wind_speed_percentile_10")))
        out["p90"].append(r1(d.get("wind_speed_percentile_90")))
        out["pres"].append(r1(d.get("air_pressure_at_sea_level")))
        out["temp"].append(r1(d.get("air_temperature")))
        out["cloud"].append(None if d.get("cloud_area_fraction") is None else int(round(d["cloud_area_fraction"])))
        out["low"].append(None if d.get("cloud_area_fraction_low") is None else int(round(d["cloud_area_fraction_low"])))
        out["fog"].append(None if d.get("fog_area_fraction") is None else int(round(d["fog_area_fraction"])))
        out["rain"].append(r1(rain))
    out["updated"] = iso_to_epoch(j["properties"]["meta"]["updated_at"])
    return out, changed


def ocean(spot):
    url = "%s/oceanforecast/2.0/complete?lat=%s&lon=%s" % (MET, c4(spot["lat"]), c4(spot["lon"]))
    data, changed = fetch(url, "Oceanforecast " + spot["id"])
    j = json.loads(data)
    out = {k: [] for k in ("t", "wave", "wdir", "cur", "cdir", "sst")}
    for e in j["properties"]["timeseries"]:
        d = e["data"]["instant"]["details"]
        out["t"].append(iso_to_epoch(e["time"]))
        out["wave"].append(r1(d.get("sea_surface_wave_height")))
        out["wdir"].append(None if d.get("sea_surface_wave_from_direction") is None else int(round(d["sea_surface_wave_from_direction"])) % 360)
        out["cur"].append(r1(d.get("sea_water_speed")))
        out["cdir"].append(None if d.get("sea_water_to_direction") is None else int(round(d["sea_water_to_direction"])) % 360)
        out["sst"].append(r1(d.get("sea_water_temperature")))
    out["updated"] = iso_to_epoch(j["properties"]["meta"]["updated_at"])
    return out, changed


def sun_moon(lat, lon, days, label):
    """Sunrise 3.0 for each local date: sunrise, sunset, solar noon, moonrise, moonset, the moon's
    upper and lower transits and its phase. Deterministic, so each date is cached for a week."""
    rows = []
    for d in days:
        off = oslo_offset(int(dt.datetime.fromisoformat(d + "T12:00:00+00:00").timestamp()))
        offs = "%s%02d:%02d" % ("+" if off >= 0 else "-", abs(off) // 3600, abs(off) % 3600 // 60)
        q = "lat=%s&lon=%s&date=%s&offset=%s" % (c4(lat), c4(lon), d, offs.replace("+", "%2B"))
        row = {"date": d}
        try:
            s, _ = fetch("%s/sunrise/3.0/sun?%s" % (MET, q), "Sunrise sun " + label, ttl=7 * 86400)
            p = json.loads(s)["properties"]
            for k, name in (("sunrise", "rise"), ("sunset", "set"), ("solarnoon", "noon")):
                v = (p.get(k) or {}).get("time")
                row[name] = iso_to_epoch(v) if v else None
            m, _ = fetch("%s/sunrise/3.0/moon?%s" % (MET, q), "Sunrise moon " + label, ttl=7 * 86400)
            p = json.loads(m)["properties"]
            for k, name in (("moonrise", "moonrise"), ("moonset", "moonset"), ("high_moon", "highMoon"), ("low_moon", "lowMoon")):
                v = (p.get(k) or {}).get("time")
                row[name] = iso_to_epoch(v) if v else None
            row["phase"] = r1(p.get("moonphase"))
        except (FetchError, ValueError, KeyError) as e:
            log("sun: %s" % e)
        rows.append(row)
    return rows


# ── Kartverket tide ──────────────────────────────────────────────────────────

def _tide_xml(spot, start, end, datatype, ttl, label):
    q = "tide_request=locationdata&lat=%s&lon=%s&fromtime=%s&totime=%s&datatype=%s&refcode=cd&interval=10&lang=en" % (
        c4(spot["lat"]), c4(spot["lon"]), start, end, datatype)
    data, changed = fetch("%s?%s" % (TIDE, q), label, ttl=ttl)
    return ET.fromstring(data), changed


def tide(spot, now):
    fmt = lambda t: time.strftime("%Y-%m-%dT%H:%M", time.gmtime(t + 3600))   # the API reads CET (+01:00)
    h0 = int(now // 3600 * 3600)
    start, end = h0 - 24 * 3600, h0 + DAYS * 86400
    root, changed = _tide_xml(spot, fmt(start), fmt(end), "all", 1800, "Tide " + spot["id"])
    loc = root.find(".//location")
    out = {"step": 600, "delayMin": None, "factor": None}
    if loc is not None:
        out["delayMin"] = int(float(loc.get("delay") or 0))
        out["factor"] = float(loc.get("factor") or 1)
    for data in root.iter("data"):
        kind = data.get("type")
        pts = [(iso_to_epoch(w.get("time")), float(w.get("value"))) for w in data.iter("waterlevel")]
        if not pts:
            continue
        key = {"prediction": "pred", "observation": "obs", "forecast": "fc"}.get(kind)
        if key:
            out[key] = {"t0": pts[0][0], "v": [round(v, 1) for _, v in pts]}
    # high and low water over 30 days around now: the extremes for the phase, and the mean range
    root2, _ = _tide_xml(spot, fmt(h0 - 15 * 86400), fmt(end + 86400), "tab", 6 * 3600, "Tide extremes " + spot["id"])
    ext = []
    for w in root2.iter("waterlevel"):
        ext.append([iso_to_epoch(w.get("time")), round(float(w.get("value")), 1), "h" if w.get("flag") == "high" else "l"])
    ext.sort()
    rng = ind.day_ranges([(e[0], e[1], e[2]) for e in ext])
    out["meanRange"] = round(sum(rng) / len(rng), 1) if rng else None
    out["ext"] = [e for e in ext if start - 86400 <= e[0] <= end + 86400]
    return out, changed


# ── per spot ─────────────────────────────────────────────────────────────────

def nearest(times, t, tol):
    best, bd = None, tol + 1
    lo, hi = 0, len(times) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        d = times[mid] - t
        if abs(d) < bd:
            best, bd = mid, abs(d)
        if d < 0:
            lo = mid + 1
        elif d > 0:
            hi = mid - 1
        else:
            break
    return best if bd <= tol else None


def level_at(series, t):
    if not series:
        return None
    k = (t - series["t0"]) / 600.0
    i = int(k)
    if i < 0 or i + 1 >= len(series["v"]):
        return None
    f = k - i
    return series["v"][i] + (series["v"][i + 1] - series["v"][i]) * f


def spot_record(spot, now):
    w, ch1 = weather(spot)
    o, ch2 = ocean(spot)
    tdd, ch3 = tide(spot, now)
    exts = [(e[0], e[1], "high" if e[2] == "h" else "low") for e in tdd["ext"]]
    pred_times = [tdd["pred"]["t0"] + 600 * i for i in range(len(tdd["pred"]["v"]))] if tdd.get("pred") else []
    rec = {"id": spot["id"], "region": region_of(spot), "updated": {"weather": w.pop("updated"), "ocean": o.pop("updated")}}
    rec.update(w)
    n = len(w["t"])
    for k in ("wave", "wdir", "cur", "cdir", "sst"):
        rec[k] = []
    for k in ("level", "rate", "phase", "pct", "slack", "turn", "chop", "drift", "driftDir", "trend", "trendWord", "light"):
        rec[k] = []
    for i, t in enumerate(w["t"]):
        j = nearest(o["t"], t, 1800)
        for k in ("wave", "wdir", "cur", "cdir", "sst"):
            rec[k].append(o[k][j] if j is not None else None)
        lv = level_at(tdd.get("pred"), t)
        rec["level"].append(r1(lv))
        rate = ind.rate_cm_per_h(pred_times, tdd["pred"]["v"], t) if pred_times else None
        rec["rate"].append(r1(rate))
        ph = ind.tide_phase(t, exts)
        rec["phase"].append(None if not ph else ph["state"][0])
        rec["pct"].append(None if not ph else ph["pct"])
        rec["slack"].append(None if not ph else int(ph["slack"]))
        rec["turn"].append(None if not ph else ph["to_turn_min"])
        cur, cdir = rec["cur"][-1], rec["cdir"][-1]
        c = ind.chop(w["wind"][i], w["dir"][i], cur, cdir)
        rec["chop"].append(None if c is None else int(c))
        ds, dd = ind.drift(cur, cdir, w["wind"][i], w["dir"][i])
        rec["drift"].append(None if ds is None else round(ds, 2))
        rec["driftDir"].append(None if dd is None else int(round(dd)) % 360)
        d3, word = ind.pressure_trend(w["t"], w["pres"], t)
        rec["trend"].append(d3)
        rec["trendWord"].append(None if word is None else word[0])
        rec["light"].append(ind.light(t, spot["lat"], spot["lon"])[0])
    # per local day: the tide's range against the mean, and the sea temperature
    days = []
    seen = []
    for t in w["t"]:
        d = oslo_date(t)
        if d not in seen:
            seen.append(d)
    sst_means = ind.daily_means(o["t"], o["sst"], oslo_offset(now))
    for d in seen[:DAYS + 1]:
        day_ext = [e for e in exts if oslo_date(e[0]) == d]
        rngs = ind.day_ranges(day_ext)
        rng = max(rngs) if rngs else None
        ratio, word = ind.range_vs_mean(rng, tdd.get("meanRange"))
        days.append({"date": d, "range": r1(rng), "ratio": ratio, "word": word, "sst": sst_means.get(d)})
    rec["days"] = days
    rec["tide"] = tdd
    return rec, (ch1 or ch2 or ch3)


def region_of(spot):
    for r in fields.REGIONS:
        if r["lat0"] <= spot["lat"] <= r["lat1"] and r["lon0"] <= spot["lon"] <= r["lon1"]:
            return r["id"]
    return None


# ── fields on disk ───────────────────────────────────────────────────────────

def place_fields(meta, tmp, dest):
    """Move this run's atlases into fishing/fields/ under names that carry each layer's run, keep
    the previous run of each layer too (so a forecast.json fetched an hour ago still finds its
    pictures), and delete everything older."""
    os.makedirs(dest, exist_ok=True)
    keep = set()
    for name, layer in meta["layers"].items():
        run = layer["run"].rstrip("Z")
        layer["file"] = "%s-{region}-%s-{chunk}.png" % (name, run)
        for f in os.listdir(tmp):
            if f.startswith(name + "-"):
                parts = f[:-4].split("-")          # layer-region-chunk
                target = "%s-%s-%s-%s.png" % (parts[0], parts[1], run, parts[2])
                shutil.copyfile(os.path.join(tmp, f), os.path.join(dest, target))
                keep.add(target)
    runs = {}
    for f in os.listdir(dest):
        if f.endswith(".png") and f.count("-") == 3:
            layer, _, run, _ = f[:-4].split("-")
            runs.setdefault(layer, set()).add(run)
    for layer, rs in runs.items():
        for old in sorted(rs)[:-2]:
            for f in list(os.listdir(dest)):
                if f.startswith(layer + "-") and ("-%s-" % old) in f:
                    os.remove(os.path.join(dest, f))


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    raw = os.environ.get("FISHING_SPOTS", "")
    try:
        spots = json.loads(raw)
        assert isinstance(spots, list) and all({"id", "lat", "lon"} <= set(s) for s in spots)
    except (ValueError, AssertionError):
        log("FISHING_SPOTS is missing or not a list of {id, lat, lon}")
        return 2
    now = time.time()
    out_path = os.path.join(ROOT, "forecast.json")
    previous = None
    if os.path.exists(out_path):
        with open(out_path) as f:
            try:
                previous = json.load(f)
            except ValueError:
                previous = None

    records = []
    for spot in spots:
        try:
            rec, _ = spot_record(spot, now)
            records.append(rec)
            log("spot %s: %d hours" % (spot["id"], len(rec["t"])))
        except (FetchError, ValueError, KeyError, ET.ParseError) as e:
            log("spot %s: failed (%s)" % (spot["id"], type(e).__name__ if not isinstance(e, FetchError) else e))
            if previous:
                old = [s for s in previous.get("spots", []) if s.get("id") == spot["id"]]
                if old:
                    records.append(old[0])

    # sun and moon per region (the spots in a region share it) or per lone spot
    first = oslo_date(now)
    day0 = dt.date.fromisoformat(first)
    dates = [(day0 + dt.timedelta(days=k)).isoformat() for k in range(DAYS + 1)]
    sun = {}
    for r in fields.REGIONS:
        if any(rec.get("region") == r["id"] for rec in records):
            sun[r["id"]] = sun_moon((r["lat0"] + r["lat1"]) / 2, (r["lon0"] + r["lon1"]) / 2, dates, r["id"])
    for spot, rec in zip(spots, records):
        if not rec.get("region"):
            sun[rec["id"]] = sun_moon(spot["lat"], spot["lon"], dates, rec["id"])

    tmp = os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "fishing-fields")
    shutil.rmtree(tmp, ignore_errors=True)
    meta, nbytes = fields.build(tmp, log=log)
    if previous and previous.get("fields"):
        # a layer that failed this hour keeps last hour's, if its files are still there
        for name, layer in previous["fields"].get("layers", {}).items():
            if name not in meta["layers"]:
                meta["layers"][name] = layer
    place_fields(meta, tmp, os.path.join(ROOT, "fields"))
    log("fields: %d bytes this run" % nbytes)

    doc = {
        "schema": SCHEMA,
        "generated": int(now),
        "tz": "Europe/Oslo",
        "params": {"chopAngle": ind.CHOP_ANGLE, "chopWind": ind.CHOP_WIND, "chopCurrent": ind.CHOP_CURRENT,
                   "leeway": ind.LEEWAY, "slackMin": ind.SLACK_MIN, "trendHpa": ind.TREND_HPA},
        "spots": records,
        "sun": sun,
        "fields": meta,
    }
    if previous:
        a = dict(previous); b = dict(doc)
        a.pop("generated", None); b.pop("generated", None)
        if json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True):
            log("forecast unchanged")
            return 0
    with open(out_path + ".tmp", "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(out_path + ".tmp", out_path)
    log("forecast.json: %d bytes, %d spots" % (os.path.getsize(out_path), len(records)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
