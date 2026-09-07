"""Things going wrong.

Every failure here has to change something the flight model already reads. A
failure whose only consequence is a message about itself is not a failure, it is
a label -- so most of these tests assert that the *aeroplane* is different, not
that a flag was set.
"""

import os
import tempfile
import unittest

from flight_sim import commands as cmd
from flight_sim import dashboard
from flight_sim import failures
from flight_sim import fbw
from flight_sim import landing
from flight_sim import physics
from flight_sim.game import Session


def cruising(key="a320neo"):
    return Session.new(key, "clear", seed=42)


def on_the_runway(key="a320neo"):
    return Session.new(key, "clear", seed=42, start=physics.RUNWAY_START)


class TestCommands(unittest.TestCase):
    def test_the_parser_understands_them(self):
        """CLAUDE.md: a parse test whenever a matcher is added."""
        for text, target in (
            ("fail engine", "engine"), ("fail fuel leak", "fuel"),
            ("fail hydraulics", "hydraulics"), ("fail brakes", "brakes"),
            ("fail flap jam", "flaps"), ("fail gear jam", "gear"),
        ):
            command = cmd.parse(text)
            self.assertEqual(command.kind, "failure", text)
            self.assertEqual(command.target, target, text)
        self.assertEqual(cmd.parse("arm fail engine").kind, "arm_failure")
        self.assertEqual(cmd.parse("failures").kind, "show_failures")

    def test_naming_an_engine_still_reaches_the_engine_matcher(self):
        """`fail engine 2` is about engine two, not about the word 'engine'.

        `_MATCHERS` is tried in order and the first hit wins, so the generic
        pattern registered ahead of it has to step aside for a digit.
        """
        self.assertEqual(cmd.parse("fail engine 2").kind, "engine_fail")
        self.assertEqual(cmd.parse("shutdown engine 1").kind, "engine_fail")

    def test_they_cost_no_simulation_time(self):
        session = cruising()
        before = session.sim.state.elapsed_s
        session.execute("fail hydraulics")
        self.assertEqual(session.sim.state.elapsed_s, before)

    def test_the_menu_lists_everything(self):
        text = failures.menu()
        for failure in failures.CATALOGUE:
            self.assertIn(failure.name, text)


