"""Routes, guidance to a destination, and the end-of-flight debrief."""

import math
import os
import tempfile
import unittest

from flight_sim import commands as cmd
from flight_sim import dashboard
from flight_sim import mapview
from flight_sim import navigation
from flight_sim import physics
from flight_sim.game import Session
from flight_sim.navigation import Route, Waypoint

SEED = 20260905


def session_with_route(ident="ANFL"):
    session = Session.new("a320neo", "clear", seed=SEED)
    session.execute("direct to {}".format(ident))
    return session


class TestWaypoint(unittest.TestCase):
    def test_distance_and_bearing(self):
        waypoint = Waypoint("North", 0.0, 10.0)
        self.assertAlmostEqual(waypoint.distance_nm(0.0, 0.0), 10.0, places=6)
        self.assertAlmostEqual(waypoint.bearing_from(0.0, 0.0), 0.0, places=6)
        east = Waypoint("East", 10.0, 0.0)
        self.assertAlmostEqual(east.bearing_from(0.0, 0.0), 90.0, places=6)

    def test_round_trip_through_a_dict(self):
        waypoint = Waypoint("Test", 1.5, -2.5, ident="TEST", is_airfield=True)
        restored = Waypoint.from_dict(waypoint.to_dict())
        self.assertEqual(restored, waypoint)

    def test_built_from_an_airfield(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        field = session.sim.airfields.by_ident("KEBR")
        waypoint = Waypoint.from_airfield(field)
        self.assertEqual(waypoint.ident, "KEBR")
        self.assertTrue(waypoint.is_airfield)
        self.assertAlmostEqual(waypoint.x_nm, field.x_nm)


class TestRoute(unittest.TestCase):
    def test_an_empty_route_has_no_active_waypoint(self):
        route = Route()
        self.assertIsNone(route.active_waypoint)
        self.assertIsNone(route.destination)

    def test_direct_to_replaces_the_route(self):
        route = Route([Waypoint("A", 0, 0), Waypoint("B", 1, 1)])
        route.active = 1
        route.direct_to(Waypoint("C", 5, 5))
        self.assertEqual(len(route.waypoints), 1)
        self.assertEqual(route.active, 0)
        self.assertEqual(route.active_waypoint.name, "C")

    def test_advancing_steps_through_the_route(self):
        route = Route([Waypoint("A", 0.0, 0.0), Waypoint("B", 50.0, 0.0)])
        self.assertFalse(route.advance_if_reached(20.0, 0.0), "not there yet")
        self.assertTrue(route.advance_if_reached(0.5, 0.0))
        self.assertEqual(route.active_waypoint.name, "B")

    def test_it_never_advances_past_the_destination(self):
        """Arriving is the point; the guidance must keep pointing at it."""
        route = Route([Waypoint("A", 0.0, 0.0)])
        self.assertFalse(route.advance_if_reached(0.0, 0.0))
        self.assertEqual(route.active_waypoint.name, "A")
        self.assertFalse(route.finished)

    def test_clear_empties_it(self):
        route = Route([Waypoint("A", 0, 0)])
        route.clear()
        self.assertIsNone(route.active_waypoint)

    def test_round_trip_through_a_dict(self):
        route = Route([Waypoint("A", 1, 2), Waypoint("B", 3, 4)], active=1)
        restored = Route.from_dict(route.to_dict())
        self.assertEqual(restored.active, 1)
        self.assertEqual([w.name for w in restored.waypoints], ["A", "B"])
        self.assertEqual(Route.from_dict(None).waypoints, [])


class TestLegGuidance(unittest.TestCase):
    def test_no_leg_without_a_route(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        self.assertIsNone(session.sim.readout().leg)

    def test_a_leg_reports_distance_bearing_and_eta(self):
        session = session_with_route("ANFL")
        leg = session.sim.readout().leg
        self.assertIsNotNone(leg)
        self.assertEqual(leg.waypoint.ident, "ANFL")
        self.assertGreater(leg.distance_nm, 1.0)
        self.assertGreater(leg.eta_s, 0.0)
        self.assertIn(":", leg.eta_text())

    def test_relative_bearing_is_signed_left_and_right(self):
        session = session_with_route("ANFL")
        state = session.sim.state
        leg = session.sim.readout().leg
        state.heading_deg = (leg.bearing_deg - 40.0) % 360.0
        self.assertGreater(session.sim.readout().leg.relative_bearing_deg, 0.0)
        state.heading_deg = (leg.bearing_deg + 40.0) % 360.0
        self.assertLess(session.sim.readout().leg.relative_bearing_deg, 0.0)

    def test_fuel_on_arrival_falls_as_the_burn_rises(self):
        session = session_with_route("ANFL")
        session.sim.state.throttle_pct = 30.0
        economical = session.sim.readout().leg.fuel_on_arrival_kg
        session.sim.state.throttle_pct = 100.0
        thirsty = session.sim.readout().leg.fuel_on_arrival_kg
        self.assertLess(thirsty, economical)

    def test_an_unreachable_destination_is_flagged(self):
        session = session_with_route("ANFL")
        session.sim.state.fuel_kg = 30.0
        leg = session.sim.readout().leg
        self.assertFalse(leg.reachable)
        self.assertLess(leg.fuel_on_arrival_kg, 0.0)

    def test_the_route_advances_in_flight(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        state = session.sim.state
        session.sim.route = Route([
            Waypoint("close", state.x_nm + 0.2, state.y_nm),
            Waypoint("far", state.x_nm + 60.0, state.y_nm),
        ])
        session.sim.step_tick()
        self.assertEqual(session.sim.route.active_waypoint.name, "far")
        self.assertEqual(session.sim.state.route["active"], 1)


class TestCommands(unittest.TestCase):
    def test_direct_to_parses_without_swallowing_the_word_to(self):
        """Regex alternation is ordered: a bare `direct` matched first."""
        for text in ("direct to KEBR", "direct KEBR", "fly to KEBR",
                     "divert to KEBR", "proceed to KEBR"):
            command = cmd.parse(text)
            self.assertEqual(command.kind, "direct_to", text)
            self.assertEqual(command.target, "kebr", text)

    def test_navigation_commands_cost_no_time(self):
        session = session_with_route()
        before = session.sim.state.elapsed_s
        for text in ("show plan", "direct to KEBR", "debrief", "clear route"):
            _output, finished = session.execute(text)
            self.assertFalse(finished)
        self.assertEqual(session.sim.state.elapsed_s, before)

    def test_setting_a_destination_by_ident_and_by_name(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        output, _f = session.execute("direct to KEBR")
        self.assertIn("Kettlebridge", output)
        self.assertEqual(session.sim.route.destination.ident, "KEBR")

        session.execute("direct to kettlebridge")
        self.assertEqual(session.sim.route.destination.ident, "KEBR")

    def test_an_unknown_destination_is_refused_helpfully(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        output, _f = session.execute("direct to ZZZZ")
        self.assertIn("No airfield", output)
        self.assertIsNone(session.sim.route.active_waypoint)

    def test_show_plan_before_and_after_setting_one(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        self.assertIn("No route set", session.execute("show plan")[0])
        session.execute("direct to ANFL")
        output, _f = session.execute("show plan")
        self.assertIn("ANFL", output)
        self.assertIn("bearing", output)

    def test_clear_route_removes_the_destination(self):
        session = session_with_route()
        session.execute("clear route")
        self.assertIsNone(session.sim.route.active_waypoint)
        self.assertIsNone(session.sim.readout().leg)

    def test_setting_a_destination_warns_when_the_fuel_will_not_reach(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.sim.state.fuel_kg = 25.0
        output, _f = session.execute("direct to ANFL")
        self.assertIn("do not have the fuel", output)


class TestFlightRecord(unittest.TestCase):
    def test_the_record_accumulates_as_the_flight_goes(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        for _ in range(6):
            session.sim.step_tick()
        state = session.sim.state
        self.assertGreater(state.distance_flown_nm, 1.0)
        self.assertGreaterEqual(state.max_altitude_ft, 4900.0)
        self.assertGreater(state.max_ias_kt, 100.0)
        self.assertLess(state.min_agl_ft, 1e8)
        self.assertGreaterEqual(state.max_load_factor, 1.0)

    def test_distance_flown_tracks_the_ground_track(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        start = (session.sim.state.x_nm, session.sim.state.y_nm)
        for _ in range(6):
            session.sim.step_tick()
        straight = math.hypot(
            session.sim.state.x_nm - start[0], session.sim.state.y_nm - start[1]
        )
        # Flown in a straight line, so path length and displacement agree.
        self.assertAlmostEqual(
            session.sim.state.distance_flown_nm, straight, delta=0.2
        )

    def test_warnings_are_remembered_once_each(self):
        session = Session.new("a320", "clear", seed=SEED)
        session.execute("idle")
        session.execute("pitch up 16")
        for _ in range(8):
            session.sim.step_tick()
            if session.sim.state.status != physics.FLYING:
                break
        seen = session.sim.state.warnings_seen
        self.assertTrue(seen)
        self.assertEqual(len(seen), len(set(seen)), "duplicated warnings")

    def test_maxima_never_go_backwards(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.execute("full power")
        session.execute("set pitch 8")
        for _ in range(10):
            session.sim.step_tick()
        peak = session.sim.state.max_altitude_ft
        session.execute("set pitch -8")
        for _ in range(10):
            session.sim.step_tick()
            if session.sim.state.status != physics.FLYING:
                break
        self.assertGreaterEqual(session.sim.state.max_altitude_ft, peak)


class TestDebrief(unittest.TestCase):
    def test_it_summarises_the_flight(self):
        session = session_with_route()
        for _ in range(6):
            session.sim.step_tick()
        text = navigation.debrief(session.sim)
        self.assertIn("Debrief", text)
        self.assertIn("A320neo", text)
        self.assertIn("Distance flown", text)
        self.assertIn("Fuel burned", text)
        self.assertIn("Closest to the ground", text)

    def test_it_names_the_outcome(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        for status, expected in [
            (physics.LANDED, "Landed"),
            (physics.OVERRUN, "Ran off the end"),
            (physics.CRASHED_TERRAIN, "Destroyed"),
            (physics.STRUCTURAL_FAILURE, "Broke up"),
        ]:
            session.sim.state.status = status
            self.assertIn(expected, navigation.debrief(session.sim))

    def test_it_includes_the_touchdown_when_there_was_one(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.sim.state.status = physics.LANDED
        session.sim.state.touchdown = {
            "grade": "greaser", "survivable": True, "sink_rate_fpm": 44.0,
            "ias_kt": 140.0, "speed_ratio": 0.98, "centreline_ft": 8.0,
            "crab_deg": 1.0, "remaining_ft": 5100.0,
            "field_name": "Anfell International", "reason": "",
        }
        text = navigation.debrief(session.sim)
        self.assertIn("greaser", text)
        self.assertIn("Touchdown sink rate", text)
        self.assertIn("44 fpm", text)

    def test_the_clock_never_reads_sixty_seconds(self):
        """119.9999 s split independently reads as 1 min 60 s."""
        session = Session.new("a320neo", "clear", seed=SEED)
        for elapsed in (119.9999, 60.0, 179.9999, 0.0):
            session.sim.state.elapsed_s = elapsed
            self.assertNotIn("60 s", navigation.debrief(session.sim))

    def test_a_clean_flight_says_so(self):
        session = Session.new("a350", "clear", seed=SEED)
        session.sim.state.warnings_seen = []
        self.assertIn("No warnings raised", navigation.debrief(session.sim))

    def test_the_debrief_is_appended_to_any_ending(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        output, finished = session.execute("quit")
        self.assertTrue(finished)
        self.assertIn("Debrief", output)
        self.assertIn("Distance flown", output)


class TestDisplay(unittest.TestCase):
    def test_the_panel_shows_the_navigation_block(self):
        session = session_with_route("ANFL")
        text = dashboard.render(session.sim, session.sim.readout())
        self.assertIn("NAV — ANFL", text)
        self.assertIn("on arrival", text)

    def test_the_panel_omits_it_without_a_route(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        self.assertNotIn(
            "NAV —", dashboard.render(session.sim, session.sim.readout())
        )

    def test_the_panel_warns_when_the_fuel_will_not_reach(self):
        session = session_with_route("ANFL")
        session.sim.state.fuel_kg = 20.0
        text = dashboard.render(session.sim, session.sim.readout())
        self.assertIn("WILL NOT REACH", text)

    def test_the_active_waypoint_is_marked_on_the_map(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        state = session.sim.state
        session.sim.route = Route([
            Waypoint("ahead", state.x_nm + 3.0, state.y_nm)
        ])
        state.heading_deg = 90.0
        text = mapview.render(session.sim, session.sim.readout())
        self.assertIn(mapview.WAYPOINT_SYMBOL, text)
        self.assertIn("waypoint", text)


class TestPersistence(unittest.TestCase):
    def test_route_and_record_survive_a_save_and_load(self):
        path = os.path.join(tempfile.mkdtemp(), "nav.json")
        session = session_with_route("ANFL")
        for _ in range(4):
            session.sim.step_tick()
        session.save(path)

        restored = Session.load(path)
        self.assertEqual(restored.sim.route.destination.ident, "ANFL")
        self.assertAlmostEqual(
            restored.sim.state.distance_flown_nm,
            session.sim.state.distance_flown_nm,
            places=9,
        )
        self.assertEqual(
            restored.sim.state.warnings_seen, session.sim.state.warnings_seen
        )
        self.assertAlmostEqual(
            restored.sim.readout().leg.distance_nm,
            session.sim.readout().leg.distance_nm,
            places=6,
        )


if __name__ == "__main__":
    unittest.main()
