"""The fishing indicators of plan 0016 §6, as pure functions (tests: test_indicators.py).

Directions follow the sources: wind is where it comes FROM (meteorological), current is where it
goes TO (oceanographic). Speeds in m/s, heights in cm, times in epoch seconds.
"""

import math

# Editable defaults, written into forecast.json so the app can show what was used.
CHOP_ANGLE = 120.0     # wind-to against current-to, degrees
CHOP_WIND = 6.0        # m/s
CHOP_CURRENT = 0.3     # m/s
LEEWAY = 0.03          # a small boat's drift, as a share of the wind (an estimate)
SLACK_MIN = 30         # minutes either side of a high or a low
TREND_HPA = 1.0        # hPa per 3 h for "falling" and "rising"


def angle_between(a, b):
    """The smaller angle between two bearings, 0 to 180 degrees."""
    d = abs((a - b) % 360.0)
    return 360.0 - d if d > 180.0 else d


def wind_against_current(wind_from, current_to):
    """Angle between the way the wind blows (from + 180) and the way the current sets."""
    return angle_between((wind_from + 180.0) % 360.0, current_to)


def chop(wind_speed, wind_from, current_speed, current_to,
         angle=CHOP_ANGLE, min_wind=CHOP_WIND, min_current=CHOP_CURRENT):
    """Wind against current: steep, short waves. True when all three thresholds are met."""
    if None in (wind_speed, wind_from, current_speed, current_to):
        return None
    return (wind_speed >= min_wind and current_speed >= min_current
            and wind_against_current(wind_from, current_to) >= angle)


def drift(current_speed, current_to, wind_speed, wind_from, leeway=LEEWAY):
    """A small boat's drift: the current plus `leeway` of the wind, as (speed m/s, to-bearing)."""
    if None in (current_speed, current_to, wind_speed, wind_from):
        return None, None
    cx = current_speed * math.sin(math.radians(current_to))
    cy = current_speed * math.cos(math.radians(current_to))
    wto = math.radians((wind_from + 180.0) % 360.0)
    wx = leeway * wind_speed * math.sin(wto)
    wy = leeway * wind_speed * math.cos(wto)
    x, y = cx + wx, cy + wy
    speed = math.hypot(x, y)
    return speed, (math.degrees(math.atan2(x, y)) % 360.0) if speed > 1e-9 else None


def tide_phase(t, extremes, slack_min=SLACK_MIN):
    """Where t sits in the tide: rising or falling, the share of the way from the last turn to the
    next (0 to 1), whether it is within slack_min of a turn, and the minutes to the next turn.
    extremes: [(time, cm, 'high'|'low')] sorted by time. None outside the extremes' span."""
    prev = nxt = None
    for e in extremes:
        if e[0] <= t:
            prev = e
        elif nxt is None:
            nxt = e
            break
    if prev is None or nxt is None:
        return None
    state = "rising" if prev[2] == "low" else "falling"
    span = nxt[0] - prev[0]
    pct = (t - prev[0]) / span if span > 0 else 0.0
    near = min(t - prev[0], nxt[0] - t)
    return {"state": state, "pct": round(pct, 3), "slack": near <= slack_min * 60,
            "to_turn_min": int(round((nxt[0] - t) / 60.0))}


def rate_cm_per_h(times, levels, t):
    """The water level's rate of change at t (cm per hour), from a regular series."""
    if len(times) < 2:
        return None
    step = times[1] - times[0]
    k = int(round((t - times[0]) / step))
    if k <= 0 or k >= len(times) - 1:
        return None
    a, b = levels[k - 1], levels[k + 1]
    if a is None or b is None:
        return None
    return (b - a) / (2 * step) * 3600.0


def day_ranges(extremes):
    """Each successive high-to-low or low-to-high difference (cm)."""
    return [abs(b[1] - a[1]) for a, b in zip(extremes, extremes[1:])]


def range_vs_mean(rng, mean_rng):
    """A range against the mean: the ratio and a word (spring above 1.15, neap below 0.85)."""
    if not rng or not mean_rng:
        return None, None
    r = rng / mean_rng
    return round(r, 2), ("spring" if r >= 1.15 else "neap" if r <= 0.85 else "mean")


def pressure_trend(times, pressures, t, hours=3, threshold=TREND_HPA):
    """hPa change over the `hours` before t and its word, from an hourly-or-coarser series."""
    def at(x):
        for i in range(len(times) - 1):
            if times[i] <= x <= times[i + 1]:
                a, b = pressures[i], pressures[i + 1]
                if a is None or b is None:
                    return None
                f = (x - times[i]) / (times[i + 1] - times[i])
                return a + (b - a) * f
        return pressures[-1] if times and x == times[-1] else None
    now, before = at(t), at(t - hours * 3600)
    if now is None or before is None:
        return None, None
    d = now - before
    return round(d, 1), ("falling" if d <= -threshold else "rising" if d >= threshold else "steady")


def solar_elevation(t, lat, lon):
    """The sun's elevation in degrees (NOAA's low-precision formulas, good to about 0.1 degree)."""
    jd = t / 86400.0 + 2440587.5
    n = jd - 2451545.0
    L = (280.460 + 0.9856474 * n) % 360
    g = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = math.radians(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)
    dec = math.asin(math.sin(eps) * math.sin(lam))
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    gmst = (18.697374558 + 24.06570982441908 * n) % 24
    ha = math.radians((gmst * 15 + lon) - math.degrees(ra))
    la = math.radians(lat)
    el = math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha))
    return math.degrees(el)


def light(t, lat, lon):
    """'day' (sun above −0.833°, the sunrise convention), 'civil' (above −6°) or 'night'."""
    e = solar_elevation(t, lat, lon)
    return "day" if e > -0.833 else "civil" if e > -6.0 else "night"


def daily_means(times, values, offset_s):
    """Mean of values per local calendar day: {'YYYY-MM-DD': mean}."""
    import time as _t
    acc = {}
    for t, v in zip(times, values):
        if v is None:
            continue
        d = _t.strftime("%Y-%m-%d", _t.gmtime(t + offset_s))
        s, n = acc.get(d, (0.0, 0))
        acc[d] = (s + v, n + 1)
    return {d: round(s / n, 2) for d, (s, n) in acc.items()}
