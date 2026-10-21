# fishing/

The data behind **Fishing Window**, a personal mini-app for Snuggery. Not for navigation.

| Path | What | Written by |
| --- | --- | --- |
| `forecast.json` | point forecasts for a few saved spots, by opaque id, and the index of the fields | `.github/workflows/fishing-forecast.yml`, hourly in the day |
| `fields/` | gridded wind, current, sea temperature and waves for two regions, RGB-packed PNG atlases | the same job; every model run of the last 48 hours stays, and at least the newest two of each layer |
| `fields/index.json`, `fields/index.png` | every run whose atlases are all here, newest first; the PNG carries the same bytes for the app, which may show pictures from here but not fetch | the same job, rewritten only when the set of runs changes |
| `depth/{z}/{x}/{y}.png` | Kartverket's 50 m depth grid for the same two regions as RGB-packed Web Mercator tiles, z8 to z12 | built once from the app's `tools/build-depth.mjs` |
| `contours/{10,12}/{x}/{y}.png` | Kartverket's sea-chart depth contours and coastline for the two regions, simplified and packed into PNGs | built once from the app's `tools/build-contours.mjs` |
| `job/` | the job: Python 3, standard library only; `python3 -m unittest discover -s fishing/job` tests the indicator maths | |

The spots' names and coordinates are not here: the job reads `{id, lat, lon}` from the repository
secret `FISHING_SPOTS`, and nothing in this folder names or places a spot.

## Sources and terms

- **MET Norway** (Meteorologisk institutt), CC BY 4.0 (<https://api.met.no/doc/License>):
  Locationforecast 2.0 *complete*, Oceanforecast 2.0 and Sunrise 3.0 from api.met.no, under the
  terms of service (<https://api.met.no/doc/TermsOfService>): the job names itself and a contact in
  its User-Agent, sends at most four decimals, honours `Expires` and `If-Modified-Since`, and runs
  from one place. MEPS 2.5 km (`mepslatest`), NorKyst v3 800 m (`fou-hi/norkystv3_800m_m00_be`) and
  MyWaveWAM 800 m (`fou-hi/mywavewam800v_curr`) from thredds.met.no, subset by OPeNDAP index ranges
  to the two regions' boxes.
- **Kartverket**, CC BY 4.0: the tide API at vannstand.kartverket.no (`locationdata`, 10-minute
  prediction, observation and weather-effect forecast; `tab` for high and low water), and
  "Dybdedata – terrengmodeller 50 m" from Geonorge (Basisdata, cells B0608 and B1412, XYZ, EUREF89
  UTM 33), and the contours (`Dybdekurve`) and coastline (`Kystkontur`) of "Sjøkart – Dybdedata"
  from its WFS (`wfs.geonorge.no/skwms1/wfs.dybdedata`), which Kartverket marks as not for
  navigation. © Kartverket. Grids finer than 50 m are classified inside the territorial limit, so
  none exists openly for these waters.

## forecast.json, schema 1

`generated` (epoch s), `tz`, `params` (the indicator thresholds), `spots[]`, `sun{}`, `fields{}`.

Each spot: `id`, `region`, `updated` (MET's issue times), then arrays aligned with `t` (epoch s;
`step` 1 for hourly, 6 for the 6-hour tail): `wind gust dir p10 p90 pres temp cloud low fog rain`
(Locationforecast), `wave wdir cur cdir sst` (Oceanforecast, nearest hour), and the indicators
`level rate` (tide cm above chart datum, cm/h), `phase` (`r`/`f`) `pct` (share of the way from the
last turn to the next) `slack` (within 30 min of a turn) `turn` (minutes to the next), `chop`
(wind ≥ 6 m/s against a current ≥ 0.3 m/s at ≥ 120°), `drift driftDir` (current plus 3 % of the
wind, an estimate), `trend trendWord` (hPa over 3 h), `light` (`d`ay, `c`ivil twilight, `n`ight).
`days[]` holds each day's tide range against the 30-day mean and the mean sea temperature;
`tide` the 10-minute series and the high and low waters.

`fields.layers.{wind,sea,wave}`: the model and run, `t0` (the run's first hour), `hours`, `file`
(`{region}` and `{chunk}` to fill), `enc` and per region the grid (`nx ny lon0 lat1 dlon dlat`).
`job/fields.py` documents the pixel encodings.

## fields/index.json, schema 1

`chunk`, `regions`, and `layers.{wind,sea,wave}`: a list of entries shaped as `forecast.json`'s
`fields.layers[name]`, newest first, one per run whose atlases are all in `fields/`. `index.png`
holds the same bytes, three to a pixel: `F`, `W`, 1, the length and the CRC-32 (4 bytes each,
big-endian), the JSON, zeros (`job/common.py`, `pack_bytes`). The app reads it when the run its
forecast names has gone, and then draws the newest run instead and says so.
