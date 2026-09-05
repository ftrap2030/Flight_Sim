"""The terrain plan view: geometry, orientation, banding and overlays."""

import unittest

from flight_sim import mapview
from flight_sim.game import Session
from flight_sim.terrain import Terrain


class RampTerrain(Terrain):
    """A world whose elevation depends only on easting.

    Makes the map's orientation testable: with a known gradient, the high ground
    must land on a known side of the display. Subclasses the real Terrain and
    overrides only `elevation`, so every scan and naming method still works.
    """

    def __init__(self, feet_per_nm=100.0, seed=0):
        super().__init__(seed=seed)
        self.feet_per_nm = feet_per_nm

    def elevation(self, x_nm, _y_nm):
        return x_nm * self.feet_per_nm


def grid_lines(text):
    """The terrain rows of a rendered map, without the frame or legend.

    Anchored on the header rather than on the dashed frame: a terrain row can
    legitimately consist entirely of '-' glyphs, and would otherwise be mistaken
    for the border.
    """
    lines = text.splitlines()
    header = next(i for i, line in enumerate(lines) if "track-up" in line)
    first_row = header + 2  # skip the header and the top border
    return [line[2:] for line in lines[first_row:first_row + mapview.HEIGHT]]


class TestGeometry(unittest.TestCase):
    def setUp(self):
        self.session = Session.new("a320", "clear", seed=42)

    def test_grid_has_the_declared_dimensions(self):
        rows = grid_lines(mapview.render(self.session.sim, self.session.sim.readout()))
        self.assertEqual(len(rows), mapview.HEIGHT)
        for row in rows:
            self.assertEqual(len(row), mapview.WIDTH)

    def test_aircraft_sits_at_its_declared_cell(self):
        rows = grid_lines(mapview.render(self.session.sim, self.session.sim.readout()))
        self.assertEqual(
            rows[mapview.AIRCRAFT_ROW][mapview.WIDTH // 2], mapview.AIRCRAFT_SYMBOL
        )
        # And exactly once on the whole map.
        self.assertEqual(
            sum(row.count(mapview.AIRCRAFT_SYMBOL) for row in rows), 1
        )

    def test_more_of_the_map_is_ahead_than_behind(self):
        self.assertGreater(mapview.AIRCRAFT_ROW, mapview.HEIGHT - 1 - mapview.AIRCRAFT_ROW)

    def test_render_is_deterministic(self):
        first = mapview.render(self.session.sim, self.session.sim.readout())
        second = mapview.render(self.session.sim, self.session.sim.readout())
        self.assertEqual(first, second)


DANGER_RANK = {".": 0, "-": 1, "~": 2, "^": 3, "#": 4}


def danger(text):
    """Mean severity of the terrain glyphs in a string.

    Comparing severity rather than hunting for one specific glyph: how high the
    ramp climbs inside the display depends on how far the map reaches in that
    direction, so the invariant is which side is *worse*, not which glyph appears.
    """
    scores = [DANGER_RANK[c] for c in text if c in DANGER_RANK]
    return sum(scores) / len(scores) if scores else 0.0


class TestOrientation(unittest.TestCase):
    """Track-up means the nose points up the page, whatever the heading."""

    def setUp(self):
        self.session = Session.new("a320", "clear", seed=42)
        self.session.sim.terrain = RampTerrain()
        state = self.session.sim.state
        state.x_nm = 0.0
        state.y_nm = 0.0
        state.altitude_ft = 0.0

    def rows(self):
        return grid_lines(
            mapview.render(self.session.sim, self.session.sim.readout())
        )

    def ahead_behind(self):
        rows = self.rows()
        return danger(rows[0]), danger(rows[-1])

    def left_right(self):
        middle = self.rows()[mapview.AIRCRAFT_ROW]
        half = mapview.WIDTH // 2
        return danger(middle[:half]), danger(middle[half + 1:])

    def test_flying_east_puts_rising_ground_ahead(self):
        """Elevation rises with easting, so heading east it must rise up-page."""
        self.session.sim.state.heading_deg = 90.0
        ahead, behind = self.ahead_behind()
        self.assertGreater(ahead, behind)

    def test_flying_west_reverses_it(self):
        self.session.sim.state.heading_deg = 270.0
        ahead, behind = self.ahead_behind()
        self.assertLess(ahead, behind)

    def test_flying_north_puts_rising_ground_on_the_right(self):
        """Heading north, east is to starboard -- so the high ground is right."""
        self.session.sim.state.heading_deg = 0.0
        left, right = self.left_right()
        self.assertGreater(right, left)

    def test_flying_south_puts_it_on_the_left(self):
        self.session.sim.state.heading_deg = 180.0
        left, right = self.left_right()
        self.assertGreater(left, right)


class TestBanding(unittest.TestCase):
    def test_glyphs_map_to_the_documented_thresholds(self):
        self.assertEqual(mapview._band_glyph(-5000.0), ".")
        self.assertEqual(mapview._band_glyph(-1500.0), "-")
        self.assertEqual(mapview._band_glyph(-500.0), "~")
        self.assertEqual(mapview._band_glyph(500.0), "^")
        self.assertEqual(mapview._band_glyph(5000.0), mapview.TERRAIN_ABOVE)

    def test_banding_is_relative_to_the_aircraft_not_sea_level(self):
        """Climbing must make the same ground read as less dangerous."""
        session = Session.new("a320", "clear", seed=42)
        session.sim.terrain = RampTerrain()
        session.sim.state.x_nm = 0.0
        session.sim.state.y_nm = 0.0

        session.sim.state.altitude_ft = 0.0
        low = grid_lines(mapview.render(session.sim, session.sim.readout()))
        session.sim.state.altitude_ft = 40000.0
        high = grid_lines(mapview.render(session.sim, session.sim.readout()))

        danger_low = sum(row.count(mapview.TERRAIN_ABOVE) for row in low)
        danger_high = sum(row.count(mapview.TERRAIN_ABOVE) for row in high)
        self.assertGreater(danger_low, 0)
        self.assertEqual(danger_high, 0)


class TestOverlays(unittest.TestCase):
    def setUp(self):
        self.session = Session.new("a320", "clear", seed=42)
        self.state = self.session.sim.state
        self.state.heading_deg = 90.0

    def test_an_overlay_ahead_is_stamped_above_the_aircraft(self):
        # Heading east, so five miles ahead is five miles east.
        overlay = (self.state.x_nm + 5.0, self.state.y_nm, "A")
        rows = grid_lines(
            mapview.render(
                self.session.sim, self.session.sim.readout(), overlays=[overlay]
            )
        )
        positions = [
            (r, c)
            for r, row in enumerate(rows)
            for c, glyph in enumerate(row)
            if glyph == "A"
        ]
        self.assertEqual(len(positions), 1)
        row, col = positions[0]
        self.assertLess(row, mapview.AIRCRAFT_ROW)
        self.assertEqual(col, mapview.WIDTH // 2)

    def test_an_overlay_outside_the_span_is_clipped_not_wrapped(self):
        overlay = (self.state.x_nm + 5000.0, self.state.y_nm, "A")
        rows = grid_lines(
            mapview.render(
                self.session.sim, self.session.sim.readout(), overlays=[overlay]
            )
        )
        self.assertNotIn("A", "".join(rows))


class TestMapCommand(unittest.TestCase):
    def test_map_costs_no_simulation_time_and_returns_a_map(self):
        session = Session.new("a350", "foggy", seed=42)
        before = session.sim.state.elapsed_s
        for phrasing in ("map", "terrain", "nd"):
            output, finished = session.execute(phrasing)
            self.assertFalse(finished)
            self.assertIn("TERRAIN", output)
            self.assertIn(mapview.AIRCRAFT_SYMBOL, output)
        self.assertEqual(session.sim.state.elapsed_s, before)


if __name__ == "__main__":
    unittest.main()
