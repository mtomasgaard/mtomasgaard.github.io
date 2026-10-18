"""Gridded fields for the two home regions, packed into RGB PNG atlases for the app.

Sources (MET Norway, CC BY 4.0), subset on thredds.met.no by OPeNDAP index ranges to each region's
box and nothing more:
  wind   MEPS 2.5 km deterministic (mepslatest): 10 m wind speed, direction and gust, hourly
  sea    NorKyst v3 800 m (fou-hi/norkystv3_800m_m00_be): surface current (eastward, northward)
         and surface temperature, hourly
  wave   MyWaveWAM 800 m with currents (fou-hi/mywavewam800v_curr, the Vestlandet domain, which
         covers both regions; the newest run file): significant wave height, mean direction, peak
         period, hourly, sampled every 1.6 km (waves vary slowly; it halves the bytes)

Each layer's atlases start at its own model run's first hour, so a run that has not changed writes
byte-identical files and the repository stores nothing new. Each field is resampled bilinearly onto a regular latitude-longitude grid per region (row 0 at the
north edge, column 0 at the west edge), so the app samples it with plain arithmetic. One PNG holds
CHUNK consecutive hours stacked top to bottom, ny rows each. Encodings (R, G, B), fixed so a frame
reads the same in every run; 0 in R means no data (land, or outside the model):
  wind   R = u/0.25 + 128, G = v/0.25 + 128 (m/s, the way the air goes), B = gust/0.25
  sea    R = u/0.02 + 128, G = v/0.02 + 128 (m/s, the way the water goes), B = (T + 2)*8 (deg C)
  wave   R = Hs/0.05 + 1 (m), G = to-direction/360*250 + 1 (the way the waves travel, as
         the model writes it), B = peak period*10 (s)
"""

import math
import os
import re
import time

from common import dap, get, fetch, write_png_rgb, FetchError

THREDDS = "https://thredds.met.no/thredds"
CHUNK = 24

REGIONS = [
    {"id": "kar", "lat0": 59.12, "lat1": 59.45, "lon0": 5.02, "lon1": 5.62},
    {"id": "rom", "lat0": 62.55, "lat1": 62.92, "lon0": 6.62, "lon1": 7.75},
]
GRIDS = {"wind": (0.0225, 0.045), "sea": (0.0072, 0.0144), "wave": (0.0144, 0.0288)}
ENC = {
    "wind": {"r": "u", "g": "v", "b": "gust", "uv_step": 0.25, "b_step": 0.25},
    "sea": {"r": "u", "g": "v", "b": "temp", "uv_step": 0.02, "b_offset": 2.0, "b_step": 0.125},
    "wave": {"r": "hs", "g": "dir", "b": "tp", "r_step": 0.05, "g_scale": 250 / 360.0, "b_step": 0.1},
}


def region_grid(region, layer):
    dlat, dlon = GRIDS[layer]
    nx = int(round((region["lon1"] - region["lon0"]) / dlon)) + 1
    ny = int(round((region["lat1"] - region["lat0"]) / dlat)) + 1
    return {"nx": nx, "ny": ny, "lon0": region["lon0"], "lat1": region["lat1"], "dlon": dlon, "dlat": dlat}


def grid_points(g):
    for j in range(g["ny"]):
        lat = g["lat1"] - j * g["dlat"]
        for i in range(g["nx"]):
            yield j, i, lat, g["lon0"] + i * g["dlon"]


# ── locating a point on a curvilinear source grid ─────────────────────────────

