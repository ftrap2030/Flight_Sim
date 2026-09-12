"""Where you are going, and how the flight went.

Two things the simulator was missing as a *game* rather than a sandbox: an
objective, and a result. A route gives the flight a point; the debrief turns
each attempt into something you can compare against the last one.

Fuel on arrival is the number that makes a route a decision rather than a
formality -- it is computed from the live fuel flow and the actual ground speed,
so a headwind or a detour around a ridge shows up in it immediately.
"""

import math
from dataclasses import dataclass, field

from . import atmosphere as atm


@dataclass
class Waypoint:
    name: str
    x_nm: float
    y_nm: float
    ident: str = ""
    is_airfield: bool = False
    # The field's published elevation, carried on the waypoint rather than
    # looked up again later: a plan names places the aeroplane has flown far
    # away from, and the airfield search that found them no longer returns
    # them. The descent has to end at the right height above sea level.
    elevation_ft: float = 0.0

    def to_dict(self):
        return {
            "name": self.name,
            "x_nm": self.x_nm,
            "y_nm": self.y_nm,
            "ident": self.ident,
            "is_airfield": self.is_airfield,
            "elevation_ft": self.elevation_ft,
        }

    @classmethod
    def from_dict(cls, data):
        # `elevation_ft` arrived later than the rest; a save from before it
        # existed still loads, at sea level.
        fields = {k: v for k, v in data.items() if k in cls.__annotations__}
        return cls(**fields)

    @classmethod
    def from_airfield(cls, airfield):
        return cls(
            name=airfield.name,
            x_nm=airfield.x_nm,
            y_nm=airfield.y_nm,
            ident=airfield.ident,
            is_airfield=True,
            elevation_ft=airfield.elevation_ft,
        )

    def distance_nm(self, x_nm, y_nm):
        return math.hypot(self.x_nm - x_nm, self.y_nm - y_nm)

    def bearing_from(self, x_nm, y_nm):
        return math.degrees(math.atan2(self.x_nm - x_nm, self.y_nm - y_nm)) % 360.0


@dataclass
class Leg:
    """The live picture for the waypoint currently being flown to."""

    waypoint: Waypoint
    distance_nm: float
    bearing_deg: float
    relative_bearing_deg: float
    eta_s: float
    fuel_required_kg: float
    fuel_on_arrival_kg: float
    remaining_after_kg: float

    @property
    def reachable(self):
        return self.fuel_on_arrival_kg > 0.0

    def eta_text(self):
        return eta_text(self.eta_s)


