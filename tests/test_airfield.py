"""Airfields: geometry, procedural placement, the authored region, and grading."""

import math
import unittest

from flight_sim import airfield as af
from flight_sim import mapview
from flight_sim.airfield import Airfield, Airfields
from flight_sim.game import Session
from flight_sim.terrain import Terrain


def runway_profile(terrain, field, samples=15):
    """Elevation along the runway as the aircraft actually meets it."""
    (tx, ty), (ex, ey) = field.runway_ends()
    return [
        terrain.elevation(tx + (ex - tx) * i / (samples - 1),
                          ty + (ey - ty) * i / (samples - 1))
        for i in range(samples)
    ]


class TestGeometry(unittest.TestCase):
    def setUp(self):
        # A 6,076 ft runway (exactly 1 nm) pointing due east, centred on origin.
        self.field = Airfield(
            ident="TEST", name="Test", x_nm=0.0, y_nm=0.0,
            elevation_ft=1000.0, runway_heading_deg=90.0,
            runway_length_ft=af.FT_PER_NM, runway_width_ft=200.0,
        )

    def test_runway_ends_straddle_the_centre(self):
        (tx, ty), (ex, ey) = self.field.runway_ends()
        self.assertAlmostEqual(tx, -0.5, places=6)
        self.assertAlmostEqual(ex, 0.5, places=6)
        self.assertAlmostEqual(ty, 0.0, places=6)
        self.assertAlmostEqual(ey, 0.0, places=6)

    def test_runway_frame_measures_from_the_threshold(self):
        along, across = self.field.runway_frame(-0.5, 0.0)
        self.assertAlmostEqual(along, 0.0, places=3)
        self.assertAlmostEqual(across, 0.0, places=3)

        along, _across = self.field.runway_frame(0.5, 0.0)
        self.assertAlmostEqual(along, af.FT_PER_NM, places=2)

    def test_across_is_positive_to_the_right_of_the_landing_direction(self):
        # Landing east; to the right is south, which is -y.
        _along, across = self.field.runway_frame(0.0, -0.01)
        self.assertGreater(across, 0.0)
        _along, across = self.field.runway_frame(0.0, 0.01)
        self.assertLess(across, 0.0)

    def test_is_over_runway_respects_length_and_width(self):
        self.assertTrue(self.field.is_over_runway(0.0, 0.0))
        self.assertFalse(self.field.is_over_runway(0.6, 0.0), "beyond the far end")
        self.assertFalse(self.field.is_over_runway(-0.6, 0.0), "short of threshold")
        # Half-width is 100 ft; 0.02 nm is 121 ft off the centreline.
        self.assertFalse(self.field.is_over_runway(0.0, 0.02))

    def test_bearing_and_distance(self):
        self.assertAlmostEqual(self.field.distance_nm(3.0, 4.0), 5.0, places=6)
        # Airfield due north of the query point.
        self.assertAlmostEqual(self.field.bearing_from(0.0, -10.0), 0.0, places=3)
        self.assertAlmostEqual(self.field.bearing_from(-10.0, 0.0), 90.0, places=3)


class TestGrading(unittest.TestCase):
    def test_an_ungraded_terrain_is_unchanged(self):
        world = Terrain(seed=42)
        for x, y in [(0, 0), (13.5, -42.25), (100.0, 77.0)]:
            self.assertEqual(world.elevation(x, y), world.natural_elevation(x, y))

    def test_grading_flattens_the_inner_radius(self):
        world = Terrain(seed=42)
        world.add_graded_site(0.0, 0.0, 1234.0, 1.0, 2.0)
        for x in (0.0, 0.3, 0.9):
            self.assertAlmostEqual(world.elevation(x, 0.0), 1234.0, places=6)

    def test_grading_blends_out_to_natural_ground(self):
        world = Terrain(seed=42)
        world.add_graded_site(0.0, 0.0, 1234.0, 1.0, 2.0)
        self.assertAlmostEqual(
            world.elevation(2.5, 0.0), world.natural_elevation(2.5, 0.0), places=6
        )
        # Mid-blend must sit between the two, not jump.
        middle = world.elevation(1.5, 0.0)
        natural = world.natural_elevation(1.5, 0.0)
        self.assertNotAlmostEqual(middle, natural, places=3)
        self.assertTrue(min(1234.0, natural) <= middle <= max(1234.0, natural))

    def test_grading_never_changes_natural_elevation(self):
        world = Terrain(seed=42)
        before = world.natural_elevation(0.5, 0.5)
        world.add_graded_site(0.0, 0.0, 9999.0, 1.0, 2.0)
        self.assertEqual(world.natural_elevation(0.5, 0.5), before)

    def test_adding_the_same_site_twice_is_idempotent(self):
        world = Terrain(seed=42)
        world.add_graded_site(1.0, 2.0, 500.0, 1.0, 2.0)
        world.add_graded_site(1.0, 2.0, 500.0, 1.0, 2.0)
        self.assertEqual(len(world.graded_sites), 1)


