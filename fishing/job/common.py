"""Shared plumbing for the Fishing Window job: HTTP with a cache, OPeNDAP ASCII, PNG writing.

Standard library only, so the workflow needs no pip install. Nothing here prints a URL: the
spots' coordinates are a repository secret, and the request addresses carry them.
"""

import json
import os
import re
import struct
import time
import urllib.error
import urllib.request
import zlib
import hashlib
import email.utils

USER_AGENT = "FishingWindow/1.0 (personal forecast job; https://github.com/mtomasgaard)"
CACHE_DIR = os.environ.get("FISHING_CACHE", os.path.join(os.path.expanduser("~"), ".fishing-cache"))


class FetchError(Exception):
    """A request failed. The message never carries the address."""


def _cache_paths(key):
    h = hashlib.sha256(key.encode()).hexdigest()[:32]
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, h + ".body"), os.path.join(CACHE_DIR, h + ".meta")


def fetch(url, label, ttl=None, timeout=90, retries=2):
    """GET url with the job's User-Agent, honouring Expires and Last-Modified.

    The body and its headers are kept in CACHE_DIR (restored between runs by actions/cache, never
    committed). A cached body is used without a request until its Expires (or `ttl` seconds) has
    passed; after that the request carries If-Modified-Since, and a 304 reuses the body. `label`
    is what errors and logs say instead of the address.
    Returns (bytes, changed) where changed is False when the cached body was reused.
    """
    body_path, meta_path = _cache_paths(url)
    meta = None
    if os.path.exists(body_path) and os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            meta = None
    now = time.time()
    if meta:
        fresh_until = meta.get("expires") or 0
        if ttl is not None:
            fresh_until = max(fresh_until, meta.get("fetched", 0) + ttl)
        if now < fresh_until:
            with open(body_path, "rb") as f:
                return f.read(), False
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    if meta and meta.get("last_modified"):
        headers["If-Modified-Since"] = meta["last_modified"]
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    data = gzip.decompress(data)
                expires = _http_time(resp.headers.get("Expires"))
                lm = resp.headers.get("Last-Modified")
                with open(body_path, "wb") as f:
                    f.write(data)
                with open(meta_path, "w") as f:
                    json.dump({"expires": expires, "last_modified": lm, "fetched": now}, f)
                return data, True
        except urllib.error.HTTPError as e:
            if e.code == 304 and meta:
                expires = _http_time(e.headers.get("Expires"))
                meta["expires"] = expires or meta.get("expires")
                meta["fetched"] = now
                with open(meta_path, "w") as f:
                    json.dump(meta, f)
                with open(body_path, "rb") as f:
                    return f.read(), False
            last_err = "HTTP %d" % e.code
            if e.code in (400, 403, 404, 422):
                break
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last_err = type(e).__name__
        time.sleep(2 * (attempt + 1))
    raise FetchError("%s: %s" % (label, last_err))


def _http_time(value):
    if not value:
        return None
    try:
        return email.utils.parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None


# ── OPeNDAP ASCII ─────────────────────────────────────────────────────────────

def dap_query(base, query):
    q = query.replace("[", "%5B").replace("]", "%5D")
    return "%s.ascii?%s" % (base, q)


def parse_dap_ascii(text):
    """Every block of an OPeNDAP .ascii response: {header name: (shape, values)}, values a flat list
    of floats in C order. A Grid's array is under "var.var" and its maps under "var.map"; get()
    finds a variable by its own name either way."""
    out = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines) and not lines[i].startswith("----"):
        i += 1
    name, vals = None, None
    for line in lines[i + 1:]:
        line = line.strip()
        if not line:
            continue
        if line[0] == "[":
            vals.extend(float(v) for v in line.split(",")[1:] if v.strip())
            continue
        first = line.split(",")[0]
        if "[" in first and re.match(r"^[A-Za-z_][\w.]*(\[\d+\])+$", first):
            name = first.split("[")[0]
            shape = [int(s) for s in re.findall(r"\[(\d+)\]", first)]
            vals = []
            out[name] = (shape, vals)
            continue
        if re.match(r"^[A-Za-z_][\w.]*$", first):          # a scalar: "name, value"
            out[first] = ([], [float(v) for v in line.split(",")[1:] if v.strip()])
            name = None
            continue
        vals.extend(float(v) for v in line.split(",") if v.strip())   # a 1-D vector's values
    return out


def get(parsed, var):
    """A variable's (shape, values) from parse_dap_ascii, whether plain or a Grid's array."""
    for key in (var + "." + var, var):
        if key in parsed:
            return parsed[key]
    raise KeyError(var)


def dap(base, query, label, ttl=None, timeout=180):
    data, changed = fetch(dap_query(base, query), label, ttl=ttl, timeout=timeout)
    return parse_dap_ascii(data.decode("utf-8", "replace")), changed


# ── PNG ──────────────────────────────────────────────────────────────────────

def write_png_rgb(path, width, height, rows):
    """An 8-bit RGB PNG, no color profile, no gamma chunk, so a canvas reads the bytes back as
    written. `rows` is a list of `height` bytearrays of 3*width bytes."""
    raw = bytearray()
    for r in rows:
        raw.append(0)
        raw.extend(r)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(png)
    return len(png)