def eta_text(eta_s):
    """Minutes and seconds, or `--:--` past a day.

    A groundspeed near zero makes the arithmetic produce a number, and it is
    not information. One owner, because the flight plan quotes it too.
    """
    if not math.isfinite(eta_s) or eta_s > 24 * 3600:
        return "--:--"
    total = int(round(eta_s))
    return "{:02d}:{:02d}".format(total // 60, total % 60)


# Distance at which a waypoint counts as reached and the route steps on.
WAYPOINT_CAPTURE_NM = 1.5


class Route:
    """An ordered list of waypoints with a cursor on the active one."""

    def __init__(self, waypoints=None, active=0):
        self.waypoints = list(waypoints or [])
        self.active = active

    # -- serialisation -------------------------------------------------

    def to_dict(self):
        return {
            "waypoints": [w.to_dict() for w in self.waypoints],
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, data):
        if not data:
            return cls()
        return cls(
            [Waypoint.from_dict(w) for w in data.get("waypoints", [])],
            data.get("active", 0),
        )

    # -- editing -------------------------------------------------------

    def direct_to(self, waypoint):
        """Abandon the rest of the route and go straight there."""
        self.waypoints = [waypoint]
        self.active = 0

    def append(self, waypoint):
        self.waypoints.append(waypoint)

    def clear(self):
        self.waypoints = []
        self.active = 0

    @property
    def active_waypoint(self):
        if 0 <= self.active < len(self.waypoints):
            return self.waypoints[self.active]
        return None

    @property
    def destination(self):
        return self.waypoints[-1] if self.waypoints else None

    @property
    def finished(self):
        return self.active >= len(self.waypoints)

    def advance_if_reached(self, x_nm, y_nm):
        """Step to the next waypoint once this one is behind you.

        Never advances past the final waypoint: arriving at the destination is
        the point of the route, and the guidance should keep pointing at it.
        """
        current = self.active_waypoint
        if current is None:
            return False
        if self.active >= len(self.waypoints) - 1:
            return False
        if current.distance_nm(x_nm, y_nm) <= WAYPOINT_CAPTURE_NM:
            self.active += 1
            return True
        return False


def leg_for(sim, readout):
    """Guidance to the active waypoint, or None if there is no route."""
    route = sim.route
    waypoint = route.active_waypoint if route else None
    if waypoint is None:
        return None

    state = sim.state
    distance = waypoint.distance_nm(state.x_nm, state.y_nm)
    bearing = waypoint.bearing_from(state.x_nm, state.y_nm)
    relative = (bearing - state.heading_deg + 180.0) % 360.0 - 180.0

    ground_speed = max(readout.ground_speed_kt, 1.0)
    eta_s = distance / ground_speed * 3600.0
    fuel_required = readout.fuel_flow_kgh * eta_s / 3600.0

    return Leg(
        waypoint=waypoint,
        distance_nm=distance,
        bearing_deg=bearing,
        relative_bearing_deg=relative,
        eta_s=eta_s,
        fuel_required_kg=fuel_required,
        fuel_on_arrival_kg=readout.fuel_kg - fuel_required,
        remaining_after_kg=readout.fuel_kg - fuel_required,
    )



# ---------------------------------------------------------------------------
# The flight plan
# ---------------------------------------------------------------------------

# How far the cruise is integrated between re-asking what the aeroplane burns.
# Cruise flow is strongly weight-dependent -- the same A321neo burns 2,300 kg/h
# at 85 tonnes and under 2,000 late in a flight -- so a plan that uses the ramp
# weight the whole way over-predicts a long route, and the debrief then reports
# a saving that never happened.
CRUISE_STEP_NM = 25.0

# Final reserve: thirty minutes holding, which is what the rule is.
RESERVE_MINUTES = 30.0
HOLDING_ALTITUDE_FT = 1500.0

# How far the level search steps down when the profile will not fit, and how
# low it is willing to go. Five thousand feet above the destination is the
# lowest a plan will file: below that there is no cruise worth the name.
LEVEL_STEP_FT = 2000.0
MINIMUM_CRUISE_ABOVE_FIELD_FT = 5000.0


@dataclass
class PlanLeg:
    waypoint: Waypoint
    distance_nm: float
    track_deg: float
    fuel_kg: float
    time_s: float


@dataclass
class Plan:
    """What a route will cost, asked of the aeroplane that will fly it.

    Three phases, each integrated through `Simulator`'s own force model rather
    than from a table: climb at full thrust to the cruise level, cruise at the
    type's cruise Mach with the mass falling as the fuel goes, and an idle
    descent to the destination's elevation. Then a reserve.

    The per-leg figures are the phase totals attributed by distance, so they
    add up to the block fuel exactly rather than being a second estimate.
    """

    legs: list
    cruise_ft: float
    climb_fuel_kg: float
    climb_time_s: float
    climb_distance_nm: float
    cruise_fuel_kg: float
    cruise_time_s: float
    descent_fuel_kg: float
    descent_time_s: float
    descent_distance_nm: float
    reserve_kg: float
    fuel_on_board_kg: float

    @property
    def distance_nm(self):
        return sum(leg.distance_nm for leg in self.legs)

    @property
    def block_fuel_kg(self):
        """What the flight costs, gate to gate. Not counting the reserve."""
        return self.climb_fuel_kg + self.cruise_fuel_kg + self.descent_fuel_kg

    @property
    def required_kg(self):
        return self.block_fuel_kg + self.reserve_kg

    @property
    def time_s(self):
        return self.climb_time_s + self.cruise_time_s + self.descent_time_s

    @property
    def enough(self):
        """Whether the fuel aboard covers the block *and* the reserve.

        The reserve is the point: a plan that arrives with nothing left is not
        a plan that works, it is one that happened to.
        """
        return self.fuel_on_board_kg >= self.required_kg

    @property
    def spare_kg(self):
        return self.fuel_on_board_kg - self.required_kg


def default_cruise_ft(craft):
    """A cruise level the type can actually hold, rounded to a thousand feet.

    Two thousand below the certified ceiling, because the ceiling is where the
    climb rate has gone to nothing and nobody plans to cruise there.
    """
    return math.floor(min(craft.ceiling_ft - 2000.0, 37000.0) / 1000.0) * 1000.0


def plan(sim, cruise_ft=None):
    """Cost the route the simulator is carrying. None if there is not one.

    Measured from where the aeroplane *is*, through the waypoints still ahead
    of it -- so it is a live figure rather than a filing, and it answers "can I
    still get there" halfway down a leg as readily as before departure.
    """
    route = getattr(sim, "route", None)
    if route is None or not route.waypoints:
        return None

    state = sim.state
    craft = sim.aircraft
    cruise_ft = default_cruise_ft(craft) if cruise_ft is None else cruise_ft

    # The legs still to fly: from the aeroplane to the active waypoint, then
    # between the waypoints after it.
    spans = []
    x_nm, y_nm = state.x_nm, state.y_nm
    for waypoint in route.waypoints[route.active:]:
        spans.append((waypoint,
                      waypoint.distance_nm(x_nm, y_nm),
                      waypoint.bearing_from(x_nm, y_nm)))
        x_nm, y_nm = waypoint.x_nm, waypoint.y_nm
    total_nm = sum(span[1] for span in spans)

    destination = route.destination
    field_elevation_ft = destination.elevation_ft if destination else 0.0

    # Choose a level the sector can actually use. Asked for FL370 on a
    # hundred-mile hop, an A320neo would spend seventy-four miles climbing and
    # a hundred and eleven descending -- a profile a hundred miles long does
    # not have room for, and costing it as though it did charges a climb the
    # aeroplane never finishes. So step down until it fits, which is what a
    # dispatcher does and why short sectors cruise low.
    floor_ft = field_elevation_ft + MINIMUM_CRUISE_ABOVE_FIELD_FT
    cruise_ft = max(cruise_ft, floor_ft)
    while True:
        climb = sim.climb_segment(state.altitude_ft, cruise_ft, state.mass_kg)
        descent = sim.descent_segment(
            cruise_ft, field_elevation_ft, state.mass_kg - climb[0]
        )
        if climb[2] + descent[2] <= total_nm or cruise_ft <= floor_ft:
            break
        cruise_ft = max(floor_ft, cruise_ft - LEVEL_STEP_FT)

    climb_fuel, climb_time, climb_nm = climb
    # Even the floor may not fit on a sector of a few miles. The aeroplane
    # genuinely spends all of its distance climbing and descending then, so
    # both phases are cut back to the share of the distance each takes.
    profile_nm = climb_nm + descent[2]
    if profile_nm > total_nm > 0.0:
        shrink = total_nm / profile_nm
        climb_fuel, climb_time, climb_nm = (v * shrink for v in climb)
        descent = tuple(v * shrink for v in descent)

    mass_kg = state.mass_kg - climb_fuel

    # The descent's fuel depends on the mass at the top of it, which depends on
    # the cruise, which depends on how much distance the descent leaves for it.
    # Two passes settles it: the descent burns so little that the second pass
    # moves the answer by grams, but the *distance* it takes moves the cruise
    # by real fuel.
    for _ in range(2):
        cruise_nm = max(0.0, total_nm - climb_nm - descent[2])
        cruise_fuel, cruise_time = _cruise_cost(sim, cruise_ft, mass_kg, cruise_nm)
        if profile_nm <= total_nm:
            descent = sim.descent_segment(
                cruise_ft, field_elevation_ft, mass_kg - cruise_fuel
            )
    descent_fuel, descent_time, descent_nm = descent
    mass_kg -= cruise_fuel + descent_fuel

    reserve_kg = sim.holding_flow_kgh(HOLDING_ALTITUDE_FT, mass_kg) * (
        RESERVE_MINUTES / 60.0
    )

    legs = _attribute_to_legs(
        spans, climb_nm, climb_fuel, climb_time,
        cruise_nm, cruise_fuel, cruise_time,
        descent_nm, descent_fuel, descent_time,
    )
    return Plan(
        legs=legs, cruise_ft=cruise_ft,
        climb_fuel_kg=climb_fuel, climb_time_s=climb_time, climb_distance_nm=climb_nm,
        cruise_fuel_kg=cruise_fuel, cruise_time_s=cruise_time,
        descent_fuel_kg=descent_fuel, descent_time_s=descent_time,
        descent_distance_nm=descent_nm,
        reserve_kg=reserve_kg, fuel_on_board_kg=state.fuel_kg,
    )


def _cruise_cost(sim, cruise_ft, start_mass_kg, distance_nm):
    """Fuel and time to cruise a distance, with the mass falling as it goes."""
    if distance_nm <= 0.0:
        return (0.0, 0.0)
    craft = sim.aircraft
    tas_kt = atm.mach_to_tas(craft.cruise_mach, cruise_ft) * atm.KT_PER_MS
    mass_kg = start_mass_kg
    fuel = time_s = 0.0
    remaining = distance_nm
    while remaining > 0.0:
        step_nm = min(CRUISE_STEP_NM, remaining)
        step_s = step_nm / max(tas_kt, 1.0) * 3600.0
        flow = sim.level_flight_flow_kgh(cruise_ft, craft.cruise_mach, mass_kg)
        burn = flow * step_s / 3600.0
        fuel += burn
        mass_kg -= burn
        time_s += step_s
        remaining -= step_nm
    return (fuel, time_s)


def _attribute_to_legs(spans, climb_nm, climb_fuel, climb_time,
                       cruise_nm, cruise_fuel, cruise_time,
                       descent_nm, descent_fuel, descent_time):
    """Split the three phase totals across the legs, by distance.

    A leg's fuel is not a fourth estimate -- it is the share of the climb, the
    cruise and the descent that happens to fall inside it, so the legs add up
    to the block fuel exactly. A short first leg that is still in the climb
    therefore costs far more per mile than a long one in the cruise, which is
    the truth about flying and is worth showing.
    """
    total_nm = sum(span[1] for span in spans)
    boundaries = [
        (min(climb_nm, total_nm), climb_fuel, climb_time),
        (max(0.0, cruise_nm), cruise_fuel, cruise_time),
        (max(0.0, min(descent_nm, total_nm)), descent_fuel, descent_time),
    ]

    legs = []
    travelled = 0.0
    for waypoint, distance_nm, track_deg in spans:
        fuel = time_s = 0.0
        start, end = travelled, travelled + distance_nm
        phase_start = 0.0
        for phase_nm, phase_fuel, phase_time in boundaries:
            phase_end = phase_start + phase_nm
            overlap = max(0.0, min(end, phase_end) - max(start, phase_start))
            if overlap > 0.0 and phase_nm > 0.0:
                fuel += phase_fuel * overlap / phase_nm
                time_s += phase_time * overlap / phase_nm
            phase_start = phase_end
        legs.append(PlanLeg(waypoint, distance_nm, track_deg, fuel, time_s))
        travelled = end
    return legs

# ---------------------------------------------------------------------------
# The debrief
# ---------------------------------------------------------------------------

OUTCOME_TEXT = {
    "landed": "Landed",
    "overrun": "Ran off the end of the runway",
    "crashed_terrain": "Destroyed",
    "structural_failure": "Broke up in flight",
    "ended_by_pilot": "Ended by the pilot",
    "flying": "Still airborne",
    "rollout": "On the runway",
}


# How a row's numbers relate to each other. The *kind* is model data and the
# rendering is not: a markdown table and an HTML card may lay a row out however
# they like, but they must not disagree about what the numbers are or about how
# many digits of each are meaningful. Six kinds cover every row there is.
#
#   plain  one number and a unit
#   clock  seconds, shown as minutes and seconds
#   of     a number out of a total -- burned, of what was loaded
#   mach   an airspeed and the Mach number at the same moment
#   ratio  an airspeed and a percentage of Vref
#   vs     what happened, against what was planned
# --------------------------------------------------------------------------
# The managed descent
#
# The flight plan already integrates an idle descent to price it. VNAV flies
# that same descent, and reads it off the same integration -- `descent_profile`
# in `physics` -- rather than computing a second one. Two descents would be two
# aeroplanes, and the arc on the navigation display would stop being the
# descent the fuel figure was based on.
# --------------------------------------------------------------------------

# How hard the path is recaptured, in feet per minute per foot high or low.
# Gentler than the glideslope's 1.9, because a descent from cruise has tens of
# miles to converge in and a passenger-carrying aeroplane should not chase it.
DESCENT_PATH_TO_VS = 1.4
# What counts as being on the path for the annunciator's purposes.
DESCENT_ON_PATH_FT = 250.0


@dataclass
class DescentGuidance:
    """Where the idle descent says the aeroplane should be, and where it is.

    Model data in the sense CLAUDE.md means it: both front ends draw the top of
    descent and neither may decide where it goes. Computed against the aircraft's
    *current* altitude and mass rather than the filed plan's, because the plan is
    what was intended and this is what is happening -- a flight that ended up
    low, or heavier than it meant to be, has to descend on the profile it can
    actually fly.
    """

    destination: object
    distance_to_go_nm: float
    top_of_descent_nm: float
    target_altitude_ft: float
    deviation_ft: float  # positive is high
    gradient_ft_per_nm: float
    field_elevation_ft: float

    @property
    def active(self):
        """Whether the aeroplane has reached the point of starting down."""
        return self.distance_to_go_nm <= self.top_of_descent_nm

    @property
    def on_path(self):
        return abs(self.deviation_ft) <= DESCENT_ON_PATH_FT


def route_distance_to_go_nm(sim):
    """How far the destination is *along the route*, not across country.

    A plan that doglegs through three waypoints is longer than the straight
    line to the last of them, and descending on the straight line would put the
    aeroplane at circuit height with a leg still to fly.
    """
    route = sim.route
    if route is None or not route.waypoints:
        return None
    remaining = route.waypoints[route.active:]
    if not remaining:
        return None
    state = sim.state
    total = remaining[0].distance_nm(state.x_nm, state.y_nm)
    for previous, following in zip(remaining, remaining[1:]):
        total += following.distance_nm(previous.x_nm, previous.y_nm)
    return total


def _altitude_on_path(points, distance_to_go_nm, field_ft):
    """Interpolate the profile, and take the local gradient with it.

    Returns `(altitude_ft, ft_per_nm)`. Past the top of descent the path is
    still level, so the gradient is zero and the target is the cruise level --
    which is what leaves the aeroplane holding its level rather than easing
    down early.
    """
    if distance_to_go_nm <= points[0][0]:
        return (field_ft, 0.0)
    for (near_nm, near_ft, _f0, _t0), (far_nm, far_ft, _f1, _t1) in zip(points, points[1:]):
        if distance_to_go_nm <= far_nm:
            span = far_nm - near_nm
            if span <= 1e-9:
                return (far_ft, 0.0)
            gradient = (far_ft - near_ft) / span
            return (near_ft + gradient * (distance_to_go_nm - near_nm), gradient)
    return (points[-1][1], 0.0)


def descent_guidance(sim):
    """The descent path from where the aeroplane actually is, or None.

    None when there is no route to descend along, which is the honest answer:
    a managed descent without a destination is not a descent, it is a dive.
    """
    route = sim.route
    destination = route.destination if route else None
    if destination is None:
        return None
    distance_to_go = route_distance_to_go_nm(sim)
    if distance_to_go is None:
        return None

    state = sim.state
    field_ft = destination.elevation_ft or 0.0
    points = sim.descent_profile(state.altitude_ft, field_ft, state.mass_kg)
    if not points:
        # At or below the field already: there is no descent left to fly, and
        # saying so is better than inventing a path that goes upwards.
        return DescentGuidance(
            destination=destination,
            distance_to_go_nm=distance_to_go,
            top_of_descent_nm=0.0,
            target_altitude_ft=field_ft,
            deviation_ft=state.altitude_ft - field_ft,
            gradient_ft_per_nm=0.0,
            field_elevation_ft=field_ft,
        )

    target_ft, gradient = _altitude_on_path(points, distance_to_go, field_ft)
    return DescentGuidance(
        destination=destination,
        distance_to_go_nm=distance_to_go,
        top_of_descent_nm=points[-1][0],
        target_altitude_ft=target_ft,
        deviation_ft=state.altitude_ft - target_ft,
        gradient_ft_per_nm=gradient,
        field_elevation_ft=field_ft,
    )



ROW_KINDS = ("plain", "clock", "of", "mach", "ratio", "vs")


@dataclass
class DebriefRow:
    """One line of the debrief, as numbers rather than as text.

    `key` is what the parity guard compares on, because a label is prose and
    might be reworded in one build and not the other. `decimals` is here for
    the same reason `kind` is: two front ends that round a sink rate
    differently are two front ends that disagree about the landing.
    """

    key: str
    label: str
    value: float
    unit: str = ""
    decimals: int = 0
    kind: str = "plain"
    extra: float = 0.0

    def to_dict(self):
        return {
            "key": self.key, "label": self.label, "value": self.value,
            "unit": self.unit, "decimals": self.decimals,
            "kind": self.kind, "extra": self.extra,
        }


@dataclass
class Debrief:
    """How the flight went, as data. `debrief()` is one rendering of it."""

    aircraft_name: str
    weather_name: str
    outcome: str  # the status key
    outcome_text: str
    grade: str  # the touchdown grade, or "" if there was no touchdown
    rows: list
    warnings_seen: list
    route_idents: list
    planned_fuel_kg: float = 0.0

    def to_dict(self):
        return {
            "aircraft_name": self.aircraft_name,
            "weather_name": self.weather_name,
            "outcome": self.outcome,
            "outcome_text": self.outcome_text,
            "grade": self.grade,
            "rows": [r.to_dict() for r in self.rows],
            "warnings_seen": list(self.warnings_seen),
            "route_idents": list(self.route_idents),
            "planned_fuel_kg": self.planned_fuel_kg,
        }


def debrief_data(sim):
    """Everything the debrief says, as numbers. The single owner.

    There are two front ends and both had grown an end-of-flight card by hand:
    the same flight, summarised twice, with different rounding and a different
    set of rows. This is the same fix `fbw.characteristic_speeds` and
    `failures.ecam` are -- the model says which rows exist, in what order, and
    to how many digits; a display picks the fonts.
    """
    state = sim.state
    craft = sim.aircraft
    touchdown = state.touchdown
    burned = max(0.0, state.initial_fuel_kg - state.fuel_kg)
    minutes = state.elapsed_s / 60.0

    rows = [
        DebriefRow("time", "Time airborne", state.elapsed_s, "s", 0, "clock"),
        DebriefRow("distance", "Distance flown", state.distance_flown_nm, "nm", 1),
        DebriefRow("fuel_burned", "Fuel burned", burned, "kg", 0, "of",
                   state.initial_fuel_kg),
    ]
    if state.planned_fuel_kg > 0.0:
        rows.append(DebriefRow("fuel_planned", "Against the plan", burned, "kg", 0,
                               "vs", state.planned_fuel_kg))
    if minutes > 0.5:
        rows.append(DebriefRow("average_burn", "Average burn",
                               burned / minutes * 60.0, "kg/h", 0))
    rows.extend([
        DebriefRow("max_altitude", "Maximum altitude", state.max_altitude_ft, "ft", 0),
        DebriefRow("max_speed", "Highest speed", state.max_ias_kt, "kt", 0, "mach",
                   state.max_mach),
        # Floored, because you cannot come closer to the ground than touching
        # it. The integrator puts the wheels a fraction below the sampled
        # surface on the substep it lands, so the raw minimum goes slightly
        # negative and the card read "-0 ft" after every landing. The state
        # keeps the true figure; the row is what a pilot reads.
        DebriefRow("min_agl", "Closest to the ground",
                   max(0.0, state.min_agl_ft), "ft", 0),
        DebriefRow("max_load", "Highest load factor", state.max_load_factor, "g", 2),
    ])
    if touchdown:
        rows.extend([
            DebriefRow("sink", "Touchdown sink rate",
                       touchdown["sink_rate_fpm"], "fpm", 0),
            DebriefRow("touchdown_speed", "Touchdown speed", touchdown["ias_kt"],
                       "kt", 0, "ratio", touchdown["speed_ratio"] * 100.0),
            DebriefRow("centreline", "Off the centreline",
                       abs(touchdown["centreline_ft"]), "ft", 0),
            DebriefRow("remaining", "Runway remaining",
                       touchdown["remaining_ft"], "ft", 0),
        ])

    # Last, and last in the browser too: `parity_check` compares the rows in
    # order, so where a row goes is as much model data as what is in it.
    # Only when there was a controller to disobey -- a clearance nobody issued
    # cannot have been departed from, and a row reading "0 s" on every
    # routeless flight is noise.
    if state.atc_cleared_altitude_ft is not None:
        rows.append(DebriefRow("atc_deviation", "Off your clearance",
                               state.atc_deviation_s, "s", 0, "clock"))

    route = getattr(sim, "route", None)
    idents = [w.ident or w.name for w in route.waypoints] if route else []

    return Debrief(
        aircraft_name=craft.name,
        weather_name=sim.weather.name,
        outcome=state.status,
        outcome_text=OUTCOME_TEXT.get(state.status, state.status),
        grade=touchdown["grade"] if touchdown else "",
        rows=rows,
        warnings_seen=sorted(state.warnings_seen),
        route_idents=idents,
        planned_fuel_kg=state.planned_fuel_kg,
    )


def round_half_up(value, decimals=0):
    """Round halves away from zero, in arithmetic both builds compute alike.

    Python rounds halves to *even* and JavaScript rounds them *away from zero*,
    so a touchdown at 140.5 kt printed 140 in the text simulator and 141 in the
    browser -- two front ends disagreeing about a landing by a knot, which is
    exactly the class of thing the parity guard exists to find, and did on its
    first run.

    The fix is not to pick one language's rule. It is to do the rounding here,
    in the model, with one multiply-add-floor over the same IEEE doubles: after
    it there is no tie left for either formatter to break.
    """
    factor = 10.0 ** decimals
    rounded = math.floor(abs(value) * factor + 0.5) / factor
    return -rounded if value < 0 else rounded


def format_row(row):
    """One row's value as text. The browser renders the same six kinds."""
    if row.kind == "clock":
        # Round to whole seconds first: 119.9999 s split independently gives
        # the minutes as 1 and the seconds as 60.
        total = int(round_half_up(row.value))
        return "{:d} min {:02d} s".format(total // 60, total % 60)
    number = "{:,.{d}f}".format(round_half_up(row.value, row.decimals),
                                d=row.decimals)
    if row.kind == "of":
        return "{} {} of {:,.0f}".format(number, row.unit,
                                         round_half_up(row.extra))
    if row.kind == "mach":
        return "{} {} / M{:.3f}".format(number, row.unit,
                                        round_half_up(row.extra, 3))
    if row.kind == "ratio":
        return "{} {} ({:.0f}% of Vref)".format(number, row.unit,
                                                round_half_up(row.extra))
    if row.kind == "vs":
        # The percentage is derived from two numbers the model owns, by one
        # expression written the same way in both builds.
        delta = (row.value / row.extra - 1.0) * 100.0 if row.extra else 0.0
        return "{} {} against a planned {:,.0f} ({:+.0f}%)".format(
            number, row.unit, round_half_up(row.extra),
            round_half_up(delta))
    return "{} {}".format(number, row.unit).strip()


def debrief(sim):
    """A markdown summary of how the flight went."""
    data = debrief_data(sim)
    lines = ["## Debrief", ""]

    outcome = data.outcome_text
    if data.grade:
        outcome = "{} — **{}**".format(outcome, data.grade)
    lines.append("**{}** · {} · {}".format(
        data.aircraft_name, data.weather_name, outcome))
    lines.append("")

    lines.append("| | |")
    lines.append("| --- | ---: |")
    for row in data.rows:
        lines.append("| {} | {} |".format(row.label, format_row(row)))

    lines.append("")
    if data.warnings_seen:
        lines.append("**Warnings raised:** {}".format(", ".join(data.warnings_seen)))
    else:
        lines.append("**No warnings raised at any point.** A clean flight.")

    return "\n".join(lines)
