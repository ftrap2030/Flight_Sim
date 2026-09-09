"""Compare the browser build's speeds and flight modes against flight_sim/.

    node web/tools/parity_check.js "$PWD/web/anfell.html" > /tmp/web.json
    python web/tools/parity_check.py /tmp/web.json

The browser's flight model is a *port* of the Python's, and CLAUDE.md says the
two must not drift. `cruise_check.js` guards the aerodynamics. This guards what
the glass cockpit puts on the glass -- which is the easier half to get wrong,
because a speed tape with its marks in the wrong place still looks like a speed
tape, an FMA announcing OP CLB while the aeroplane levels off still looks like
an FMA, and an ECAM naming the wrong engine still looks like an ECAM. None of
them is caught by looking at a screenshot.

It also guards the **weather**, which nothing guarded until it had drifted badly:
`cruise_check` flies in still air and the states above zero the wind, so the
browser's air was free to wander and did. Those comparisons are exact rather
than tolerant, because every one of them is a pure function of the profile, the
seed and the clock -- there is no integration between the two builds for error
to accumulate in. The sample points are deliberately chosen where the answers
are *not* zero, and the check says so if the sweep stops finding rough air: two
builds agreeing that nothing is happening is not agreement about anything.

Some of the states are **transitions** rather than resting states -- a fire then
a restart, a failure cleared and another raised -- because a resting state cannot
catch an un-setting. A fire that survived a restart went through sixty-eight
states that only ever broke the aeroplane, leaving the ECAM silent while the
E/WD painted FIRE over an engine that was running.

Runs on every push; see `.github/workflows/tests.yml`.

Exits non-zero on the first disagreement, and says which one.
"""

import json
import sys

sys.path.insert(0, __file__.rsplit("/web/", 1)[0])

from flight_sim import aircraft as fleet  # noqa: E402
from flight_sim import atmosphere as atm  # noqa: E402
from flight_sim import autopilot  # noqa: E402
from flight_sim import engines  # noqa: E402
from flight_sim import failures as broken  # noqa: E402
from flight_sim import fbw  # noqa: E402
from flight_sim import navigation  # noqa: E402
from flight_sim import physics  # noqa: E402
from flight_sim import weather as wx  # noqa: E402
from flight_sim.game import Session  # noqa: E402
from flight_sim.terrain import Terrain  # noqa: E402

# Knots. The two builds compute in the same units from the same constants, so
# any real disagreement is thousands of times bigger than this; the tolerance is
# for float ordering, not for slack.
TOLERANCE_KT = 0.01

# The weather is a pure function of the profile, the seed and the clock, with no
# integration between the two builds to accumulate error -- so it agrees to
# float noise or it does not agree.
WEATHER_TOLERANCE = 1e-3

# The world both builds share. Seed 20260905 grows the same ANFL in each, to
# every printed digit, so the terrain under a sample point is identical by
# construction rather than by luck.
SEED = 20260905

# fbw.Speeds field -> the browser's name for it.
SPEED_FIELDS = {
    "stall": "stall", "alpha_max": "alphaMax", "alpha_prot": "alphaProt",
    "vls": "vls", "green_dot": "greenDot", "vref": "vref", "vmax": "vmax",
}


