"""Tests for the indicator maths and the field encoding: python3 -m unittest discover fishing/job"""

import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import indicators as ind   # noqa: E402
import fields              # noqa: E402
import common              # noqa: E402

H = 3600


class Angles(unittest.TestCase):
    def test_angle_between(self):
        self.assertEqual(ind.angle_between(10, 350), 20)
        self.assertEqual(ind.angle_between(0, 180), 180)
        self.assertEqual(ind.angle_between(90, 90), 0)
        self.assertEqual(ind.angle_between(-10, 10), 20)

    def test_wind_against_current(self):
        # a north wind blows toward the south; a current setting north runs straight into it
        self.assertEqual(ind.wind_against_current(0, 0), 180)
        # a south wind blows north, with a current setting north
        self.assertEqual(ind.wind_against_current(180, 0), 0)
        self.assertEqual(ind.wind_against_current(270, 0), 90)


class Chop(unittest.TestCase):
    def test_all_three_thresholds(self):
        self.assertTrue(ind.chop(8, 0, 0.5, 0))          # opposed, strong enough
        self.assertFalse(ind.chop(5.9, 0, 0.5, 0))       # wind below 6
        self.assertFalse(ind.chop(8, 0, 0.29, 0))        # current below 0.3
        self.assertFalse(ind.chop(8, 180, 0.5, 0))       # wind with the current
        self.assertTrue(ind.chop(6, 300, 0.3, 0))        # exactly 120 degrees and the minimums

    def test_missing(self):
        self.assertIsNone(ind.chop(None, 0, 0.5, 0))


class Drift(unittest.TestCase):
    def test_current_only(self):
        s, d = ind.drift(0.5, 90, 0, 0)
        self.assertAlmostEqual(s, 0.5)
        self.assertAlmostEqual(d, 90)

    def test_wind_adds_three_percent(self):
        # a west wind (from 270) pushes east at 3 % of 10 m/s = 0.3 m/s; with a 0.4 m/s current north
        s, d = ind.drift(0.4, 0, 10, 270)
        self.assertAlmostEqual(s, 0.5, places=6)
        self.assertAlmostEqual(d, math.degrees(math.atan2(0.3, 0.4)), places=6)

    def test_cancelling(self):
        s, d = ind.drift(0.3, 90, 10, 90)               # east wind blows west at 0.3
        self.assertAlmostEqual(s, 0.0, places=9)
        self.assertIsNone(d)


class Tide(unittest.TestCase):
    EXT = [(0, 20, "low"), (6 * H, 120, "high"), (12 * H + 25 * 60, 18, "low")]

    def test_phase(self):
        p = ind.tide_phase(3 * H, self.EXT)
        self.assertEqual(p["state"], "rising")
        self.assertAlmostEqual(p["pct"], 0.5)
        self.assertFalse(p["slack"])
        self.assertEqual(p["to_turn_min"], 180)

    def test_slack_window(self):
        self.assertTrue(ind.tide_phase(6 * H + 29 * 60, self.EXT)["slack"])
        self.assertFalse(ind.tide_phase(6 * H + 31 * 60, self.EXT)["slack"])
        self.assertEqual(ind.tide_phase(6 * H + 60, self.EXT)["state"], "falling")

    def test_outside(self):
        self.assertIsNone(ind.tide_phase(-60, self.EXT))
        self.assertIsNone(ind.tide_phase(13 * H, self.EXT))

    def test_rate(self):
        times = [i * 600 for i in range(10)]
        levels = [100 + 5 * i for i in range(10)]       # 5 cm per 10 min = 30 cm/h
        self.assertAlmostEqual(ind.rate_cm_per_h(times, levels, 1800), 30.0)
        self.assertIsNone(ind.rate_cm_per_h(times, levels, 0))

    def test_ranges(self):
        self.assertEqual(ind.day_ranges(self.EXT), [100, 102])
        self.assertEqual(ind.range_vs_mean(120, 100), (1.2, "spring"))
        self.assertEqual(ind.range_vs_mean(80, 100), (0.8, "neap"))
        self.assertEqual(ind.range_vs_mean(100, 100), (1.0, "mean"))
        self.assertEqual(ind.range_vs_mean(None, 100), (None, None))


class Pressure(unittest.TestCase):
    def test_trend(self):
        t = [0, H, 2 * H, 3 * H, 9 * H]
        p = [1010, 1009.5, 1009, 1008.5, 1012.5]
        self.assertEqual(ind.pressure_trend(t, p, 3 * H), (-1.5, "falling"))
        self.assertEqual(ind.pressure_trend(t, p, 6 * H), (2.0, "rising"))   # 1010.5 against 1008.5
        self.assertIsNone(ind.pressure_trend(t, p, H)[0])                    # not three hours of it yet

    def test_steady(self):
        self.assertEqual(ind.pressure_trend([0, 3 * H], [1000, 1000.9], 3 * H), (0.9, "steady"))


