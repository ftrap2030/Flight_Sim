"""Takeoff and landing performance.

The claim this file exists to check is not that the distances match a chart --
they do not yet, and `CLAUDE.md` says why and what it would take. It is that the
solver reads the *same physics the aeroplane actually flies*, that V1 sits where
the two distances cross, and that the runway, the wind and the weight all move
the answer the way they move the real one.

A solve costs about a fifth of a second, so cards are computed once and shared.
"""

import math
import unittest

from flight_sim import aircraft as fleet
from flight_sim import atmosphere as atm
from flight_sim import commands
from flight_sim import dashboard
from flight_sim import fbw
from flight_sim import game
from flight_sim import navigation
from flight_sim import performance
from flight_sim import physics
from flight_sim import traffic
from flight_sim import weather as wx


def runway_sim(key, mass_kg=None, still_air=True):
    sim = physics.Simulator.new_flight(key, "clear", start=physics.RUNWAY_START)
    if still_air:
        sim.weather.hold(wind_speed_kt=0.0, gust_kt=0.0, turbulence=0.0)
    if mass_kg is not None:
        sim.state.mass_kg = mass_kg
    return sim


def field_of(sim, ident):
    return sim.airfields.by_ident(ident, radius_nm=400.0)


# One solve per type, shared: the whole fleet is eleven cards and each is a few
# thousand force evaluations.
_CARDS = {}


def card(key, ident="ANFL"):
    if (key, ident) not in _CARDS:
        sim = runway_sim(key)
        _CARDS[(key, ident)] = (
            sim, performance.takeoff(sim, field_of(sim, ident))
        )
    return _CARDS[(key, ident)]


