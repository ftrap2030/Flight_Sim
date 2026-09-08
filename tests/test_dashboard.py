"""The markdown instrument panel.

`dashboard.py` is the second-largest module in the repo and had no test file of
its own -- which is how it came to be missing the engine parameters entirely
while the glass cockpit had had an E/WD for two phases. It was covered only
incidentally, by substring assertions scattered across seven other files.

These tests are about the panel as a *display*: that it shows what the model
says and works nothing out for itself.
"""

import unittest

from flight_sim import dashboard
from flight_sim import engines
from flight_sim import physics
from flight_sim.game import Session


def flying(key="a320neo"):
    return Session.new(key, "clear", seed=42)


def panel(session):
    return dashboard.render(session.sim, session.sim.readout())


class TestTheEngineBlock(unittest.TestCase):
    """The E/WD's top half. The text simulator could not show N1 at all."""

    def test_every_engine_gets_a_column(self):
        for key, count in (("a320neo", 2), ("a380", 4)):
            session = flying(key)
            block = "\n".join(dashboard.engine_block(session.sim.readout()))
            for index in range(count):
                self.assertIn("ENG {}".format(index + 1), block, key)
            self.assertNotIn("ENG {}".format(count + 1), block, key)

    def test_it_shows_the_readout_and_does_no_arithmetic(self):
        """Every figure has to be the model's, or the two front ends can drift
        apart while both look entirely plausible."""
        session = flying()
        readout = session.sim.readout()
        block = "\n".join(dashboard.engine_block(readout))
        for entry in readout.engines:
            self.assertIn("{:.1f}".format(entry.n1_pct), block)
            self.assertIn("{:.1f}".format(entry.n2_pct), block)
            self.assertIn("{:.0f}".format(entry.egt_c), block)

    def test_the_egt_band_is_marked_rather_than_recomputed(self):
        """A text panel has no colour, so it marks the band some other way --
        but *which* band it is remains `engines.egt_band`'s to say."""
        session = flying()
        readout = session.sim.readout()
        hot = [
            e.__class__(**dict(e.__dict__, egt_c=1000.0,
                               egt_band=engines.EGT_OVER_LIMIT))
            for e in readout.engines
        ]
        readout.engines = hot
        self.assertIn(
            dashboard._EGT_MARK[engines.EGT_OVER_LIMIT],
            "\n".join(dashboard.engine_block(readout)),
        )

    def test_a_failed_engine_is_marked_but_still_reads(self):
        """The fan is running down, not stopped, and watching N1 decay is how
        the asymmetry announces itself. A column of dashes would hide that."""
        session = flying("a380")
        session.execute("fail engine 3")
        for _ in range(25):
            session.sim._substep(0.1)
        block = "\n".join(dashboard.engine_block(session.sim.readout()))
        self.assertIn("ENG 3*", block)
        self.assertIn("OUT", block)
        failed = session.sim.readout().engines[2]
        self.assertGreater(failed.n1_pct, 0.0, "the fan stopped instantly")
        self.assertIn("{:.1f}".format(failed.n1_pct), block)

    def test_a_fire_says_so_under_its_own_engine(self):
        session = flying("a380")
        session.execute("fail engine 2 fire")
        rows = dashboard.engine_block(session.sim.readout())
        fire_row = [row for row in rows if "FIRE" in row]
        self.assertEqual(len(fire_row), 1)
        # Under engine two's column, not engine one's.
        self.assertLess(rows[0].index("ENG 2"), fire_row[0].index("FIRE") + 7)
        self.assertGreater(fire_row[0].index("FIRE"), rows[0].index("ENG 1"))

    def test_it_sits_above_the_ecam_as_it_does_on_the_aeroplane(self):
        session = flying()
        session.execute("fail hydraulics")
        text = panel(session)
        self.assertLess(text.index("ENG 1"), text.index("HYD SYS LO PR"))

    def test_the_panel_survives_an_aircraft_with_no_engine_state_yet(self):
        """`engine_n1_pct` is empty until something settles it, and a display
        that raises is worse than one that says nothing."""
        session = flying()
        session.sim.state.engine_n1_pct = []
        self.assertIn("IAS", panel(session))


class TestThePanelAsAWhole(unittest.TestCase):
    def test_it_renders_for_every_type_in_the_fleet(self):
        from flight_sim import aircraft as fleet

        for key in fleet.FLEET_BY_KEY:
            session = flying(key)
            self.assertIn("IAS", panel(session), key)

    def test_it_renders_on_the_ground_and_in_the_air(self):
        on_ground = Session.new(
            "a320neo", "clear", seed=42, start=physics.RUNWAY_START
        )
        self.assertIn("V1", panel(on_ground))
        self.assertIn("VLS", panel(flying()))

    def test_it_renders_with_everything_broken_at_once(self):
        session = flying("a380")
        for command in ("fail engine 1 fire", "fail fuel leak", "fail hydraulics",
                        "fail brakes", "fail flap jam", "fail gear jam"):
            session.execute(command)
        text = panel(session)
        self.assertIn("ENG 1 FIRE", text)
        self.assertIn("BRAKES DEGRADED", text)


if __name__ == "__main__":
    unittest.main()
