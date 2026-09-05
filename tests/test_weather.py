"""Living weather: evolution, wind shear, terrain coupling and time of day."""

import math
import os
import tempfile
import unittest

from flight_sim import commands as cmd
from flight_sim import dashboard
from flight_sim import weather as wx
from flight_sim.game import Session
from flight_sim.terrain import Terrain
from flight_sim.weather import WeatherState

SEED = 20260905


class TestWeatherState(unittest.TestCase):
    def test_it_delegates_to_the_profile_for_fixed_character(self):
        state = WeatherState(wx.STORMY, seed=1)
        self.assertEqual(state.name, "Stormy")
        self.assertEqual(state.key, "stormy")
        self.assertTrue(state.lightning)
        self.assertEqual(state.summary, wx.STORMY.summary)

    def test_live_values_win_over_the_profile(self):
        state = WeatherState(wx.CLEAR, seed=1).hold(wind_speed_kt=99.0)
        self.assertEqual(state.wind_speed_kt, 99.0)
        self.assertEqual(state.profile.wind_speed_kt, wx.CLEAR.wind_speed_kt)

    def test_methods_report_live_values_not_the_profiles(self):
        """Reached through __getattr__ these would bind to the profile."""
        state = WeatherState(wx.CLEAR, seed=1).hold(turbulence=0.9)
        self.assertEqual(state.turbulence_label(), "EXTREME")
        self.assertEqual(wx.CLEAR.turbulence_label(), "NIL")

        state.hold(wind_speed_kt=50.0, wind_dir_deg=90.0)
        headwind, _cross = state.wind_components(90.0)
        self.assertAlmostEqual(headwind, 50.0, places=6)


class TestEvolution(unittest.TestCase):
    def test_conditions_drift_over_a_flight(self):
        state = WeatherState(wx.CROSSWIND, seed=7)
        samples = []
        for minutes in range(0, 120, 10):
            state.advance_to(minutes * 60.0)
            samples.append((state.wind_speed_kt, state.wind_dir_deg,
                            state.visibility_sm, state.turbulence))
        self.assertGreater(len({s[0] for s in samples}), 5, "wind never changed")
        self.assertGreater(len({s[1] for s in samples}), 5, "direction never changed")

    def test_evolution_is_deterministic(self):
        first = WeatherState(wx.STORMY, seed=7)
        second = WeatherState(wx.STORMY, seed=7)
        for minutes in (0, 20, 55):
            first.advance_to(minutes * 60.0)
            second.advance_to(minutes * 60.0)
            self.assertAlmostEqual(first.wind_speed_kt, second.wind_speed_kt)
            self.assertAlmostEqual(first.visibility_sm, second.visibility_sm)

    def test_different_seeds_give_different_days(self):
        first = WeatherState(wx.STORMY, seed=1)
        second = WeatherState(wx.STORMY, seed=2)
        first.advance_to(1800.0)
        second.advance_to(1800.0)
        self.assertNotAlmostEqual(first.wind_speed_kt, second.wind_speed_kt)

    def test_it_stays_recognisably_the_same_weather(self):
        """Stormy must not quietly drift into a calm afternoon."""
        state = WeatherState(wx.STORMY, seed=3)
        for minutes in range(0, 180, 5):
            state.advance_to(minutes * 60.0)
            self.assertGreater(state.turbulence, 0.4)
            self.assertLess(state.visibility_sm, 6.0)
            self.assertGreater(state.wind_speed_kt, 20.0)

    def test_holding_stops_the_drift(self):
        state = WeatherState(wx.STORMY, seed=3).hold(wind_speed_kt=10.0)
        state.advance_to(9999.0)
        self.assertEqual(state.wind_speed_kt, 10.0)


