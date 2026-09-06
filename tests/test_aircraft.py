"""Fleet data and the performance differences that should emerge from it."""

import unittest

from flight_sim import aircraft as fleet
from flight_sim import atmosphere as atm


class TestFleetData(unittest.TestCase):
    def test_nine_aircraft(self):
        self.assertEqual(len(fleet.FLEET), 9)
        self.assertEqual(
            [a.key for a in fleet.FLEET],
            [
                "a319neo", "a320", "a320neo", "a321", "a321xlr",
                "a330neo", "a350", "a350k", "a380",
            ],
        )

    def test_masses_are_self_consistent(self):
        for craft in fleet.FLEET:
            self.assertLessEqual(
                craft.start_mass_kg, craft.mtow_kg,
                "{} starts above MTOW".format(craft.name),
            )
            self.assertLessEqual(craft.start_fuel_kg, craft.fuel_capacity_kg)
            # Empty plus payload cannot exceed the zero-fuel limit, and the
            # aircraft has to be able to land at its own start weight or the
            # mission is impossible before it begins.
            self.assertLessEqual(
                craft.oew_kg + craft.payload_kg, craft.mzfw_kg,
                "{} exceeds MZFW before fuel".format(craft.name),
            )
            self.assertLess(craft.mlw_kg, craft.mtow_kg)
            self.assertLess(craft.mzfw_kg, craft.mlw_kg)

    def test_published_dimensions_are_present_and_plausible(self):
        for craft in fleet.FLEET:
            self.assertTrue(craft.icao_type, "{} has no type code".format(craft.name))
            self.assertGreater(craft.height_m, 5.0)
            self.assertLess(craft.height_m, craft.length_m)
            self.assertGreater(craft.fuselage_height_m, craft.fuselage_width_m - 0.01)
            self.assertGreater(craft.seats_max, craft.seats_typical)
            self.assertGreater(craft.range_nm, 1000.0)
            self.assertGreater(craft.entry_service, 1980)

    def test_fuel_mass_follows_from_tank_volume(self):
        """Capacity is quoted in litres; the kilograms are derived, not typed."""
        for craft in fleet.FLEET:
            self.assertAlmostEqual(
                craft.fuel_capacity_kg,
                craft.fuel_capacity_l * fleet.JET_A1_KG_PER_L,
                places=6,
            )
        # The XLR's whole point is the rear centre tank.
        self.assertGreater(fleet.A321XLR.fuel_capacity_l, fleet.A321.fuel_capacity_l)
        self.assertGreater(fleet.A321XLR.range_nm, fleet.A321.range_nm)

    def test_resolve_accepts_menu_numbers_and_names(self):
        self.assertIs(fleet.resolve("1"), fleet.A319NEO)
        self.assertIs(fleet.resolve("2"), fleet.A320)
        self.assertIs(fleet.resolve("9"), fleet.A380)
        self.assertIs(fleet.resolve("A320neo"), fleet.A320NEO)
        self.assertIs(fleet.resolve("  a350  "), fleet.A350)
        self.assertIs(fleet.resolve("A380-800"), fleet.A380)
        self.assertIs(fleet.resolve("a320 neo"), fleet.A320NEO)
        self.assertIs(fleet.resolve("A350-1000"), fleet.A350K)
        self.assertIs(fleet.resolve("a35k"), fleet.A350K)
        self.assertIs(fleet.resolve("A321XLR"), fleet.A321XLR)
        self.assertIs(fleet.resolve("A330-900"), fleet.A330NEO)
        self.assertIs(fleet.resolve("A319neo"), fleet.A319NEO)
        self.assertIsNone(fleet.resolve("boeing 737"))
        self.assertIsNone(fleet.resolve(None))

    def test_menu_numbers_track_the_fleet_order(self):
        """The digits on the selection card must select what they point at."""
        for index, craft in enumerate(fleet.FLEET, start=1):
            self.assertIs(fleet.resolve(str(index)), craft)

    def test_a320_and_neo_differ_only_where_they_should(self):
        """Same wing area, more span -- so the neo's aspect ratio is higher."""
        self.assertEqual(fleet.A320.wing_area_m2, fleet.A320NEO.wing_area_m2)
        self.assertGreater(fleet.A320NEO.wing_span_m, fleet.A320.wing_span_m)
        self.assertGreater(fleet.A320NEO.aspect_ratio, fleet.A320.aspect_ratio)
        # Higher aspect ratio must mean lower induced drag for a given CL.
        self.assertLess(
            fleet.A320NEO.induced_drag_factor, fleet.A320.induced_drag_factor
        )
        # And the LEAP must be the more efficient engine.
        self.assertLess(fleet.A320NEO.tsfc, fleet.A320.tsfc)

    def test_a321_is_the_heavy_stretch_on_the_same_wing(self):
        self.assertEqual(fleet.A321.wing_area_m2, fleet.A320.wing_area_m2)
        self.assertGreater(fleet.A321.length_m, fleet.A320.length_m)
        self.assertGreater(fleet.A321.mtow_kg, fleet.A320.mtow_kg)
        self.assertLess(fleet.A321.roll_rate_deg_s, fleet.A320.roll_rate_deg_s)

    def test_roll_rate_falls_as_the_aircraft_gets_heavier(self):
        """Nimbleness must fall with size -- but as a physical claim.

        FLEET is listed by family, not by inertia, so a plain sort no longer
        expresses this: the A330-900 rolls better than the A350-1000 while
        sitting earlier in the list. What has to hold is that sorting by weight
        sorts by roll rate.
        """
        by_weight = sorted(fleet.FLEET, key=lambda a: a.mtow_kg)
        rates = [a.roll_rate_deg_s for a in by_weight]
        self.assertEqual(
            rates, sorted(rates, reverse=True),
            "roll rate does not fall with weight: {}".format(
                [(a.name, a.roll_rate_deg_s) for a in by_weight]
            ),
        )
        # Pitch response follows the same argument.
        pitch = [a.pitch_rate_deg_s for a in by_weight]
        self.assertEqual(pitch, sorted(pitch, reverse=True))

    def test_within_a_family_the_stretch_is_the_less_nimble_one(self):
        for shorter, longer in (
            (fleet.A319NEO, fleet.A320NEO),
            (fleet.A320NEO, fleet.A321),
            (fleet.A321, fleet.A321XLR),
            (fleet.A350, fleet.A350K),
        ):
            self.assertGreaterEqual(shorter.roll_rate_deg_s, longer.roll_rate_deg_s)
            self.assertGreaterEqual(longer.length_m, shorter.length_m)
            self.assertGreater(longer.mtow_kg, shorter.mtow_kg)

    def test_stall_speed_matches_closed_form(self):
        craft = fleet.A320
        mass = 65000.0
        expected = (
            2.0 * mass * atm.G0 / (atm.RHO0 * craft.wing_area_m2 * craft.cl_max_clean)
        ) ** 0.5
        self.assertAlmostEqual(
            craft.stall_speed_ias_ms(mass, 1.0, 0), expected, places=6
        )

    def test_stall_speed_rises_with_mass_load_and_falls_with_flaps(self):
        craft = fleet.A320
        light = craft.stall_speed_ias_ms(60000, 1.0, 0)
        heavy = craft.stall_speed_ias_ms(75000, 1.0, 0)
        banked = craft.stall_speed_ias_ms(60000, 2.0, 0)
        flapped = craft.stall_speed_ias_ms(60000, 1.0, 4)
        self.assertGreater(heavy, light)
        self.assertGreater(banked, light)
        self.assertLess(flapped, light)

    def test_a321_stalls_faster_than_a320_at_operating_weight(self):
        """Same wing, fifteen tonnes more aeroplane: higher wing loading.

        At *equal* mass the two are identical -- they share a wing -- so the
        meaningful comparison is at each type's own typical operating weight.
        """
        a320_vs = fleet.A320.stall_speed_ias_ms(fleet.A320.start_mass_kg)
        a321_vs = fleet.A321.stall_speed_ias_ms(fleet.A321.start_mass_kg)
        self.assertGreater(a321_vs, a320_vs)
        self.assertAlmostEqual(
            fleet.A321.stall_speed_ias_ms(60000), fleet.A320.stall_speed_ias_ms(60000)
        )

    def test_config_drag_increases_with_flaps_gear_and_spoilers(self):
        craft = fleet.A350
        clean = craft.cd_0_for_config(0, False, False)
        self.assertGreater(craft.cd_0_for_config(2, False, False), clean)
        self.assertGreater(craft.cd_0_for_config(0, True, False), clean)
        self.assertGreater(craft.cd_0_for_config(0, False, True), clean)

    def test_flap_setting_is_clamped(self):
        craft = fleet.A320
        self.assertEqual(craft.cl_max_for_flaps(99), craft.cl_max_for_flaps(4))
        self.assertEqual(craft.cl_max_for_flaps(-3), craft.cl_max_for_flaps(0))

    def test_lift_curve_slope_is_physical(self):
        """Finite-wing slope must sit below the 2*pi thin-aerofoil limit."""
        import math

        for craft in fleet.FLEET:
            self.assertLess(craft.cl_alpha, 2.0 * math.pi)
            self.assertGreater(craft.cl_alpha, 3.5)
        # Higher aspect ratio -> steeper lift curve.
        self.assertGreater(fleet.A320NEO.cl_alpha, fleet.A320.cl_alpha)


if __name__ == "__main__":
    unittest.main()
