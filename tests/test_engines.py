"""Fan speed, the spool, and the numbers an ECAM shows.

The claim worth testing is the one the feature exists for: the thrust levers no
longer produce an instant change in anything, and everything downstream of that
follows.
"""

import os
import tempfile
import unittest

from flight_sim import aircraft as fleet
from flight_sim import atmosphere as atm
from flight_sim import engines
from flight_sim import physics
from flight_sim.game import Session


def cruising(key="a320neo"):
    return Session.new(key, "clear", seed=42)


class TestTheCurve(unittest.TestCase):
    def test_fan_speed_and_thrust_are_inverses(self):
        """Two functions describing one relationship must agree exactly.

        If they drift, the levers ask for a fan speed that makes a different
        thrust from the one the trim solver assumed, and the aeroplane trims to
        a setting that does not hold its speed -- which is the bug CLAUDE.md
        records against `_aero_state` in the first place.
        """
        for fraction in (0.0, 0.05, 0.25, 0.5, 0.75, 1.0):
            n1 = engines.n1_for_thrust_fraction(fraction)
            self.assertAlmostEqual(
                engines.thrust_fraction_for_n1(n1), fraction, places=12
            )

    def test_thrust_is_not_linear_in_fan_speed(self):
        """Half the fan speed range is nowhere near half the thrust.

        This is why the bottom of the lever range feels dead on a real one.
        """
        half = engines.N1_IDLE_PCT + (engines.N1_MAX_PCT - engines.N1_IDLE_PCT) / 2
        self.assertLess(engines.thrust_fraction_for_n1(half), 0.3)

    def test_egt_rises_with_fan_speed_and_falls_with_altitude(self):
        """It is ambient plus a rise, so colder air in means a cooler exhaust."""
        self.assertGreater(engines.egt_c(90.0, 0.0), engines.egt_c(60.0, 0.0))
        self.assertGreater(engines.egt_c(90.0, 0.0), engines.egt_c(90.0, 35000.0))

    def test_egt_lands_where_a_turbofan_lands(self):
        """Near 400 C at flight idle and 850 C at the takeoff rating.

        Derived rather than published -- see the module docstring -- but it has
        to be the right *shape* or it is decoration with no meaning at all.
        """
        idle_n1 = engines.n1_for_thrust_fraction(0.05)
        self.assertAlmostEqual(engines.egt_c(idle_n1, 0.0), 400.0, delta=25.0)
        self.assertAlmostEqual(engines.egt_c(100.0, 0.0), 850.0, delta=25.0)


