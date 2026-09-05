"""The autopilot: channel behaviour, capture accuracy, and handing back."""

import math
import os
import tempfile
import unittest

from flight_sim import autopilot
from flight_sim import commands as cmd
from flight_sim import dashboard
from flight_sim import physics
from flight_sim.game import Session

SEED = 20260905


def cruising(key="a320neo", weather="clear"):
    return Session.new(key, weather, seed=SEED)


def fly(session, ticks, seconds=20.0):
    for _ in range(ticks):
        if session.sim.state.status != physics.FLYING:
            break
        session.sim.step_tick(seconds)
    return session.sim.readout()


class TestChannels(unittest.TestCase):
    def test_nothing_engaged_by_default(self):
        session = cruising()
        self.assertFalse(session.sim.state.ap_engaged)
        self.assertEqual(autopilot.channels(session.sim.state), [])
        self.assertEqual(autopilot.status_text(session.sim.state), "AP OFF")

    def test_engaging_holds_the_present_altitude_and_heading(self):
        session = cruising()
        altitude = session.sim.state.altitude_ft
        heading = session.sim.state.heading_deg
        session.execute("autopilot on")
        self.assertTrue(session.sim.state.ap_engaged)
        self.assertAlmostEqual(session.sim.state.ap_altitude_ft, altitude)
        self.assertAlmostEqual(session.sim.state.ap_heading_deg, heading)

    def test_disengaging_clears_every_channel(self):
        session = cruising()
        session.execute("set altitude 12000")
        session.execute("set speed 280")
        session.execute("autopilot off")
        state = session.sim.state
        self.assertFalse(state.ap_engaged)
        self.assertIsNone(state.ap_altitude_ft)
        self.assertIsNone(state.ap_speed_kt)
        self.assertIsNone(state.ap_heading_deg)

    def test_altitude_and_vertical_speed_are_mutually_exclusive(self):
        session = cruising()
        session.execute("set altitude 12000")
        session.execute("vertical speed 1200")
        self.assertIsNone(session.sim.state.ap_altitude_ft)
        self.assertAlmostEqual(session.sim.state.ap_vs_fpm, 1200.0)
        session.execute("set altitude 9000")
        self.assertIsNone(session.sim.state.ap_vs_fpm)

    def test_status_text_names_the_engaged_modes(self):
        session = cruising()
        session.execute("set altitude 12000")
        session.execute("set speed 280")
        text = autopilot.status_text(session.sim.state)
        self.assertIn("ALT 12,000", text)
        self.assertIn("SPD 280", text)

    def test_the_panel_shows_the_autopilot_only_when_engaged(self):
        session = cruising()
        self.assertNotIn("AP:", dashboard.render(session.sim, session.sim.readout()))
        session.execute("set altitude 14000")
        self.assertIn("AP:", dashboard.render(session.sim, session.sim.readout()))


class TestCommands(unittest.TestCase):
    def test_phrasings(self):
        for text, kind, value in [
            ("autopilot on", "ap_on", 0.0),
            ("ap off", "ap_off", 0.0),
            ("set altitude 12000", "ap_altitude", 12000.0),
            ("flight level 350", "ap_altitude", 35000.0),
            ("set speed 280", "ap_speed", 280.0),
            ("vertical speed -1500", "ap_vs", -1500.0),
            ("approach mode", "ap_approach", 0.0),
            ("ils", "ap_approach", 0.0),
        ]:
            command = cmd.parse(text)
            self.assertEqual(command.kind, kind, text)
            self.assertAlmostEqual(command.value, value, msg=text)

    def test_autopilot_commands_cost_no_simulation_time(self):
        session = cruising()
        before = session.sim.state.elapsed_s
        for text in ("set altitude 12000", "set speed 280", "autopilot off"):
            session.execute(text)
        self.assertEqual(session.sim.state.elapsed_s, before)

    def test_targets_are_clamped_to_something_flyable(self):
        session = cruising()
        session.execute("set altitude 90000")
        self.assertLessEqual(session.sim.state.ap_altitude_ft, 45000.0)
        session.execute("set speed 900")
        self.assertLessEqual(session.sim.state.ap_speed_kt, 400.0)


