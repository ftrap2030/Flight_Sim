"""Session persistence, the dashboard, and the command-line entry point."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import main as cli
from flight_sim import aircraft as fleet
from flight_sim import dashboard
from flight_sim import weather as wx
from flight_sim.game import Session


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "flight.json")

    def test_round_trip_preserves_the_flight_exactly(self):
        session = Session.new("a350", "stormy", seed=99)
        for text in ("full power", "turn left heading 210", "pitch up 4"):
            session.execute(text)
        session.save(self.path)

        restored = Session.load(self.path)
        original, copy = session.sim.state, restored.sim.state
        for field in ("tas_ms", "altitude_ft", "pitch_deg", "bank_deg",
                      "heading_deg", "gamma_deg", "fuel_kg", "mass_kg",
                      "x_nm", "y_nm", "elapsed_s", "tick"):
            self.assertAlmostEqual(
                getattr(original, field), getattr(copy, field), places=9, msg=field
            )
        self.assertEqual(copy.aircraft_key, "a350")
        self.assertEqual(copy.weather_key, "stormy")

    def test_resumed_session_continues_identically(self):
        """Save, reload, and the next tick must match an uninterrupted run."""
        straight = Session.new("a320neo", "crosswind", seed=7)
        straight.execute("increase throttle 10")
        straight.execute("hold")

        interrupted = Session.new("a320neo", "crosswind", seed=7)
        interrupted.execute("increase throttle 10")
        interrupted.save(self.path)
        resumed = Session.load(self.path)
        resumed.execute("hold")

        self.assertAlmostEqual(
            straight.sim.state.altitude_ft, resumed.sim.state.altitude_ft, places=6
        )
        self.assertAlmostEqual(
            straight.sim.state.tas_ms, resumed.sim.state.tas_ms, places=6
        )

    def test_turbulence_state_survives_the_round_trip(self):
        session = Session.new("a380", "stormy", seed=3)
        session.execute("hold")
        session.save(self.path)
        self.assertNotEqual(session.sim.state.turb, [0.0, 0.0, 0.0])
        self.assertEqual(Session.load(self.path).sim.state.turb, session.sim.state.turb)

    def test_narrator_memory_survives_so_prose_keeps_varying(self):
        session = Session.new("a320", "clear", seed=5)
        session.execute("hold")
        session.save(self.path)
        self.assertEqual(
            list(Session.load(self.path).narrator._recent),
            list(session.narrator._recent),
        )

    def test_saved_file_is_valid_json(self):
        Session.new("a321", "foggy", seed=1).save(self.path)
        with open(self.path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["version"], 1)
        self.assertIn("state", data)


class TestDashboard(unittest.TestCase):
    def test_renders_for_every_aircraft_and_weather(self):
        for craft in fleet.FLEET:
            for profile in wx.WEATHER_OPTIONS:
                session = Session.new(craft.key, profile.key, seed=42)
                text = dashboard.render(session.sim, session.sim.readout())
                self.assertIn(craft.name, text)
                self.assertIn("Airspeed", text)
                self.assertIn("Altitude", text)

    def test_warnings_appear_in_the_panel(self):
        session = Session.new("a320", "clear", seed=42)
        session.sim.state.fuel_kg = 100.0
        text = dashboard.render(session.sim, session.sim.readout())
        self.assertIn("LOW FUEL", text)

    def test_attitude_indicator_has_the_expected_shape(self):
        rows = dashboard.attitude_indicator(5.0, 20.0)
        self.assertEqual(len(rows), 7)
        for row in rows:
            self.assertEqual(len(row), dashboard.HORIZON_WIDTH)

    def test_attitude_indicator_is_not_inverted(self):
        """Pitch up must show more sky, as on a real ADI."""
        def sky_rows(pitch):
            return sum(1 for row in dashboard.attitude_indicator(pitch, 0.0)
                       if row.count("`") > dashboard.HORIZON_WIDTH // 2)

        self.assertGreater(sky_rows(20.0), sky_rows(0.0))
        self.assertLess(sky_rows(-20.0), sky_rows(0.0))

    def test_clock_rounds_a_ten_second_tick_to_ten(self):
        session = Session.new("a320", "clear", seed=42)
        session.execute("hold")
        self.assertIn("T+00:10", dashboard.render(session.sim, session.sim.readout()))

    def test_menus_list_the_whole_fleet_and_all_weather(self):
        menu = dashboard.fleet_menu()
        for craft in fleet.FLEET:
            self.assertIn(craft.name, menu)
        weather_menu = dashboard.weather_menu()
        for profile in wx.WEATHER_OPTIONS:
            self.assertIn(profile.name, weather_menu)

    def test_briefing_names_the_aircraft_and_weather(self):
        session = Session.new("a380", "foggy", seed=42)
        text = dashboard.briefing(
            session.sim.aircraft, session.sim.weather, session.sim.readout()
        )
        self.assertIn("A380-800", text)
        self.assertIn("Foggy", text)
        self.assertIn(wx.FOGGY.summary, text)


class TestWeatherResolution(unittest.TestCase):
    def test_resolve_accepts_numbers_and_names(self):
        self.assertIs(wx.resolve("1"), wx.CLEAR)
        self.assertIs(wx.resolve("clear skies"), wx.CLEAR)
        self.assertIs(wx.resolve("STORMY"), wx.STORMY)
        self.assertIs(wx.resolve("fog"), wx.FOGGY)
        self.assertIs(wx.resolve("heavy crosswinds"), wx.CROSSWIND)
        self.assertIsNone(wx.resolve("sleet"))

    def test_wind_components(self):
        # Wind straight down the nose is pure headwind.
        headwind, crosswind = wx.CROSSWIND.wind_components(wx.CROSSWIND.wind_dir_deg)
        self.assertAlmostEqual(headwind, wx.CROSSWIND.wind_speed_kt, places=6)
        self.assertAlmostEqual(crosswind, 0.0, places=6)
        # Ninety degrees off is pure crosswind.
        _headwind, crosswind = wx.CROSSWIND.wind_components(
            wx.CROSSWIND.wind_dir_deg - 90.0
        )
        self.assertAlmostEqual(abs(crosswind), wx.CROSSWIND.wind_speed_kt, places=6)


class TestCommandLine(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "cli.json")

    def run_cli(self, argv):
        buffer = io.StringIO()
        # Error-path tests deliberately trigger usage messages; keep them out of
        # the test runner's output.
        with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
            code = cli.main(argv)
        return code, buffer.getvalue()

    def test_list_prints_both_menus(self):
        code, output = self.run_cli(["--list"])
        self.assertEqual(code, 0)
        self.assertIn("A320neo", output)
        self.assertIn("Heavy Crosswinds", output)

    def test_new_flight_then_resume_from_the_state_file(self):
        code, output = self.run_cli(
            ["--state-file", self.path, "--new", "--aircraft", "a350",
             "--weather", "stormy", "--command", "full power"]
        )
        self.assertEqual(code, 0)
        self.assertIn("A350-900", output)
        self.assertTrue(os.path.exists(self.path))

        code, output = self.run_cli(
            ["--state-file", self.path, "--command", "turn left heading 180"]
        )
        self.assertEqual(code, 0)
        self.assertIn("turn left heading 180", output)
        self.assertEqual(Session.load(self.path).sim.state.tick, 2)

    def test_unknown_aircraft_is_rejected(self):
        code, _output = self.run_cli(
            ["--state-file", self.path, "--new", "--aircraft", "concorde",
             "--command", "hold"]
        )
        self.assertEqual(code, 2)

    def test_command_without_state_file_is_rejected(self):
        self.assertEqual(self.run_cli(["--command", "hold"])[0], 2)

    def test_several_commands_run_in_order(self):
        _code, output = self.run_cli(
            ["--state-file", self.path, "--new", "--aircraft", "a320",
             "--command", "full power", "--command", "pitch up 5",
             "--command", "hold"]
        )
        self.assertLess(output.index("full power"), output.index("pitch up 5"))
        self.assertEqual(Session.load(self.path).sim.state.tick, 3)


if __name__ == "__main__":
    unittest.main()
