"""Tests for how long the field pictures stay on Pages and for their index:
python3 -m unittest discover -s fishing/job"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common              # noqa: E402
import forecast            # noqa: E402

H = 3600
NOW = forecast.run_epoch("20261021T12")


def entry(layer, run, hours=66):
    """A layer entry as fields.build describes one, two regions."""
    grid = {"nx": 2, "ny": 2, "lon0": 5.0, "lat1": 59.4, "dlon": 0.1, "dlat": 0.1, "first": 0, "last": hours - 1}
    return {"model": "M", "run": run + "Z", "t0": forecast.run_epoch(run), "hours": hours,
            "regions": {"kar": dict(grid), "rom": dict(grid)}, "enc": {},
            "file": "%s-{region}-%s-{chunk}.png" % (layer, run)}


def touch_run(dest, layer, run, hours=66):
    for rid in ("kar", "rom"):
        for c in range((hours + 23) // 24):
            with open(os.path.join(dest, "%s-%s-%s-%d.png" % (layer, rid, run, c)), "wb") as f:
                f.write(b"png")


class Retention(unittest.TestCase):
    def test_two_days_and_the_newest_two(self):
        runs = {"20261018T06", "20261019T09", "20261019T15", "20261020T06", "20261021T06"}
        # 48 h before 21 Oct 12:00 is 19 Oct 12:00: 19 Oct 09 is out, 19 Oct 15 is in
        self.assertEqual(forecast.runs_to_keep(runs, NOW), {"20261019T15", "20261020T06", "20261021T06"})

    def test_old_runs_keep_the_newest_two(self):
        # a job that has not run for days still leaves the two newest runs on Pages
        runs = {"20261010T00", "20261011T00", "20261012T00"}
        self.assertEqual(forecast.runs_to_keep(runs, NOW), {"20261011T00", "20261012T00"})

    def test_place_fields_prunes_by_run(self):
        with tempfile.TemporaryDirectory() as d:
            dest, tmp = os.path.join(d, "fields"), os.path.join(d, "tmp")
            os.makedirs(dest)
            os.makedirs(tmp)
            for run in ("20261018T06", "20261020T06", "20261021T03"):
                touch_run(dest, "wind", run)
            for rid in ("kar", "rom"):
                for c in range(3):
                    with open(os.path.join(tmp, "wind-%s-%d.png" % (rid, c)), "wb") as f:
                        f.write(b"new")
            meta = {"chunk": 24, "regions": [], "layers": {"wind": entry("wind", "20261021T09")}}
            forecast.place_fields(meta, tmp, dest, NOW)
            self.assertEqual(forecast.runs_on_disk(dest), {"wind": {"20261020T06", "20261021T03", "20261021T09"}})
            self.assertEqual(meta["layers"]["wind"]["file"], "wind-{region}-20261021T09-{chunk}.png")


class Index(unittest.TestCase):
    def test_lists_runs_on_disk_newest_first_from_every_source(self):
        with tempfile.TemporaryDirectory() as dest:
            for run in ("20261020T06", "20261021T03", "20261021T09"):
                touch_run(dest, "wind", run)
            touch_run(dest, "sea", "20261021T00", hours=72)
            # the index from last time knows 20 Oct 06; the previous forecast knows 21 Oct 03
            with open(os.path.join(dest, "index.json"), "w") as f:
                json.dump({"layers": {"wind": [entry("wind", "20261020T06"), entry("wind", "20261019T06")]}}, f)
            previous = {"layers": {"wind": entry("wind", "20261021T03")}}
            meta = {"chunk": 24, "regions": [{"id": "kar"}], "layers": {"wind": entry("wind", "20261021T09"),
                                                                       "sea": entry("sea", "20261021T00", hours=72)}}
            index = forecast.write_index(meta, previous, dest)
            self.assertEqual([e["run"] for e in index["layers"]["wind"]], ["20261021T09Z", "20261021T03Z", "20261020T06Z"])
            self.assertEqual([e["run"] for e in index["layers"]["sea"]], ["20261021T00Z"])
            # the PNG carries exactly the JSON
            w, h, raw = common.read_png_rgb(os.path.join(dest, "index.png"))
            payload = common.unpack_bytes(raw)
            with open(os.path.join(dest, "index.json"), "rb") as f:
                self.assertEqual(payload, f.read())
            self.assertEqual(json.loads(payload), index)

    def test_a_run_with_a_file_missing_or_never_described_is_left_out(self):
        with tempfile.TemporaryDirectory() as dest:
            touch_run(dest, "wave", "20261021T00")
            touch_run(dest, "wave", "20261020T18")      # on disk, but no source describes it
            os.remove(os.path.join(dest, "wave-rom-20261021T00-2.png"))
            touch_run(dest, "wave", "20261021T06")
            meta = {"chunk": 24, "regions": [], "layers": {"wave": entry("wave", "20261021T06")}}
            index = forecast.write_index(meta, {"layers": {"wave": entry("wave", "20261021T00")}}, dest)
            self.assertEqual([e["run"] for e in index["layers"]["wave"]], ["20261021T06Z"])

    def test_unchanged_runs_write_identical_files(self):
        with tempfile.TemporaryDirectory() as dest:
            touch_run(dest, "wind", "20261021T09")
            meta = {"chunk": 24, "regions": [], "layers": {"wind": entry("wind", "20261021T09")}}
            forecast.write_index(meta, None, dest)
            with open(os.path.join(dest, "index.png"), "rb") as f:
                first = f.read()
            forecast.write_index(meta, None, dest)
            with open(os.path.join(dest, "index.png"), "rb") as f:
                self.assertEqual(f.read(), first)


class Packed(unittest.TestCase):
    def test_round_trip_and_refusal(self):
        payload = bytes(range(256)) * 3 + b"tail"
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.png")
            common.write_bytes_png(p, payload)
            _, _, raw = common.read_png_rgb(p)
            self.assertEqual(common.unpack_bytes(raw), payload)
            bad = bytearray(raw)
            bad[20] ^= 1
            with self.assertRaises(ValueError):
                common.unpack_bytes(bytes(bad))


if __name__ == "__main__":
    unittest.main()
