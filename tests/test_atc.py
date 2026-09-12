"""A controller who expects you to do what you are told."""

import math
import unittest

from flight_sim import atc
from flight_sim import navigation as nav
from flight_sim import physics


def _inbound(key="a320neo", ident="CROW", distance_nm=150.0, altitude_ft=23000.0):
    sim = physics.Simulator.new_flight(key, "clear")
    field = sim.airfields.by_ident(ident)
    state = sim.state
    heading = 215.0
    state.altitude_ft = altitude_ft
    state.x_nm = field.x_nm - math.sin(math.radians(heading)) * distance_nm
    state.y_nm = field.y_nm - math.cos(math.radians(heading)) * distance_nm
    state.heading_deg = heading
    state.tas_ms = sim.profile_tas_ms(altitude_ft)
    state.throttle_pct = sim.throttle_for_level_flight()
    sim.settle_engines()
    sim.route.direct_to(nav.Waypoint.from_airfield(field))
    sim.sync_route()
    return sim, field


class TestSemicircularLevels(unittest.TestCase):
    """Eastbound odd, westbound even -- the oldest rule in the air."""

    def test_eastbound_gets_an_odd_thousand(self):
        for track in (10.0, 90.0, 170.0):
            level = atc.semicircular_level_ft(track, 23400.0)
            self.assertEqual(level % 2000.0, 1000.0,
                             "{}deg gave {}".format(track, level))

    def test_westbound_gets_an_even_thousand(self):
        for track in (190.0, 270.0, 350.0):
            level = atc.semicircular_level_ft(track, 23400.0)
            self.assertEqual(level % 2000.0, 0.0,
                             "{}deg gave {}".format(track, level))

    def test_two_aeroplanes_head_on_are_a_thousand_feet_apart(self):
        """Which is the entire point of the rule."""
        east = atc.semicircular_level_ft(90.0, 23000.0)
        west = atc.semicircular_level_ft(270.0, 23000.0)
        self.assertEqual(abs(east - west), 1000.0)

    def test_it_does_not_apply_down_low(self):
        self.assertEqual(atc.semicircular_level_ft(90.0, 3200.0), 3000.0)
        self.assertEqual(atc.semicircular_level_ft(270.0, 3200.0), 3000.0)

    def test_it_never_returns_something_below_the_floor(self):
        self.assertGreaterEqual(atc.semicircular_level_ft(90.0, 5200.0), 5000.0)


class TestClearances(unittest.TestCase):

    def test_no_route_means_no_controller(self):
        sim = physics.Simulator.new_flight("a320neo", "clear")
        sim.route.clear()
        sim.sync_route()
        atc.update(sim)
        self.assertIsNone(atc.clearance(sim))

    def test_first_contact_assigns_a_legal_level(self):
        sim, _field = _inbound()
        fresh = atc.update(sim)
        self.assertTrue(fresh)
        clearance = atc.clearance(sim)
        self.assertIsNotNone(clearance)
        # Westbound, so an even thousand.
        self.assertEqual(clearance["cleared_altitude_ft"] % 2000.0, 0.0)

    def test_it_says_descend_rather_than_maintain_when_it_means_descend(self):
        """Assigning a level the aeroplane is not at and then calling it a
        deviation is how the first version worked, and it nagged for ever."""
        sim, _field = _inbound(altitude_ft=23000.0)  # westbound: legal is 22,000
        fresh = atc.update(sim)
        self.assertIn("descend to", fresh[0].text)

    def test_being_on_the_way_to_a_level_is_not_a_deviation(self):
        sim, _field = _inbound(altitude_ft=23000.0)
        atc.update(sim)
        self.assertTrue(atc.clearance(sim)["on_clearance"])

    def test_ignoring_an_instruction_is_chased_but_not_for_ever(self):
        """A call that never stops is a call nobody hears."""
        sim, _field = _inbound(altitude_ft=23000.0)
        for _ in range(60):
            atc.update(sim, tick_s=10.0)
            sim.state.elapsed_s += 10.0
        chases = [m for m in atc.messages(sim, limit=40)
                  if m.urgent and m.kind == atc.LEVEL]
        self.assertTrue(chases)
        self.assertLessEqual(len(chases), atc.MAX_CHASES)

    def test_time_off_the_clearance_is_counted_even_when_nothing_is_said(self):
        sim, _field = _inbound(altitude_ft=23000.0)
        for _ in range(60):
            atc.update(sim, tick_s=10.0)
            sim.state.elapsed_s += 10.0
        self.assertGreater(sim.state.atc_deviation_s, atc.MAX_CHASES * 45.0)

    def test_descent_is_withheld_until_it_is_given(self):
        sim, _field = _inbound(distance_nm=250.0)
        atc.update(sim)
        self.assertFalse(atc.clearance(sim)["descent_cleared"])

    def test_descent_is_cleared_before_the_profile_needs_it(self):
        sim, _field = _inbound(distance_nm=150.0)
        for _ in range(200):
            sim.step_tick()
            if sim.state.atc_descent_cleared:
                break
        guidance = sim.descent_guidance()
        self.assertTrue(sim.state.atc_descent_cleared)
        # Cleared while there was still room to start down on the profile.
        self.assertGreaterEqual(
            guidance.distance_to_go_nm, guidance.top_of_descent_nm - 1.0)

    def test_once_cleared_to_descend_the_level_is_a_floor_not_a_target(self):
        """An aeroplane correctly flying its profile was permanently reported
        as more than a thousand feet above its cleared level."""
        sim, _field = _inbound(distance_nm=60.0, altitude_ft=22000.0)
        sim.state.atc_cleared_altitude_ft = 3000.0
        sim.state.atc_descent_cleared = True
        sim.state.atc_level_reached = True
        clearance = atc.clearance(sim)
        self.assertGreater(clearance["deviation_ft"], 1000.0)
        self.assertTrue(clearance["on_clearance"])

    def test_below_a_descent_clearance_is_still_a_deviation(self):
        sim, _field = _inbound(distance_nm=60.0, altitude_ft=1000.0)
        sim.state.atc_cleared_altitude_ft = 3000.0
        sim.state.atc_descent_cleared = True
        self.assertFalse(atc.clearance(sim)["on_clearance"])