class TestProcedural(unittest.TestCase):
    def setUp(self):
        self.world = Terrain(seed=20260905)
        self.source = Airfields(self.world)

    def test_every_runway_is_flat_enough_to_land_on(self):
        """The whole point of grading: no humps in the landing surface."""
        fields = self.source.near(300.0, 120.0, radius_nm=110.0)
        self.assertGreater(len(fields), 3)
        for field in fields:
            profile = runway_profile(self.world, field)
            self.assertLess(
                max(profile) - min(profile), 1.0,
                "{} has a {:.0f} ft hump".format(
                    field.ident, max(profile) - min(profile)
                ),
            )

    def test_placement_is_deterministic(self):
        other = Airfields(Terrain(seed=20260905))
        mine = self.source.near(400.0, 0.0, 90.0)
        theirs = other.near(400.0, 0.0, 90.0)
        self.assertEqual([a.ident for a in mine], [a.ident for a in theirs])
        for a, b in zip(mine, theirs):
            self.assertAlmostEqual(a.x_nm, b.x_nm, places=9)
            self.assertAlmostEqual(a.elevation_ft, b.elevation_ft, places=9)

    def test_different_seeds_place_fields_differently(self):
        other = Airfields(Terrain(seed=7))
        mine = {a.ident for a in self.source.near(400.0, 0.0, 90.0)}
        theirs = {a.ident for a in other.near(400.0, 0.0, 90.0)}
        self.assertNotEqual(mine, theirs)

    def test_results_are_sorted_by_distance(self):
        fields = self.source.near(250.0, 250.0, 150.0)
        distances = [a.distance_nm(250.0, 250.0) for a in fields]
        self.assertEqual(distances, sorted(distances))

    def test_radius_is_respected(self):
        for field in self.source.near(250.0, 250.0, 60.0):
            self.assertLessEqual(field.distance_nm(250.0, 250.0), 60.0)

    def test_procedural_idents_cannot_be_read_as_real_icao_codes(self):
        for field in self.source.procedural.near(500.0, 500.0, 120.0):
            self.assertTrue(field.ident.startswith("X"))
            self.assertEqual(len(field.ident), 4)

    def test_runway_lengths_vary(self):
        lengths = {
            a.runway_length_ft
            for a in self.source.procedural.near(0.0, 0.0, 400.0)
        }
        self.assertGreater(len(lengths), 1)


