"""Lateral-directional dynamics: sideslip, rudder, and engine-out asymmetry."""

import unittest

from flight_sim import aircraft as fleet
from flight_sim import atmosphere as atm
from flight_sim import commands as cmd
from flight_sim import dashboard
from flight_sim import physics
from flight_sim.game import Session


def settled(sim, seconds=20.0, dt=0.25):
    """Let the sideslip reach its equilibrium without moving the aircraft."""
    for _ in range(int(seconds / dt)):
        sim._update_sideslip(dt)
    return sim.state.sideslip_deg


def at_speed(key, ias_kt, altitude_ft=8000.0, **state_kwargs):
    session = Session.new(key, "clear", seed=42)
    state = session.sim.state
    state.altitude_ft = altitude_ft
    state.tas_ms = atm.ias_to_tas(ias_kt * atm.MS_PER_KT, altitude_ft)
    for name, value in state_kwargs.items():
        setattr(state, name, value)
    return session.sim


class TestSideslip(unittest.TestCase):
    def test_coordinated_flight_has_no_sideslip(self):
        for craft in fleet.FLEET:
            sim = at_speed(craft.key, 250.0)
            self.assertAlmostEqual(settled(sim), 0.0, places=6)

    def test_rudder_produces_sideslip_in_its_own_direction(self):
        sim = at_speed("a320neo", 200.0, rudder_deg=15.0)
        self.assertGreater(settled(sim), 1.0)
        sim = at_speed("a320neo", 200.0, rudder_deg=-15.0)
        self.assertLess(settled(sim), -1.0)

    def test_centring_the_rudder_returns_beta_to_zero(self):
        """Weathercock stability must actually restore."""
        sim = at_speed("a320neo", 200.0, rudder_deg=20.0)
        self.assertGreater(settled(sim), 1.0)
        sim.state.rudder_deg = 0.0
        self.assertAlmostEqual(settled(sim, seconds=40.0), 0.0, places=3)

    def test_sideslip_lags_rather_than_snapping(self):
        sim = at_speed("a320neo", 200.0, rudder_deg=20.0)
        sim._update_sideslip(0.1)
        immediate = abs(sim.state.sideslip_deg)
        eventual = abs(settled(sim))
        self.assertLess(immediate, eventual * 0.5)

    def test_bigger_aircraft_respond_more_slowly(self):
        taus = [craft.yaw_tau_s for craft in fleet.FLEET]
        self.assertEqual(taus, sorted(taus))

    def test_sideslip_is_bounded(self):
        sim = at_speed("a320neo", 130.0, rudder_deg=200.0)
        self.assertLessEqual(abs(settled(sim)), physics.MAX_SIDESLIP_DEG)


class TestRudderLimiter(unittest.TestCase):
    def test_travel_falls_with_airspeed(self):
        slow = at_speed("a320neo", 140.0).max_rudder_deg()
        fast = at_speed("a320neo", 300.0).max_rudder_deg()
        self.assertGreater(slow, fast)
        self.assertAlmostEqual(slow, fleet.A320NEO.max_rudder_deg, places=6)
        self.assertGreaterEqual(fast, physics.MIN_RUDDER_TRAVEL_DEG)

    def test_commanded_rudder_is_clamped_to_available_travel(self):
        sim = at_speed("a320neo", 300.0, rudder_deg=30.0)
        sim._update_sideslip(0.1)
        self.assertLessEqual(abs(sim.state.rudder_deg), sim.max_rudder_deg() + 1e-9)

    def test_full_rudder_gives_a_large_but_survivable_slip(self):
        """Without a travel limiter this would be a fin-detaching sideslip."""
        for ias in (150.0, 250.0, 330.0):
            sim = at_speed("a320neo", ias, rudder_deg=60.0)
            self.assertLess(abs(settled(sim)), 22.0, "at {:.0f} kt".format(ias))


