"""Flight dynamics: trim, envelope, failure modes and published performance."""

import unittest

from flight_sim import aircraft as fleet
from flight_sim import atmosphere as atm
from flight_sim import physics
from flight_sim.game import Session

CRUISE_TARGETS = {
    # key: (altitude_ft, published cruise fuel flow kg/h)
    "a320": (35000, 2400),
    "a320neo": (35000, 2000),
    "a321": (35000, 2700),
    "a350": (37000, 5800),
    "a380": (37000, 11500),
}


def trimmed_at_cruise(key):
    """A simulator trimmed for level flight at the type's cruise condition."""
    session = Session.new(key, "clear", seed=42)
    state = session.sim.state
    craft = session.sim.aircraft
    altitude = CRUISE_TARGETS[key][0]
    state.altitude_ft = altitude
    state.tas_ms = atm.mach_to_tas(craft.cruise_mach, altitude)
    state.pitch_deg = state.cmd_pitch_deg = session.sim.level_flight_pitch_deg()
    state.throttle_pct = session.sim.throttle_for_level_flight()
    return session.sim


class TestTrim(unittest.TestCase):
    def test_new_flight_starts_level_at_5000_feet(self):
        sim = Session.new("a320neo", "clear", seed=42).sim
        readout = sim.readout()
        self.assertAlmostEqual(readout.altitude_ft, 5000.0, places=3)
        self.assertAlmostEqual(readout.bank_deg, 0.0, places=6)
        self.assertLess(abs(readout.vertical_speed_fpm), 1.0)

    def test_new_flight_has_room_to_manoeuvre(self):
        """The opening position must not put a ridge inside the first minute."""
        for key in fleet.FLEET_BY_KEY:
            readout = Session.new(key, "clear", seed=42).sim.readout()
            self.assertGreater(
                readout.agl_ft, 2000.0, "{} starts too close to terrain".format(key)
            )

    def test_level_flight_holds_altitude_and_speed(self):
        """Trim must actually be trim: three minutes hands-off, no drift."""
        for key in fleet.FLEET_BY_KEY:
            sim = Session.new(key, "clear", seed=42).sim
            start_alt = sim.state.altitude_ft
            start_ias = sim.readout().ias_kt
            for _ in range(18):
                sim.step_tick()
            readout = sim.readout()
            self.assertLess(
                abs(readout.altitude_ft - start_alt), 100.0,
                "{} drifted in altitude".format(key),
            )
            self.assertLess(
                abs(readout.ias_kt - start_ias), 5.0,
                "{} drifted in speed".format(key),
            )

    def test_thrust_equals_drag_when_trimmed(self):
        """The trim solver and the integrator must use one thrust model."""
        for key in CRUISE_TARGETS:
            aero = trimmed_at_cruise(key)._aero_state()
            self.assertAlmostEqual(
                aero.thrust / aero.drag, 1.0, places=2,
                msg="{} trim disagrees with the integrator".format(key),
            )