def python_row(key, case):
    """The same state, set up on the Python side."""
    session = Session.new(key, "clear", seed=42)
    sim = session.sim
    craft = sim.aircraft
    state = sim.state
    state.altitude_ft = case["alt"]
    state.flaps = case["flaps"]
    state.gear_down = case["gear"]
    state.spoilers = False
    state.on_ground = bool(case.get("ground"))
    state.status = physics.ROLLOUT if state.on_ground else physics.FLYING
    state.pitch_deg = 0.0 if state.on_ground else 2.0
    state.bank_deg = state.gamma_deg = 0.0
    state.sideslip_deg = state.rudder_deg = 0.0
    state.alpha_floor_latched = False
    state.engines_failed = []
    state.engines_on_fire = []
    state.failures = []
    state.jammed_flaps = None
    state.jammed_gear_down = None
    state.armed_failure = None
    state.throttle_pct = float(case.get("throttle", 60))
    state.elapsed_s = 100.0
    state.mass_kg = (
        craft.mtow_kg if case.get("mass") == "mtow" else craft.start_mass_kg
    )
    state.tas_ms = atm.ias_to_tas(case["ias"] * atm.MS_PER_KT, case["alt"])

    ap = case["ap"]
    state.ap_engaged = ap.get("engaged", False)
    state.ap_altitude_ft = ap.get("altFt")
    state.ap_vs_fpm = ap.get("vsFpm")
    state.ap_heading_deg = ap.get("hdgDeg")
    state.ap_speed_kt = ap.get("spdKt")
    state.ap_approach = ap.get("appr", False)
    state.ap_nav = ap.get("nav", False)
    if case.get("dest"):
        sim.route = navigation.Route(
            [navigation.Waypoint("DEST", state.x_nm, state.y_nm + 40.0)]
        )
        sim.sync_route()

    # No readout: the browser cases never have an approach captured, and asking
    # for one here would cost a terrain scan per case for an answer both sides
    # already agree is None.
    sim.settle_engines()

    # Break it in the case's order, then run the fans down for as long as it
    # says: a half-decayed N1 is the value that catches an error in either time
    # constant or in the direction test between them.
    for key, index in case.get("fail", ()):
        broken.trigger(sim, key, index)
    for _ in range(case.get("spool", 0)):
        engines.spool(state, craft, 0.1)
    # Then un-set things. Every case above only ever breaks the aeroplane, and
    # a state that is never repaired is how a fire came to survive a restart.
    for step in case.get("then", ()):
        operation, argument = (step + [None])[:2] if isinstance(step, list) else step
        if operation == "restore":
            broken.restore_engines(sim)
        elif operation == "clear":
            broken.clear(sim, argument)
        elif operation == "fail":
            broken.trigger(sim, argument)

    motors = engines.readouts(sim)
    if case.get("egt") is not None:
        # Forced: no flyable state reaches the caution band, because EGT is a
        # function of fan speed and the fan tops out at the takeoff rating. It
        # is the band that is being compared, not the way it was arrived at.
        forced = float(case["egt"])
        motors = [
            type(e)(**dict(e.__dict__, egt_c=forced,
                           egt_band=engines.egt_band(forced)))
            for e in motors
        ]

    speeds = fbw.characteristic_speeds(sim)
    takeoff = fbw.takeoff_speeds(sim)
    autopilot.note_mode_changes(sim, None)
    annunciator = autopilot.fma(sim, None)
    return (speeds, takeoff, annunciator, autopilot.channels(state),
            motors, broken.ecam(sim))


# An integration is not a pure function of a seed, so these are compared to a
# tolerance rather than exactly -- but a tight one. Both builds run the same
# steps over the same drag polar in the same order, so the only difference
# available to them is the last bits of a float.
PLAN_TOLERANCE = 1e-6