class TestDisengagement(unittest.TestCase):
    """The pilot taking a control wins, immediately and without argument."""

    def test_a_pitch_input_drops_the_vertical_channel_only(self):
        session = cruising()
        session.execute("set altitude 12000")
        session.execute("set speed 280")
        session.execute("pitch up 3")
        self.assertIsNone(session.sim.state.ap_altitude_ft)
        self.assertAlmostEqual(session.sim.state.ap_speed_kt, 280.0)

    def test_a_throttle_input_drops_the_speed_channel_only(self):
        session = cruising()
        session.execute("set altitude 12000")
        session.execute("set speed 280")
        session.execute("increase throttle 5")
        self.assertIsNone(session.sim.state.ap_speed_kt)
        self.assertAlmostEqual(session.sim.state.ap_altitude_ft, 12000.0)

    def test_a_bank_input_drops_the_heading_channel(self):
        session = cruising()
        session.execute("autopilot on")
        session.execute("bank left 20")
        self.assertIsNone(session.sim.state.ap_heading_deg)

    def test_a_heading_command_retargets_rather_than_disengages(self):
        session = cruising()
        session.execute("autopilot on")
        session.execute("heading 210")
        self.assertAlmostEqual(session.sim.state.ap_heading_deg, 210.0)

    def test_levelling_off_drops_the_vertical_channels(self):
        session = cruising()
        session.execute("set altitude 20000")
        session.execute("level off")
        self.assertIsNone(session.sim.state.ap_altitude_ft)
        self.assertIsNone(session.sim.state.ap_vs_fpm)


class TestCapture(unittest.TestCase):
    def test_it_climbs_to_an_altitude_and_holds_it(self):
        session = cruising()
        session.execute("set altitude 12000")
        session.execute("set speed 280")
        readout = fly(session, 45)
        self.assertEqual(session.sim.state.status, physics.FLYING)
        self.assertAlmostEqual(readout.altitude_ft, 12000.0, delta=120.0)
        self.assertLess(abs(readout.vertical_speed_fpm), 200.0)

    def test_it_descends_to_a_lower_altitude_too(self):
        session = cruising()
        session.sim.state.altitude_ft = 16000.0
        session.execute("set altitude 9000")
        session.execute("set speed 270")
        readout = fly(session, 40)
        self.assertAlmostEqual(readout.altitude_ft, 9000.0, delta=200.0)

    def test_the_autothrottle_holds_the_commanded_speed(self):
        session = cruising()
        session.execute("set altitude 12000")
        session.execute("set speed 265")
        readout = fly(session, 45)
        self.assertAlmostEqual(readout.ias_kt, 265.0, delta=6.0)

    def test_it_captures_a_heading_and_settles(self):
        session = cruising()
        session.execute("set altitude 10000")
        session.execute("heading 200")
        readout = fly(session, 30, seconds=15.0)
        self.assertLess(
            abs(physics.wrap180(readout.heading_deg - 200.0)), 3.0
        )
        self.assertLess(abs(readout.bank_deg), 4.0, "still oscillating")

    def test_it_holds_altitude_through_severe_turbulence(self):
        session = cruising("a350", "stormy")
        session.execute("set altitude 14000")
        session.execute("set speed 280")
        readout = fly(session, 40, seconds=15.0)
        self.assertAlmostEqual(readout.altitude_ft, 14000.0, delta=350.0)

    def test_vertical_speed_mode_flies_the_commanded_rate(self):
        session = cruising()
        session.execute("vertical speed 1000")
        session.execute("set speed 260")
        fly(session, 4)
        readout = fly(session, 6)
        self.assertAlmostEqual(readout.vertical_speed_fpm, 1000.0, delta=250.0)

    def test_it_does_not_exceed_its_own_limits(self):
        session = cruising()
        session.execute("set altitude 30000")
        session.execute("set speed 280")
        peak_vs = 0.0
        for _ in range(30):
            readout = session.sim.step_tick(15.0)
            peak_vs = max(peak_vs, abs(readout.vertical_speed_fpm))
            if session.sim.state.status != physics.FLYING:
                break
        self.assertLessEqual(peak_vs, autopilot.MAX_AP_VS_FPM + 250.0)