class TakeoffSolver(unittest.TestCase):
    def test_the_solver_flies_the_same_roll_the_aeroplane_does(self):
        """The whole architecture, stated as a number.

        `performance` owns no forces: every step calls `landing.ground_forces`,
        which is the call `_ground_substep` makes. So the distance the solver
        says it takes to reach VR has to be the distance the real integrator
        actually covers getting there -- and if somebody ever copies the
        friction model instead of calling it, these two drift apart and this is
        what notices.
        """
        sim = runway_sim("a320neo", mass_kg=71190.0)
        s = sim.state
        field = field_of(sim, s.landing_field_ident)
        flaps = max(s.flaps, 1)
        vr = fbw.takeoff_speeds(sim).vr

        with performance._Roll(
            sim, flaps, 0.0, field.elevation_ft, s.mass_kg
        ) as roll:
            roll.run_to_speed(vr)
            solved_ft = roll.distance_ft

        # The same standing start, flown for real.
        x0, y0 = s.x_nm, s.y_nm
        s.brakes = 0.0
        s.throttle_pct = 100.0
        s.cmd_pitch_deg = 12.0
        sim.settle_engines()
        flown = 0.0
        while s.on_ground and flown < 120.0:
            sim._ground_substep(0.02)
            flown += 0.02
        real_ft = math.hypot(s.x_nm - x0, s.y_nm - y0) * 6076.115

        # The real roll goes a little further, because it keeps accelerating
        # from VR until the wing genuinely carries the aeroplane. Nothing else
        # should separate them.
        self.assertGreater(real_ft, solved_ft)
        self.assertLess(real_ft, solved_ft * 1.15, (solved_ft, real_ft))

    def test_v1_sits_between_vmcg_and_vr(self):
        """The window, on every type. A V1 below the minimum control speed is
        a decision the rudder cannot honour; one above VR is a decision taken
        after the nose has already come up."""
        for a in fleet.FLEET:
            sim, c = card(a.key)
            vmcg = performance.vmcg_kt(
                sim, field_of(sim, "ANFL").elevation_ft,
                sim.state.mass_kg, c.flaps,
            )
            self.assertGreaterEqual(c.v1_kt, vmcg - 0.5, a.key)
            self.assertLessEqual(c.v1_kt, c.vr_kt + 0.5, a.key)

    def test_the_two_distances_cross_at_v1(self):
        """That is what balanced *means*, so it is checkable.

        Only for the cases the solve actually balanced: where V1 is clamped the
        two distances are deliberately unequal, and asserting otherwise would
        be asserting the clamp away.
        """
        balanced = 0
        for a in fleet.FLEET:
            sim, c = card(a.key)
            if c.limited_by != performance.BALANCED:
                continue
            balanced += 1
            self.assertAlmostEqual(
                c.accelerate_stop_ft, c.accelerate_go_ft,
                delta=max(60.0, c.field_length_ft * 0.01), msg=a.key,
            )
        # The vacuity guard: a suite where nothing balanced would pass this
        # test with the bisection deleted.
        self.assertGreater(balanced, 4)

    def test_the_field_length_takes_its_third_term_somewhere(self):
        """`takeoff_field_length_ft` is max(stop, go, clean x 1.15), and the
        third term is the one that is easy to leave in as decoration.

        It binds on exactly one shape of aeroplane in this fleet, and the reason
        is the physics rather than the arithmetic: with four engines, losing one
        costs a quarter of the thrust instead of half, so the engine-out
        distance barely exceeds the clean one and the regulator's fifteen
        percent overtakes both. On every twinjet here the balanced pair
        dominates at every weight and flap.

        So the A380 at flaps 1 is the type whose takeoff is limited by the case
        where nothing goes wrong -- and without a test that says so,
        `ALL_ENGINES_MARGIN` could be deleted and no number in either build
        would move. That is not hypothetical: it is what a deliberate breakage
        of the parity guard found.
        """
        def binding_term(c):
            margined = c.all_engines_ft * performance.ALL_ENGINES_MARGIN
            if margined >= max(c.accelerate_stop_ft, c.accelerate_go_ft):
                return "all engines"
            return "balanced pair"

        sim = runway_sim("a380")
        four_engined = performance._takeoff_at_flap(
            sim, field_of(sim, "ANFL"), sim.state.mass_kg, 1
        )
        self.assertEqual(binding_term(four_engined), "all engines")
        self.assertAlmostEqual(
            four_engined.field_length_ft,
            four_engined.all_engines_ft * performance.ALL_ENGINES_MARGIN,
            places=6,
        )
        self.assertEqual(len(sim.aircraft.engine_arms_m), 4)

        # And the other half of the claim, or the test above is about the A380
        # rather than about the number of engines.
        twin = runway_sim("a350")
        self.assertEqual(
            binding_term(performance._takeoff_at_flap(
                twin, field_of(twin, "ANFL"), twin.state.mass_kg, 1)),
            "balanced pair",
        )

    def test_vmcg_is_a_speed_an_airliner_actually_has(self):
        """Not asserted into existence -- it is the crossing where the rudder
        runs out, and `tests/test_yaw.py` already holds that crossing to the
        engine arm and the dynamic pressure. This only says the answer lands
        where an airliner's does."""
        for a in fleet.FLEET:
            sim, c = card(a.key)
            vmcg = performance.vmcg_kt(
                sim, field_of(sim, "ANFL").elevation_ft,
                sim.state.mass_kg, c.flaps,
            )
            self.assertGreater(vmcg, 70.0, a.key)
            self.assertLess(vmcg, 150.0, a.key)
            self.assertLess(vmcg, c.vr_kt, a.key)

    def test_a_solve_leaves_the_aeroplane_exactly_where_it_found_it(self):
        """The solver puts the aeroplane on a runway it is not on, at a speed
        it is not doing, with an engine that has not failed. Everything it
        touches is restored in a `finally`, or the next substep flies a state
        the pilot never asked for."""
        sim = runway_sim("a321")
        s = sim.state
        before = {
            name: (list(v) if isinstance(v, list) else v)
            for name, v in (
                (n, getattr(s, n)) for n in performance._ROLL_FIELDS
            )
        }
        performance.takeoff(sim, field_of(sim, "ANFL"))
        performance.landing_performance(sim, field_of(sim, "ANFL"))
        performance.vmcg_kt(sim, 1000.0, s.mass_kg, 2)
        for name, was in before.items():
            self.assertEqual(getattr(s, name), was, name)