class TestAuthoredRegion(unittest.TestCase):
    def setUp(self):
        self.world = Terrain(seed=20260905)
        self.source = Airfields(self.world)

    def test_the_home_region_has_its_five_designed_fields(self):
        idents = {a.ident for a in self.source.authored.fields}
        self.assertEqual(idents, {"ANFL", "KEBR", "HRWD", "VSPR", "CROW"})

    def test_authored_fields_sit_inside_the_home_region(self):
        for field in self.source.authored.fields:
            self.assertTrue(
                af.in_home_region(field.x_nm, field.y_nm), field.ident
            )

    def test_authored_fields_carry_their_designed_character(self):
        by_ident = {a.ident: a for a in self.source.authored.fields}
        # The easy one is long, wide and instrument-equipped.
        self.assertGreater(by_ident["ANFL"].runway_length_ft, 12000)
        self.assertTrue(by_ident["ANFL"].has_ils)
        # The hard one is short, narrow and visual only.
        self.assertLess(by_ident["HRWD"].runway_length_ft, 6000)
        self.assertFalse(by_ident["HRWD"].has_ils)
        self.assertLess(by_ident["HRWD"].runway_width_ft, 150.0)
        # The high one is genuinely high.
        self.assertGreater(by_ident["VSPR"].elevation_ft, 2500)
        for field in self.source.authored.fields:
            self.assertTrue(field.note)

    def test_authored_runways_are_also_graded_flat(self):
        for field in self.source.authored.fields:
            profile = runway_profile(self.world, field)
            self.assertLess(max(profile) - min(profile), 1.0, field.ident)

    def test_procedural_fields_are_suppressed_inside_the_home_region(self):
        """Otherwise a generated runway would land on top of an authored one."""
        for field in self.source.near(0.0, 0.0, af.HOME_RADIUS_NM):
            if field.ident.startswith("X"):
                self.assertFalse(
                    af.in_home_region(field.x_nm, field.y_nm), field.ident
                )

    def test_procedural_fields_still_appear_outside_it(self):
        far = self.source.near(400.0, 400.0, 120.0)
        self.assertTrue(any(a.ident.startswith("X") for a in far))
        self.assertFalse(any(a.ident in {"ANFL", "KEBR"} for a in far))

    def test_lookup_by_ident(self):
        self.assertIsNotNone(self.source.by_ident("ANFL"))
        self.assertIsNotNone(self.source.by_ident("  anfl "))
        self.assertIsNone(self.source.by_ident("ZZZZ"))

    def test_nearest_finds_something_from_the_home_region(self):
        nearest = self.source.nearest(0.0, 0.0)
        self.assertIsNotNone(nearest)
        self.assertIn(nearest.ident, {"ANFL", "KEBR", "HRWD", "VSPR", "CROW"})


class TestIntegration(unittest.TestCase):
    def test_a_new_flight_starts_in_the_authored_region(self):
        """Otherwise the hand-designed fields would never be met."""
        for key in ("a320", "a350", "a380"):
            session = Session.new(key, "clear", seed=20260905)
            state = session.sim.state
            self.assertTrue(af.in_home_region(state.x_nm, state.y_nm), key)

    def test_authored_fields_are_within_reach_of_the_start(self):
        session = Session.new("a320neo", "clear", seed=20260905)
        state = session.sim.state
        nearby = session.sim.airfields.near(state.x_nm, state.y_nm, 90.0)
        authored = [a for a in nearby if not a.ident.startswith("X")]
        self.assertGreaterEqual(len(authored), 3)

    def test_sites_are_graded_before_anything_queries_the_terrain(self):
        session = Session.new("a320neo", "clear", seed=20260905)
        self.assertGreater(len(session.sim.terrain.graded_sites), 0)

    def test_the_world_reloads_as_the_aircraft_flies_on(self):
        # A seed of its own: worlds are shared between simulators, so another
        # test having already explored this one would mask the reload.
        session = Session.new("a320neo", "clear", seed=555001)
        before = len(session.sim.terrain.graded_sites)
        session.sim.state.x_nm += 200.0
        session.sim.step_tick()
        self.assertGreater(len(session.sim.terrain.graded_sites), before)

    def test_simulators_on_one_seed_share_a_world(self):
        first = Session.new("a320", "clear", seed=20260905)
        second = Session.new("a380", "stormy", seed=20260905)
        self.assertIs(first.sim.terrain, second.sim.terrain)
        self.assertIs(first.sim.airfields, second.sim.airfields)
        self.assertIsNot(
            first.sim.terrain, Session.new("a320", "clear", seed=99).sim.terrain
        )

    def test_airfields_command_lists_them_without_costing_time(self):
        session = Session.new("a320neo", "clear", seed=20260905)
        before = session.sim.state.elapsed_s
        output, finished = session.execute("airfields")
        self.assertFalse(finished)
        self.assertIn("Kettlebridge", output)
        self.assertIn("Bearing", output)
        self.assertEqual(session.sim.state.elapsed_s, before)

    def test_airfields_appear_on_the_map(self):
        session = Session.new("a320neo", "clear", seed=20260905)
        # Put a field squarely ahead so it must be rendered.
        field = session.sim.airfields.nearest(
            session.sim.state.x_nm, session.sim.state.y_nm
        )
        session.sim.state.heading_deg = field.bearing_from(
            session.sim.state.x_nm, session.sim.state.y_nm
        )
        output = mapview.render(session.sim, session.sim.readout())
        self.assertIn(mapview.AIRFIELD_SYMBOL, output)
        self.assertIn("airfield", output)


if __name__ == "__main__":
    unittest.main()
