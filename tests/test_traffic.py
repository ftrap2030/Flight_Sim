"""The sky with other aeroplanes in it."""

import unittest

from flight_sim import aircraft as fleet
from flight_sim import physics
from flight_sim import traffic


class TestTakeoffLength(unittest.TestCase):
    """The runway a type needs, which is what keeps the timetable flyable."""

    def test_it_ranks_the_fleet_rather_than_flattening_it(self):
        """The first version gave the whole fleet 3,500 ft to within four
        percent, so every runway accepted every type and an A380 was scheduled
        into a 5,400 ft strip. The spread is the whole point of the number."""
        lengths = [traffic.takeoff_length_ft(craft) for craft in fleet.FLEET]
        self.assertGreater(max(lengths) / min(lengths), 1.4)

    def test_the_biggest_aeroplane_needs_the_most_runway(self):
        a380 = traffic.takeoff_length_ft(fleet.FLEET_BY_KEY["a380"])
        a319 = traffic.takeoff_length_ft(fleet.FLEET_BY_KEY["a319neo"])
        self.assertGreater(a380, a319)

    def test_more_weight_on_the_same_wing_needs_more_runway(self):
        """The A321XLR is the A321neo's wing under nine more tonnes, so it must
        come out longer -- a check on the physics rather than on a table."""
        a321 = fleet.FLEET_BY_KEY["a321"]
        xlr = fleet.FLEET_BY_KEY["a321xlr"]
        self.assertEqual(a321.wing_area_m2, xlr.wing_area_m2)
        self.assertGreater(xlr.start_mass_kg, a321.start_mass_kg)
        self.assertGreater(traffic.takeoff_length_ft(xlr),
                           traffic.takeoff_length_ft(a321))

    def test_a_short_runway_takes_fewer_types_than_a_long_one(self):
        short = traffic.types_for_runway(5400.0)
        long = traffic.types_for_runway(12200.0)
        self.assertLess(len(short), len(long))
        self.assertIn(fleet.FLEET_BY_KEY["a380"], long)
        self.assertNotIn(fleet.FLEET_BY_KEY["a380"], short)

    def test_it_never_returns_nothing(self):
        """A runway too short for anything must still offer the smallest type
        rather than an empty list, or the schedule silently loses a service."""
        self.assertTrue(traffic.types_for_runway(1000.0))


class TestSchedule(unittest.TestCase):

    def setUp(self):
        self.sim = physics.Simulator.new_flight("a320neo", "clear")
        self.profiles = physics.traffic_for_seed(self.sim.state.seed)

    def test_the_timetable_is_a_pure_function_of_the_seed(self):
        _terrain, airfields = physics.world_for_seed(20260905)
        first = traffic.schedule(20260905, airfields)
        second = traffic.schedule(20260905, airfields)
        self.assertEqual([f.callsign for f in first], [f.callsign for f in second])
        self.assertEqual([f.aircraft_key for f in first],
                         [f.aircraft_key for f in second])
        self.assertEqual([f.departure_s for f in first],
                         [f.departure_s for f in second])

    def test_a_different_seed_is_a_different_timetable(self):
        _t1, a1 = physics.world_for_seed(20260905)
        _t2, a2 = physics.world_for_seed(11111111)
        self.assertNotEqual([f.callsign for f in traffic.schedule(20260905, a1)],
                            [f.callsign for f in traffic.schedule(11111111, a2)])

    def test_no_service_is_scheduled_out_of_a_runway_it_cannot_use(self):
        """The reason the type is chosen before the airfields."""
        for profile in self.profiles:
            flight = profile.flight
            craft = fleet.FLEET_BY_KEY[flight.aircraft_key]
            needed = traffic.takeoff_length_ft(craft)
            for field in (flight.origin, flight.destination):
                self.assertGreaterEqual(
                    field.runway_length_ft, needed,
                    "{} scheduled into {} ({:,} ft) needing {:,.0f} ft".format(
                        flight.aircraft_key, field.ident,
                        field.runway_length_ft, needed))

    def test_nothing_flies_to_where_it_started(self):
        for profile in self.profiles:
            self.assertNotEqual(profile.flight.origin.ident,
                                profile.flight.destination.ident)

    def test_the_sky_holds_more_than_one_kind_of_aeroplane(self):
        """Choosing the pair first and the type second gave twelve A319neos out
        of fourteen, because two of the five fields are short."""
        keys = {profile.flight.aircraft_key for profile in self.profiles}
        self.assertGreaterEqual(len(keys), 4)

    def test_the_sky_is_never_empty(self):
        """Fourteen departures scattered at random across an hour left gaps of
        minutes. Evenly spaced with a jitter, there is always somebody up."""
        counts = [len(traffic.sky_at(self.profiles, t))
                  for t in range(0, int(traffic.CYCLE_S), 120)]
        self.assertGreater(min(counts), 0)