class TestWindShear(unittest.TestCase):
    def setUp(self):
        self.state = WeatherState(wx.CROSSWIND, seed=1).hold(
            wind_speed_kt=45.0, wind_dir_deg=270.0
        )

    def test_wind_strengthens_with_height(self):
        speeds = [self.state.wind_at(agl)[0] for agl in (0, 200, 800, 2000, 6000)]
        self.assertEqual(speeds, sorted(speeds))
        self.assertLess(speeds[0], speeds[-1] * 0.6)

    def test_wind_backs_near_the_surface(self):
        surface = self.state.wind_at(0.0)[1]
        aloft = self.state.wind_at(5000.0)[1]
        self.assertAlmostEqual(aloft, 270.0, places=6)
        self.assertAlmostEqual(surface, 270.0 - wx.SURFACE_BACKING_DEG, places=6)

    def test_above_the_friction_layer_the_wind_is_the_gradient_wind(self):
        for agl in (wx.FRICTION_LAYER_FT, 10000.0, 30000.0):
            speed, direction = self.state.wind_at(agl)
            self.assertAlmostEqual(speed, 45.0, places=6)
            self.assertAlmostEqual(direction, 270.0, places=6)

    def test_descending_through_the_shear_changes_the_drift(self):
        session = Session.new("a320neo", "crosswind", seed=SEED)
        state = session.sim.state
        ground = session.sim.terrain.elevation(state.x_nm, state.y_nm)
        state.altitude_ft = ground + 6000.0
        high = session.sim.readout()
        state.altitude_ft = ground + 150.0
        low = session.sim.readout()
        self.assertNotAlmostEqual(high.wind_speed_kt, low.wind_speed_kt, places=1)
        self.assertNotAlmostEqual(high.drift_deg, low.drift_deg, places=1)


class TestTerrainCoupling(unittest.TestCase):
    """The prose promised rotor turbulence long before the physics delivered it."""

    def setUp(self):
        self.world = Terrain(seed=SEED)
        self.state = WeatherState(wx.CROSSWIND, seed=1).hold(
            wind_speed_kt=45.0, wind_dir_deg=270.0
        )

    def worst_rotor(self, agl_ft=1500.0):
        worst = 0.0
        where = None
        for i in range(120):
            x = 300.0 + i * 0.3
            value = self.state.mechanical_turbulence(self.world, x, 200.0, agl_ft)
            if value > worst:
                worst, where = value, x
        return worst, where

    def test_a_ridge_lee_is_genuinely_rough(self):
        worst, _where = self.worst_rotor()
        self.assertGreater(worst, 0.4)

    def test_calm_air_produces_no_rotor(self):
        self.state.hold(wind_speed_kt=2.0)
        worst, _where = self.worst_rotor()
        self.assertEqual(worst, 0.0)

    def test_rotor_dies_away_with_height(self):
        low, _w = self.worst_rotor(agl_ft=800.0)
        high, _w = self.worst_rotor(agl_ft=12000.0)
        self.assertGreater(low, high)
        self.assertEqual(high, 0.0)

    def test_stronger_wind_means_rougher_air(self):
        self.state.hold(wind_speed_kt=20.0)
        gentle, _w = self.worst_rotor()
        self.state.hold(wind_speed_kt=60.0)
        fierce, _w = self.worst_rotor()
        self.assertGreater(fierce, gentle)

    def test_mountain_wave_lifts_on_one_side_and_sinks_on_the_other(self):
        readings = [
            self.state.orographic_vertical_fpm(self.world, 300.0 + i * 0.3, 200.0, 1500.0)
            for i in range(120)
        ]
        self.assertGreater(max(readings), 150.0, "no updraught anywhere")
        self.assertLess(min(readings), -150.0, "no downdraught anywhere")

    def test_wave_decays_with_height_and_dies_in_calm_air(self):
        low = abs(self.state.orographic_vertical_fpm(self.world, 306.0, 200.0, 500.0))
        high = abs(self.state.orographic_vertical_fpm(self.world, 306.0, 200.0, 30000.0))
        self.assertGreater(low, high)
        self.state.hold(wind_speed_kt=1.0)
        self.assertEqual(
            self.state.orographic_vertical_fpm(self.world, 306.0, 200.0, 500.0), 0.0
        )

    def test_the_simulator_feels_the_terrain(self):
        session = Session.new("a320neo", "crosswind", seed=SEED)
        session.sim.weather.hold(wind_speed_kt=55.0, wind_dir_deg=270.0)
        found_rough = False
        for _ in range(40):
            session.sim.step_tick()
            if session.sim.state.status != "flying":
                break
            if session.sim.readout().rotor_turbulence > 0.05:
                found_rough = True
                break
        self.assertTrue(found_rough, "never met rough air in 40 ticks of 55 kt wind")


