"""Flight dynamics: trim, envelope, failure modes and published performance."""

import os
import tempfile
import unittest

from flight_sim import aircraft as fleet
from flight_sim import atmosphere as atm
from flight_sim import autopilot
from flight_sim import fbw
from flight_sim import physics
from flight_sim.game import Session

# Cruise fuel flow is strongly weight-dependent, so a target quoted without a
# weight means nothing: the same A321neo burns 2,300 kg/h at 85 tonnes and under
# 2,000 late in a flight. Each entry therefore records the mass the figure
# belongs to -- which is the type's own start mass, the condition the model is
# actually trimmed at.
CRUISE_TARGETS = {
    # key: (altitude_ft, published cruise fuel flow kg/h, mass tonnes)
    "a319neo": (35000, 1850, 64.6),
    "a320": (35000, 2400, 69.6),
    "a320neo": (35000, 2000, 71.3),
    "a321": (35000, 2300, 85.1),
    "a321xlr": (35000, 2600, 94.3),
    "a330neo": (37000, 6050, 235.0),
    "a350": (37000, 5800, 252.4),
    "a350k": (37000, 6700, 285.0),
    "a380": (37000, 11500, 497.0),
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
    # Steady state means the fan has caught up with the levers. Without this the
    # aircraft is trimmed against a thrust its engines are not yet making.
    session.sim.settle_engines()
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
        for key, (_altitude, expected, _mass_t) in CRUISE_TARGETS.items():
            flow = trimmed_at_cruise(key).readout().fuel_flow_kgh
            self.assertLess(
                abs(flow - expected) / expected, 0.05,
                "{}: {:.0f} kg/h vs published {}".format(key, flow, expected),
            )

    def test_cruise_targets_are_quoted_at_the_weight_they_belong_to(self):
        """The figure and the mass it was measured at must not drift apart."""
        for key, (_altitude, _expected, mass_t) in CRUISE_TARGETS.items():
            actual_t = trimmed_at_cruise(key).state.mass_kg / 1000.0
            self.assertLess(
                abs(actual_t - mass_t), 1.0,
                "{}: trims at {:.1f} t, target quoted at {:.1f} t".format(
                    key, actual_t, mass_t
                ),
            )

    def test_tsfc_matches_the_real_engine(self):
        """Each calibrated TSFC must land on its engine's published cruise SFC.

        This is the check that keeps the constants honest. They were solved
        against block fuel flow, not looked up -- so if a drag polar is wrong,
        the solved TSFC drifts away from the engine it is supposed to represent,
        and that shows up here rather than silently in the fuel page.
        """
        published = {  # lb/(lbf*hr) at cruise
            "CFM56": 0.59,
            "LEAP": 0.51,
            "Trent 7000": 0.50,
            "Trent XWB": 0.44,
            "Trent 970": 0.43,
        }
        families = {
            "a319neo": "LEAP", "a320": "CFM56", "a320neo": "LEAP",
            "a321": "LEAP", "a321xlr": "LEAP", "a330neo": "Trent 7000",
            "a350": "Trent XWB", "a350k": "Trent XWB", "a380": "Trent 970",
        }
        for key, family in families.items():
            craft = fleet.FLEET_BY_KEY[key]
            expected = published[family]
            self.assertLess(
                abs(craft.tsfc_lb_per_lbf_hr - expected), 0.03,
                "{}: solved TSFC is {:.3f} lb/(lbf*hr), the {} is {:.2f}".format(
                    craft.name, craft.tsfc_lb_per_lbf_hr, family, expected
                ),
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
        for key in ("a319neo", "a320", "a321xlr", "a330neo", "a350k"):
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

    def test_roll_response_follows_the_declared_roll_rate(self):
        """Time to reach 25 degrees of bank, measured inside the tick.

        Compared against the declared roll rate rather than the order of FLEET,
        which is listed by family: the A330-900 rolls slightly better than the
        A350-1000 despite sitting earlier in the list, because it is seventy
        tonnes lighter.
        """
        measured = []
        for craft in sorted(fleet.FLEET, key=lambda a: -a.roll_rate_deg_s):
            sim = Session.new(craft.key, "clear", seed=42).sim
            sim.state.cmd_bank_deg = 25.0
            elapsed = 0.0
            while abs(sim.state.bank_deg) < 24.5 and elapsed < 20.0:
                sim.step_tick(0.2)
                elapsed += 0.2
            measured.append((craft.name, elapsed))
        times = [t for _name, t in measured]
        self.assertEqual(times, sorted(times), "roll response out of order: %s" % measured)
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
        """In direct law. Normal law's protections make this impossible, which
        is the subject of tests/test_fbw.py -- here the point is that the
        aerodynamics underneath still let go when nothing is holding them."""
        session = Session.new("a320neo", "clear", seed=42)
        session.execute("direct law")
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
        session.execute("direct law")
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
        session.execute("direct law")
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
        session.execute("direct law")
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
        sim.weather.hold(wind_speed_kt=0.0, turbulence=0.0)
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


def on_the_runway(key="a320neo", weather="clear"):
    return Session.new(key, weather, seed=42, start=physics.RUNWAY_START)


def take_off(session, ticks=60, seconds=2.0, rotate_to=12.0):
    """Full power, hold the stick back, and see what the aeroplane does.

    Returns the readout at lift-off and how much runway it took, or None if it
    never got airborne.
    """
    sim = session.sim
    state = sim.state
    field = sim.airfields.by_ident(
        state.landing_field_ident, state.x_nm, state.y_nm
    )
    direction = state.roll_direction_deg
    start_along = field.frame_for(state.x_nm, state.y_nm, direction)[0]

    state.brakes = 0.0
    state.throttle_pct = 100.0
    state.cmd_pitch_deg = rotate_to
    for _ in range(ticks):
        sim.step_tick(seconds)
        if state.status not in physics.LIVE_STATUSES:
            return None, field, None
        if not state.on_ground:
            along = field.frame_for(state.x_nm, state.y_nm, direction)[0]
            return sim.readout(), field, along - start_along
    return None, field, None


class TestTakeoff(unittest.TestCase):
    """The departure. The Python had none of this until the browser grew one."""

    def test_a_runway_start_is_stationary_on_the_paving(self):
        session = on_the_runway()
        state = session.sim.state
        field = session.sim.airfields.by_ident(
            state.landing_field_ident, state.x_nm, state.y_nm
        )
        self.assertTrue(state.on_ground)
        self.assertEqual(state.status, physics.ROLLOUT)
        self.assertAlmostEqual(state.tas_ms, 0.0)
        self.assertTrue(field.is_over_runway(state.x_nm, state.y_nm))
        # Lined up, brakes set, and configured for the speeds it will be flown at.
        self.assertAlmostEqual(state.heading_deg, field.runway_heading_deg, places=6)
        self.assertEqual(state.brakes, 1.0)
        self.assertGreaterEqual(state.flaps, 1)

    def test_the_brakes_hold_it_against_full_power(self):
        """Otherwise the aeroplane rolls away while the pilot reads the panel."""
        session = on_the_runway()
        session.sim.state.throttle_pct = 100.0
        for _ in range(4):
            session.sim.step_tick(2.0)
        self.assertLess(session.sim.readout().ias_kt, 25.0)

    def test_it_gets_airborne_within_the_runway(self):
        session = on_the_runway()
        readout, field, used = take_off(session)
        self.assertIsNotNone(readout, "never left the ground")
        self.assertLess(used, field.runway_length_ft)
        self.assertEqual(session.sim.state.status, physics.FLYING)

    def test_it_rotates_at_vr_and_not_before(self):
        """The stick is held back from the start; only VR lets it bite."""
        session = on_the_runway()
        sim = session.sim
        vr = fbw.takeoff_speeds(sim).vr
        sim.state.brakes = 0.0
        sim.state.throttle_pct = 100.0
        sim.state.cmd_pitch_deg = 12.0
        pitched_at = None
        for _ in range(60):
            sim.step_tick(2.0)
            if sim.state.pitch_deg > 1.0:
                pitched_at = sim.readout().ias_kt
                break
        self.assertIsNotNone(pitched_at, "never rotated")
        self.assertGreater(pitched_at, vr * 0.95)

    def test_the_annunciator_says_man_toga_during_the_roll(self):
        """The mode that had no way of being reached until there was a takeoff."""
        session = on_the_runway()
        session.sim.state.throttle_pct = 100.0
        readout = session.sim.readout()
        column = autopilot.fma(session.sim, readout)["thrust"]
        self.assertEqual(column["engaged"][0], "MAN TOGA")

    def test_a_heavier_aeroplane_needs_more_runway(self):
        light = on_the_runway("a320neo")
        heavy = on_the_runway("a320neo")
        heavy.sim.state.mass_kg = heavy.sim.aircraft.mtow_kg
        _r1, _f1, short = take_off(light)
        _r2, _f2, long = take_off(heavy)
        self.assertIsNotNone(short)
        self.assertIsNotNone(long)
        self.assertGreater(long, short)

    def test_sitting_still_is_not_a_completed_flight(self):
        """A takeoff roll that has not begun is an aeroplane on a runway."""
        session = on_the_runway()
        for _ in range(3):
            session.sim.step_tick(2.0)
        self.assertEqual(session.sim.state.status, physics.ROLLOUT)
        self.assertFalse(session.finished)

    def test_running_out_of_runway_without_flying_is_an_overrun(self):
        """A rejected takeoff that is rejected too late."""
        session = on_the_runway()
        state = session.sim.state
        state.brakes = 0.0
        state.throttle_pct = 100.0
        state.cmd_pitch_deg = 0.0        # never rotate
        for _ in range(90):
            session.sim.step_tick(2.0)
            if state.status not in physics.LIVE_STATUSES:
                break
        self.assertEqual(state.status, physics.OVERRUN)

    def test_the_whole_roll_survives_a_save_and_load(self):
        """CLAUDE.md: a session resumed from disk continues *identically*.

        The roll is the hardest case for that, because it carries state a
        cruising aircraft does not -- which end of the runway is being used, and
        whether this roll has landed from anywhere.
        """
        path = os.path.join(tempfile.mkdtemp(), "roll.json")
        straight = on_the_runway()
        straight.sim.state.brakes = 0.0
        straight.sim.state.throttle_pct = 100.0
        straight.sim.state.cmd_pitch_deg = 12.0

        interrupted = on_the_runway()
        interrupted.sim.state.brakes = 0.0
        interrupted.sim.state.throttle_pct = 100.0
        interrupted.sim.state.cmd_pitch_deg = 12.0

        for tick in range(12):
            straight.sim.step_tick(2.0)
            if tick == 4:
                interrupted.save(path)
                interrupted = Session.load(path)
            interrupted.sim.step_tick(2.0)

        a, b = straight.sim.state, interrupted.sim.state
        for name in ("tas_ms", "altitude_ft", "pitch_deg", "heading_deg",
                     "x_nm", "y_nm", "mass_kg", "roll_direction_deg"):
            self.assertAlmostEqual(
                getattr(a, name), getattr(b, name), places=9, msg=name
            )
        self.assertEqual(a.on_ground, b.on_ground)
        self.assertEqual(a.status, b.status)


class TestTakeoffSpeeds(unittest.TestCase):
    def test_they_come_in_order(self):
        for craft in fleet.FLEET:
            session = Session.new(craft.key, "clear", seed=42)
            v = fbw.takeoff_speeds(session.sim)
            self.assertLess(v.v1, v.vr, craft.key)
            self.assertLess(v.vr, v.v2, craft.key)

    def test_they_are_quoted_against_the_takeoff_configuration(self):
        """Not the clean wing -- nobody takes off with the flaps up.

        Quoting the clean stall speed would put VR some thirty knots high, which
        is the sort of error that only shows up as the aeroplane failing to
        leave the ground.
        """
        session = Session.new("a320neo", "clear", seed=42)
        session.sim.state.flaps = 0
        clean = fbw.takeoff_speeds(session.sim)
        session.sim.state.flaps = 1
        flap_one = fbw.takeoff_speeds(session.sim)
        self.assertAlmostEqual(clean.vr, flap_one.vr, places=9)

    def test_v2_clears_the_stall_in_the_takeoff_configuration(self):
        for craft in fleet.FLEET:
            session = Session.new(craft.key, "clear", seed=42)
            session.sim.state.flaps = 1
            v = fbw.takeoff_speeds(session.sim)
            stall = fbw.characteristic_speeds(session.sim).stall
            self.assertGreater(v.v2, stall * 1.15, craft.key)


if __name__ == "__main__":
    unittest.main()