class TestPositions(unittest.TestCase):

    def setUp(self):
        self.sim = physics.Simulator.new_flight("a320neo", "clear")
        self.profiles = physics.traffic_for_seed(self.sim.state.seed)

    def test_a_flight_starts_and_ends_at_its_own_airfields(self):
        profile = self.profiles[0]
        start = profile.at(0.0)
        end = profile.at(profile.duration_s)
        self.assertAlmostEqual(start.x_nm, profile.flight.origin.x_nm, places=6)
        self.assertAlmostEqual(start.y_nm, profile.flight.origin.y_nm, places=6)
        self.assertAlmostEqual(start.altitude_ft,
                               profile.flight.origin.elevation_ft, places=6)
        self.assertAlmostEqual(end.x_nm, profile.flight.destination.x_nm, places=3)
        self.assertAlmostEqual(end.altitude_ft,
                               profile.flight.destination.elevation_ft, places=3)

    def test_nothing_is_airborne_before_it_rolls_or_after_it_parks(self):
        profile = self.profiles[0]
        self.assertIsNone(profile.at(-1.0))
        self.assertIsNone(profile.at(profile.duration_s + 1.0))

    def test_it_climbs_then_levels_then_descends(self):
        profile = max(self.profiles, key=lambda p: p.duration_s)
        altitudes = [profile.at(profile.duration_s * f).altitude_ft
                     for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
        self.assertLess(altitudes[0], altitudes[1])
        self.assertLess(altitudes[-1], altitudes[-2])
        self.assertLessEqual(max(altitudes), profile.cruise_ft + 1.0)

    def test_the_sky_is_the_same_sky_every_time_it_is_asked(self):
        """Traffic is evaluated rather than simulated, so asking twice must
        give the same answer -- which is also what lets a flight be resumed
        from disk without the sky jumping."""
        first = traffic.sky_at(self.profiles, 1234.0)
        second = traffic.sky_at(self.profiles, 1234.0)
        self.assertEqual([(c.callsign, c.x_nm, c.y_nm, c.altitude_ft) for c in first],
                         [(c.callsign, c.x_nm, c.y_nm, c.altitude_ft) for c in second])

    def test_the_timetable_repeats_rather_than_running_out(self):
        early = traffic.sky_at(self.profiles, 300.0)
        later = traffic.sky_at(self.profiles, 300.0 + traffic.CYCLE_S)
        self.assertEqual([c.callsign for c in early], [c.callsign for c in later])
        self.assertTrue(early)


class TestTcas(unittest.TestCase):
    """The band is model data, so both front ends call a contact the same."""

    def test_the_bands_tighten_as_a_contact_closes(self):
        self.assertEqual(traffic.band_for(30.0, 0.0), traffic.DISTANT)
        self.assertEqual(traffic.band_for(5.0, 500.0), traffic.PROXIMATE)
        self.assertEqual(traffic.band_for(3.0, 500.0), traffic.TRAFFIC)
        self.assertEqual(traffic.band_for(1.0, 100.0), traffic.THREAT)

    def test_height_matters_as_much_as_range(self):
        """Something directly overhead at a thousand feet is not a conflict,
        and a box that said otherwise would cry wolf on every airway."""
        self.assertEqual(traffic.band_for(0.5, 5000.0), traffic.DISTANT)
        self.assertEqual(traffic.band_for(0.5, 100.0), traffic.THREAT)

    def test_it_is_symmetric_above_and_below(self):
        for height in (200.0, 800.0, 1100.0):
            self.assertEqual(traffic.band_for(2.0, height),
                             traffic.band_for(2.0, -height))


class TestSimulatorIntegration(unittest.TestCase):

    def test_traffic_near_is_sorted_and_bounded(self):
        sim = physics.Simulator.new_flight("a320neo", "clear")
        sim.state.elapsed_s = 900.0
        contacts = sim.traffic_near(radius_nm=60.0)
        self.assertTrue(contacts)
        ranges = [c.range_nm for c in contacts]
        self.assertEqual(ranges, sorted(ranges))
        self.assertLessEqual(max(ranges), 60.0)

    def test_every_contact_is_a_type_the_fleet_knows(self):
        sim = physics.Simulator.new_flight("a320neo", "clear")
        sim.state.elapsed_s = 1500.0
        for contact in sim.traffic_near(radius_nm=200.0):
            self.assertIn(contact.aircraft_key, fleet.FLEET_BY_KEY)


if __name__ == "__main__":
    unittest.main()
