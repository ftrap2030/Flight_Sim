"""Approach guidance, touchdown grading, the rollout, and the new endings."""

import math
import unittest

from flight_sim import atmosphere as atm
from flight_sim import commands as cmd
from flight_sim import dashboard
from flight_sim import landing
from flight_sim import physics
from flight_sim.game import Session

SEED = 20260905


def place_on_final(key="a320neo", ident="KEBR", distance_nm=4.0, ias_kt=None,
                   gamma_deg=-3.0, crab_deg=0.0, bank_deg=0.0, across_ft=0.0,
                   height_offset_ft=0.0):
    """Put an aircraft on final approach to a named field, configured to land."""
    session = Session.new(key, "clear", seed=SEED)
    field = session.sim.airfields.by_ident(ident)
    direction = field.landing_direction_for_heading(field.runway_heading_deg)
    half = field.runway_length_nm / 2.0
    rad = math.radians(direction)
    thresh_x = field.x_nm - math.sin(rad) * half
    thresh_y = field.y_nm - math.cos(rad) * half

    state = session.sim.state
    state.x_nm = thresh_x - math.sin(rad) * distance_nm + math.cos(rad) * across_ft / 6076.12
    state.y_nm = thresh_y - math.cos(rad) * distance_nm - math.sin(rad) * across_ft / 6076.12
    state.altitude_ft = (
        field.elevation_ft
        + distance_nm * 6076.12 * math.tan(math.radians(-gamma_deg))
        + height_offset_ft
    )
    state.heading_deg = (direction + crab_deg) % 360.0
    state.bank_deg = bank_deg
    state.flaps = 4
    state.gear_down = True
    state.gamma_deg = gamma_deg
    reference = ias_kt if ias_kt is not None else landing.vref_kt(session.sim)
    state.tas_ms = atm.ias_to_tas(reference * atm.MS_PER_KT, state.altitude_ft)
    state.pitch_deg = state.cmd_pitch_deg = (
        session.sim.level_flight_pitch_deg() + gamma_deg
    )
    state.throttle_pct = session.sim.throttle_for_flight_path(gamma_deg)
    return session, field, direction


def touchdown_on_runway(sink_fpm=200.0, bank_deg=0.0, crab_deg=0.0,
                        across_ft=0.0, ias_kt=None, on_runway=True):
    """Grade a touchdown directly, without having to fly a whole approach."""
    session, field, direction = place_on_final(distance_nm=0.0, crab_deg=crab_deg,
                                               bank_deg=bank_deg, ias_kt=ias_kt)
    state = session.sim.state
    # Sit the aircraft a little way down the runway with a chosen sink rate.
    rad = math.radians(direction)
    half = field.runway_length_nm / 2.0
    along_nm = 1000.0 / 6076.12
    state.x_nm = (field.x_nm - math.sin(rad) * half + math.sin(rad) * along_nm
                  + math.cos(rad) * across_ft / 6076.12)
    state.y_nm = (field.y_nm - math.cos(rad) * half + math.cos(rad) * along_nm
                  - math.sin(rad) * across_ft / 6076.12)
    state.altitude_ft = field.elevation_ft
    vs_ms = -sink_fpm / atm.FPM_PER_MS
    state.gamma_deg = math.degrees(math.asin(
        max(-0.99, min(0.99, vs_ms / max(state.tas_ms, 1.0)))
    ))
    return session, field, landing.grade_touchdown(
        session.sim, field, session.sim.readout(), on_runway
    )


