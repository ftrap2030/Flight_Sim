"""Command parsing and application."""

import unittest

from flight_sim import commands as cmd
from flight_sim.game import Session


class TestParsing(unittest.TestCase):
    def assert_parses(self, text, kind, value=None):
        command = cmd.parse(text)
        self.assertEqual(command.kind, kind, "{!r} -> {}".format(text, command.kind))
        if value is not None:
            self.assertAlmostEqual(command.value, value, msg=repr(text))
        return command

    def test_throttle_phrasings(self):
        self.assert_parses("increase throttle 10%", "throttle_delta", 10.0)
        self.assert_parses("increase throttle 10", "throttle_delta", 10.0)
        self.assert_parses("add power 5", "throttle_delta", 5.0)
        self.assert_parses("reduce throttle 15", "throttle_delta", -15.0)
        self.assert_parses("decrease power by 20", "throttle_delta", -20.0)
        self.assert_parses("throttle 85", "throttle_set", 85.0)
        self.assert_parses("set throttle to 40", "throttle_set", 40.0)
        self.assert_parses("full power", "throttle_set", 100.0)
        self.assert_parses("idle", "throttle_set", 0.0)
        self.assert_parses("climb power", "throttle_set", 92.0)

    def test_pitch_phrasings(self):
        self.assert_parses("pitch nose down 5 degrees", "pitch_delta", -5.0)
        self.assert_parses("pitch up 3", "pitch_delta", 3.0)
        self.assert_parses("nose down 8", "pitch_delta", -8.0)
        self.assert_parses("set pitch 10", "pitch_set", 10.0)
        self.assert_parses("set pitch -4", "pitch_set", -4.0)
        self.assert_parses("descend", "pitch_delta", -5.0)
        self.assert_parses("climb", "pitch_delta", 8.0)
        self.assert_parses("level off", "level")
        self.assert_parses("straight and level", "level")

    def test_lateral_phrasings(self):
        self.assert_parses("turn left heading 180", "heading", 180.0)
        self.assert_parses("heading 090", "heading", 90.0)
        self.assert_parses("fly to heading 270", "heading", 270.0)
        self.assert_parses("bank right 25", "bank_set", 25.0)
        self.assert_parses("bank left 30", "bank_set", -30.0)
        self.assert_parses("turn left", "bank_set", -25.0)
        self.assert_parses("roll level", "bank_set", 0.0)

    def test_configuration_phrasings(self):
        self.assert_parses("flaps 2", "flaps", 2.0)
        self.assert_parses("flaps full", "flaps", 4.0)
        self.assert_parses("flaps up", "flaps", 0.0)
        self.assert_parses("gear down", "gear", 1.0)
        self.assert_parses("gear up", "gear", 0.0)
        self.assert_parses("speedbrakes out", "spoilers", 1.0)
        self.assert_parses("spoilers in", "spoilers", 0.0)

    def test_time_and_meta(self):
        self.assertEqual(cmd.parse("hold").kind, "hold")
        self.assertEqual(cmd.parse("wait 60 seconds").seconds, 60.0)
        self.assertEqual(cmd.parse("wait 2 minutes").seconds, 120.0)
        for text in ("help", "status", "quit"):
            self.assertFalse(cmd.parse(text).advances_time)

    def test_word_numbers_and_case(self):
        self.assert_parses("PITCH UP FIVE", "pitch_delta", 5.0)
        self.assert_parses("Increase Throttle Ten Percent", "throttle_delta", 10.0)

    def test_unrecognised_input_raises_with_a_hint(self):
        for text in ("", "   ", "do a barrel roll", "asdf"):
            with self.assertRaises(cmd.ParseError):
                cmd.parse(text)
        try:
            cmd.parse("fly to the moon")
        except cmd.ParseError as error:
            self.assertIn("help", str(error))


class TestApplication(unittest.TestCase):
    def setUp(self):
        self.session = Session.new("a320neo", "clear", seed=42)
        self.state = self.session.sim.state

    def apply(self, text):
        cmd.apply(self.session.sim, cmd.parse(text))

    def test_throttle_is_clamped_to_the_valid_range(self):
        self.apply("throttle 95")
        self.apply("increase throttle 40")
        self.assertEqual(self.state.throttle_pct, 100.0)
        self.apply("reduce throttle 500")
        self.assertEqual(self.state.throttle_pct, 0.0)

    def test_pitch_delta_accumulates_on_the_command_not_the_attitude(self):
        self.state.cmd_pitch_deg = 0.0
        self.apply("pitch up 5")
        self.apply("pitch up 5")
        self.assertAlmostEqual(self.state.cmd_pitch_deg, 10.0)

    def test_heading_command_clears_a_manual_bank(self):
        self.apply("bank left 30")
        self.assertIsNone(self.state.cmd_heading_deg)
        self.apply("turn left heading 180")
        self.assertEqual(self.state.cmd_heading_deg, 180.0)
        self.apply("bank right 20")
        self.assertIsNone(self.state.cmd_heading_deg)

    def test_level_resets_bank_and_heading_hold(self):
        self.apply("turn left heading 200")
        self.apply("level off")
        self.assertIsNone(self.state.cmd_heading_deg)
        self.assertEqual(self.state.cmd_bank_deg, 0.0)

    def test_configuration_changes_apply(self):
        self.apply("flaps 3")
        self.assertEqual(self.state.flaps, 3)
        self.apply("gear down")
        self.assertTrue(self.state.gear_down)
        self.apply("speedbrakes out")
        self.assertTrue(self.state.spoilers)

    def test_gear_and_flaps_increase_drag_in_flight(self):
        clean = self.session.sim._aero_state().drag
        self.apply("gear down")
        self.apply("flaps 2")
        self.assertGreater(self.session.sim._aero_state().drag, clean)


class TestSessionLoop(unittest.TestCase):
    def setUp(self):
        self.session = Session.new("a350", "clear", seed=42)

    def test_help_and_status_cost_no_simulation_time(self):
        before = self.session.sim.state.elapsed_s
        for text in ("help", "status"):
            output, finished = self.session.execute(text)
            self.assertFalse(finished)
            self.assertTrue(output)
        self.assertEqual(self.session.sim.state.elapsed_s, before)

    def test_a_bad_command_costs_no_time(self):
        before = self.session.sim.state.elapsed_s
        output, finished = self.session.execute("teleport to paris")
        self.assertFalse(finished)
        self.assertIn("Unrecognised", output)
        self.assertEqual(self.session.sim.state.elapsed_s, before)

    def test_a_good_command_advances_ten_seconds(self):
        before = self.session.sim.state.elapsed_s
        self.session.execute("increase throttle 5")
        self.assertAlmostEqual(self.session.sim.state.elapsed_s, before + 10.0)

    def test_wait_advances_the_requested_duration(self):
        before = self.session.sim.state.elapsed_s
        self.session.execute("wait 60 seconds")
        self.assertAlmostEqual(self.session.sim.state.elapsed_s, before + 60.0)

    def test_quit_ends_the_simulation(self):
        output, finished = self.session.execute("quit")
        self.assertTrue(finished)
        self.assertTrue(self.session.finished)
        self.assertIn("SIMULATION ENDED", output)

    def test_commands_are_refused_after_the_flight_ends(self):
        self.session.execute("quit")
        output, finished = self.session.execute("full power")
        self.assertTrue(finished)
        self.assertIn("already ended", output)


if __name__ == "__main__":
    unittest.main()