class WhatMovesTheAnswer(unittest.TestCase):
    def test_a_headwind_shortens_it_and_a_tailwind_stretches_it(self):
        """The relationship the ground roll already has, one level up: the
        distance goes as the square of the speed the wheels have to reach, and
        a headwind is speed the aeroplane is given for nothing.

        The surface wind is backed thirty degrees from the gradient wind the
        profile carries, so a runway-aligned headwind on the wheels is a
        gradient wind thirty degrees the other side of the runway heading. And
        the case reports the component it actually got, because `hold` sets
        whatever attribute name it is handed: a misspelt one is a dead field
        nothing reads, and the comparison then quietly measures the profile's
        own wind twice.
        """
        sim = runway_sim("a320neo", still_air=False)
        field = field_of(sim, "ANFL")
        rwy = field.runway_heading_deg

        def case(wind_kt, direction_offset):
            sim.weather.hold(
                wind_speed_kt=wind_kt, gust_kt=0.0, turbulence=0.0,
                wind_dir_deg=(rwy + direction_offset) % 360,
            )
            sim.state.roll_direction_deg = rwy
            headwind_ms, _cross = sim.ground_wind_ms(rwy)
            return headwind_ms, performance.takeoff(sim, field).field_length_ft

        still_w, still = case(0.0, 0.0)
        head_w, head = case(25.0, 30.0)
        tail_w, tail = case(25.0, 210.0)
        # The vacuity half: the three cases must really be three winds.
        self.assertAlmostEqual(still_w, 0.0, delta=0.05)
        self.assertGreater(head_w, 4.0)
        self.assertLess(tail_w, -4.0)

        self.assertLess(head, still)
        self.assertGreater(tail, still)

    def test_weight_costs_more_than_its_share(self):
        """Lift-off speed goes as the root of the weight and the distance as
        its square, so the runway a takeoff needs grows faster than the
        aeroplane does."""
        light = performance.takeoff(
            runway_sim("a321", mass_kg=75000.0),
            field_of(runway_sim("a321"), "ANFL"),
        )
        heavy = performance.takeoff(
            runway_sim("a321", mass_kg=95000.0),
            field_of(runway_sim("a321"), "ANFL"),
        )
        ratio = heavy.field_length_ft / light.field_length_ft
        self.assertGreater(ratio, 95000.0 / 75000.0, (light, heavy))

    def test_the_runway_is_a_constraint_rather_than_a_caption(self):
        """The point of the whole phase. Harrow Deep is 5,400 ft and Anfell is
        12,200, and until this existed the aeroplane quoted the same V1 on
        both and neither number meant anything."""
        sim = runway_sim("a350")
        long_field = performance.takeoff(sim, field_of(sim, "ANFL"))
        short_field = performance.takeoff(sim, field_of(sim, "HRWD"))
        self.assertTrue(long_field.legal)
        self.assertFalse(short_field.legal)
        # And the aeroplane needs about the same runway either way -- what
        # changed is how much there is of it, not how much it wants. (Not
        # exactly the same: Harrow Deep sits at a different elevation.)
        self.assertLess(
            abs(long_field.field_length_ft - short_field.field_length_ft),
            long_field.field_length_ft * 0.25,
        )

    def test_more_flap_is_a_shorter_roll_and_a_worse_climb(self):
        """Which is why the setting is chosen rather than fixed."""
        sim = runway_sim("a320neo")
        field = field_of(sim, "ANFL")
        one = performance.takeoff(sim, field, flaps=1)
        three = performance.takeoff(sim, field, flaps=3)
        self.assertLess(three.vr_kt, one.vr_kt)
        self.assertLess(three.field_length_ft, one.field_length_ft)

    def test_the_flap_search_is_not_decorative(self):
        """`best_flap` tries every setting, so at least one type had better
        come out wanting something other than the same answer as the rest --
        otherwise the search could be replaced by a constant and nothing here
        would notice."""
        chosen = set()
        for a in fleet.FLEET:
            sim = runway_sim(a.key)
            chosen.add(performance.best_flap(sim, field_of(sim, "ANFL")).flaps)
        self.assertGreater(len(chosen), 1, chosen)