class TestSideslipConsequences(unittest.TestCase):
    def test_sideslip_costs_drag(self):
        clean = at_speed("a320neo", 250.0)
        settled(clean)
        slipping = at_speed("a320neo", 250.0, rudder_deg=25.0)
        settled(slipping)
        self.assertGreater(
            slipping._aero_state().drag, clean._aero_state().drag * 1.05
        )

    def test_the_aircraft_flies_where_it_points_only_when_coordinated(self):
        sim = at_speed("a320neo", 250.0)
        sim.weather.hold(wind_speed_kt=0.0, turbulence=0.0)
        self.assertAlmostEqual(sim.readout().drift_deg, 0.0, places=3)

        sim.state.rudder_deg = 25.0
        settled(sim)
        self.assertGreater(abs(sim.readout().drift_deg), 2.0)

    def test_drift_separates_into_sideslip_and_wind(self):
        """The panel shows two honest numbers where it used to show one fudge."""
        sim = at_speed("a320neo", 250.0, rudder_deg=20.0)
        settled(sim)
        readout = sim.readout()
        # drift = wind_drift - sideslip, by construction.
        self.assertAlmostEqual(
            readout.drift_deg,
            physics.wrap180(readout.wind_drift_deg - readout.sideslip_deg),
            places=6,
        )

    def test_rudder_rolls_the_aircraft_through_dihedral_effect(self):
        """Rudder and roll go the same way, and the roll must be visible."""
        session = Session.new("a320neo", "clear", seed=42)
        session.execute("rudder right 20")
        for _ in range(3):
            session.sim.step_tick()
        readout = session.sim.readout()
        self.assertGreater(readout.sideslip_deg, 1.0)
        self.assertGreater(readout.bank_deg, 1.0)

    def test_a_rudder_only_input_turns_the_aircraft(self):
        session = Session.new("a320neo", "clear", seed=42)
        start = session.sim.state.heading_deg
        session.execute("rudder right 25")
        for _ in range(4):
            session.sim.step_tick()
        turned = physics.wrap180(session.sim.state.heading_deg - start)
        self.assertGreater(turned, 3.0)


class TestEngineFailure(unittest.TestCase):
    def test_all_engines_running_gives_no_asymmetry(self):
        for craft in fleet.FLEET:
            sim = at_speed(craft.key, 250.0, throttle_pct=90.0)
            self.assertAlmostEqual(sim._asymmetric_yaw_moment(), 0.0, places=6)

    def test_thrust_falls_by_the_failed_fraction(self):
        twin = at_speed("a320neo", 250.0, throttle_pct=90.0)
        full = twin._thrust_n()
        twin.state.engines_failed = [0]
        self.assertAlmostEqual(twin._thrust_n(), full / 2.0, places=3)

        quad = at_speed("a380", 250.0, throttle_pct=90.0)
        full = quad._thrust_n()
        quad.state.engines_failed = [0]
        self.assertAlmostEqual(quad._thrust_n(), full * 0.75, places=3)

    def test_the_aircraft_yaws_toward_the_dead_engine(self):
        # Engine 0 is the left one; losing it must yaw the nose left (beta < 0).
        sim = at_speed("a320neo", 200.0, throttle_pct=100.0, engines_failed=[0])
        self.assertLess(settled(sim), -1.0)
        # And the right engine failing yaws it right.
        sim = at_speed("a320neo", 200.0, throttle_pct=100.0, engines_failed=[1])
        self.assertGreater(settled(sim), 1.0)

    def test_rudder_can_hold_it_straight(self):
        sim = at_speed("a320neo", 200.0, throttle_pct=100.0, engines_failed=[0])
        uncorrected = settled(sim)
        sim.state.rudder_deg = -uncorrected * 2.0  # roughly the right way
        corrected = settled(sim, seconds=40.0)
        self.assertLess(abs(corrected), abs(uncorrected))

    def test_an_outboard_engine_is_worse_than_an_inboard_one(self):
        """On the A380, the 21.6 m arm bites harder than the 12.4 m one."""
        outer = at_speed("a380", 200.0, throttle_pct=100.0, engines_failed=[0])
        inner = at_speed("a380", 200.0, throttle_pct=100.0, engines_failed=[1])
        self.assertGreater(abs(settled(outer)), abs(settled(inner)))

    def test_vmc_emerges_from_the_geometry(self):
        """Vmc is a crossing point, not a constant.

        Below Vmc the required deflection exceeds the available travel; well
        above it, there is margin. Neither number is coded -- both come out of
        the engine arm, the thrust and the dynamic pressure.
        """
        craft = fleet.A320NEO

        def required_rudder(ias_kt):
            sim = at_speed(
                "a320neo", ias_kt, altitude_ft=2000.0,
                throttle_pct=100.0, engines_failed=[0],
            )
            v = max(sim.state.tas_ms, 25.0)
            reference = (
                0.5 * atm.density(sim.state.altitude_ft) * v * v
                * craft.wing_area_m2 * craft.wing_span_m
            )
            cn = sim._asymmetric_yaw_moment() / reference
            return abs(cn / craft.rudder_power), sim.max_rudder_deg()

        needed_fast, available_fast = required_rudder(200.0)
        self.assertLess(needed_fast, available_fast)

        needed_slow, available_slow = required_rudder(95.0)
        self.assertGreater(needed_slow, available_slow)

    def test_vmc_warning_fires_when_control_is_lost(self):
        session = Session.new("a320neo", "clear", seed=42)
        state = session.sim.state
        state.altitude_ft = 6000.0
        state.tas_ms = atm.ias_to_tas(95.0 * atm.MS_PER_KT, 6000.0)
        state.throttle_pct = 100.0
        state.engines_failed = [0]
        settled(session.sim, seconds=30.0)
        warnings = session.sim.readout().warnings
        self.assertTrue(any("VMC" in w for w in warnings), warnings)

    def test_engine_failure_warning_names_the_count(self):
        session = Session.new("a380", "clear", seed=42)
        session.execute("shutdown engine 2")
        warnings = session.sim.readout().warnings
        self.assertTrue(any("ENGINE FAILURE (3/4)" in w for w in warnings), warnings)


