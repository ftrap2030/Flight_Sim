"""The flight control laws.

The claims worth testing are the ones a pilot would make about the aeroplane:
that normal law will not let you stall it, that alternate law will, that
neither will let you pull it apart, and that losing everything takes the
protections away in the order the real reversions do.
"""

import unittest

from flight_sim import fbw
from flight_sim import physics
from flight_sim.game import Session


def fly(law, commands, ticks=14, key="a320neo", weather="clear"):
    """Run a scenario in one control law and report what the flight did."""
    session = Session.new(key, weather, seed=42)
    session.sim.state.control_law = law
    for command in commands:
        session.execute(command)

    peak_alpha = 0.0
    peak_n = 0.0
    peak_ias = 0.0
    stalled = False
    protections = []
    for _ in range(ticks):
        readout = session.sim.step_tick()
        peak_alpha = max(peak_alpha, readout.alpha_deg)
        peak_n = max(peak_n, readout.load_factor)
        peak_ias = max(peak_ias, readout.ias_kt)
        stalled = stalled or readout.stalled
        for label in readout.protections:
            if label not in protections:
                protections.append(label)
        if session.sim.state.status not in physics.LIVE_STATUSES:
            break
    return {
        "session": session,
        "alpha": peak_alpha,
        "n": peak_n,
        "ias": peak_ias,
        "stalled": stalled,
        "status": session.sim.state.status,
        "protections": protections,
    }


STALL_ATTEMPT = ["idle", "pitch up 20"]
OVERSTRESS_ATTEMPT = ["bank right 80", "set pitch 12"]
OVERSPEED_ATTEMPT = ["full power", "set pitch -25"]


class TestNormalLaw(unittest.TestCase):
    def test_the_aircraft_starts_in_normal_law(self):
        sim = Session.new("a350", "clear", seed=42).sim
        self.assertEqual(sim.state.control_law, fbw.NORMAL)
        self.assertEqual(sim.readout().control_law, fbw.NORMAL)

    def test_full_back_stick_will_not_stall_the_aeroplane(self):
        result = fly(fbw.NORMAL, STALL_ATTEMPT)
        self.assertFalse(result["stalled"], "normal law allowed a stall")
        self.assertIn("α-MAX", result["protections"])

    def test_alpha_is_held_at_alpha_max_and_no_further(self):
        craft = Session.new("a320neo", "clear", seed=42).sim.aircraft
        _prot, _floor, alpha_max = fbw.alpha_thresholds(craft)
        result = fly(fbw.NORMAL, STALL_ATTEMPT)
        self.assertLessEqual(result["alpha"], alpha_max + 0.5)
        # And it really did get there -- a protection that never engages proves
        # nothing about whether it works.
        self.assertGreater(result["alpha"], alpha_max - 1.5)

    def test_alpha_floor_firewalls_the_thrust(self):
        result = fly(fbw.NORMAL, STALL_ATTEMPT)
        self.assertIn("A.FLOOR TOGA", result["protections"])
        self.assertEqual(result["session"].sim.state.throttle_pct, 100.0)

    def test_alpha_floor_is_inhibited_near_the_ground(self):
        """It exists to save an aeroplane in the air, not to firewall the
        thrust levers in the flare."""
        session = Session.new("a320neo", "clear", seed=42)
        state = session.sim.state
        state.altitude_ft = session.sim.terrain.elevation(state.x_nm, state.y_nm) + 50.0
        state.alpha_floor_latched = True
        active = []
        fbw._alpha_floor(session.sim, 14.0, 11.5, 13.0, active)
        self.assertEqual(active, [])
        self.assertFalse(state.alpha_floor_latched)

    def test_bank_stops_at_sixty_seven_degrees(self):
        result = fly(fbw.NORMAL, ["bank right 80"], ticks=14, key="a350")
        self.assertLessEqual(abs(result["session"].sim.readout().bank_deg), 67.5)
        self.assertIn("BANK LIM", result["protections"])

    def test_load_factor_stops_at_two_and_a_half_g(self):
        result = fly(fbw.NORMAL, OVERSTRESS_ATTEMPT, key="a350", ticks=12)
        self.assertLessEqual(result["n"], 2.55)
        self.assertIn("LOAD n", result["protections"])
        self.assertNotEqual(result["status"], physics.STRUCTURAL_FAILURE)

    def test_high_speed_protection_keeps_a_dive_survivable(self):
        result = fly(fbw.NORMAL, OVERSPEED_ATTEMPT, ticks=20)
        self.assertIn("HIGH SPEED PROT", result["protections"])
        self.assertNotEqual(result["status"], physics.STRUCTURAL_FAILURE)

    def test_the_pitch_command_cannot_exceed_the_attitude_limits(self):
        session = Session.new("a350", "clear", seed=42)
        session.sim.state.cmd_pitch_deg = 45.0
        session.sim.state.gamma_deg = 40.0  # so alpha is not what limits it
        fbw.apply(session.sim, 0.1)
        self.assertLessEqual(session.sim.law_pitch_target, fbw.PITCH_MAX_DEG)


