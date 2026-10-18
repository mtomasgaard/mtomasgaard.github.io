# fishing/

The data behind **Fishing Window**, a personal mini-app for Snuggery. Not for navigation.

| Path | What | Written by |
| --- | --- | --- |
| `forecast.json` | point forecasts for a few saved spots, by opaque id, and the index of the fields | `.github/workflows/fishing-forecast.yml`, hourly in the day |
| `fields/` | gridded wind, current, sea temperature and waves for two regions, RGB-packed PNG atlases | the same job; each layer keeps its current and previous model run |
| `depth/{z}/{x}/{y}.png` | Kartverket's 50 m depth grid for the same two regions as RGB-packed Web Mercator tiles, z8 to z12 | built once from the app's `tools/build-depth.mjs` |
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
  UTM 33), which Kartverket marks as not for navigation. © Kartverket.

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