class TestConsequences(unittest.TestCase):
    """Each failure must change the aeroplane, not merely announce itself."""

    def test_a_fuel_leak_empties_the_tanks_faster_than_the_burn(self):
        def remaining(leaking):
            session = cruising()
            if leaking:
                session.execute("fail fuel leak")
            start = session.sim.state.fuel_kg
            for _ in range(6):
                session.sim.step_tick()
            return start - session.sim.state.fuel_kg

        self.assertGreater(remaining(True), remaining(False) * 1.5)

    def test_a_fuel_leak_takes_the_mass_with_it(self):
        """Fuel that has left the aeroplane is not still weighing it down."""
        session = cruising()
        session.execute("fail fuel leak")
        mass = session.sim.state.mass_kg
        fuel = session.sim.state.fuel_kg
        session.sim.step_tick()
        self.assertAlmostEqual(
            mass - session.sim.state.mass_kg,
            fuel - session.sim.state.fuel_kg,
            places=6,
        )

    def test_jammed_flaps_will_not_move(self):
        session = cruising()
        session.sim.state.flaps = 2
        session.execute("fail flap jam")
        session.execute("flaps 4")
        session.sim.step_tick()
        self.assertEqual(session.sim.state.flaps, 2)

    def test_jammed_flaps_change_the_speed_you_must_fly(self):
        """The consequence that actually matters on the approach.

        Stuck at flap 2, VLS and Vref sit well above where they would have been
        at full flap -- so the aeroplane has to be flown down faster, and the
        landing distance grows because of it.
        """
        session = cruising()
        session.sim.state.flaps = 4
        full = fbw.characteristic_speeds(session.sim).vref
        session.sim.state.flaps = 2
        session.execute("fail flap jam")
        session.sim.step_tick()
        self.assertGreater(fbw.characteristic_speeds(session.sim).vref, full + 5.0)

    def test_a_jammed_gear_stays_where_it_was(self):
        session = cruising()
        session.sim.state.gear_down = False
        session.execute("fail gear jam")
        session.execute("gear down")
        session.sim.step_tick()
        self.assertFalse(session.sim.state.gear_down)

    def test_lost_hydraulics_slow_the_controls(self):
        """How fast the surfaces move, so the roll has to be caught mid-way.

        `execute` advances a full ten-second tick, which is long enough for even
        a crippled aeroplane to finish a forty-five degree roll -- so the command
        goes in directly and the clock is stopped after two seconds.
        """
        def rolled(broken):
            session = cruising("a350")
            if broken:
                session.execute("fail hydraulics")
            session.sim.state.cmd_bank_deg = 60.0
            session.sim.step_tick(2.0)
            return abs(session.sim.state.bank_deg)

        self.assertLess(rolled(True), rolled(False) * 0.7)

    def test_degraded_brakes_lengthen_the_rollout(self):
        def decel(broken):
            session = on_the_runway()
            state = session.sim.state
            state.touchdown = {"grade": "normal landing"}
            state.tas_ms = 60.0
            state.brakes = 1.0
            if broken:
                session.execute("fail brakes")
            return landing.rollout_deceleration(session.sim)

        self.assertLess(decel(True), decel(False))

    def test_an_engine_that_stops_still_yaws_the_aeroplane(self):
        """The physics was here before the failure system was.

        This is the check that the new way of triggering it reaches the old
        machinery rather than setting a flag beside it.
        """
        session = cruising()
        session.sim.state.throttle_pct = 100.0
        session.sim.settle_engines()
        session.execute("fail engine")
        # Long enough for the dead engine's fan to run down: the asymmetry
        # arrives over a second or two now rather than instantly.
        for _ in range(80):
            session.sim._substep(0.1)
        self.assertGreater(abs(session.sim.readout().sideslip_deg), 1.0)


class TestFire(unittest.TestCase):
    def test_a_fire_stops_the_engine_and_says_so(self):
        session = cruising()
        failures.trigger(session.sim, "fire", 0)
        self.assertIn(0, session.sim.state.engines_failed)
        self.assertIn(0, session.sim.state.engines_on_fire)
        self.assertIn("ENG 1 FIRE", failures.ecam_text(session.sim))

    def test_a_fire_outranks_a_plain_failure_on_the_ecam(self):
        session = cruising("a380")
        failures.trigger(session.sim, "fire", 0)
        text = failures.ecam_text(session.sim)
        self.assertIn("AGENT 1 . . . DISCHARGE", text)
        self.assertNotIn("ENG 1 FAIL", text)