class TestAlternateLaw(unittest.TestCase):
    def test_a_determined_pull_does_stall_it(self):
        result = fly(fbw.ALTERNATE, STALL_ATTEMPT)
        self.assertTrue(result["stalled"], "alternate law prevented a stall")
        self.assertIn("LOW SPEED STAB", result["protections"])

    def test_low_speed_stability_resists_without_preventing(self):
        """The distinction that matters: the demand opposes the pilot rather
        than overriding them, so a small pull is damped and a big one wins."""
        session = Session.new("a320neo", "clear", seed=42)
        state = session.sim.state
        state.control_law = fbw.ALTERNATE
        _prot, _floor, alpha_max = fbw.alpha_thresholds(session.sim.aircraft)
        # Slow, so that the load factor ceiling is far past the stall and out of
        # the way. At the opening 250 kt it is *below* alpha max and would be
        # the thing doing the limiting, which is a different behaviour.
        state.tas_ms *= 0.6
        state.gamma_deg = 0.0
        state.cmd_pitch_deg = alpha_max + 6.0
        fbw.apply(session.sim, 0.1)
        target_alpha = session.sim.law_pitch_target - state.gamma_deg
        self.assertGreater(target_alpha, alpha_max)  # not clamped to the limit
        self.assertLess(target_alpha, state.cmd_pitch_deg)  # but resisted
        # The pilot's own command is left alone -- overwriting it would turn
        # a resisted pull into a forbidden one on the very next substep.
        self.assertAlmostEqual(state.cmd_pitch_deg, alpha_max + 6.0)

    def test_load_factor_limiting_survives_the_reversion(self):
        """It is part of the basic pitch law, not one of the protections."""
        result = fly(fbw.ALTERNATE, OVERSTRESS_ATTEMPT, key="a350", ticks=12)
        self.assertLess(result["n"], 3.0)
        self.assertIn("LOAD n", result["protections"])

    def test_bank_is_no_longer_limited(self):
        result = fly(fbw.ALTERNATE, ["bank right 80"], ticks=16, key="a350")
        self.assertGreater(result["session"].sim.readout().bank_deg, 70.0)
        self.assertNotIn("BANK LIM", result["protections"])


class TestDirectLaw(unittest.TestCase):
    def test_nothing_is_protecting_you(self):
        result = fly(fbw.DIRECT, STALL_ATTEMPT)
        self.assertTrue(result["stalled"])
        self.assertEqual(result["protections"], [])

    def test_the_aircraft_can_be_pulled_apart(self):
        result = fly(fbw.DIRECT, OVERSTRESS_ATTEMPT, key="a350", ticks=12)
        self.assertGreater(result["n"], 3.0)
        self.assertEqual(result["status"], physics.STRUCTURAL_FAILURE)

    def test_the_aircraft_can_be_dived_past_vmo_until_it_breaks(self):
        result = fly(fbw.DIRECT, OVERSPEED_ATTEMPT, ticks=20)
        self.assertGreater(result["ias"], 400.0)
        self.assertEqual(result["status"], physics.STRUCTURAL_FAILURE)