def plan_failures(data):
    """The flight plan, which is a block fuel figure with nothing to check it.

    A speed tape with its marks in the wrong place still looks like a speed
    tape; a plan that is 6% out looks like a plan. There is nothing on the
    screen to catch it, which is why it is compared here field by field.
    """
    if "plans" not in data:
        return ["the browser dump has no plan section -- re-run parity_check.js"]

    out = []
    longest_cruise_nm = 0.0
    fields = {
        "cruiseFt": "cruise_ft", "distanceNm": "distance_nm", "timeS": "time_s",
        "climbFuelKg": "climb_fuel_kg", "climbTimeS": "climb_time_s",
        "climbDistanceNm": "climb_distance_nm",
        "cruiseFuelKg": "cruise_fuel_kg", "cruiseTimeS": "cruise_time_s",
        "descentFuelKg": "descent_fuel_kg", "descentTimeS": "descent_time_s",
        "descentDistanceNm": "descent_distance_nm",
        "reserveKg": "reserve_kg", "blockFuelKg": "block_fuel_kg",
        "requiredKg": "required_kg", "spareKg": "spare_kg",
    }
    for case, theirs in zip(data["planCases"], data["plans"]):
        where = "{} / {}".format(case["key"], " ".join(case["route"]))
        session = Session.new(case["key"], "clear", seed=SEED)
        sim = session.sim
        state = sim.state
        home = sim.airfields.by_ident(
            case["route"][0], state.x_nm, state.y_nm, radius_nm=400.0
        )
        state.x_nm, state.y_nm = home.x_nm, home.y_nm
        state.altitude_ft = case["alt"]
        state.mass_kg = case["massT"] * 1000.0
        state.fuel_kg = case["fuel"]
        session.execute("route " + " ".join(case["route"]))
        mine = navigation.plan(sim)
        longest_cruise_nm = max(
            longest_cruise_nm,
            mine.distance_nm - mine.climb_distance_nm - mine.descent_distance_nm,
        )

        for browser_name, python_name in fields.items():
            a = getattr(mine, python_name)
            b = theirs[browser_name]
            if abs(a - b) > max(PLAN_TOLERANCE, abs(a) * PLAN_TOLERANCE):
                out.append("{}: plan {} is {:,.4f} in Python and {:,.4f} in the "
                           "browser".format(where, python_name, a, b))
        if mine.enough != theirs["enough"]:
            out.append("{}: `enough` is {} in Python and {} in the browser -- one "
                       "of them is telling a pilot they can make it"
                       .format(where, mine.enough, theirs["enough"]))

        if len(mine.legs) != len(theirs["legs"]):
            out.append("{}: {} legs in Python and {} in the browser".format(
                where, len(mine.legs), len(theirs["legs"])))
            continue
        for index, (leg, other) in enumerate(zip(mine.legs, theirs["legs"])):
            label = leg.waypoint.ident or leg.waypoint.name
            if label != other["label"]:
                out.append("{}: leg {} is {} in Python and {} in the browser"
                           .format(where, index, label, other["label"]))
            for name, a, b in (("distance_nm", leg.distance_nm, other["distanceNm"]),
                               ("track_deg", leg.track_deg, other["trackDeg"]),
                               ("fuel_kg", leg.fuel_kg, other["fuelKg"]),
                               ("time_s", leg.time_s, other["timeS"])):
                if abs(a - b) > max(PLAN_TOLERANCE, abs(a) * PLAN_TOLERANCE):
                    out.append("{}: leg {} {} is {:,.4f} in Python and {:,.4f} "
                               "in the browser".format(where, label, name, a, b))

    # The rotor sweep's lesson, applied to the plan: every case here files a
    # level low enough that climb and descent fill the distance, and two miles
    # of cruise cannot tell a mass model from a constant. So at least one case
    # must genuinely cruise, or the cruise comparison is agreeing about nothing.
    floor_nm = data.get("minimumCruiseNm", 60.0)
    if longest_cruise_nm < floor_nm:
        out.append(
            "the plan cases never cruise: the longest is {:,.1f} nm against a "
            "floor of {:,.0f} -- add a route that starts at level".format(
                longest_cruise_nm, floor_nm)
        )
    return out