class TestTheV1Cut(unittest.TestCase):
    """The scenario the whole thing exists for."""

    def test_an_armed_failure_waits_for_v1(self):
        session = on_the_runway()
        session.execute("arm fail engine")
        state = session.sim.state
        v1 = fbw.takeoff_speeds(session.sim).v1
        state.brakes = 0.0
        state.throttle_pct = 100.0

        fired_at = None
        for _ in range(40):
            session.sim.step_tick(1.0)
            if state.engines_failed and fired_at is None:
                fired_at = session.sim.readout().ias_kt
                break
        self.assertIsNotNone(fired_at, "the armed failure never fired")
        self.assertGreaterEqual(fired_at, v1 - 1.0)
        self.assertLess(fired_at, v1 + 25.0)

    def test_it_does_not_fire_before_the_roll_begins(self):
        session = on_the_runway()
        session.execute("arm fail engine")
        for _ in range(3):
            session.sim.step_tick(1.0)
        self.assertEqual(session.sim.state.engines_failed, [])

    def test_the_aircraft_still_flies_away_on_the_remaining_engine(self):
        """A V1 cut is survivable, and has to be, or it is not a scenario."""
        session = on_the_runway()
        session.execute("arm fail engine")
        state = session.sim.state
        state.brakes = 0.0
        state.throttle_pct = 100.0
        state.cmd_pitch_deg = 12.0
        ground_ft = session.sim.terrain.elevation(state.x_nm, state.y_nm)

        for _ in range(40):
            session.sim.step_tick(2.0)
            if state.status not in physics.LIVE_STATUSES:
                break
            # The pilot flies it: rudder toward the live engine.
            if state.engines_failed and abs(state.sideslip_deg) > 1.0:
                state.rudder_deg = max(
                    -25.0, min(25.0, state.rudder_deg - state.sideslip_deg * 0.8)
                )
            if not state.on_ground and state.altitude_ft > ground_ft + 1500.0:
                break

        self.assertEqual(state.status, physics.FLYING)
        self.assertFalse(state.on_ground, "never got airborne")
        # A few hundred feet, not a few thousand: half the thrust and a rudder
        # holding the drag of a sideslip is a climb of a few hundred feet a
        # minute, which is exactly why a V1 cut is worth practising.
        self.assertGreater(state.altitude_ft, ground_ft + 400.0)
        self.assertEqual(len(state.engines_failed), 1)


class TestEcam(unittest.TestCase):
    def test_it_says_nothing_when_nothing_is_wrong(self):
        self.assertEqual(failures.ecam(cruising().sim), [])

    def test_the_title_carries_the_actions_under_it(self):
        session = cruising()
        session.execute("fail fuel leak")
        lines = failures.ecam(session.sim)
        self.assertEqual(lines[0].text, "FUEL LEAK")
        self.assertFalse(lines[0].indent)
        self.assertTrue(all(line.indent for line in lines[1:]))
        self.assertIn("LAND ASAP", [line.text for line in lines])

    def test_warnings_come_above_cautions(self):
        """Red first, because that is the order they have to be dealt with."""
        session = cruising()
        session.execute("fail fuel leak")   # amber
        session.execute("fail engine")      # red
        lines = failures.ecam(session.sim)
        self.assertEqual(lines[0].colour, failures.RED)
        self.assertIn("ENG 1 FAIL", lines[0].text)
        self.assertIn("FUEL LEAK", [line.text for line in lines])

    def test_the_panel_shows_it_under_the_annunciator(self):
        session = cruising()
        session.execute("fail hydraulics")
        text = dashboard.render(session.sim, session.sim.readout())
        self.assertIn("HYD SYS LO PR", text)
        self.assertIn("F/CTL . . . SLOW", text)
        # Above the attitude indicator, as on the aeroplane.
        self.assertLess(text.index("HYD SYS LO PR"), text.index("IAS "))


class TestPersistence(unittest.TestCase):
    def test_failures_survive_a_save_and_load(self):
        path = os.path.join(tempfile.mkdtemp(), "broken.json")
        session = cruising()
        session.sim.state.flaps = 2
        session.execute("fail flap jam")
        session.execute("fail engine")
        session.sim.step_tick()
        session.save(path)

        restored = Session.load(path)
        state = restored.sim.state
        self.assertIn("flaps", state.failures)
        self.assertEqual(state.jammed_flaps, 2)
        self.assertEqual(state.engines_failed, [0])
        self.assertIn("F/CTL FLAPS LOCKED", failures.ecam_text(restored.sim))

    def test_an_armed_failure_survives_a_save_and_load(self):
        path = os.path.join(tempfile.mkdtemp(), "armed.json")
        session = on_the_runway()
        session.execute("arm fail engine")
        session.save(path)
        self.assertIsNotNone(Session.load(path).sim.state.armed_failure)


if __name__ == "__main__":
    unittest.main()