class Locator:
    """Fractional (i, j) of a latitude-longitude on a source grid given its 2-D latitude and
    longitude arrays (row-major, ny × nx): Newton steps on the bilinear map, started from the
    nearest node. Works for any of the three projections without coding them."""

    def __init__(self, lat, lon, nx, ny):
        self.lat, self.lon, self.nx, self.ny = lat, lon, nx, ny
        self.buckets = {}
        for j in range(ny):
            for i in range(nx):
                k = (round(lat[j * nx + i] * 20), round(lon[j * nx + i] * 10))
                self.buckets.setdefault(k, []).append((i, j))

    def _ll(self, fi, fj):
        nx = self.nx
        i0 = min(max(int(math.floor(fi)), 0), self.nx - 2)
        j0 = min(max(int(math.floor(fj)), 0), self.ny - 2)
        tx, ty = fi - i0, fj - j0
        a, b, c, d = j0 * nx + i0, j0 * nx + i0 + 1, (j0 + 1) * nx + i0, (j0 + 1) * nx + i0 + 1
        out = []
        for arr in (self.lat, self.lon):
            top = arr[a] + (arr[b] - arr[a]) * tx
            bot = arr[c] + (arr[d] - arr[c]) * tx
            out.append(top + (bot - top) * ty)
        return out

    def index(self, lat, lon):
        best, bd = None, 1e9
        kl, ko = round(lat * 20), round(lon * 10)
        for dk in (-1, 0, 1):
            for do in (-1, 0, 1):
                for (i, j) in self.buckets.get((kl + dk, ko + do), ()):
                    p = j * self.nx + i
                    d = (self.lat[p] - lat) ** 2 + ((self.lon[p] - lon) * math.cos(math.radians(lat))) ** 2
                    if d < bd:
                        best, bd = (i, j), d
        if best is None:
            return None
        fi, fj = float(best[0]), float(best[1])
        for _ in range(8):
            la, lo = self._ll(fi, fj)
            e = 1e-3
            la_i, lo_i = self._ll(fi + e, fj)
            la_j, lo_j = self._ll(fi, fj + e)
            a, b = (la_i - la) / e, (la_j - la) / e
            c, d = (lo_i - lo) / e, (lo_j - lo) / e
            det = a * d - b * c
            if abs(det) < 1e-12:
                break
            r1, r2 = lat - la, lon - lo
            di = (d * r1 - b * r2) / det
            dj = (-c * r1 + a * r2) / det
            fi, fj = fi + di, fj + dj
            if abs(di) < 1e-4 and abs(dj) < 1e-4:
                break
        if fi < -0.5 or fj < -0.5 or fi > self.nx - 0.5 or fj > self.ny - 0.5:
            return None
        return fi, fj


def bilinear(arr, nx, ny, fi, fj):
    """NaN-aware: corners without data are left out, and a point with less than half its weight
    in valid corners has no value (keeps the land mask honest while filling the coast's cells)."""
    fi = min(max(fi, 0.0), nx - 1.0)
    fj = min(max(fj, 0.0), ny - 1.0)
    i0, j0 = min(int(fi), nx - 2), min(int(fj), ny - 2)
    tx, ty = fi - i0, fj - j0
    s = w = 0.0
    for (di, dj, wt) in ((0, 0, (1 - tx) * (1 - ty)), (1, 0, tx * (1 - ty)), (0, 1, (1 - tx) * ty), (1, 1, tx * ty)):
        v = arr[(j0 + dj) * nx + i0 + di]
        if v == v:
            s += v * wt
            w += wt
    return s / w if w >= 0.5 else float("nan")


# ── one source ───────────────────────────────────────────────────────────────