class TestReversions(unittest.TestCase):
    def test_losing_every_engine_drops_to_alternate_law(self):
        session = Session.new("a320neo", "clear", seed=42)
        self.assertEqual(session.sim.state.control_law, fbw.NORMAL)
        session.execute("engine failure")
        session.execute("shutdown engine 2")
        session.sim.step_tick()
        self.assertEqual(session.sim.state.control_law, fbw.ALTERNATE)

    def test_one_engine_out_of_four_does_not_degrade_anything(self):
        session = Session.new("a380", "clear", seed=42)
        session.execute("engine failure")
        session.sim.step_tick()
        self.assertEqual(session.sim.state.control_law, fbw.NORMAL)

    def test_gear_down_in_alternate_law_reverts_to_direct(self):
        session = Session.new("a320neo", "clear", seed=42)
        session.sim.state.control_law = fbw.ALTERNATE
        session.execute("gear down")
        session.sim.step_tick()
        self.assertEqual(session.sim.state.control_law, fbw.DIRECT)

    def test_gear_down_in_normal_law_changes_nothing(self):
        session = Session.new("a320neo", "clear", seed=42)
        session.execute("gear down")
        session.sim.step_tick()
        self.assertEqual(session.sim.state.control_law, fbw.NORMAL)

    def test_the_reversion_latches(self):
        """Finding an engine again does not give the protections back."""
        session = Session.new("a320neo", "clear", seed=42)
        session.execute("engine failure")
        session.execute("shutdown engine 2")
        session.sim.step_tick()
        session.execute("restart engines")
        session.sim.step_tick()
        self.assertTrue(session.sim.state.engines_running)
        self.assertEqual(session.sim.state.control_law, fbw.ALTERNATE)


class TestLimitsAndThresholds(unittest.TestCase):
    def test_alpha_thresholds_are_ordered_and_below_the_stall(self):
        from flight_sim import aircraft as fleet

        for craft in fleet.FLEET:
            prot, floor, maximum = fbw.alpha_thresholds(craft)
            self.assertLess(prot, floor)
            self.assertLess(floor, maximum)
            self.assertLess(maximum, craft.alpha_crit_deg)

    def test_flaps_tighten_the_load_factor_envelope(self):
        session = Session.new("a320neo", "clear", seed=42)
        clean = fbw.load_factor_limits(session.sim.state)
        session.sim.state.flaps = 3
        flapped = fbw.load_factor_limits(session.sim.state)
        self.assertLess(flapped[1], clean[1])
        self.assertGreater(flapped[0], clean[0])

    def test_the_load_factor_law_is_not_a_second_alpha_protection(self):
        """It limits the g demanded, not the g the wing can make.

        Clamping it at CL_max would have made it into an accidental AoA
        protection, silently active in the two laws that are supposed to have
        none -- which is exactly the bug this test exists to catch.
        """
        session = Session.new("a320neo", "clear", seed=42)
        state = session.sim.state
        state.tas_ms *= 0.55  # slow: 2.5 g is far beyond what the wing can make
        demanded = fbw.alpha_for_load_factor(session.sim, 2.5)
        self.assertGreater(demanded, session.sim.aircraft.alpha_crit_deg)

    def test_the_two_limits_swap_places_with_speed(self):
        """Fast, the load factor limit binds; slow, only alpha max is left."""
        session = Session.new("a320neo", "clear", seed=42)
        state = session.sim.state
        _prot, _floor, alpha_max = fbw.alpha_thresholds(session.sim.aircraft)

        state.tas_ms *= 1.6
        self.assertLess(fbw.alpha_for_load_factor(session.sim, 2.5), alpha_max)
        state.tas_ms /= 2.6
        self.assertGreater(fbw.alpha_for_load_factor(session.sim, 2.5), alpha_max)

    def test_overspeed_reads_zero_inside_the_envelope(self):
        sim = Session.new("a350", "clear", seed=42).sim
        self.assertEqual(fbw.overspeed_kt(sim), 0.0)
        sim.state.tas_ms *= 2.0
        self.assertGreater(fbw.overspeed_kt(sim), 0.0)

    def test_resolve_accepts_what_a_pilot_would_type(self):
        self.assertEqual(fbw.resolve("normal"), fbw.NORMAL)
        self.assertEqual(fbw.resolve("Direct Law"), fbw.DIRECT)
        self.assertEqual(fbw.resolve("altn"), fbw.ALTERNATE)
        self.assertIsNone(fbw.resolve("sideways"))
        self.assertIsNone(fbw.resolve(""))