class TestVref(unittest.TestCase):
    def test_vref_is_1_3_times_the_stall_speed(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.sim.state.flaps = 4
        stall = session.sim.aircraft.stall_speed_ias_ms(
            session.sim.state.mass_kg, 1.0, 4
        ) * atm.KT_PER_MS
        self.assertAlmostEqual(
            landing.vref_kt(session.sim), stall * 1.3, places=6
        )

    def test_more_flap_lowers_vref(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        speeds = []
        for flaps in (0, 2, 4):
            session.sim.state.flaps = flaps
            speeds.append(landing.vref_kt(session.sim))
        self.assertEqual(speeds, sorted(speeds, reverse=True))

    def test_approach_attitude_is_not_on_the_edge_of_the_stall(self):
        """Flaps add camber, so Vref with full flap is a low deck angle.

        Raising CL_max without raising CL_0 would put the aircraft at 15 degrees
        of alpha at Vref -- past the critical angle, on every single approach.
        """
        session = Session.new("a320neo", "clear", seed=SEED)
        state = session.sim.state
        state.flaps = 4
        state.tas_ms = atm.ias_to_tas(
            landing.vref_kt(session.sim) * atm.MS_PER_KT, state.altitude_ft
        )
        state.pitch_deg = session.sim.level_flight_pitch_deg()
        state.gamma_deg = 0.0
        alpha = session.sim._aero_state().alpha_deg
        self.assertLess(alpha, session.sim.aircraft.alpha_crit_deg - 8.0)
        self.assertFalse(session.sim._aero_state().stalled)


class TestTouchdownGrading(unittest.TestCase):
    def test_sink_rate_bands(self):
        for sink, expected in [
            (30.0, "greaser"),
            (150.0, "normal landing"),
            (400.0, "firm landing"),
            (750.0, "hard landing"),
        ]:
            _s, _f, verdict = touchdown_on_runway(sink_fpm=sink)
            self.assertEqual(verdict.grade, expected, "at {:.0f} fpm".format(sink))
            self.assertTrue(verdict.survivable)

    def test_arriving_too_fast_collapses_the_gear(self):
        _s, _f, verdict = touchdown_on_runway(sink_fpm=1200.0)
        self.assertEqual(verdict.grade, "gear collapse")
        self.assertFalse(verdict.survivable)
        self.assertIn("feet a minute", verdict.reason)

    def test_landing_banked_strikes_a_wingtip(self):
        _s, _f, verdict = touchdown_on_runway(sink_fpm=150.0, bank_deg=14.0)
        self.assertEqual(verdict.grade, "wingtip strike")
        self.assertFalse(verdict.survivable)

    def test_landing_sideways_collapses_the_gear(self):
        _s, _f, verdict = touchdown_on_runway(sink_fpm=150.0, crab_deg=28.0)
        self.assertEqual(verdict.grade, "gear collapse")
        self.assertFalse(verdict.survivable)
        self.assertIn("out of line", verdict.reason)

    def test_a_small_crab_is_acceptable(self):
        _s, _f, verdict = touchdown_on_runway(sink_fpm=150.0, crab_deg=6.0)
        self.assertTrue(verdict.survivable)

    def test_the_verdict_records_the_numbers(self):
        _s, field, verdict = touchdown_on_runway(sink_fpm=200.0)
        self.assertEqual(verdict.field_ident, field.ident)
        self.assertGreater(verdict.vref_kt, 0.0)
        self.assertGreater(verdict.remaining_ft, 0.0)
        self.assertLess(verdict.remaining_ft, field.runway_length_ft)
        self.assertIn("fpm", verdict.summary())

    def test_a_gentle_excursion_is_survivable_and_a_violent_one_is_not(self):
        _s, _f, gentle = touchdown_on_runway(sink_fpm=200.0, on_runway=False)
        self.assertEqual(gentle.grade, "runway excursion")
        self.assertTrue(gentle.survivable)

        _s, _f, violent = touchdown_on_runway(sink_fpm=800.0, on_runway=False)
        self.assertEqual(violent.grade, "runway excursion")
        self.assertFalse(violent.survivable)


class TestApproachGuidance(unittest.TestCase):
    def test_guidance_appears_when_a_runway_is_ahead(self):
        session, field, _d = place_on_final(distance_nm=5.0)
        approach = session.sim.readout().approach
        self.assertIsNotNone(approach)
        self.assertTrue(approach.on_approach)
        self.assertEqual(approach.field.ident, field.ident)
        self.assertAlmostEqual(approach.distance_nm, 5.0, delta=0.2)

    def test_on_the_glidepath_reads_near_zero(self):
        """Placed on a path aimed at the touchdown point, deviation is nil.

        `place_on_final` positions the aircraft on a path aimed at the
        *threshold*, so the reading is offset by exactly the threshold crossing
        height -- correct, and worth measuring rather than papering over.
        """
        session, _f, _d = place_on_final(distance_nm=5.0)
        deviation = session.sim.readout().approach.glideslope_dev_ft
        aim_offset = landing.AIM_POINT_FT * math.tan(
            math.radians(landing.GLIDESLOPE_DEG)
        )
        self.assertAlmostEqual(deviation, -aim_offset, delta=20.0)

    def test_the_glidepath_crosses_the_threshold_at_about_fifty_feet(self):
        """The aim point is what gives a real approach its 50 ft TCH."""
        session, field, direction = place_on_final(distance_nm=5.0)
        state = session.sim.state
        # Put the aircraft exactly over the threshold, on the path.
        rad = math.radians(direction)
        half = field.runway_length_nm / 2.0
        state.x_nm = field.x_nm - math.sin(rad) * half
        state.y_nm = field.y_nm - math.cos(rad) * half
        approach = session.sim.readout().approach
        crossing_height = approach.target_altitude_ft - field.elevation_ft
        self.assertTrue(
            35.0 < crossing_height < 70.0,
            "threshold crossing height is {:.0f} ft".format(crossing_height),
        )

    def test_high_and_low_are_signed_correctly(self):
        high, _f, _d = place_on_final(distance_nm=5.0, height_offset_ft=400.0)
        self.assertGreater(high.sim.readout().approach.glideslope_dev_ft, 300.0)
        self.assertTrue(high.sim.readout().approach.high)

        low, _f, _d = place_on_final(distance_nm=5.0, height_offset_ft=-400.0)
        self.assertLess(low.sim.readout().approach.glideslope_dev_ft, -300.0)
        self.assertFalse(low.sim.readout().approach.high)

    def test_left_and_right_of_the_centreline_are_signed_correctly(self):
        right, _f, _d = place_on_final(distance_nm=5.0, across_ft=800.0)
        self.assertGreater(right.sim.readout().approach.across_ft, 500.0)
        self.assertGreater(right.sim.readout().approach.localiser_dev_deg, 0.0)

        left, _f, _d = place_on_final(distance_nm=5.0, across_ft=-800.0)
        self.assertLess(left.sim.readout().approach.across_ft, -500.0)
        self.assertLess(left.sim.readout().approach.localiser_dev_deg, 0.0)

    def test_a_runway_can_be_landed_from_either_end(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        field = session.sim.airfields.by_ident("KEBR")
        primary, reciprocal = field.landing_directions()
        self.assertAlmostEqual(
            abs(physics.wrap180(primary - reciprocal)), 180.0, places=6
        )
        self.assertEqual(field.landing_direction_for_heading(primary), primary)
        self.assertEqual(
            field.landing_direction_for_heading(reciprocal), reciprocal
        )

    def test_the_localiser_does_not_blow_up_over_the_threshold(self):
        """A foot of offset at the threshold must not read as 80 degrees."""
        session, _f, _d = place_on_final(distance_nm=0.01, across_ft=20.0)
        approach = session.sim.readout().approach
        self.assertLess(abs(approach.localiser_dev_deg), 15.0)

    def test_being_near_a_runway_is_not_the_same_as_approaching_it(self):
        """Guidance exists whenever a field is in range; on_approach is stricter.

        A cruising aircraft several miles abeam a field is not on its approach,
        and the panel must not clutter itself with guidance for a runway nobody
        is aiming at.
        """
        session = Session.new("a320neo", "clear", seed=SEED)
        session.sim.state.x_nm = 3000.0
        session.sim.state.y_nm = 3000.0
        approach = landing.approach_guidance(session.sim)
        self.assertIsNotNone(approach, "there is a field within range")
        self.assertFalse(approach.on_approach)

    def test_no_guidance_at_all_when_no_runway_is_within_range(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        state = session.sim.state
        # Find somewhere with nothing inside the approach range.
        for offset in range(0, 4000, 137):
            state.x_nm = 5000.0 + offset
            state.y_nm = -5000.0 - offset
            session.sim.airfields.ensure_loaded(state.x_nm, state.y_nm)
            if not session.sim.airfields.near(
                state.x_nm, state.y_nm, landing.APPROACH_RANGE_NM
            ):
                break
        else:
            self.skipTest("no empty quarter found in the search range")
        self.assertIsNone(landing.approach_guidance(session.sim))


class TestApproachSurfaces(unittest.TestCase):
    def test_the_glidepath_into_every_authored_field_is_clear(self):
        """A runway you cannot approach is not a runway."""
        session = Session.new("a320neo", "clear", seed=SEED)
        world = session.sim.terrain
        for field in session.sim.airfields.authored.fields:
            for direction in field.landing_directions():
                rad = math.radians(direction)
                half = field.runway_length_nm / 2.0
                tx = field.x_nm - math.sin(rad) * half
                ty = field.y_nm - math.cos(rad) * half
                for nm in (0.5, 1.0, 2.0, 3.0, 4.0, 5.0):
                    px = tx - math.sin(rad) * nm
                    py = ty - math.cos(rad) * nm
                    path = field.elevation_ft + nm * 6076.12 * math.tan(
                        math.radians(3.0)
                    )
                    self.assertLess(
                        world.elevation(px, py), path,
                        "{} runway {:.0f} blocked at {:.1f} nm".format(
                            field.ident, direction, nm
                        ),
                    )

    def test_approach_surfaces_only_cut_down_never_fill(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        world = session.sim.terrain
        field = session.sim.airfields.by_ident("KEBR")
        rad = math.radians(field.runway_heading_deg)
        half = field.runway_length_nm / 2.0
        tx = field.x_nm - math.sin(rad) * half
        ty = field.y_nm - math.cos(rad) * half
        for nm in (1.0, 2.5, 4.0, 6.0):
            px, py = tx - math.sin(rad) * nm, ty - math.cos(rad) * nm
            self.assertLessEqual(
                world.elevation(px, py), world.natural_elevation(px, py) + 1e-6
            )


class TestGroundContact(unittest.TestCase):
    def test_touching_down_off_an_airfield_is_still_a_crash(self):
        session = Session.new("a320", "clear", seed=SEED)
        session.execute("idle")
        session.execute("set pitch -25")
        for _ in range(60):
            session.sim.step_tick()
            if session.sim.state.status != physics.FLYING:
                break
        self.assertEqual(session.sim.state.status, physics.CRASHED_TERRAIN)
        self.assertIsNone(session.sim.state.touchdown)

    def test_a_survivable_arrival_starts_a_rollout_not_an_ending(self):
        session, field, _d = place_on_final(distance_nm=0.2)
        for _ in range(40):
            session.sim.step_tick(1.0)
            if session.sim.state.status != physics.FLYING:
                break
        self.assertEqual(session.sim.state.status, physics.ROLLOUT)
        self.assertFalse(session.finished, "the rollout is not an ending")
        self.assertTrue(session.sim.state.on_ground)
        self.assertEqual(session.sim.state.landing_field_ident, field.ident)

    def test_a_destroyed_arrival_is_a_crash_with_a_touchdown_record(self):
        session, _f, _d = place_on_final(distance_nm=0.2, bank_deg=25.0)
        # Hold the bank in: the control law would otherwise roll it out before
        # the wheels arrived, and the test would prove nothing.
        session.sim.state.cmd_bank_deg = 25.0
        for _ in range(40):
            session.sim.step_tick(1.0)
            if session.sim.state.status != physics.FLYING:
                break
        self.assertEqual(session.sim.state.status, physics.CRASHED_TERRAIN)
        self.assertIsNotNone(session.sim.state.touchdown)
        self.assertFalse(session.sim.state.touchdown["survivable"])


class TestRollout(unittest.TestCase):
    def rolled_out(self, brakes=True, reverse=True, spoilers=True):
        session, field, _d = place_on_final(distance_nm=0.2)
        for _ in range(40):
            session.sim.step_tick(1.0)
            if session.sim.state.status != physics.FLYING:
                break
        self.assertEqual(session.sim.state.status, physics.ROLLOUT)
        state = session.sim.state
        state.brakes = 1.0 if brakes else 0.0
        state.reverse_thrust = reverse
        state.spoilers = spoilers
        start = field.frame_for(
            state.x_nm, state.y_nm,
            field.landing_direction_for_heading(state.heading_deg))[0]
        for _ in range(120):
            session.sim.step_tick(1.0)
            if session.sim.state.status not in physics.LIVE_STATUSES:
                break
        end = field.frame_for(
            state.x_nm, state.y_nm,
            field.landing_direction_for_heading(state.heading_deg))[0]
        return session, field, end - start

    def test_braking_stops_the_aircraft_on_the_runway(self):
        session, field, used = self.rolled_out()
        self.assertEqual(session.sim.state.status, physics.LANDED)
        self.assertLess(used, field.runway_length_ft)
        self.assertLess(session.sim.readout().tas_kt, landing.STOPPED_KT)

    def test_braking_shortens_the_roll(self):
        _s1, _f1, with_brakes = self.rolled_out(brakes=True, reverse=True)
        _s2, _f2, without = self.rolled_out(brakes=False, reverse=False,
                                            spoilers=False)
        self.assertLess(with_brakes, without)

    def test_running_out_of_runway_is_an_overrun(self):
        session, field, _d = place_on_final(distance_nm=0.2)
        for _ in range(40):
            session.sim.step_tick(1.0)
            if session.sim.state.status != physics.FLYING:
                break
        # Sitting near the far end with no braking at all.
        state = session.sim.state
        direction = field.landing_direction_for_heading(state.heading_deg)
        rad = math.radians(direction)
        half = field.runway_length_nm / 2.0
        near_end_nm = (field.runway_length_ft - 400.0) / 6076.12
        state.x_nm = field.x_nm - math.sin(rad) * half + math.sin(rad) * near_end_nm
        state.y_nm = field.y_nm - math.cos(rad) * half + math.cos(rad) * near_end_nm
        state.brakes = 0.0
        state.reverse_thrust = False
        for _ in range(60):
            session.sim.step_tick(1.0)
            if session.sim.state.status not in physics.LIVE_STATUSES:
                break
        self.assertEqual(session.sim.state.status, physics.OVERRUN)

    def test_deceleration_responds_to_the_controls(self):
        session, _f, _d = place_on_final(distance_nm=0.2)
        for _ in range(40):
            session.sim.step_tick(1.0)
            if session.sim.state.status != physics.FLYING:
                break
        state = session.sim.state
        state.brakes = 0.0
        state.reverse_thrust = False
        state.spoilers = False
        idle = landing.rollout_deceleration(session.sim)
        state.brakes = 1.0
        braking = landing.rollout_deceleration(session.sim)
        state.reverse_thrust = True
        everything = landing.rollout_deceleration(session.sim)
        self.assertGreater(braking, idle)
        self.assertGreater(everything, braking)


class TestGpwsSuppression(unittest.TestCase):
    def test_a_normal_approach_does_not_trigger_a_terrain_warning(self):
        """A warning that fires on every landing is a warning nobody reads."""
        session, _f, _d = place_on_final(distance_nm=1.5)
        warnings = session.sim.readout().warnings
        self.assertNotIn("TERRAIN -- PULL UP", warnings)
        self.assertNotIn("TERRAIN", warnings)

    def test_it_still_warns_when_not_configured_to_land(self):
        session, _f, _d = place_on_final(distance_nm=1.5)
        session.sim.state.gear_down = False
        session.sim.state.flaps = 0
        self.assertTrue(
            any("TERRAIN" in w for w in session.sim.readout().warnings)
        )


class TestPanelAndCommands(unittest.TestCase):
    def test_the_panel_shows_approach_guidance_on_final(self):
        session, field, _d = place_on_final(distance_nm=5.0)
        text = dashboard.render(session.sim, session.sim.readout())
        self.assertIn("APPROACH", text)
        self.assertIn(field.name, text)
        self.assertIn("G/S", text)
        self.assertIn("LOC", text)
        self.assertIn("Vref", text)

    def test_the_panel_omits_guidance_in_the_cruise(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.sim.state.x_nm = 3000.0
        session.sim.state.y_nm = 3000.0
        self.assertNotIn(
            "APPROACH", dashboard.render(session.sim, session.sim.readout())
        )

    def test_ground_controls_parse_and_apply(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.execute("max brakes")
        self.assertAlmostEqual(session.sim.state.brakes, 1.0)
        session.execute("release brakes")
        self.assertAlmostEqual(session.sim.state.brakes, 0.0)
        session.execute("reverse thrust")
        self.assertTrue(session.sim.state.reverse_thrust)
        session.execute("stow reversers")
        self.assertFalse(session.sim.state.reverse_thrust)

    def test_brake_and_reverse_show_in_the_configuration(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.execute("brakes 60")
        session.execute("reverse thrust")
        text = dashboard.render(session.sim, session.sim.readout())
        self.assertIn("BRK 60%", text)
        self.assertIn("REV", text)


class TestEndings(unittest.TestCase):
    def ending_for(self, status, grade=None, survivable=True):
        session, field, _d = place_on_final(distance_nm=0.2)
        session.sim.state.status = status
        if grade:
            session.sim.state.touchdown = {
                "field_name": field.name, "grade": grade,
                "survivable": survivable, "sink_rate_fpm": 180.0,
                "ias_kt": 140.0, "speed_ratio": 1.0, "centreline_ft": 12.0,
                "crab_deg": 2.0, "remaining_ft": 5200.0,
                "reason": "the gear folded",
            }
        return session.narrator.ending(session.sim, session.sim.readout())

    def test_every_landing_grade_has_its_own_ending(self):
        texts = {}
        for grade in ("greaser", "normal landing", "firm landing",
                      "hard landing", "runway excursion"):
            text = self.ending_for(physics.LANDED, grade)
            self.assertIn("SIMULATION ENDED", text)
            texts[grade] = text
        # A greaser and an excursion must not read the same.
        self.assertNotEqual(texts["greaser"], texts["runway excursion"])
        self.assertIn("EXCURSION", texts["runway excursion"])

    def test_an_overrun_has_its_own_ending(self):
        text = self.ending_for(physics.OVERRUN, "normal landing")
        self.assertIn("OVERRUN", text)

    def test_a_wrecked_arrival_does_not_read_like_a_mountainside(self):
        crash = self.ending_for(physics.CRASHED_TERRAIN, "gear collapse", False)
        self.assertIn("LANDING ACCIDENT", crash)
        self.assertNotIn("CONTROLLED FLIGHT INTO TERRAIN", crash)


class TestPersistence(unittest.TestCase):
    def test_ground_state_survives_a_save_and_load(self):
        import os
        import tempfile

        path = os.path.join(tempfile.mkdtemp(), "landing.json")
        session, _f, _d = place_on_final(distance_nm=0.2)
        for _ in range(40):
            session.sim.step_tick(1.0)
            if session.sim.state.status != physics.FLYING:
                break
        session.execute("max brakes")
        session.save(path)

        restored = Session.load(path)
        self.assertEqual(restored.sim.state.on_ground, session.sim.state.on_ground)
        self.assertAlmostEqual(restored.sim.state.brakes, session.sim.state.brakes)
        self.assertEqual(
            restored.sim.state.landing_field_ident,
            session.sim.state.landing_field_ident,
        )
        self.assertEqual(
            restored.sim.state.touchdown["grade"],
            session.sim.state.touchdown["grade"],
        )


if __name__ == "__main__":
    unittest.main()