class TestPublishedPerformance(unittest.TestCase):
    def test_cruise_fuel_flow_matches_published_figures(self):
        for key, (_altitude, expected) in CRUISE_TARGETS.items():
            flow = trimmed_at_cruise(key).readout().fuel_flow_kgh
            self.assertLess(
                abs(flow - expected) / expected, 0.05,
                "{}: {:.0f} kg/h vs published {}".format(key, flow, expected),
            )

    def test_lift_to_drag_ratio_is_realistic(self):
        """A jet airliner cruises at L/D somewhere between 15 and 20."""
        for key in CRUISE_TARGETS:
            aero = trimmed_at_cruise(key)._aero_state()
            self.assertTrue(
                15.0 < aero.cl / aero.cd < 20.0,
                "{} has L/D {:.1f}".format(key, aero.cl / aero.cd),
            )

    def test_aircraft_cannot_climb_far_past_its_certified_ceiling(self):
        for key in ("a320", "a320neo", "a321", "a350"):
            session = Session.new(key, "clear", seed=42)
            session.execute("full power")
            session.execute("set pitch 5")
            for _ in range(210):
                session.sim.step_tick()
            readout = session.sim.readout()
            ceiling = session.sim.aircraft.ceiling_ft
            self.assertLess(
                readout.altitude_ft, ceiling + 1500.0,
                "{} climbed to {:.0f} ft".format(key, readout.altitude_ft),
            )
            self.assertGreater(readout.altitude_ft, ceiling - 2000.0)

    def test_neo_burns_less_than_ceo_at_identical_mass(self):
        """Isolates the sharklet and engine gains from the weight difference."""
        burned = {}
        for key in ("a320", "a320neo"):
            session = Session.new(key, "clear", seed=42)
            state = session.sim.state
            state.altitude_ft = 35000.0
            state.mass_kg = 70000.0
            state.fuel_kg = 12000.0
            state.tas_ms = atm.mach_to_tas(0.78, 35000.0)
            state.pitch_deg = state.cmd_pitch_deg = (
                session.sim.level_flight_pitch_deg()
            )
            state.throttle_pct = session.sim.throttle_for_level_flight()
            start_fuel = state.fuel_kg
            for _ in range(180):
                session.sim.step_tick()
            burned[key] = start_fuel - state.fuel_kg

        saving = (burned["a320"] - burned["a320neo"]) / burned["a320"]
        self.assertGreater(saving, 0.10, "neo saved only {:.1%}".format(saving))
        self.assertLess(saving, 0.25)

    def test_roll_response_is_ordered_by_type(self):
        """Time to reach 25 degrees of bank, measured inside the tick."""
        times = []
        for craft in fleet.FLEET:
            sim = Session.new(craft.key, "clear", seed=42).sim
            sim.state.cmd_bank_deg = 25.0
            elapsed = 0.0
            while abs(sim.state.bank_deg) < 24.5 and elapsed < 20.0:
                sim.step_tick(0.2)
                elapsed += 0.2
            times.append(elapsed)
        self.assertEqual(times, sorted(times), "roll response out of order: %s" % times)
        self.assertLess(times[0], times[-1])


