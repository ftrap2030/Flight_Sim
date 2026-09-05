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


def debrief(sim):
    """A markdown summary of how the flight went."""
    state = sim.state
    craft = sim.aircraft
    lines = ["## Debrief", ""]

    outcome = OUTCOME_TEXT.get(state.status, state.status)
    touchdown = state.touchdown
    if touchdown:
        outcome = "{} — **{}**".format(outcome, touchdown["grade"])

    lines.append("**{}** · {} · {}".format(craft.name, sim.weather.name, outcome))
    lines.append("")

    burned = max(0.0, state.initial_fuel_kg - state.fuel_kg)
    minutes = state.elapsed_s / 60.0
    lines.append("| | |")
    lines.append("| --- | ---: |")
    lines.append("| Time airborne | {:.0f} min {:.0f} s |".format(
        minutes // 1, state.elapsed_s % 60.0))
    lines.append("| Distance flown | {:,.1f} nm |".format(state.distance_flown_nm))
    lines.append("| Fuel burned | {:,.0f} kg of {:,.0f} |".format(
        burned, state.initial_fuel_kg))
    if minutes > 0.5:
        lines.append("| Average burn | {:,.0f} kg/h |".format(burned / minutes * 60.0))
    lines.append("| Maximum altitude | {:,.0f} ft |".format(state.max_altitude_ft))
    lines.append("| Highest speed | {:,.0f} kt / M{:.3f} |".format(
        state.max_ias_kt, state.max_mach))
    lines.append("| Closest to the ground | {:,.0f} ft |".format(state.min_agl_ft))
    lines.append("| Highest load factor | {:.2f} g |".format(state.max_load_factor))

    if touchdown:
        lines.append("| Touchdown sink rate | {:,.0f} fpm |".format(
            touchdown["sink_rate_fpm"]))
        lines.append("| Touchdown speed | {:,.0f} kt ({:.0f}% of Vref) |".format(
            touchdown["ias_kt"], touchdown["speed_ratio"] * 100.0))
        lines.append("| Off the centreline | {:,.0f} ft |".format(
            abs(touchdown["centreline_ft"])))
        lines.append("| Runway remaining | {:,.0f} ft |".format(
            touchdown["remaining_ft"]))

    lines.append("")
    if state.warnings_seen:
        lines.append("**Warnings raised:** {}".format(
            ", ".join(sorted(state.warnings_seen))))
    else:
        lines.append("**No warnings raised at any point.** A clean flight.")

    return "\n".join(lines)