class TestTheSpool(unittest.TestCase):
    def test_the_levers_no_longer_change_the_thrust_at_once(self):
        """The whole point of the feature, in one assertion."""
        sim = cruising().sim
        before = sim._thrust_n()
        sim.state.throttle_pct = 100.0
        self.assertAlmostEqual(sim._thrust_n(), before, places=6)
        sim._substep(0.5)
        self.assertGreater(sim._thrust_n(), before)

    def test_it_spools_up_more_slowly_than_it_spools_down(self):
        """A turbofan accelerates its own rotating mass against the air it is
        pumping; closing the throttle merely stops feeding it. Which is what
        makes a late go-around frightening."""
        def travelled(from_pct, to_pct, seconds=1.0):
            sim = cruising().sim
            sim.state.throttle_pct = from_pct
            sim.settle_engines()
            start = sim.state.engine_n1_pct[0]
            sim.state.throttle_pct = to_pct
            for _ in range(int(seconds / 0.1)):
                engines.spool(sim.state, sim.aircraft, 0.1)
            return abs(sim.state.engine_n1_pct[0] - start)

        self.assertLess(travelled(20.0, 100.0), travelled(100.0, 20.0))

    def test_it_reaches_the_commanded_speed_eventually(self):
        """To within far less than an instrument could show.

        A first-order lag never quite arrives, so this asks for a hundredth of a
        percent rather than for equality -- which is a thousand times finer than
        the one decimal place an E/WD prints.
        """
        sim = cruising().sim
        sim.state.throttle_pct = 85.0
        for _ in range(300):
            engines.spool(sim.state, sim.aircraft, 0.1)
        self.assertAlmostEqual(
            sim.state.engine_n1_pct[0],
            engines.commanded_n1_pct(sim.aircraft, 85.0),
            delta=0.01,
        )

    def test_a_failed_engine_runs_down_to_nothing(self):
        session = cruising("a380")
        session.execute("shutdown engine 1")
        for _ in range(60):
            session.sim._substep(0.1)
        n1 = session.sim.state.engine_n1_pct
        self.assertLess(n1[0], 1.0, "the failed engine is still turning")
        self.assertGreater(n1[1], 40.0, "a live engine was stopped too")

    def test_settling_puts_the_fan_where_the_levers_are(self):
        sim = cruising().sim
        sim.state.throttle_pct = 72.0
        sim.settle_engines()
        for n1 in sim.state.engine_n1_pct:
            self.assertAlmostEqual(
                n1, engines.commanded_n1_pct(sim.aircraft, 72.0), places=9
            )

    def test_every_type_has_one_fan_speed_per_engine(self):
        for craft in fleet.FLEET:
            state = cruising(craft.key).sim.state
            self.assertEqual(len(state.engine_n1_pct), craft.engine_count, craft.key)

    def test_the_fan_speed_survives_a_save_and_load(self):
        """CLAUDE.md: anything that must survive a save lives on FlightState.

        Thrust is derived from N1, so a resumed flight whose engines came back
        at the wrong speed would be flying on the wrong thrust.
        """
        path = os.path.join(tempfile.mkdtemp(), "n1.json")
        session = cruising()
        session.execute("full power")
        session.sim.step_tick(2.0)
        session.save(path)
        restored = Session.load(path)
        for mine, theirs in zip(
            session.sim.state.engine_n1_pct, restored.sim.state.engine_n1_pct
        ):
            self.assertAlmostEqual(mine, theirs, places=9)


class TestGoAround(unittest.TestCase):
    def test_thrust_from_idle_takes_seconds_to_arrive(self):
        """The reason any of this matters.

        Firewalling the levers on an approach does not give you the thrust; it
        gives you the thrust in about eight seconds, and on a go-around from
        two hundred feet those seconds are the whole decision.
        """
        sim = cruising().sim
        sim.state.throttle_pct = 0.0
        sim.settle_engines()
        full = sim._thrust_available_n()
        sim.state.throttle_pct = 100.0

        reached = None
        for step in range(200):
            engines.spool(sim.state, sim.aircraft, 0.1)
            if sim._thrust_n() > full * 0.9:
                reached = (step + 1) * 0.1
                break
        self.assertIsNotNone(reached, "never reached ninety percent of thrust")
        self.assertGreater(reached, 3.0, "the spool is too quick to be felt")
        self.assertLess(reached, 20.0, "the spool is slower than any real engine")


class TestReadouts(unittest.TestCase):
    def test_one_entry_per_engine_with_the_ecam_numbers(self):
        readout = cruising("a380").sim.readout()
        self.assertEqual(len(readout.engines), 4)
        for entry in readout.engines:
            self.assertGreater(entry.n1_pct, 20.0)
            self.assertGreater(entry.egt_c, 100.0)
            self.assertGreater(entry.fuel_flow_kgh, 0.0)
            self.assertFalse(entry.failed)

    def test_a_failed_engine_shows_it(self):
        session = cruising("a380")
        session.execute("shutdown engine 2")
        for _ in range(40):
            session.sim._substep(0.1)
        entries = session.sim.readout().engines
        self.assertTrue(entries[1].failed)
        self.assertLess(entries[1].fuel_flow_kgh, 1.0)
        self.assertGreater(entries[0].fuel_flow_kgh, 0.0)

    def test_the_engines_add_up_to_the_thrust_the_aircraft_is_making(self):
        """The panel and the physics must not disagree about the thrust."""
        for key in ("a320neo", "a350", "a380"):
            sim = cruising(key).sim
            readout = sim.readout()
            total = sum(entry.thrust_n for entry in readout.engines)
            self.assertAlmostEqual(total, readout.thrust_n, delta=1.0, msg=key)


if __name__ == "__main__":
    unittest.main()
