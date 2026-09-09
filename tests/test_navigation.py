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
        # Settled either side, because the question is what each *setting*
        # costs. Read straight after moving the levers, both would report the
        # burn of the fan speed the engines still happen to be at.
        session.sim.state.throttle_pct = 30.0
        session.sim.settle_engines()
        economical = session.sim.readout().leg.fuel_on_arrival_kg
        session.sim.state.throttle_pct = 100.0
        session.sim.settle_engines()
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
        # The plan quotes what it will cost, which is the point of having one.
        self.assertIn("Block fuel", output)
        self.assertIn("reserve", output)
        self.assertIn("Cruise **FL", output)

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


class TestMultiLegRoutes(unittest.TestCase):
    """`Route.append` had no caller anywhere in the repo until now."""

    def test_a_route_command_builds_the_whole_plan(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.execute("route ANFL KEBR CROW")
        idents = [w.ident for w in session.sim.route.waypoints]
        self.assertEqual(idents, ["ANFL", "KEBR", "CROW"])
        self.assertEqual(session.sim.route.active, 0)
        self.assertEqual(session.sim.route.destination.ident, "CROW")

    def test_a_plan_with_a_hole_in_it_is_refused_entirely(self):
        """All or nothing: the guidance would fly across the gap and not say why."""
        session = Session.new("a320neo", "clear", seed=SEED)
        session.execute("route ANFL KEBR")
        output, _ = session.execute("route ANFL ZZZZ CROW")
        # The parser lowercases, so the message quotes what it looked for.
        self.assertIn("zzzz", output.lower())
        # The plan it already had is untouched.
        self.assertEqual([w.ident for w in session.sim.route.waypoints],
                         ["ANFL", "KEBR"])

    def test_adding_and_removing_waypoints(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.execute("route ANFL KEBR")
        session.execute("add CROW")
        self.assertEqual([w.ident for w in session.sim.route.waypoints],
                         ["ANFL", "KEBR", "CROW"])
        session.execute("remove KEBR")
        self.assertEqual([w.ident for w in session.sim.route.waypoints],
                         ["ANFL", "CROW"])

    def test_removing_a_waypoint_keeps_the_cursor_on_the_one_being_flown(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.execute("route ANFL KEBR CROW HRWD")
        session.sim.route.active = 2  # flying to CROW
        session.execute("remove ANFL")
        self.assertEqual(session.sim.route.active_waypoint.ident, "CROW")

    def test_removing_something_not_in_the_plan_says_so(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.execute("route ANFL KEBR")
        output, _ = session.execute("remove CROW")
        self.assertIn("not in the plan", output)
        self.assertEqual(len(session.sim.route.waypoints), 2)

    def test_a_multi_leg_plan_survives_a_save_and_load(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.execute("route ANFL KEBR CROW")
        session.sim.route.active = 1
        session.sim.sync_route()
        restored = Session.from_dict(session.to_dict())
        self.assertEqual([w.ident for w in restored.sim.route.waypoints],
                         ["ANFL", "KEBR", "CROW"])
        self.assertEqual(restored.sim.route.active, 1)


class TestPlanning(unittest.TestCase):
    """What a route costs, asked of the aeroplane that will fly it."""

    def planned(self, key, route, **kwargs):
        session = Session.new(key, "clear", seed=SEED,
                              start=physics.RUNWAY_START)
        session.execute("route " + route)
        return session, navigation.plan(session.sim, **kwargs)

    def test_the_legs_add_up_to_the_block_fuel(self):
        """A leg's fuel is a *share* of the three phases, not a fourth estimate."""
        for key in ("a320neo", "a350", "belugaxl"):
            _s, plan = self.planned(key, "ANFL KEBR CROW HRWD")
            self.assertAlmostEqual(
                sum(leg.fuel_kg for leg in plan.legs), plan.block_fuel_kg,
                places=6, msg=key,
            )
            self.assertAlmostEqual(
                sum(leg.distance_nm for leg in plan.legs), plan.distance_nm,
                places=6, msg=key,
            )

    def test_a_short_sector_files_a_lower_level(self):
        """FL370 on a hundred-mile hop is a climb the aeroplane never finishes.

        Charging it as though it did is the failure this guards: the profile
        must fit inside the distance, which is why short sectors cruise low.
        """
        _s, short = self.planned("a320neo", "ANFL KEBR")
        _s, long = self.planned("a320neo", "ANFL KEBR CROW HRWD")
        self.assertLess(short.cruise_ft, long.cruise_ft)
        for plan in (short, long):
            self.assertLessEqual(
                plan.climb_distance_nm + plan.descent_distance_nm,
                plan.distance_nm + 1e-6,
            )

    def test_the_cruise_is_integrated_with_the_mass_falling(self):
        """Twice the distance must cost less than twice the fuel.

        Cruise flow is strongly weight-dependent, so an aeroplane that gets
        lighter as it goes burns less per mile at the end than at the start. A
        planner using the ramp weight throughout would come out exactly linear.
        """
        session = Session.new("a350", "clear", seed=SEED)
        one = navigation._cruise_cost(session.sim, 37000.0, 250000.0, 2000.0)[0]
        two = navigation._cruise_cost(session.sim, 37000.0, 250000.0, 4000.0)[0]
        self.assertLess(two, 2.0 * one)
        self.assertGreater(two, 1.9 * one)

    def test_the_reserve_is_thirty_minutes_of_holding(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.execute("route ANFL KEBR")
        plan = navigation.plan(session.sim)
        flow = session.sim.holding_flow_kgh(
            navigation.HOLDING_ALTITUDE_FT,
            session.sim.state.mass_kg - plan.block_fuel_kg,
        )
        self.assertAlmostEqual(plan.reserve_kg, flow * 0.5, delta=flow * 0.02)

    def test_a_plan_knows_when_there_is_not_enough_fuel(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.execute("route ANFL KEBR CROW")
        session.sim.state.fuel_kg = 200.0
        plan = navigation.plan(session.sim)
        self.assertFalse(plan.enough)
        self.assertLess(plan.spare_kg, 0.0)

    def test_no_route_costs_nothing(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        self.assertIsNone(navigation.plan(session.sim))

    def test_cancelling_a_plan_cancels_what_it_was_going_to_cost(self):
        """`clear route` had its own write path and forgot the block fuel.

        Every other route change goes through `record_plan`; this one cleared
        the waypoints directly, so a cancelled plan's cost stayed on the state
        and the debrief went on grading the flight against a plan the pilot had
        thrown away. The two-write-paths family, again.
        """
        session = Session.new("a320neo", "clear", seed=SEED)
        session.execute("route ANFL KEBR CROW")
        self.assertGreater(session.sim.state.planned_fuel_kg, 0.0)
        session.execute("clear route")
        self.assertEqual(session.sim.state.planned_fuel_kg, 0.0)
        self.assertNotIn(
            "fuel_planned",
            [r.key for r in navigation.debrief_data(session.sim).rows],
        )

    def test_every_route_command_leaves_the_cost_agreeing_with_the_route(self):
        """Whatever changed the route, the two must not be able to disagree."""
        session = Session.new("a350", "clear", seed=SEED)
        for text in ("route ANFL KEBR CROW", "add HRWD", "remove KEBR",
                     "direct to CROW", "clear route", "route ANFL VSPR"):
            session.execute(text)
            costing = navigation.plan(session.sim)
            expected = costing.block_fuel_kg if costing else 0.0
            self.assertAlmostEqual(
                session.sim.state.planned_fuel_kg, expected, delta=1.0,
                msg="after `{}`".format(text),
            )

    def test_the_plan_is_recorded_on_the_state_when_it_is_filed(self):
        session, plan = self.planned("a320neo", "ANFL KEBR CROW")
        session.execute("show plan")
        self.assertGreater(session.sim.state.planned_fuel_kg, 0.0)
        self.assertAlmostEqual(session.sim.state.planned_fuel_kg,
                               plan.block_fuel_kg, delta=1.0)

    def test_the_climb_and_descent_flow_through_the_one_force_model(self):
        """A probe must leave the aeroplane exactly where it found it.

        Everything here trims the live state somewhere hypothetical and puts it
        back. If that restore ever slipped, the aircraft would silently end up
        at the planning condition -- which is a bug that would look like a
        physics bug for a long time.
        """
        session = Session.new("a350", "clear", seed=SEED)
        state = session.sim.state
        before = {f: getattr(state, f) for f in session.sim._PROBE_FIELDS}
        session.sim.climb_segment(0.0, 37000.0, 250000.0)
        session.sim.descent_segment(37000.0, 0.0, 240000.0)
        session.sim.level_flight_flow_kgh(37000.0, 0.85, 250000.0)
        session.sim.holding_flow_kgh(1500.0, 240000.0)
        for name, value in before.items():
            self.assertEqual(getattr(state, name), value, name)

    def test_the_integration_has_converged_at_the_step_it_uses(self):
        """A thousand-foot step must not be the reason for the answer.

        Cheap to check and worth checking: an integration whose answer moves
        when the step changes is reporting its own discretisation, and the
        difference would be indistinguishable from a modelling error.
        """
        for key, top in (("a320neo", 35000.0), ("a350", 37000.0)):
            sim = Session.new(key, "clear", seed=SEED).sim
            mass = sim.aircraft.oew_kg + sim.aircraft.payload_kg
            coarse = sim.climb_segment(0.0, top, mass, step_ft=1000.0)[0]
            fine = sim.climb_segment(0.0, top, mass, step_ft=250.0)[0]
            self.assertLess(abs(coarse / fine - 1.0), 0.005, key)

    def test_a_planned_climb_predicts_a_flown_one(self):
        """The only test of whether the planner is honest: fly it.

        The residue is the flying rather than the plan -- the speed hold below
        is a proportional nudge on the commanded pitch and lets the aircraft
        sit a few knots slow, which costs climb rate, so the flown climb comes
        out consistently longer than the planned one. The integration itself is
        converged to a twentieth of a percent, which the test above shows.
        """
        for key, top in (("a320neo", 33000.0), ("a350", 35000.0)):
            session = Session.new(key, "clear", seed=SEED)
            sim, state = session.sim, session.sim.state
            planned_fuel, planned_time, planned_nm = sim.climb_segment(
                state.altitude_ft, top, state.mass_kg
            )

            state.tas_ms = sim.profile_tas_ms(state.altitude_ft)
            state.pitch_deg = state.cmd_pitch_deg = sim.level_flight_pitch_deg()
            state.throttle_pct = 100.0
            sim.settle_engines()
            fuel0, x0, y0, t0 = (state.fuel_kg, state.x_nm, state.y_nm,
                                 state.elapsed_s)
            while state.altitude_ft < top and state.elapsed_s - t0 < 5400:
                error = state.tas_ms - sim.profile_tas_ms(state.altitude_ft)
                state.cmd_pitch_deg = physics.clamp(
                    state.cmd_pitch_deg + error * 0.25, -5.0, 25.0
                )
                sim.step_tick(1.0)
                self.assertIn(state.status, physics.LIVE_STATUSES, key)

            flown_fuel = fuel0 - state.fuel_kg
            flown_nm = math.hypot(state.x_nm - x0, state.y_nm - y0)
            for name, planned, flown in (("fuel", planned_fuel, flown_fuel),
                                         ("time", planned_time,
                                          state.elapsed_s - t0),
                                         ("distance", planned_nm, flown_nm)):
                self.assertLess(
                    abs(flown / planned - 1.0), 0.12,
                    "{}: planned {} {:,.0f}, flew {:,.0f}".format(
                        key, name, planned, flown),
                )

    def test_a_descent_covers_about_three_miles_per_thousand_feet(self):
        """The rule of thumb every pilot carries, and it has to fall out.

        It comes from the glide angle, so a draggy aeroplane must come out
        steeper -- and the BelugaXL, at an L/D of 14 against an A330's 19,
        duly does.
        """
        ratios = {}
        for key, top in (("a320neo", 37000.0), ("a330-800", 37000.0),
                         ("belugaxl", 33000.0)):
            sim = Session.new(key, "clear", seed=SEED).sim
            _fuel, _time, distance = sim.descent_segment(
                top, 0.0, sim.aircraft.oew_kg + sim.aircraft.payload_kg
            )
            ratios[key] = distance / (top / 1000.0)
            self.assertTrue(2.0 < ratios[key] < 4.0,
                            "{}: {:.1f} nm per 1,000 ft".format(key, ratios[key]))
        self.assertLess(ratios["belugaxl"], ratios["a330-800"])


class TestDebriefData(unittest.TestCase):
    """The debrief as numbers, which is what stops the two cards drifting."""

    def landed(self, **overrides):
        session = Session.new("a320neo", "clear", seed=SEED)
        state = session.sim.state
        state.status = physics.LANDED
        state.elapsed_s = 200.0
        state.touchdown = {
            "grade": "greaser", "survivable": True, "sink_rate_fpm": 44.0,
            "ias_kt": 140.0, "speed_ratio": 0.98, "centreline_ft": 8.0,
            "crab_deg": 1.0, "remaining_ft": 5100.0,
            "field_name": "Anfell International", "reason": "",
        }
        for key, value in overrides.items():
            setattr(state, key, value)
        return session

    def test_every_row_declares_a_kind_the_formatter_knows(self):
        """A row whose kind no display understands renders as nothing at all."""
        data = navigation.debrief_data(self.landed(planned_fuel_kg=900.0).sim)
        for row in data.rows:
            self.assertIn(row.kind, navigation.ROW_KINDS, row.key)
            self.assertTrue(navigation.format_row(row), row.key)

    def test_row_keys_are_unique(self):
        """The parity guard matches on the key, so two rows sharing one hides
        a disagreement behind whichever is compared second."""
        keys = [r.key for r in navigation.debrief_data(
            self.landed(planned_fuel_kg=900.0).sim).rows]
        self.assertEqual(len(keys), len(set(keys)))

    def test_the_outcome_is_a_key_as_well_as_a_sentence(self):
        """A display branches on the key; only a human reads the sentence."""
        data = navigation.debrief_data(self.landed().sim)
        self.assertEqual(data.outcome, physics.LANDED)
        self.assertEqual(data.outcome_text, "Landed")
        self.assertEqual(data.grade, "greaser")

    def test_the_plan_row_appears_only_when_there_was_a_plan(self):
        without = navigation.debrief_data(self.landed().sim)
        self.assertNotIn("fuel_planned", [r.key for r in without.rows])
        with_plan = navigation.debrief_data(self.landed(planned_fuel_kg=900.0).sim)
        self.assertIn("fuel_planned", [r.key for r in with_plan.rows])

    def test_the_clock_row_never_reads_sixty_seconds(self):
        row = navigation.DebriefRow("time", "Time airborne", 119.9999, "s", 0, "clock")
        self.assertEqual(navigation.format_row(row), "2 min 00 s")

    def test_each_kind_renders_the_way_the_browser_does(self):
        """These exact strings are what `formatRow` in anfell.html produces."""
        cases = [
            (navigation.DebriefRow("a", "", 15.4, "nm", 1), "15.4 nm"),
            (navigation.DebriefRow("b", "", 111.0, "kg", 0, "of", 12000.0),
             "111 kg of 12,000"),
            (navigation.DebriefRow("c", "", 250.0, "kt", 0, "mach", 0.414),
             "250 kt / M0.414"),
            (navigation.DebriefRow("d", "", 140.0, "kt", 0, "ratio", 98.0),
             "140 kt (98% of Vref)"),
            (navigation.DebriefRow("e", "", 8340.0, "kg", 0, "vs", 7900.0),
             "8,340 kg against a planned 7,900 (+6%)"),
            (navigation.DebriefRow("f", "", 1.0, "g", 2), "1.00 g"),
        ]
        for row, expected in cases:
            self.assertEqual(navigation.format_row(row), expected, row.key)

    def test_halves_round_away_from_zero_in_both_builds(self):
        """Python rounds halves to even and JavaScript away from zero.

        A touchdown at 140.5 kt printed 140 in the text simulator and 141 in
        the browser -- two front ends disagreeing about a landing by a knot,
        which the parity guard found on its first run. Neither language's rule
        is more correct; what matters is that one arithmetic expression does
        the rounding before either formatter sees a tie.
        """
        for value, decimals, expected in [
            (140.5, 0, 141.0), (5100.5, 0, 5101.0), (2.5, 0, 3.0),
            (3.5, 0, 4.0), (0.5, 0, 1.0), (-2.5, 0, -3.0),
            (1234.5, 0, 1235.0), (1.0049, 2, 1.0),
            # 2.675 is 2.67499999999999982 as a double, but scaling it lands on
            # 267.5 exactly and both languages then floor 268.0 -- so the
            # answer is 2.68 in both, which is the property that matters here.
            (2.675, 2, 2.68), (1.005, 2, 1.0), (8.835, 2, 8.84),
        ]:
            self.assertEqual(
                navigation.round_half_up(value, decimals), expected,
                "{} to {} dp".format(value, decimals),
            )

    def test_a_row_is_rendered_from_the_rounded_number(self):
        row = navigation.DebriefRow("sink", "", 140.5, "kt", 0)
        self.assertEqual(navigation.format_row(row), "141 kt")

    def test_the_closest_approach_to_the_ground_is_never_negative(self):
        """"-0 ft" appeared on the card after every landing.

        The integrator puts the wheels a fraction below the sampled surface on
        the substep it touches down, so the raw minimum goes slightly negative
        and rounds to a signed zero. Both builds agreed on it, so the parity
        guard was happy; it was still wrong on the glass.
        """
        session = self.landed()
        session.sim.state.min_agl_ft = -0.42
        row = [r for r in navigation.debrief_data(session.sim).rows
               if r.key == "min_agl"][0]
        self.assertEqual(row.value, 0.0)
        self.assertEqual(navigation.format_row(row), "0 ft")
        # The state keeps the true figure; only the row is floored.
        self.assertEqual(session.sim.state.min_agl_ft, -0.42)

    def test_warnings_come_out_sorted(self):
        """Sorted by the model, so two builds cannot list them differently."""
        session = self.landed()
        session.sim.state.warnings_seen = ["STALL", "ALTERNATE LAW", "OVERSPEED"]
        self.assertEqual(
            navigation.debrief_data(session.sim).warnings_seen,
            ["ALTERNATE LAW", "OVERSPEED", "STALL"],
        )

    def test_the_route_is_named_for_the_logbook(self):
        session = session_with_route()
        idents = navigation.debrief_data(session.sim).route_idents
        self.assertTrue(idents)
        self.assertTrue(all(isinstance(i, str) and i for i in idents))


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