class TestSequencing(unittest.TestCase):

    def test_far_out_you_are_number_one(self):
        sim, _field = _inbound(distance_nm=200.0)
        position, ahead = atc.inbound_sequence(sim, "CROW")
        self.assertEqual(position, 1)
        self.assertIsNone(ahead)

    def test_the_sequence_counts_real_traffic_going_to_the_same_field(self):
        """'Number two' has to be an aeroplane you could look at."""
        sim, field = _inbound(distance_nm=20.0)
        sim.state.elapsed_s = 1010.0
        position, ahead = atc.inbound_sequence(sim, "CROW")
        if position > 1:
            self.assertIsNotNone(ahead)
            self.assertEqual(ahead.destination, "CROW")
            mine = math.hypot(field.x_nm - sim.state.x_nm,
                              field.y_nm - sim.state.y_nm)
            theirs = math.hypot(field.x_nm - ahead.x_nm, field.y_nm - ahead.y_nm)
            self.assertLess(theirs, mine)

    def test_nobody_is_sequenced_against_a_different_destination(self):
        sim, _field = _inbound(distance_nm=20.0)
        sim.state.elapsed_s = 1010.0
        _position, ahead = atc.inbound_sequence(sim, "CROW")
        if ahead is not None:
            self.assertEqual(ahead.destination, "CROW")


class TestMessages(unittest.TestCase):

    def test_the_same_call_is_not_repeated_every_tick(self):
        sim, _field = _inbound()
        atc.update(sim)
        first = len(sim.state.atc_messages)
        for _ in range(5):
            atc.update(sim)
        self.assertEqual(len(sim.state.atc_messages), first)

    def test_messages_survive_a_round_trip_through_a_dict(self):
        message = atc.Message(atc.LEVEL, "hello", 12.5, urgent=True)
        again = atc.Message.from_dict(message.to_dict())
        self.assertEqual((again.kind, again.text, again.elapsed_s, again.urgent),
                         (atc.LEVEL, "hello", 12.5, True))

    def test_the_log_does_not_grow_without_end(self):
        sim, _field = _inbound()
        for tick in range(400):
            sim.state.elapsed_s += 60.0
            atc.update(sim, tick_s=60.0)
        self.assertLessEqual(len(sim.state.atc_messages), 40)


class TestDebrief(unittest.TestCase):

    def test_a_flight_with_no_controller_has_no_clearance_row(self):
        sim = physics.Simulator.new_flight("a320neo", "clear")
        sim.route.clear()
        sim.sync_route()
        keys = [row.key for row in nav.debrief_data(sim).rows]
        self.assertNotIn("atc_deviation", keys)

    def test_a_flight_under_control_is_graded_on_its_clearance(self):
        sim, _field = _inbound()
        atc.update(sim)
        keys = [row.key for row in nav.debrief_data(sim).rows]
        self.assertIn("atc_deviation", keys)
        # Last, because `parity_check` compares the rows in order and the
        # browser puts it last too.
        self.assertEqual(keys[-1], "atc_deviation")


if __name__ == "__main__":
    unittest.main()
