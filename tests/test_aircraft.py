"""Fleet data and the performance differences that should emerge from it."""

import unittest

from flight_sim import aircraft as fleet
from flight_sim import atmosphere as atm


class TestFleetData(unittest.TestCase):
    def test_the_fleet_is_what_it_says_it_is(self):
        self.assertEqual(len(fleet.FLEET), 11)
        self.assertEqual(
            [a.key for a in fleet.FLEET],
            [
                "a319neo", "a320", "a320neo", "a321", "a321xlr",
                "a330-800", "a330neo", "a350", "a350k", "a380", "belugaxl",
            ],
        )
        # Ten airliners and one freighter. The fleet stopped being all
        # airliners with the Beluga, and several of these tests had quietly
        # assumed it was.
        self.assertEqual(sum(1 for a in fleet.FLEET if a.carries_passengers), 10)

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

    def test_every_type_publishes_its_fan(self):
        for craft in fleet.FLEET:
            self.assertGreater(craft.fan_diameter_m, 1.0, craft.name)
            self.assertLess(craft.fan_diameter_m, 3.5, craft.name)
            self.assertGreaterEqual(craft.fan_blades, 16, craft.name)
            self.assertLessEqual(craft.fan_blades, 40, craft.name)

    def test_a_bigger_fan_turns_more_slowly(self):
        """The physical fact the whole derivation rests on.

        A three-metre fan cannot turn as fast as a 1.7-metre one without its
        tips going supersonic, so shaft speed must fall as diameter rises --
        across the whole fleet, with no exceptions, or the constant is wrong.
        """
        by_size = sorted(fleet.FLEET, key=lambda c: c.fan_diameter_m)
        speeds = [c.fan_rpm_100 for c in by_size]
        self.assertEqual(speeds, sorted(speeds, reverse=True))

    def test_the_derived_shaft_speeds_are_real_engine_speeds(self):
        """The check that the published diameters are not nonsense.

        Nothing in the derivation knows what a turbofan's shaft speed is; it
        comes out of one tip-speed constant and a diameter. If a diameter were
        mistyped the speed would leave the band real engines run in, which is
        roughly 2,500 rpm for the largest fans and 5,000 for the smallest.
        """
        for craft in fleet.FLEET:
            self.assertTrue(
                2500.0 < craft.fan_rpm_100 < 5200.0,
                "{}: {:,.0f} rpm at 100% N1".format(craft.name, craft.fan_rpm_100),
            )

    def test_the_fan_sounds_like_a_turbofan_and_not_a_propeller(self):
        """A propeller's blade-passing tone is a hundred-odd hertz.

        A turbofan's is a couple of kilohertz, and that one fact is most of
        what makes them sound like different machines. The browser's audio is
        built on this, so it is asserted here rather than left to the ear.
        """
        for craft in fleet.FLEET:
            tone = craft.fan_tone_hz(1.0)
            self.assertTrue(
                900.0 < tone < 3200.0,
                "{}: {:,.0f} Hz at full power".format(craft.name, tone),
            )
            # And at idle it must still be well clear of a propeller's range.
            self.assertGreater(craft.fan_tone_hz(0.22), 200.0, craft.name)

    def test_the_two_a320s_sound_completely_different(self):
        """Same airframe, same wing area, and the one difference you can hear.

        The ceo's CFM56 has thirty-six narrow blades on a 1.7 m fan; the neo's
        LEAP has eighteen wide ones on a 2 m fan. The ceo screams at nearly
        three kilohertz and the neo does not, which is exactly why the neo is
        the quieter aeroplane on approach.
        """
        ceo, neo = fleet.A320, fleet.A320NEO
        self.assertEqual(ceo.wing_area_m2, neo.wing_area_m2)
        self.assertGreater(ceo.fan_tone_hz(1.0), 2.0 * neo.fan_tone_hz(1.0))
        self.assertGreater(neo.fan_diameter_m, ceo.fan_diameter_m)
        self.assertLess(neo.fan_blades, ceo.fan_blades)

    def test_the_fan_tone_is_linear_in_n1_and_zero_when_stopped(self):
        craft = fleet.A350
        self.assertEqual(craft.fan_tone_hz(0.0), 0.0)
        self.assertAlmostEqual(craft.fan_tone_hz(0.5) * 2.0, craft.fan_tone_hz(1.0))
        self.assertEqual(craft.fan_tone_hz(-0.3), 0.0)

    def test_published_dimensions_are_present_and_plausible(self):
        for craft in fleet.FLEET:
            self.assertTrue(craft.icao_type, "{} has no type code".format(craft.name))
            self.assertGreater(craft.height_m, 5.0)
            self.assertLess(craft.height_m, craft.length_m)
            self.assertGreater(craft.fuselage_height_m, craft.fuselage_width_m - 0.01)
            self.assertGreater(craft.range_nm, 1000.0)
            self.assertGreater(craft.entry_service, 1980)
            # An airliner is published by its seats and a freighter by its hold.
            # This was `seats_max > seats_typical` for every type, unqualified,
            # for as long as every type carried passengers.
            if craft.carries_passengers:
                self.assertGreater(craft.seats_max, craft.seats_typical)
                self.assertEqual(craft.hold_volume_m3, 0.0)
            else:
                self.assertEqual(craft.seats_typical, 0)
                self.assertGreater(
                    craft.hold_volume_m3, 0.0,
                    "{} carries neither passengers nor freight".format(craft.name),
                )

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
        """Names, not digits -- `test_menu_numbers_track_the_fleet_order` owns
        those, and owns them structurally. A digit written down here is a third
        copy of the fleet order and goes stale the moment a type is inserted,
        which is what `resolve("9")` did when the A330-800 went in."""
        self.assertIs(fleet.resolve("A320neo"), fleet.A320NEO)
        self.assertIs(fleet.resolve("  a350  "), fleet.A350)
        self.assertIs(fleet.resolve("A380-800"), fleet.A380)
        self.assertIs(fleet.resolve("a320 neo"), fleet.A320NEO)
        self.assertIs(fleet.resolve("A350-1000"), fleet.A350K)
        self.assertIs(fleet.resolve("a35k"), fleet.A350K)
        self.assertIs(fleet.resolve("A321XLR"), fleet.A321XLR)
        self.assertIs(fleet.resolve("A330-900"), fleet.A330NEO)
        self.assertIs(fleet.resolve("A330-800"), fleet.A330_800)
        self.assertIs(fleet.resolve("a338"), fleet.A330_800)
        # Bare "a330" is the -900 deliberately: an ambiguous alias that quietly
        # picks the rarer of two is worse than no alias.
        self.assertIs(fleet.resolve("a330"), fleet.A330NEO)
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

        **Among airliners.** Weight is a proxy for roll inertia only while every
        type puts its mass in the same place, and the Beluga does not: its load
        rides in a lobe well above the roll axis, so it is the one type here
        that rolls worse than its weight predicts. That is a property of the
        aeroplane, not a hole in the claim, and it is asserted on its own below.
        """
        by_weight = sorted(
            [a for a in fleet.FLEET if a.carries_passengers],
            key=lambda a: a.mtow_kg,
        )
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

    def test_the_freighter_is_less_nimble_than_its_weight_predicts(self):
        """The deliberate exception, and the reason for it.

        Fifty-one tonnes in a lobe above the wing is a roll inertia no airliner
        of the same weight carries, and a side area forward of the fin that no
        airliner has -- so it both rolls and settles in yaw more slowly than
        anything its size.
        """
        beluga, donor = fleet.BELUGA_XL, fleet.A330_800
        # Against the aeroplane it is built from, which is the sharp comparison:
        # same wing area, twenty-four tonnes *heavier*, and it still rolls and
        # settles better. Nothing about the weight explains that; the lobe does.
        self.assertGreater(donor.mtow_kg, beluga.mtow_kg)
        self.assertGreater(donor.roll_rate_deg_s, beluga.roll_rate_deg_s)
        self.assertGreater(donor.pitch_rate_deg_s, beluga.pitch_rate_deg_s)
        self.assertLess(donor.yaw_tau_s, beluga.yaw_tau_s)
        self.assertEqual(donor.wing_area_m2, beluga.wing_area_m2)
        # And it gave up span to do it: the ceo wing, no sharklets.
        self.assertLess(beluga.wing_span_m, donor.wing_span_m)
        self.assertLess(beluga.aspect_ratio, donor.aspect_ratio)

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