class TestCommands(unittest.TestCase):
    def test_rudder_phrasings(self):
        for text, value in [
            ("rudder left 10", -10.0),
            ("rudder right 5", 5.0),
            ("full left rudder", -30.0),
            ("right rudder", 30.0),
            ("centre rudder", 0.0),
            ("center rudder", 0.0),
            ("rudder neutral", 0.0),
        ]:
            command = cmd.parse(text)
            self.assertEqual(command.kind, "rudder_set", text)
            self.assertAlmostEqual(command.value, value, msg=text)

    def test_rudder_does_not_shadow_the_bank_commands(self):
        self.assertEqual(cmd.parse("turn left 30").kind, "bank_set")
        self.assertEqual(cmd.parse("bank left 25").kind, "bank_set")
        self.assertEqual(cmd.parse("roll level").kind, "bank_set")

    def test_engine_phrasings(self):
        self.assertEqual(cmd.parse("engine failure").kind, "engine_fail")
        self.assertEqual(cmd.parse("shutdown engine 2").value, 1.0)
        self.assertEqual(cmd.parse("restart engines").kind, "engine_restart")

    def test_failing_the_same_engine_twice_is_idempotent(self):
        session = Session.new("a380", "clear", seed=42)
        session.execute("shutdown engine 1")
        session.execute("shutdown engine 1")
        self.assertEqual(session.sim.state.engines_failed, [0])

    def test_restart_restores_every_engine(self):
        session = Session.new("a380", "clear", seed=42)
        session.execute("shutdown engine 1")
        session.execute("shutdown engine 4")
        self.assertEqual(len(session.sim.state.engines_failed), 2)
        session.execute("restart engines")
        self.assertEqual(session.sim.state.engines_failed, [])
        self.assertEqual(session.sim.readout().engines_running_count, 4)

    def test_levelling_off_also_centres_the_rudder(self):
        session = Session.new("a320", "clear", seed=42)
        session.execute("rudder left 20")
        self.assertNotEqual(session.sim.state.rudder_deg, 0.0)
        session.execute("level off")
        self.assertEqual(session.sim.state.rudder_deg, 0.0)


class TestPanel(unittest.TestCase):
    def test_slip_ball_swings_opposite_to_the_sideslip(self):
        """Step on the ball: yaw right, ball goes left."""
        centre = dashboard.slip_ball(0.0).index("O")
        right_slip = dashboard.slip_ball(10.0).index("O")
        left_slip = dashboard.slip_ball(-10.0).index("O")
        self.assertLess(right_slip, centre)
        self.assertGreater(left_slip, centre)

    def test_slip_ball_is_bounded(self):
        for beta in (-90.0, 0.0, 90.0):
            ball = dashboard.slip_ball(beta)
            self.assertEqual(len(ball), dashboard.SLIP_BALL_WIDTH + 2)
            self.assertEqual(ball.count("O"), 1)

    def test_panel_reports_sideslip_and_drift_separately(self):
        session = Session.new("a320neo", "crosswind", seed=42)
        session.execute("rudder right 15")
        text = dashboard.render(session.sim, session.sim.readout())
        self.assertIn("Sideslip", text)
        self.assertIn("Wind drift", text)
        self.assertIn("Rudder", text)
        self.assertNotIn("Yaw / drift", text)

    def test_panel_flags_a_failed_engine(self):
        session = Session.new("a350", "clear", seed=42)
        text = dashboard.render(session.sim, session.sim.readout())
        self.assertIn("2/2 OK", text)
        session.execute("engine failure")
        text = dashboard.render(session.sim, session.sim.readout())
        self.assertIn("OUT", text)


class TestPersistence(unittest.TestCase):
    def test_yaw_state_survives_a_save_and_load(self):
        import os
        import tempfile

        path = os.path.join(tempfile.mkdtemp(), "yaw.json")
        session = Session.new("a380", "stormy", seed=11)
        session.execute("shutdown engine 1")
        session.execute("rudder right 12")
        session.save(path)

        restored = Session.load(path)
        self.assertEqual(restored.sim.state.engines_failed, [0])
        self.assertAlmostEqual(
            restored.sim.state.rudder_deg, session.sim.state.rudder_deg, places=9
        )
        self.assertAlmostEqual(
            restored.sim.state.sideslip_deg, session.sim.state.sideslip_deg, places=9
        )


if __name__ == "__main__":
    unittest.main()