class TestApproachMode(unittest.TestCase):
    """The approach channel flies the ILS and hands over for the flare."""

    def place_on_final(self, key="a320neo", ident="KEBR", distance_nm=6.0):
        session = Session.new(key, "clear", seed=SEED)
        field = session.sim.airfields.by_ident(ident)
        direction = field.landing_direction_for_heading(field.runway_heading_deg)
        half = field.runway_length_nm / 2.0
        rad = math.radians(direction)
        state = session.sim.state
        state.x_nm = field.x_nm - math.sin(rad) * (half + distance_nm)
        state.y_nm = field.y_nm - math.cos(rad) * (half + distance_nm)
        state.altitude_ft = field.elevation_ft + distance_nm * 6076.12 * math.tan(
            math.radians(3.0)
        )
        state.heading_deg = direction
        state.flaps = 4
        state.gear_down = True
        state.gamma_deg = -3.0
        from flight_sim import atmosphere as atm
        from flight_sim import landing

        vref = landing.vref_kt(session.sim)
        state.tas_ms = atm.ias_to_tas(vref * atm.MS_PER_KT, state.altitude_ft)
        state.pitch_deg = state.cmd_pitch_deg = (
            session.sim.level_flight_pitch_deg() - 3.0
        )
        state.throttle_pct = session.sim.throttle_for_flight_path(-3.0)
        state.ap_engaged = True
        state.ap_approach = True
        state.ap_speed_kt = vref
        return session, field, direction

    def run_to_handover(self, session, limit=2000):
        for _ in range(limit):
            if not session.sim.state.ap_approach:
                return True
            session.sim.step_tick(0.5)
            if session.sim.state.status != physics.FLYING:
                return False
        return False

    def test_it_hands_over_on_the_centreline_at_the_right_height(self):
        session, field, direction = self.place_on_final()
        self.assertTrue(self.run_to_handover(session), "never reached handover")

        state = session.sim.state
        readout = session.sim.readout()
        along, across = field.frame_for(state.x_nm, state.y_nm, direction)
        height = state.altitude_ft - field.elevation_ft

        self.assertLess(abs(height - autopilot.HANDOVER_AGL_FT), 20.0,
                        "handed over at {:.0f} ft above the runway".format(height))
        self.assertLess(abs(across), 60.0, "off the centreline at handover")
        self.assertTrue(-1500.0 < along < 2500.0,
                        "handed over {:.0f} ft from the threshold".format(along))
        self.assertLess(readout.vertical_speed_fpm, 0.0, "not descending")
        self.assertAlmostEqual(readout.ias_kt, readout.vref_kt, delta=12.0)

    def test_a_flare_after_handover_lands_the_aircraft(self):
        """The whole chain: guidance, handover, flare, touchdown."""
        session, field, _direction = self.place_on_final()
        hold = None
        for _ in range(2500):
            state = session.sim.state
            height = state.altitude_ft - field.elevation_ft
            if height < 30.0:
                if hold is None:
                    hold = session.sim.level_flight_pitch_deg() + 1.5
                state.ap_speed_kt = None
                state.ap_engaged = False
                state.cmd_pitch_deg = hold
                state.throttle_pct = 0.0
            session.sim.step_tick(0.4)
            if session.sim.state.status != physics.FLYING:
                break
        touchdown = session.sim.state.touchdown
        self.assertIsNotNone(touchdown, "never touched down")
        self.assertTrue(touchdown["survivable"], touchdown.get("reason"))
        self.assertEqual(touchdown["field_ident"], field.ident)

    def test_the_approach_channel_gives_up_rather_than_freezing(self):
        """Losing the approach must hand the aircraft back, not hold a pitch."""
        session, _field, direction = self.place_on_final(distance_nm=9.0)
        # Point it well away from the runway: the localiser is lost.
        session.sim.state.heading_deg = (direction + 120.0) % 360.0
        session.sim.state.ap_heading_deg = None
        for _ in range(30):
            session.sim.step_tick(1.0)
            if session.sim.state.status != physics.FLYING:
                break
        state = session.sim.state
        self.assertFalse(state.ap_approach)
        # It should now be holding an altitude rather than flying a frozen pitch.
        self.assertTrue(
            state.ap_altitude_ft is not None or not state.ap_engaged
        )


class TestPersistence(unittest.TestCase):
    def test_autopilot_state_survives_a_save_and_load(self):
        path = os.path.join(tempfile.mkdtemp(), "ap.json")
        session = cruising()
        session.execute("set altitude 15000")
        session.execute("set speed 275")
        session.execute("heading 120")
        fly(session, 3)
        session.save(path)

        restored = Session.load(path)
        state = restored.sim.state
        self.assertTrue(state.ap_engaged)
        self.assertAlmostEqual(state.ap_altitude_ft, 15000.0)
        self.assertAlmostEqual(state.ap_speed_kt, 275.0)
        self.assertAlmostEqual(state.ap_heading_deg, 120.0)


if __name__ == "__main__":
    unittest.main()
