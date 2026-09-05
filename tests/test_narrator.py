"""The prose engine: template safety, band selection, and anti-repetition."""

import unittest

from flight_sim import aircraft as fleet
from flight_sim import narrator as nar
from flight_sim import weather as wx
from flight_sim.game import Session


def all_pools():
    """Every clause pool in the corpus.

    Collected in one place so a newly added corpus cannot quietly escape the
    template-rendering and unique-id checks -- which are the only thing standing
    between a placeholder typo and prose silently vanishing at runtime.
    """
    pools = []
    for band in nar.SKY.values():
        pools.extend(band.values())
    for mapping in (nar.TERRAIN, nar.TERRAIN_OBSCURED, nar.MOTION,
                    nar.SENSORY, nar.THREAT, nar.LIGHT, nar.ENDING_LANDED):
        pools.extend(mapping.values())
    pools.append(nar.APPROACH)
    pools.append(nar.ROLLOUT)
    pools.extend([nar.ENDING_TERRAIN, nar.ENDING_STRUCTURAL, nar.ENDING_PILOT,
                  nar.ENDING_OVERRUN, nar.ENDING_BOTCHED_LANDING])
    return pools


class TestBands(unittest.TestCase):
    def test_band_thresholds_follow_the_brief(self):
        self.assertEqual(nar.band_for(9000), nar.HIGH)
        self.assertEqual(nar.band_for(2001), nar.HIGH)
        self.assertEqual(nar.band_for(2000), nar.LOW)
        self.assertEqual(nar.band_for(1500), nar.LOW)
        self.assertEqual(nar.band_for(1000), nar.LOW)
        self.assertEqual(nar.band_for(999), nar.CRITICAL)
        self.assertEqual(nar.band_for(-50), nar.CRITICAL)


class TestCorpusIntegrity(unittest.TestCase):
    def test_every_band_covers_every_weather(self):
        for band in (nar.HIGH, nar.LOW, nar.CRITICAL):
            for profile in wx.WEATHER_OPTIONS:
                self.assertIn(profile.key, nar.SKY[band], "{}/{}".format(band, profile.key))
                self.assertTrue(nar.SKY[band][profile.key])

    def test_clause_ids_are_unique(self):
        seen = set()
        for pool in all_pools():
            for key, _template in pool:
                self.assertNotIn(key, seen, "duplicate clause id {!r}".format(key))
                seen.add(key)

    def test_every_template_renders_against_the_live_context(self):
        """Guards against a typo in a format placeholder silently dropping prose."""
        session = Session.new("a320neo", "clear", seed=42)
        context = nar._context(session.sim, session.sim.readout())

        for pool in all_pools():
            for key, template in pool:
                try:
                    rendered = template.format(**context)
                except (KeyError, IndexError, ValueError) as error:
                    self.fail("clause {!r} failed to render: {}".format(key, error))
                self.assertTrue(rendered.strip(), "clause {!r} is empty".format(key))


class TestDescription(unittest.TestCase):
    def test_describes_every_aircraft_and_weather_combination(self):
        for craft in fleet.FLEET:
            for profile in wx.WEATHER_OPTIONS:
                session = Session.new(craft.key, profile.key, seed=42)
                text = session.narrator.describe(session.sim, session.sim.readout())
                self.assertGreater(len(text), 150, "{}/{}".format(craft.key, profile.key))

    def test_description_changes_with_altitude_band(self):
        session = Session.new("a320", "clear", seed=42)
        high = session.narrator.describe(session.sim, session.sim.readout())
        session.sim.state.altitude_ft = session.sim.readout().terrain_ft + 600.0
        low = session.narrator.describe(session.sim, session.sim.readout())
        self.assertNotEqual(high, low)

    def test_repeated_descriptions_do_not_immediately_repeat(self):
        """The anti-repetition window must actually vary the prose."""
        session = Session.new("a350", "clear", seed=42)
        outputs = [
            session.narrator.describe(session.sim, session.sim.readout())
            for _ in range(6)
        ]
        self.assertGreaterEqual(len(set(outputs)), 5)

    def test_fog_uses_the_obscured_terrain_corpus(self):
        session = Session.new("a320", "foggy", seed=42)
        obscured_ids = {key for pool in nar.TERRAIN_OBSCURED.values() for key, _ in pool}
        for _ in range(6):
            session.narrator.describe(session.sim, session.sim.readout())
        self.assertTrue(obscured_ids & set(session.narrator._recent))

    def test_the_critical_band_has_enough_to_say(self):
        """It was the thinnest cell and the one a pilot reads most closely."""
        for profile in wx.WEATHER_OPTIONS:
            self.assertGreaterEqual(
                len(nar.SKY[nar.CRITICAL][profile.key]), 6, profile.key
            )

    def test_a_long_low_run_does_not_start_repeating(self):
        session = Session.new("a320", "clear", seed=42)
        session.sim.state.altitude_ft = session.sim.readout().terrain_ft + 700.0
        outputs = [
            session.narrator.describe(session.sim, session.sim.readout())
            for _ in range(12)
        ]
        self.assertGreaterEqual(len(set(outputs)), 11)

    def test_an_approach_uses_the_approach_register(self):
        """On final the outside view is cues, not scenery."""
        from tests.test_landing import place_on_final

        session, _field, _direction = place_on_final(distance_nm=4.0)
        approach_ids = {key for key, _ in nar.APPROACH}
        for _ in range(6):
            session.narrator.describe(session.sim, session.sim.readout())
        self.assertTrue(approach_ids & set(session.narrator._recent))

    def test_the_rollout_has_its_own_register(self):
        session = Session.new("a320neo", "clear", seed=42)
        session.sim.state.on_ground = True
        rollout_ids = {key for key, _ in nar.ROLLOUT}
        for _ in range(6):
            session.narrator.describe(session.sim, session.sim.readout())
        self.assertTrue(rollout_ids & set(session.narrator._recent))

    def test_ending_prose_is_produced_for_each_terminal_status(self):
        for status in ("crashed_terrain", "structural_failure", "ended_by_pilot"):
            session = Session.new("a380", "stormy", seed=42)
            session.sim.state.status = status
            session.sim._record_impact()
            text = session.narrator.ending(session.sim, session.sim.readout())
            self.assertIn("SIMULATION ENDED", text)


if __name__ == "__main__":
    unittest.main()