class Light(unittest.TestCase):
    def test_noon_and_midnight_at_molde(self):
        # 21 June 2026, 11:30 UTC is near solar noon at 7 degrees east; 23:30 UTC is night-ish twilight
        noon = 1782041400
        self.assertGreater(ind.solar_elevation(noon, 62.74, 7.16), 49)
        self.assertEqual(ind.light(noon, 62.74, 7.16), "day")
        # midsummer midnight at 62.7 N: the sun dips about 4 degrees below the horizon, civil twilight
        self.assertEqual(ind.light(noon + 12 * H, 62.74, 7.16), "civil")

    def test_winter_night(self):
        # 21 December 2026, 23:00 UTC at Kopervik is plain night
        self.assertEqual(ind.light(1797894000, 59.28, 5.31), "night")


class Encoding(unittest.TestCase):
    def test_wind_roundtrip(self):
        r, g, b = fields.encode("wind", -3.0, 4.25, 12.5)
        self.assertEqual(((r - 128) * 0.25, (g - 128) * 0.25, b * 0.25), (-3.0, 4.25, 12.5))

    def test_sea_land_and_values(self):
        self.assertEqual(fields.encode("sea", float("nan"), 0, 10), (0, 0, 0))
        r, g, b = fields.encode("sea", 0.5, -0.24, 11.75)
        self.assertAlmostEqual((r - 128) * 0.02, 0.5)
        self.assertAlmostEqual((g - 128) * 0.02, -0.24)
        self.assertAlmostEqual(b * 0.125 - 2, 11.75)

    def test_clamps(self):
        self.assertEqual(fields.encode("wind", 80, -80, 99)[:2], (255, 1))

    def test_wave(self):
        r, g, b = fields.encode("wave", 1.2, 135, 11.2)
        self.assertAlmostEqual((r - 1) * 0.05, 1.2)
        self.assertAlmostEqual((g - 1) / 250 * 360, 135, delta=1.5)
        self.assertAlmostEqual(b * 0.1, 11.2)

    def test_png_is_plain_rgb(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.png")
            common.write_png_rgb(p, 2, 1, [bytearray([1, 2, 3, 4, 5, 6])])
            data = open(p, "rb").read()
            self.assertTrue(data.startswith(b"\x89PNG"))
            self.assertNotIn(b"gAMA", data)
            self.assertNotIn(b"iCCP", data)
            self.assertNotIn(b"sRGB", data)


class Locator(unittest.TestCase):
    def test_regular_grid(self):
        nx, ny = 5, 4
        lat = [60 + 0.1 * j for j in range(ny) for i in range(nx)]
        lon = [5 + 0.2 * i for j in range(ny) for i in range(nx)]
        loc = fields.Locator(lat, lon, nx, ny)
        fi, fj = loc.index(60.15, 5.5)
        self.assertAlmostEqual(fi, 2.5, places=3)
        self.assertAlmostEqual(fj, 1.5, places=3)

    def test_bilinear_mask(self):
        arr = [1.0, float("nan"), 3.0, 5.0]
        v = fields.bilinear(arr, 2, 2, 0.5, 0.5)
        self.assertAlmostEqual(v, 3.0)                   # the three valid corners, equally weighted
        self.assertNotEqual(fields.bilinear([float("nan")] * 3 + [1.0], 2, 2, 0.1, 0.1),
                            fields.bilinear([float("nan")] * 3 + [1.0], 2, 2, 0.1, 0.1))  # NaN


class DapParse(unittest.TestCase):
    def test_grid_and_scalar(self):
        text = ("Dataset {\n} x;\n---------------------------------------------\n"
                "forecast_reference_time, 1.7922924E9\nx[2]\n-1.5, 2.5\n\n"
                "wind_speed.wind_speed[2][1][1][2]\n[0][0][0], 2.25, 3.5\n[1][0][0], 1.0, 2.0\n\n"
                "wind_speed.time[2]\n1.0, 2.0\n")
        p = common.parse_dap_ascii(text)
        self.assertEqual(p["forecast_reference_time"], ([], [1792292400.0]))
        self.assertEqual(common.get(p, "x"), ([2], [-1.5, 2.5]))
        self.assertEqual(common.get(p, "wind_speed"), ([2, 1, 1, 2], [2.25, 3.5, 1.0, 2.0]))


if __name__ == "__main__":
    unittest.main()