class TestTimeOfDay(unittest.TestCase):
    def test_the_sun_rises_and_sets(self):
        self.assertLess(wx.solar_elevation_deg(0.0), 0.0)
        self.assertGreater(wx.solar_elevation_deg(12.0), 50.0)
        self.assertLess(wx.solar_elevation_deg(23.0), 0.0)

    def test_light_phases(self):
        self.assertEqual(wx.light_phase(2.0), "night")
        self.assertEqual(wx.light_phase(12.0), "day")
        self.assertEqual(wx.light_phase(22.0), "night")
        self.assertIn(wx.light_phase(6.4), ("dawn", "golden"))
        self.assertIn(wx.light_phase(17.6), ("dusk", "golden"))

    def test_the_clock_advances_with_the_flight(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        start = session.sim.state.time_of_day_h
        flown = 0.0
        for _ in range(6):
            if session.sim.state.status != "flying":
                break
            session.sim.step_tick(120.0)
            flown += 120.0
        self.assertGreater(flown, 0.0)
        self.assertAlmostEqual(
            session.sim.state.time_of_day_h,
            (start + flown / 3600.0) % 24.0,
            places=6,
        )

    def test_the_clock_wraps_at_midnight(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.execute("time 23:59")
        session.sim.step_tick(120.0)
        self.assertLess(session.sim.state.time_of_day_h, 1.0)

    def test_setting_the_time(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        for text, expected in [("time 0530", 5.5), ("time 23:45", 23.75),
                               ("dawn", 5.5), ("midday", 12.0), ("night", 22.0)]:
            session.execute(text)
            self.assertAlmostEqual(
                session.sim.state.time_of_day_h, expected, places=3, msg=text
            )

    def test_setting_the_time_costs_no_simulation_time(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        before = session.sim.state.elapsed_s
        session.execute("dusk")
        self.assertEqual(session.sim.state.elapsed_s, before)

    def test_the_panel_shows_local_time_and_light(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.execute("time 2200")
        text = dashboard.render(session.sim, session.sim.readout())
        self.assertIn("22:00", text)
        self.assertIn("NIGHT", text)

    def test_night_changes_the_narration(self):
        session = Session.new("a320neo", "clear", seed=SEED)
        session.execute("midday")
        day = " ".join(
            session.narrator.describe(session.sim, session.sim.readout())
            for _ in range(4)
        )
        session.execute("night")
        night = " ".join(
            session.narrator.describe(session.sim, session.sim.readout())
            for _ in range(4)
        )
        self.assertNotEqual(day, night)
        self.assertTrue(
            any(word in night.lower() for word in ("night", "dark", "starlight")),
            night[:200],
        )


class TestPanelAndPersistence(unittest.TestCase):
    def test_the_panel_reports_the_wind_the_aircraft_is_actually_in(self):
        session = Session.new("a320neo", "crosswind", seed=SEED)
        text = dashboard.render(session.sim, session.sim.readout())
        self.assertIn("Wind here", text)
        self.assertIn("Wind drift", text)

    def test_weather_and_clock_survive_a_save_and_load(self):
        path = os.path.join(tempfile.mkdtemp(), "weather.json")
        session = Session.new("a320neo", "stormy", seed=SEED)
        session.execute("time 0415")
        for _ in range(5):
            session.sim.step_tick()
        session.save(path)

        restored = Session.load(path)
        self.assertAlmostEqual(
            restored.sim.state.time_of_day_h,
            session.sim.state.time_of_day_h,
            places=9,
        )
        # Weather is rebuilt from the seed and the clock, so it must match.
        self.assertAlmostEqual(
            restored.sim.weather.wind_speed_kt,
            session.sim.weather.wind_speed_kt,
            places=9,
        )
        self.assertAlmostEqual(
            restored.sim.weather.visibility_sm,
            session.sim.weather.visibility_sm,
            places=9,
        )


if __name__ == "__main__":
    unittest.main()