def debrief_failures(data):
    """The end-of-flight card, which both builds used to write by hand.

    Compared on the rendered string as well as on the numbers, because that is
    what a pilot reads: two builds can agree on 8339.7 and print 8,340 and
    8,339, and only one of them is what the logbook then records.
    """
    if "debriefs" not in data:
        return ["the browser dump has no debrief section -- re-run parity_check.js"]

    out = []
    for case, theirs in zip(data["debriefCases"], data["debriefs"]):
        where = case["name"]
        session = Session.new(case["key"], "clear", seed=SEED)
        sim = session.sim
        state = sim.state
        if case["route"]:
            session.execute("route " + " ".join(case["route"]))
        touchdown = case["touchdown"]
        state.status = {
            "landed": physics.LANDED, "terrain": physics.CRASHED_TERRAIN,
            "structural": physics.STRUCTURAL_FAILURE, "overrun": physics.OVERRUN,
            "hardlanding": physics.LANDED, "runwayoverrun": physics.OVERRUN,
        }[case["kind"]]
        state.elapsed_s = case["t"]
        state.distance_flown_nm = case["distance"]
        state.initial_fuel_kg = case["initialFuel"]
        state.fuel_kg = case["fuel"]
        state.planned_fuel_kg = case["planned"]
        state.max_altitude_ft = case["maxAlt"]
        state.max_ias_kt = case["maxIas"]
        state.max_mach = case["maxMach"]
        state.min_agl_ft = case["minAgl"]
        state.max_load_factor = case["maxG"]
        state.warnings_seen = list(case["warnings"])
        state.touchdown = None if touchdown is None else {
            "grade": touchdown["grade"],
            "sink_rate_fpm": touchdown["sink"],
            "ias_kt": touchdown["ias"],
            "speed_ratio": touchdown["ratio"],
            "centreline_ft": touchdown["across"],
            "remaining_ft": touchdown["remaining"],
        }
        mine = navigation.debrief_data(sim)

        for name, a, b in (("outcome", mine.outcome, theirs["outcome"]),
                           ("outcome_text", mine.outcome_text, theirs["outcomeText"]),
                           ("grade", mine.grade, theirs["grade"]),
                           ("warnings_seen", mine.warnings_seen, theirs["warningsSeen"]),
                           ("route_idents", mine.route_idents, theirs["routeIdents"])):
            if a != b:
                out.append("{}: {} is {!r} in Python and {!r} in the browser"
                           .format(where, name, a, b))

        keys = [r.key for r in mine.rows]
        other_keys = [r["key"] for r in theirs["rows"]]
        if keys != other_keys:
            out.append("{}: rows are {} in Python and {} in the browser"
                       .format(where, keys, other_keys))
            continue
        for row, other in zip(mine.rows, theirs["rows"]):
            for name, a, b in (("label", row.label, other["label"]),
                               ("unit", row.unit, other["unit"]),
                               ("decimals", row.decimals, other["decimals"]),
                               ("kind", row.kind, other["kind"])):
                if a != b:
                    out.append("{}: row {} {} is {!r} in Python and {!r} in the "
                               "browser".format(where, row.key, name, a, b))
            for name, a, b in (("value", row.value, other["value"]),
                               ("extra", row.extra, other["extra"])):
                if abs(a - b) > max(1e-9, abs(a) * 1e-9):
                    out.append("{}: row {} {} is {} in Python and {} in the "
                               "browser".format(where, row.key, name, a, b))
            rendered = navigation.format_row(row)
            if rendered != other["text"]:
                out.append("{}: row {} reads {!r} in Python and {!r} in the "
                           "browser".format(where, row.key, rendered, other["text"]))
    return out