class TestEnvelope(unittest.TestCase):
    def test_fuel_decreases_monotonically_and_reduces_mass(self):
        sim = Session.new("a320", "clear", seed=42).sim
        previous_fuel = sim.state.fuel_kg
        previous_mass = sim.state.mass_kg
        for _ in range(30):
            sim.step_tick()
            self.assertLess(sim.state.fuel_kg, previous_fuel)
            self.assertLess(sim.state.mass_kg, previous_mass)
            previous_fuel = sim.state.fuel_kg
            previous_mass = sim.state.mass_kg

    def test_engines_flame_out_when_fuel_is_exhausted(self):
        sim = Session.new("a320", "clear", seed=42).sim
        sim.state.fuel_kg = 5.0
        sim.state.throttle_pct = 100.0
        for _ in range(6):
            sim.step_tick()
        self.assertEqual(sim.state.fuel_kg, 0.0)
        self.assertFalse(sim.state.engines_running)
        self.assertEqual(sim.readout().thrust_n, 0.0)
        self.assertIn("ENGINES OUT", sim.readout().warnings)

    def test_pitching_up_at_idle_stalls_the_wing(self):
        session = Session.new("a320neo", "clear", seed=42)
        session.execute("idle")
        session.execute("pitch up 18")
        stalled = False
        for _ in range(8):
            readout = session.sim.step_tick()
            if readout.stalled or "STALL" in readout.warnings:
                stalled = True
                break
        self.assertTrue(stalled, "the wing never stalled")

    def test_departed_wing_drops_its_nose_and_loses_authority(self):
        """A stalled airliner does not hold a commanded 20 degrees nose-up."""
        session = Session.new("a320neo", "clear", seed=42)
        session.execute("idle")
        session.execute("pitch up 20")
        for _ in range(10):
            session.sim.step_tick()
            if session.sim.state.status != physics.FLYING:
                break
        # Whatever else happened, alpha must not have run away to absurdity.
        self.assertLess(session.sim.readout().alpha_deg, 35.0)

    def test_the_pilot_is_warned_before_the_wing_lets_go(self):
        """A low-speed cue must appear on a tick before the stall does.

        Which rung of the ladder shows depends on how fast alpha is rising: a
        brisk pull can cross the whole stall-warning band inside one ten-second
        tick, leaving LOW SPEED as the cue that was visible. Either counts --
        what must never happen is a stall with no prior warning at all.
        """
        session = Session.new("a320", "clear", seed=42)
        session.execute("idle")
        session.execute("pitch up 14")
        warned = False
        for _ in range(12):
            readout = session.sim.step_tick()
            if "STALL" in readout.warnings:
                break
            if {"STALL WARNING", "LOW SPEED"} & set(readout.warnings):
                warned = True
        self.assertTrue(warned, "the wing stalled with no preceding cue")

    def test_load_factor_is_true_lift_over_weight(self):
        """Not the 1/cos(bank) approximation.

        A stalled wing at zero bank supports less than the aircraft's weight, so
        load factor must read below 1.0 -- where 1/cos(bank) would insist on
        exactly 1.0 and hide the fact that the aeroplane is falling.
        """
        sim = Session.new("a350", "clear", seed=42).sim
        self.assertAlmostEqual(sim.readout().load_factor, 1.0, places=1)

        session = Session.new("a320", "clear", seed=42)
        session.execute("idle")
        session.execute("pitch up 18")
        for _ in range(8):
            readout = session.sim.step_tick()
            if readout.stalled:
                break
        self.assertTrue(readout.stalled, "never reached the stall")
        self.assertLess(abs(readout.bank_deg), 5.0)
        self.assertLess(readout.load_factor, 1.0)

    def test_load_factor_rises_in_a_steep_turn(self):
        sim = Session.new("a350", "clear", seed=42).sim
        sim.state.cmd_bank_deg = 45.0
        for _ in range(4):
            sim.step_tick()
        self.assertGreater(sim.readout().load_factor, 1.15)

    def test_overspeed_warning_fires_past_vmo(self):
        sim = Session.new("a320", "clear", seed=42).sim
        sim.state.tas_ms = atm.ias_to_tas(
            (sim.aircraft.vmo_kt + 20) * atm.MS_PER_KT, sim.state.altitude_ft
        )
        self.assertIn("OVERSPEED", sim.readout().warnings)

    def test_flying_into_a_mountain_ends_the_simulation(self):
        session = Session.new("a320", "clear", seed=42)
        session.execute("idle")
        session.execute("set pitch -25")
        for _ in range(60):
            session.sim.step_tick()
            if session.sim.state.status != physics.FLYING:
                break
        self.assertEqual(session.sim.state.status, physics.CRASHED_TERRAIN)
        self.assertGreater(session.sim.state.impact_ias_kt, 0.0)
        self.assertTrue(session.sim.state.impact_feature)

    def test_simulation_does_not_step_once_finished(self):
        session = Session.new("a320", "clear", seed=42)
        session.sim.state.status = physics.CRASHED_TERRAIN
        before = session.sim.state.elapsed_s
        session.sim.step_tick()
        self.assertEqual(session.sim.state.elapsed_s, before)

    def test_commanded_heading_is_captured_and_released(self):
        sim = Session.new("a320", "clear", seed=42).sim
        sim.state.cmd_heading_deg = 180.0
        for _ in range(40):
            sim.step_tick()
            if sim.state.cmd_heading_deg is None:
                break
        self.assertIsNone(sim.state.cmd_heading_deg, "never rolled out")
        self.assertLess(abs(physics.wrap180(sim.state.heading_deg - 180.0)), 3.0)
        self.assertLess(abs(sim.state.bank_deg), 3.0)

    def test_wind_displaces_the_ground_track(self):
        """In a crosswind, track and heading must differ."""
        sim = Session.new("a320", "crosswind", seed=42).sim
        sim.step_tick()
        readout = sim.readout()
        self.assertGreater(abs(readout.drift_deg), 1.0)
        self.assertNotAlmostEqual(readout.track_deg, readout.heading_deg, places=1)

    def test_still_air_produces_no_drift(self):
        sim = Session.new("a320", "clear", seed=42).sim
        sim.weather = type(sim.weather)(
            **dict(vars(sim.weather), wind_speed_kt=0.0, turbulence=0.0)
        )
        readout = sim.readout()
        self.assertAlmostEqual(readout.drift_deg, 0.0, places=3)


class TestAngleHelpers(unittest.TestCase):
    def test_wrap180(self):
        self.assertAlmostEqual(physics.wrap180(190.0), -170.0)
        self.assertAlmostEqual(physics.wrap180(-190.0), 170.0)
        self.assertAlmostEqual(physics.wrap180(0.0), 0.0)

    def test_wrap360(self):
        self.assertAlmostEqual(physics.wrap360(-10.0), 350.0)
        self.assertAlmostEqual(physics.wrap360(370.0), 10.0)

    def test_clamp(self):
        self.assertEqual(physics.clamp(5, 0, 3), 3)
        self.assertEqual(physics.clamp(-5, 0, 3), 0)
        self.assertEqual(physics.clamp(2, 0, 3), 2)


if __name__ == "__main__":
    unittest.main()
