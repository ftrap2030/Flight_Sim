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

    def to_dict(self):
        return {
            "name": self.name,
            "x_nm": self.x_nm,
            "y_nm": self.y_nm,
            "ident": self.ident,
            "is_airfield": self.is_airfield,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

    @classmethod
    def from_airfield(cls, airfield):
        return cls(
            name=airfield.name,
            x_nm=airfield.x_nm,
            y_nm=airfield.y_nm,
            ident=airfield.ident,
            is_airfield=True,
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
        if not math.isfinite(self.eta_s) or self.eta_s > 24 * 3600:
            return "--:--"
        total = int(round(self.eta_s))
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
        DebriefRow("min_agl", "Closest to the ground", state.min_agl_ft, "ft", 0),
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


def format_row(row):
    """One row's value as text. The browser renders the same six kinds."""
    if row.kind == "clock":
        # Round to whole seconds first: 119.9999 s split independently gives
        # the minutes as 1 and the seconds as 60.
        total = int(round(row.value))
        return "{:d} min {:02d} s".format(total // 60, total % 60)
    number = "{:,.{d}f}".format(row.value, d=row.decimals)
    if row.kind == "of":
        return "{} {} of {:,.0f}".format(number, row.unit, row.extra)
    if row.kind == "mach":
        return "{} {} / M{:.3f}".format(number, row.unit, row.extra)
    if row.kind == "ratio":
        return "{} {} ({:.0f}% of Vref)".format(number, row.unit, row.extra)
    if row.kind == "vs":
        # The percentage is derived from two numbers the model owns, by one
        # expression written the same way in both builds.
        delta = (row.value / row.extra - 1.0) * 100.0 if row.extra else 0.0
        return "{} {} against a planned {:,.0f} ({:+.0f}%)".format(
            number, row.unit, row.extra, delta)
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