def weather_failures(data):
    """The weather, which nothing guarded until it had drifted badly.

    Everything here is a pure function of the profile, the seed and the clock,
    so it is compared exactly rather than flown -- but the sample points are
    chosen where the answers are *not* zero, because two builds agreeing that
    nothing is happening is not agreement about anything.
    """
    if "weather" not in data:
        return ["the browser dump has no weather section -- re-run parity_check.js"]

    out = []
    profiles = {w.key: w for w in wx.WEATHER_OPTIONS}
    world = Terrain(seed=SEED)
    sweep = data["rotorSweep"]
    worst_rotor = 0.0
    worst_wave = 0.0

    for row in data["weather"]:
        where = "{} at t+{}".format(row["profile"], row["t"])
        state = wx.WeatherState(profiles[row["profile"]], seed=SEED,
                                elapsed_s=row["t"])
        for field, mine, theirs in (
            ("wind speed", state.wind_speed_kt, row["windKt"]),
            ("wind direction", state.wind_dir_deg, row["windDir"]),
            ("visibility", state.visibility_sm, row["visSm"]),
            ("turbulence", state.turbulence, row["turbulence"]),
        ):
            if abs(mine - theirs) > WEATHER_TOLERANCE:
                out.append("{}: {} is {:.6f} in Python and {:.6f} in the browser"
                           .format(where, field, mine, theirs))

        for agl, speed, direction in row["at"]:
            mine_speed, mine_dir = state.wind_at(agl)
            if abs(mine_speed - speed) > WEATHER_TOLERANCE:
                out.append("{}: wind at {:.0f} ft is {:.6f} kt in Python and "
                           "{:.6f} in the browser".format(where, agl, mine_speed, speed))
            if abs(mine_dir - direction) > WEATHER_TOLERANCE:
                out.append("{}: wind direction at {:.0f} ft is {:.6f} in Python "
                           "and {:.6f} in the browser"
                           .format(where, agl, mine_dir, direction))

        for sample, theirs in zip((-3.0, 0.0, 3.0), row["gust"]):
            mine = state.wind_at(1500.0, sample)[0]
            if abs(mine - theirs) > WEATHER_TOLERANCE:
                out.append("{}: the gust at {:+.0f} sigma is {:.6f} kt in Python "
                           "and {:.6f} in the browser".format(where, sample, mine, theirs))

        held = wx.WeatherState(profiles[row["profile"]], seed=SEED,
                               elapsed_s=row["t"]).hold(
            wind_speed_kt=state.wind_speed_kt, wind_dir_deg=state.wind_dir_deg
        )
        for i, (rotor, wave) in enumerate(zip(row["rotor"], row["wave"])):
            x = sweep["x0"] + i * sweep["step"]
            mine_rotor = held.mechanical_turbulence(world, x, sweep["y"],
                                                    sweep["aglFt"])
            mine_wave = held.orographic_vertical_fpm(world, x, sweep["y"],
                                                     sweep["aglFt"])
            worst_rotor = max(worst_rotor, abs(mine_rotor))
            worst_wave = max(worst_wave, abs(mine_wave))
            if abs(mine_rotor - rotor) > WEATHER_TOLERANCE:
                out.append("{}: rotor at x={:.1f} is {:.6f} in Python and {:.6f} "
                           "in the browser".format(where, x, mine_rotor, rotor))
            # The wave is in feet per minute and runs to hundreds, so it gets a
            # tolerance scaled to its magnitude rather than the absolute one.
            if abs(mine_wave - wave) > WEATHER_TOLERANCE * max(1.0, abs(mine_wave)):
                out.append("{}: mountain wave at x={:.1f} is {:.6f} fpm in Python "
                           "and {:.6f} in the browser".format(where, x, mine_wave, wave))

    # A comparison that only ever saw flat ground and still air would pass while
    # proving nothing, so the sweep has to have found the effects it is checking.
    if worst_rotor < 0.3:
        out.append("the rotor sweep never found rough air (peak {:.3f}) -- the "
                   "sample points no longer sit in a ridge's lee".format(worst_rotor))
    if worst_wave < 100.0:
        out.append("the wave sweep never found sloping ground (peak {:.0f} fpm)"
                   .format(worst_wave))

    # The gusts come from the shared lattice hash, so they agree exactly or the
    # two builds are not drawing from the same arithmetic.
    sim = Session.new("a320neo", "stormy", seed=SEED).sim
    sim.state.seed = SEED
    for tick, index, axis, theirs in data.get("gusts", ()):
        sim.state.tick = tick
        mine = sim._gust_draw(index, axis)
        if abs(mine - theirs) > 1e-12:
            out.append("gust (tick {}, substep {}, axis {}) is {!r} in Python and "
                       "{!r} in the browser".format(tick, index, axis, mine, theirs))
    return out