class TestReporting(unittest.TestCase):
    def test_the_law_is_on_the_panel(self):
        from flight_sim import dashboard

        session = Session.new("a320neo", "clear", seed=42)
        panel = dashboard.render(session.sim, session.sim.readout())
        self.assertIn("NORMAL LAW", panel)

        session.execute("direct law")
        panel = dashboard.render(session.sim, session.sim.readout())
        self.assertIn("DIRECT LAW", panel)

    def test_a_degraded_law_raises_a_warning(self):
        session = Session.new("a320neo", "clear", seed=42)
        self.assertNotIn("NORMAL LAW", session.sim.readout().warnings)
        session.execute("alternate law")
        self.assertIn("ALTERNATE LAW", session.sim.readout().warnings)

    def test_protections_are_accumulated_over_the_whole_tick(self):
        """A limit that held for half a second inside a ten-second tick still
        held, and the panel has to be able to say so."""
        result = fly(fbw.NORMAL, STALL_ATTEMPT, ticks=6)
        self.assertIn("α-PROT", result["protections"])

    def test_the_law_card_describes_the_active_law(self):
        from flight_sim import dashboard

        session = Session.new("a350", "clear", seed=42)
        for law, phrase in (
            (fbw.NORMAL, "cannot stall"),
            (fbw.ALTERNATE, "can now be stalled"),
            (fbw.DIRECT, "stick moves the surfaces"),
        ):
            session.sim.state.control_law = law
            card = dashboard.law_card(session.sim)
            self.assertIn(fbw.LAW_NAMES[law], card)
            self.assertIn(phrase, card)

    def test_selecting_a_law_costs_no_simulation_time(self):
        session = Session.new("a320neo", "clear", seed=42)
        before = session.sim.state.elapsed_s
        text, finished = session.execute("direct law")
        self.assertFalse(finished)
        self.assertEqual(session.sim.state.elapsed_s, before)
        self.assertIn("DIRECT LAW", text)

    def test_the_law_survives_serialisation(self):
        session = Session.new("a320neo", "clear", seed=42)
        session.execute("alternate law")
        session.sim.state.alpha_floor_latched = True
        restored = Session.from_dict(session.to_dict())
        self.assertEqual(restored.sim.state.control_law, fbw.ALTERNATE)
        self.assertTrue(restored.sim.state.alpha_floor_latched)


class TestLowEnergy(unittest.TestCase):
    def test_speed_speed_speed_on_a_decaying_approach(self):
        session = Session.new("a320neo", "clear", seed=42)
        state = session.sim.state
        state.flaps = 3
        state.gear_down = True
        state.altitude_ft = (
            session.sim.terrain.elevation(state.x_nm, state.y_nm) + 800.0
        )
        state.throttle_pct = 20.0
        readout = session.sim.readout()
        state.tas_ms = readout.stall_ias_kt * 1.05 * 0.514444
        self.assertIn("SPEED SPEED SPEED", session.sim.readout().warnings)

    def test_it_stays_quiet_when_the_aircraft_has_energy(self):
        session = Session.new("a320neo", "clear", seed=42)
        self.assertNotIn("SPEED SPEED SPEED", session.sim.readout().warnings)


if __name__ == "__main__":
    unittest.main()