class Source:
    """A THREDDS dataset with 2-D latitude/longitude, subset per region."""

    def __init__(self, base, lat_var, lon_var, ydim, xdim, label):
        self.base, self.lat_var, self.lon_var, self.label = base, lat_var, lon_var, label
        self.ny, self.nx = ydim, xdim

    def locate_box(self, g, stride=8, margin=3):
        """The index box of the source grid that covers the region's grid, from a strided read of
        the whole grid's coordinates (cached for a month), then the box's own coordinates."""
        q = "%s[0:%d:%d][0:%d:%d],%s[0:%d:%d][0:%d:%d]" % (
            self.lat_var, stride, self.ny - 1, stride, self.nx - 1, self.lon_var, stride, self.ny - 1, stride, self.nx - 1)
        p, _ = dap(self.base, q, self.label + " coordinates", ttl=30 * 86400)
        (sh, la), (_, lo) = get(p, self.lat_var), get(p, self.lon_var)
        loc = Locator(la, lo, sh[1], sh[0])
        i_s, j_s = [], []
        for (_, _, lat, lon) in grid_points(g):
            r = loc.index(lat, lon)
            if r:
                i_s.append(r[0]); j_s.append(r[1])
        if not i_s:
            raise FetchError("%s: region outside the model" % self.label)
        i0 = max(0, int(min(i_s) * stride) - margin * stride // 2 - margin)
        i1 = min(self.nx - 1, int(max(i_s) * stride) + margin * stride // 2 + margin + 1)
        j0 = max(0, int(min(j_s) * stride) - margin * stride // 2 - margin)
        j1 = min(self.ny - 1, int(max(j_s) * stride) + margin * stride // 2 + margin + 1)
        q = "%s[%d:1:%d][%d:1:%d],%s[%d:1:%d][%d:1:%d]" % (self.lat_var, j0, j1, i0, i1, self.lon_var, j0, j1, i0, i1)
        p, _ = dap(self.base, q, self.label + " box coordinates", ttl=30 * 86400)
        (sh, la), (_, lo) = get(p, self.lat_var), get(p, self.lon_var)
        box = {"i0": i0, "i1": i1, "j0": j0, "j1": j1, "nx": sh[1], "ny": sh[0]}
        box["loc"] = Locator(la, lo, sh[1], sh[0])
        # every output point's fractional index in the box, once
        box["frac"] = [box["loc"].index(lat, lon) for (_, _, lat, lon) in grid_points(g)]
        return box


def meps_latest():
    """The newest complete MEPS deterministic run: its dataset path and reference time."""
    data, _ = fetch(THREDDS + "/catalog/mepslatest/catalog.xml", "MEPS catalogue", ttl=600)
    names = sorted(set(re.findall(r'name="(meps_det_2_5km_(\d{8}T\d{2})Z\.ncml)"', data.decode())))
    for name, run in reversed(names):
        base = THREDDS + "/dodsC/mepslatest/" + name
        try:
            data, _ = fetch(base + ".dds", "MEPS structure", ttl=3600)
        except FetchError:
            continue
        m = re.search(r"Float64 time\[time = (\d+)\]", data.decode())
        if m and int(m.group(1)) >= 61:
            return base, run, int(m.group(1))
    raise FetchError("MEPS: no complete run")


def dim_of(base, var, label):
    data, _ = fetch(base + ".dds", label + " structure", ttl=600)
    m = re.search(r"%s\[time = (\d+)\]\[" % var, data.decode())
    return int(m.group(1)) if m else None


def times_of(base, n, k, label):
    """The last k time values (epoch seconds) of a dataset with n times."""
    p, _ = dap(base, "time[%d:1:%d]" % (n - k, n - 1), label + " times", ttl=600)
    return get(p, "time")[1]


def hours_from(times, t_start, hours):
    """Indexes of `times` on each whole hour from t_start for `hours` hours (None where missing)."""
    pos = {int(round(t)): i for i, t in enumerate(times) if t is not None}
    return [pos.get(int(t_start + 3600 * h)) for h in range(hours)]


def resample(values, shape_tyx, box, g, scale=1.0, fill=None, offset=0.0):
    """values: a flat (t, y, x) block over the box; returns per-step output grids (lists)."""
    nt, ny, nx = shape_tyx
    out = []
    plane = ny * nx
    for t in range(nt):
        src = values[t * plane:(t + 1) * plane]
        if fill is not None or scale != 1.0 or offset:
            src = [float("nan") if (fill is not None and v == fill) or v != v or abs(v) > 1e20 else v * scale + offset for v in src]
        else:
            src = [float("nan") if abs(v) > 1e20 else v for v in src]
        grid = []
        for f in box["frac"]:
            grid.append(float("nan") if f is None else bilinear(src, nx, ny, f[0], f[1]))
        out.append(grid)
    return out


def _q(var, ts, lev, box):
    lev_s = "[%d]" % lev if lev is not None else ""
    return "%s[%d:1:%d]%s[%d:1:%d][%d:1:%d]" % (var, ts[0], ts[1], lev_s, box["j0"], box["j1"], box["i0"], box["i1"])


# ── the builder ──────────────────────────────────────────────────────────────

def _anchor(ref):
    """A run's first whole hour, the start of every one of its atlases."""
    return int(math.ceil(ref / 3600.0) * 3600)


def _region_meta(g, idx, have):
    return {**{k: g[k] for k in ("nx", "ny", "lon0", "lat1", "dlon", "dlat")},
            "first": idx.index(have[0]), "last": len(idx) - 1 - idx[::-1].index(have[-1])}


def _layer(out_dir, name, base, nt, times, ref, model, src, fetch_region, log):
    """One layer for both regions. Its atlases start at the run's own first hour (`t0`), so a run
    that has not changed writes byte-identical files and git stores nothing new."""
    t0 = _anchor(ref)
    idx = hours_from(times, t0, HOURS[name])
    have = [i for i in idx if i is not None]
    if not have:
        raise FetchError("%s: no hours" % name)
    layer = {"model": model, "run": time.strftime("%Y%m%dT%HZ", time.gmtime(ref)), "t0": t0,
             "hours": HOURS[name], "regions": {}, "enc": ENC[name]}
    written = 0
    for r in REGIONS:
        g = region_grid(r, name)
        box = src.locate_box(g)
        planes = fetch_region(r, g, box, (min(have), max(have)))
        frames = [None if i is None else tuple(p[i - min(have)] for p in planes) for i in idx]
        written += write_layer(out_dir, name, r["id"], g, frames)
        layer["regions"][r["id"]] = _region_meta(g, idx, have)
    log("fields: %s from %s %s, %d of %d hours" % (name, model, layer["run"], len(have), HOURS[name]))
    return layer, written


HOURS = {"wind": 66, "sea": 72, "wave": 66}


def build(out_dir, log=print, only=None):
    """Fetch, resample and write every layer for every region into out_dir. Returns the metadata
    block for forecast.json (what exists, its grid, its encoding, its model run and first hour) and
    the bytes written. A layer that fails is left out and logged; the others still ship."""
    meta = {"chunk": CHUNK, "regions": [{k: r[k] for k in ("id", "lat0", "lat1", "lon0", "lon1")} for r in REGIONS],
            "layers": {}}
    written = 0

    def run(name, fn):
        nonlocal written
        if only and name not in only:
            return
        try:
            layer, n = fn()
            meta["layers"][name] = layer
            written += n
        except (FetchError, KeyError, ValueError, AttributeError) as e:
            log("fields: %s skipped (%s)" % (name, e))

    def wind():
        base, run_id, nt = meps_latest()
        times = times_of(base, nt, nt, "MEPS")
        p, _ = dap(base, "forecast_reference_time", "MEPS run", ttl=3600)
        ref = p["forecast_reference_time"][1][0]
        src = Source(base, "latitude", "longitude", 1069, 949, "MEPS")

        def region(r, g, box, ts):
            q = ",".join(_q(v, ts, 0, box) for v in ("wind_speed", "wind_direction", "wind_speed_of_gust"))
            p, _ = dap(base, q, "MEPS wind " + r["id"], ttl=3 * 3600)
            sh = get(p, "wind_speed")[0]
            shp = (sh[0], sh[2], sh[3])
            sp, dr = get(p, "wind_speed")[1], get(p, "wind_direction")[1]
            # a direction cannot be interpolated as a number: through u and v (the way the air goes)
            ok = lambda s, d: abs(s) < 1e20 and abs(d) < 1e20
            u = [-s * math.sin(math.radians(d)) if ok(s, d) else float("nan") for s, d in zip(sp, dr)]
            v = [-s * math.cos(math.radians(d)) if ok(s, d) else float("nan") for s, d in zip(sp, dr)]
            return (resample(u, shp, box, g), resample(v, shp, box, g),
                    resample(get(p, "wind_speed_of_gust")[1], shp, box, g))
        return _layer(out_dir, "wind", base, nt, times, ref, "MEPS 2.5 km", src, region, log)

    def sea():
        base = THREDDS + "/dodsC/fou-hi/norkystv3_800m_m00_be"
        nt = dim_of(base, "u_eastward", "NorKyst")
        k = 200
        times = [None] * (nt - k) + times_of(base, nt, k, "NorKyst")
        p, _ = dap(base, "forecast_reference_time", "NorKyst run", ttl=600)
        ref = p["forecast_reference_time"][1][0]
        src = Source(base, "lat", "lon", 1148, 2747, "NorKyst")

        def region(r, g, box, ts):
            q = ",".join(_q(v, ts, 0, box) for v in ("u_eastward", "v_northward", "temperature"))
            p, _ = dap(base, q, "NorKyst sea " + r["id"], ttl=6 * 3600, timeout=300)
            sh = get(p, "u_eastward")[0]
            shp = (sh[0], sh[2], sh[3])
            return (resample(get(p, "u_eastward")[1], shp, box, g, scale=0.001, fill=-32767),
                    resample(get(p, "v_northward")[1], shp, box, g, scale=0.001, fill=-32767),
                    resample(get(p, "temperature")[1], shp, box, g, scale=0.001, fill=-32767, offset=10.0))
        return _layer(out_dir, "sea", base, nt, times, ref, "NorKyst v3 800 m", src, region, log)

    def wave():
        # the newest of the Vestlandet domain's four daily run files (the hourly aggregation
        # answers too slowly to be worth its convenience); the domain covers both regions
        best = None
        for hh in ("00", "06", "12", "18"):
            b = THREDDS + "/dodsC/fou-hi/mywavewam800v_curr/MyWave_wam800_curr_c3WAVE%s.nc" % hh
            try:
                p, _ = dap(b, "forecast_reference_time", "WAM800 run", ttl=1800)
            except FetchError:
                continue
            ref = p["forecast_reference_time"][1][0]
            ref = ref * 86400 if ref < 1e6 else ref        # this file counts days since 1970
            if best is None or ref > best[1]:
                best = (b, ref)
        if best is None:
            raise FetchError("WAM800: no run")
        base, ref = best
        data, _ = fetch(base + ".dds", "WAM800 structure", ttl=1800)
        dds = data.decode()
        nt = int(re.search(r"hs\[time = (\d+)\]", dds).group(1))
        wy = int(re.search(r"rlat\[rlat = (\d+)\]", dds).group(1))
        wx = int(re.search(r"rlon\[rlon = (\d+)\]", dds).group(1))
        times = times_of(base, nt, nt, "WAM800")
        src = Source(base, "latitude", "longitude", wy, wx, "WAM800")

        def region(r, g, box, ts):
            q = ",".join(_q(v, ts, None, box) for v in ("hs", "thq", "tp"))
            p, _ = dap(base, q, "WAM800 " + r["id"], ttl=6 * 3600, timeout=300)
            sh = get(p, "hs")[0]
            th = get(p, "thq")[1]
            cx = resample([math.cos(math.radians(d)) if abs(d) < 1e5 else float("nan") for d in th], sh, box, g)
            sx = resample([math.sin(math.radians(d)) if abs(d) < 1e5 else float("nan") for d in th], sh, box, g)
            dirs = [[(math.degrees(math.atan2(s, c)) % 360) if s == s and c == c else float("nan") for s, c in zip(S, C)]
                    for S, C in zip(sx, cx)]
            return (resample(get(p, "hs")[1], sh, box, g, fill=-9999999.0), dirs,
                    resample(get(p, "tp")[1], sh, box, g, fill=-9999999.0))
        return _layer(out_dir, "wave", base, nt, times, ref, "MyWaveWAM 800 m", src, region, log)

    run("wind", wind)
    run("sea", sea)
    run("wave", wave)
    return meta, written


def _byte(v):
    return 0 if v != v else max(1, min(255, int(round(v))))


def encode(layer, a, b, c):
    e = ENC[layer]
    if layer in ("wind", "sea"):
        if a != a or b != b:
            return 0, 0, 0
        r = _byte(a / e["uv_step"] + 128)
        g = _byte(b / e["uv_step"] + 128)
        bb = _byte((c + e.get("b_offset", 0)) / e["b_step"]) if c == c else 0
        return r, g, bb
    if a != a:
        return 0, 0, 0
    return _byte(a / e["r_step"] + 1), _byte(b * e["g_scale"] + 1) if b == b else 0, _byte(c / e["b_step"]) if c == c else 0


def write_layer(out_dir, layer, rid, g, frames):
    """One PNG per CHUNK hours: frame k of the chunk is rows k*ny … k*ny + ny − 1."""
    nx, ny = g["nx"], g["ny"]
    total = 0
    for c0 in range(0, len(frames), CHUNK):
        part = frames[c0:c0 + CHUNK]
        rows = []
        for fr in part:
            for j in range(ny):
                row = bytearray(3 * nx)
                if fr is not None:
                    A, B, C = fr
                    for i in range(nx):
                        k = j * nx + i
                        row[3 * i:3 * i + 3] = bytes(encode(layer, A[k], B[k], C[k]))
                rows.append(row)
        total += write_png_rgb(os.path.join(out_dir, "%s-%s-%d.png" % (layer, rid, c0 // CHUNK)), nx, len(rows), rows)
    return total