class AgainstTheRestOfTheModel(unittest.TestCase):
    def test_the_timetable_estimate_ranks_the_fleet_the_way_the_solve_does(self):
        """`traffic.takeoff_length_ft` carries a 2.1 factor and exists only to
        decide which types can work between which fields. It is far too rough
        to be a field length, but it has to put the fleet in the right order --
        and until now nothing anywhere checked that it did."""
        by_estimate = sorted(
            fleet.FLEET, key=lambda a: traffic.takeoff_length_ft(a)
        )
        solved = {}
        for a in fleet.FLEET:
            sim, c = card(a.key)
            solved[a.key] = c.field_length_ft
        by_solve = sorted(fleet.FLEET, key=lambda a: solved[a.key])
        # Not identical -- the estimate does not know about elevation or the
        # flap choice -- but the same broad order. Count the pairs the two
        # disagree about.
        order = {a.key: i for i, a in enumerate(by_solve)}
        inversions = sum(
            1
            for i, a in enumerate(by_estimate)
            for b in by_estimate[i + 1:]
            if order[a.key] > order[b.key]
        )
        pairs = len(fleet.FLEET) * (len(fleet.FLEET) - 1) / 2
        self.assertLess(inversions / pairs, 0.30, (inversions, pairs))

    def test_the_readout_quotes_the_configuration_it_is_in(self):
        """A card for a flap setting the lever is not on would rotate the
        aeroplane at a speed the card does not show."""
        sim = runway_sim("a330neo")
        r = sim.readout()
        self.assertEqual(r.performance.flaps, max(sim.state.flaps, 1))
        self.assertAlmostEqual(
            r.performance.vr_kt, fbw.takeoff_speeds(sim).vr, places=6
        )
        self.assertAlmostEqual(r.takeoff.v1, r.performance.v1_kt, places=6)

    def test_there_is_no_card_in_the_air(self):
        """There is no runway to compute one against, and inventing one would
        be a performance calculation with nothing behind it."""
        sim = physics.Simulator.new_flight("a320neo", "clear")
        self.assertIsNone(sim.readout().performance)

    def test_the_cache_answers_the_same_question_twice(self):
        sim = runway_sim("a320neo")
        first = sim.takeoff_performance()
        self.assertIs(first, sim.takeoff_performance())
        # A hundred kilogrammes of fuel is below the key's resolution, which is
        # deliberate: keyed finely the cache would miss every substep of a roll.
        sim.state.mass_kg -= 10.0
        self.assertIs(first, sim.takeoff_performance())
        sim.state.mass_kg -= 5000.0
        self.assertIsNot(first, sim.takeoff_performance())