def main():
    if len(sys.argv) < 2:
        print("usage: parity_check.py <json from parity_check.js>", file=sys.stderr)
        return 2
    data = json.load(open(sys.argv[1]))
    by_name = {c["name"]: c for c in data["cases"]}

    failures = weather_failures(data) + plan_failures(data) + debrief_failures(data)
    for row in data["rows"]:
        case = by_name[row["case"]]
        where = "{} / {}".format(row["key"], row["case"])
        speeds, takeoff, annunciator, channels, motors, ecam = python_row(
            row["key"], case
        )

        for field, browser_name in SPEED_FIELDS.items():
            mine = getattr(speeds, field)
            theirs = row["speeds"][browser_name]
            if abs(mine - theirs) > TOLERANCE_KT:
                failures.append(
                    "{}: {} is {:.3f} kt in Python and {:.3f} in the browser"
                    .format(where, field, mine, theirs)
                )

        for field in ("v1", "vr", "v2"):
            mine = getattr(takeoff, field)
            theirs = row["takeoff"][field]
            if abs(mine - theirs) > TOLERANCE_KT:
                failures.append(
                    "{}: {} is {:.3f} kt in Python and {:.3f} in the browser"
                    .format(where, field.upper(), mine, theirs)
                )

        for column, expected in row["fma"].items():
            mine = annunciator[column]
            for row_name in ("engaged", "armed"):
                text = mine[row_name][0] if mine[row_name] else None
                if text != expected[row_name]:
                    failures.append(
                        "{}: FMA {} {} is {!r} in Python and {!r} in the browser"
                        .format(where, column, row_name, text, expected[row_name])
                    )

        # Engine parameters: N1 flies the aeroplane, so a disagreement here is a
        # disagreement about thrust, not about a gauge.
        for index, (mine_e, theirs_e) in enumerate(zip(motors, row["engines"])):
            for field, browser_name, tol in (
                ("n1_pct", "n1", 0.01), ("n2_pct", "n2", 0.01),
                ("egt_c", "egt", 0.05), ("fuel_flow_kgh", "flow", 0.5),
            ):
                mine = getattr(mine_e, field)
                theirs = theirs_e[browser_name]
                if abs(mine - theirs) > tol:
                    failures.append(
                        "{}: ENG {} {} is {:.3f} in Python and {:.3f} in the browser"
                        .format(where, index + 1, field, mine, theirs)
                    )
            # The band, not just the value: the thresholds are the model's and
            # the browser used to hold its own copy and make the decision, so a
            # drifted limit would have passed a comparison of the temperature.
            if mine_e.egt_band != theirs_e["egtBand"]:
                failures.append(
                    "{}: ENG {} EGT band is {!r} in Python and {!r} in the browser"
                    .format(where, index + 1, mine_e.egt_band, theirs_e["egtBand"])
                )
            for field in ("failed", "fire"):
                if getattr(mine_e, field) != theirs_e[field]:
                    failures.append(
                        "{}: ENG {} {} is {} in Python and {} in the browser"
                        .format(where, index + 1, field,
                                getattr(mine_e, field), theirs_e[field])
                    )

        # The ECAM. Compared line for line, colour included: the whole point of
        # giving the messages one owner is that the two builds cannot complain
        # about different things, in a different order, in different colours.
        mine_lines = [(line.text, line.colour, line.indent) for line in ecam]
        their_lines = [
            (line["text"], line["colour"], line["indent"]) for line in row["ecam"]
        ]
        if mine_lines != their_lines:
            failures.append(
                "{}: the ECAM reads {} in Python and {} in the browser"
                .format(where, mine_lines, their_lines)
            )

        if channels != row["channels"]:
            failures.append(
                "{}: channels are {} in Python and {} in the browser"
                .format(where, channels, row["channels"])
            )

    print("{} states, {} types, {} weather cases, {} flight plans, "
          "{} debriefs".format(
              len(data["rows"]), len({r["key"] for r in data["rows"]}),
              len(data.get("weather", ())), len(data.get("plans", ())),
              len(data.get("debriefs", ()))))
    if failures:
        print("\nDISAGREEMENTS ({}):".format(len(failures)))
        for line in failures[:40]:
            print("  " + line)
        if len(failures) > 40:
            print("  ... and {} more".format(len(failures) - 40))
        return 1
    print("the two builds agree on every speed, engine parameter, flight mode, "
          "ECAM line,\nweather sample, flight-plan figure and debrief row -- "
          "and on every gust, exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