class Landing(unittest.TestCase):
    def test_stopping_needs_more_runway_than_the_roll_it_takes_to_stop(self):
        """The required distance is the demonstrated one with the regulator's
        factor on it, plus the air distance from a fifty-foot threshold."""
        sim = runway_sim("a320neo")
        land = performance.landing_performance(sim, field_of(sim, "ANFL"))
        self.assertGreater(land.landing_distance_ft, land.ground_roll_ft)
        self.assertGreater(land.required_ft, land.landing_distance_ft)
        self.assertTrue(land.legal)

    def test_a_heavier_aeroplane_needs_more_of_it(self):
        sim = runway_sim("a350")
        field = field_of(sim, "ANFL")
        light = performance.landing_performance(sim, field, mass_kg=200000.0)
        heavy = performance.landing_performance(sim, field, mass_kg=260000.0)
        self.assertGreater(heavy.ground_roll_ft, light.ground_roll_ft)
        self.assertGreater(heavy.vref_kt, light.vref_kt)

    def test_the_plan_says_whether_the_far_end_will_hold_you(self):
        """The other half of "can I get there", and the plan is the only thing
        that knows the weight it happens at -- it has just integrated the fuel
        away. An A380 has the range to reach Harrow Deep several times over and
        cannot stop on it once."""
        sim = runway_sim("a380")
        fields = [field_of(sim, i) for i in ("ANFL", "HRWD")]
        sim.route = navigation.Route(
            [navigation.Waypoint.from_airfield(f) for f in fields]
        )
        sim.sync_route()
        costing = navigation.plan(sim)
        self.assertTrue(costing.enough, "fuel is not the problem here")
        self.assertFalse(costing.fits_destination)
        self.assertLess(costing.landing_margin_ft, 0.0)
        self.assertEqual(costing.destination_runway_ft, fields[1].runway_length_ft)
        # And it is asked at the *arrival* weight, which is lighter than this
        # aeroplane is now. A landing check made at the departure weight is a
        # check on a landing nobody makes.
        self.assertLess(costing.arrival_mass_kg, sim.state.mass_kg)
        self.assertGreater(
            costing.arrival_mass_kg, sim.state.mass_kg - costing.required_kg
        )

    def test_and_says_so_when_it_does_fit(self):
        """A check that is always negative is not a check.

        Crowmarsh rather than Harrow Deep, and the difference between the two
        is worth knowing: 5,400 ft is short of the *factored* despatch distance
        for every type in this fleet, though the unfactored roll fits easily.
        Harrow Deep is a field these aeroplanes may leave and may not be filed
        into, which is what a 1/0.6 despatch factor means on a short runway.
        """
        sim = runway_sim("a319neo")
        fields = [field_of(sim, i) for i in ("ANFL", "CROW")]
        sim.route = navigation.Route(
            [navigation.Waypoint.from_airfield(f) for f in fields]
        )
        sim.sync_route()
        costing = navigation.plan(sim)
        self.assertTrue(costing.fits_destination)
        self.assertGreater(costing.landing_margin_ft, 0.0)


class TheFrontEnd(unittest.TestCase):
    """The card has to be reachable, or it is a calculation nobody can see."""

    def test_perf_parses_to_one_command(self):
        for text in ("perf", "performance", "takeoff data", "v speeds"):
            parsed = commands.parse(text)
            self.assertIsNotNone(parsed, text)
            self.assertEqual(parsed.kind, "show_performance", text)
            self.assertFalse(parsed.advances_time, text)

    def test_the_card_is_rendered_on_the_ground(self):
        session = game.Session.new(
            "a320neo", "clear", start=physics.RUNWAY_START)
        text, finished = session.execute("perf")
        self.assertFalse(finished)
        self.assertIn("Takeoff performance", text)
        self.assertIn("V1 / VR / V2", text)
        card = session.sim.takeoff_performance(force=True)
        self.assertIn("{:,.0f} ft".format(card.field_length_ft), text)

    def test_and_the_landing_one_in_the_air(self):
        """Which of the two you get is which question you can still do
        anything about."""
        session = game.Session.new("a380", "clear")
        sim = session.sim
        sim.route = navigation.Route([
            navigation.Waypoint.from_airfield(field_of(sim, i))
            for i in ("ANFL", "HRWD")
        ])
        sim.sync_route()
        text, _finished = session.execute("perf")
        self.assertIn("Landing performance", text)
        self.assertIn("SHORT", text)

    def test_an_unflyable_card_says_so_rather_than_printing_an_infinity(self):
        card = performance._unflyable(12200.0, 100.0, 150.0, 160.0, 3)
        rendered = dashboard.performance_block(card)
        self.assertNotIn("inf", rendered)
        self.assertIn("cannot depart", rendered)
        self.assertEqual(card.limited_by, performance.UNFLYABLE)


if __name__ == "__main__":
    unittest.main()
