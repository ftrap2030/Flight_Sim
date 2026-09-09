"""Session orchestration: Phase 1 setup, the Phase 2 flight loop, persistence.

A Session couples a Simulator with a Narrator and knows how to serialise both,
which is what lets the same flight be driven either from an interactive REPL or
one command at a time across separate process invocations.
"""

import json
import os

from . import aircraft as fleet
from . import commands as cmd
from . import dashboard
from . import mapview
from . import navigation
from . import physics
from . import weather as wx
from .narrator import Narrator

SAVE_VERSION = 1


class Session:
    def __init__(self, sim, narrator):
        self.sim = sim
        self.narrator = narrator

    # -- lifecycle -----------------------------------------------------

    @classmethod
    def new(cls, aircraft_key, weather_key, seed=20260905, altitude_ft=5000.0,
            start=physics.AIRBORNE_START):
        sim = physics.Simulator.new_flight(
            aircraft_key, weather_key, seed=seed, altitude_ft=altitude_ft,
            start=start,
        )
        return cls(sim, Narrator(seed=seed))

    def to_dict(self):
        rng_version, rng_state, rng_gauss = self.narrator._rng.getstate()
        return {
            "version": SAVE_VERSION,
            "state": self.sim.state.to_dict(),
            "narrator": {
                "recent": list(self.narrator._recent),
                "rng": [rng_version, list(rng_state), rng_gauss],
            },
        }

    @classmethod
    def from_dict(cls, data):
        state = physics.FlightState.from_dict(data["state"])
        sim = physics.Simulator(state)
        narrator = Narrator(seed=state.seed)
        saved = data.get("narrator") or {}
        for key in saved.get("recent", []):
            narrator._recent.append(key)
        rng = saved.get("rng")
        if rng:
            narrator._rng.setstate((rng[0], tuple(rng[1]), rng[2]))
        return cls(sim, narrator)

    def save(self, path):
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=1)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    # -- reporting -----------------------------------------------------

    @property
    def finished(self):
        # The rollout is not an ending: you are down, but you are still moving,
        # and you can still run off the end.
        return self.sim.state.status not in physics.LIVE_STATUSES

    def initial_report(self):
        """The briefing plus the first dashboard and view, at T+0."""
        readout = self.sim.readout()
        blocks = [
            dashboard.briefing(self.sim.aircraft, self.sim.weather, readout),
            "",
            "---",
            "",
            dashboard.render(self.sim, readout),
            "",
            self.narrator.describe(self.sim, readout),
        ]
        return "\n".join(blocks)

    def airfield_list(self, radius_nm=120.0):
        """Airfields within reach, nearest first, with bearing and distance."""
        state = self.sim.state
        found = self.sim.airfields.near(state.x_nm, state.y_nm, radius_nm)
        if not found:
            return "No airfield within {:.0f} nm.".format(radius_nm)

        lines = ["### Airfields within {:.0f} nm".format(radius_nm), ""]
        lines.append("| Ident | Name | Bearing | Distance | Elev | Runway | Approach |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- |")
        for a in found[:10]:
            lines.append(
                "| **{}** | {} | {:03.0f}° | {:.1f} nm | {:,.0f} ft | "
                "{:02.0f} / {:,.0f} ft | {} |".format(
                    a.ident,
                    a.name,
                    a.bearing_from(state.x_nm, state.y_nm),
                    a.distance_nm(state.x_nm, state.y_nm),
                    a.elevation_ft,
                    a.runway_heading_deg / 10.0,
                    a.runway_length_ft,
                    "ILS" if a.has_ils else "visual",
                )
            )
        notes = [a for a in found[:10] if a.note]
        if notes:
            lines.append("")
            for a in notes:
                lines.append("**{}** — {}".format(a.name, a.note))
        return "\n".join(lines)

    def _find_field(self, target):
        """An airfield by ident, or failing that by name. None if neither."""
        state = self.sim.state
        field = self.sim.airfields.by_ident(
            target, state.x_nm, state.y_nm, radius_nm=400.0
        )
        if field is not None:
            return field
        # Fall back to a name match, so "direct to kettlebridge" works too.
        wanted = (target or "").strip().lower()
        for candidate in self.sim.airfields.near(state.x_nm, state.y_nm, 400.0):
            if wanted and wanted in candidate.name.lower():
                return candidate
        return None

    def _no_such_field(self, target):
        return (
            "No airfield matching `{}` within 400 nm. Try `airfields` for "
            "what is in reach.".format(target)
        )

    def set_route(self, target):
        """Build a whole flight plan: `route ANFL KEBR CROW`.

        All or nothing. A plan with a hole in it is worse than no plan, because
        the guidance would fly the legs either side of the gap as though it
        were a straight line and never say why.
        """
        idents = (target or "").split()
        if not idents:
            return "Name at least one waypoint: `route ANFL KEBR CROW`."
        fields = []
        for ident in idents:
            field = self._find_field(ident)
            if field is None:
                return self._no_such_field(ident)
            fields.append(field)

        self.sim.route.clear()
        for field in fields:
            self.sim.route.append(navigation.Waypoint.from_airfield(field))
        self.sim.sync_route()
        return self.plan_text()

    def add_waypoint(self, target):
        """Append one waypoint to the end of the plan."""
        field = self._find_field(target)
        if field is None:
            return self._no_such_field(target)
        self.sim.route.append(navigation.Waypoint.from_airfield(field))
        self.sim.sync_route()
        return self.plan_text()

    def remove_waypoint(self, target):
        """Take one waypoint out of the plan, by ident or by name."""
        wanted = (target or "").strip().lower()
        route = self.sim.route
        for index, waypoint in enumerate(route.waypoints):
            if wanted in (waypoint.ident or "").lower() or (
                wanted and wanted in waypoint.name.lower()
            ):
                route.waypoints.pop(index)
                # Keep the cursor on the waypoint it was on, or on the last one
                # if that was the one removed.
                if route.active > index:
                    route.active -= 1
                route.active = max(0, min(route.active, len(route.waypoints) - 1))
                self.sim.sync_route()
                return self.plan_text()
        return "`{}` is not in the plan.".format(target)

    def set_destination(self, target):
        """Point the route at a named airfield."""
        field = self._find_field(target)
        if field is None:
            return self._no_such_field(target)

        self.sim.route.direct_to(navigation.Waypoint.from_airfield(field))
        self.sim.sync_route()
        self.record_plan()
        readout = self.sim.readout()
        leg = readout.leg
        return "**Destination set: {}**\n\n{}\n\n{}".format(
            field.describe(),
            "{:.1f} nm on a bearing of {:03.0f}°, ETA {}.".format(
                leg.distance_nm, leg.bearing_deg, leg.eta_text()
            ),
            "Estimated fuel on arrival: **{:,.0f} kg**.".format(
                leg.fuel_on_arrival_kg
            )
            if leg.reachable
            else "**You do not have the fuel.** Short by roughly "
                 "{:,.0f} kg at the current burn.".format(
                     abs(leg.fuel_on_arrival_kg)
                 ),
        )

    def record_plan(self):
        """Cost the route and remember the figure the flight will be judged on.

        Written at the moment the plan changes rather than read back at the
        end: a plan recomputed on arrival would be a plan that has watched the
        flight, and the comparison would always come out even.
        """
        costing = navigation.plan(self.sim)
        self.sim.state.planned_fuel_kg = costing.block_fuel_kg if costing else 0.0
        return costing

    def plan_text(self):
        route = self.sim.route
        if not route.waypoints:
            return (
                "No route set. `route ANFL KEBR CROW` files a plan, "
                "`direct to <ident>` picks a single destination, and "
                "`airfields` lists what is in reach."
            )
        costing = self.record_plan()
        state = self.sim.state
        lines = ["### Flight plan", "", "| | | | | |", "| --- | ---: | ---: | ---: | ---: |"]

        flown = route.waypoints[: route.active]
        for waypoint in flown:
            lines.append("| ~~{}~~ | | | | |".format(waypoint.ident or waypoint.name))
        for index, leg in enumerate(costing.legs):
            lines.append("| {}{} | {:.0f}° | {:,.1f} nm | {:,.0f} kg | {} |".format(
                "**> " if index == 0 else "",
                (leg.waypoint.ident or leg.waypoint.name) + ("**" if index == 0 else ""),
                leg.track_deg, leg.distance_nm, leg.fuel_kg,
                navigation.eta_text(leg.time_s),
            ))

        lines.append("")
        lines.append(
            "Cruise **FL{:03.0f}** · {:,.1f} nm · {:.0f} min".format(
                costing.cruise_ft / 100.0, costing.distance_nm, costing.time_s / 60.0
            )
        )
        lines.append("")
        lines.append(
            "Block fuel **{:,.0f} kg** — {:,.0f} climb, {:,.0f} cruise, "
            "{:,.0f} descent — and {:,.0f} kg of reserve.".format(
                costing.block_fuel_kg, costing.climb_fuel_kg,
                costing.cruise_fuel_kg, costing.descent_fuel_kg, costing.reserve_kg
            )
        )
        if costing.enough:
            lines.append(
                "You have {:,.0f} kg aboard: **{:,.0f} kg spare** over the "
                "reserve.".format(state.fuel_kg, costing.spare_kg)
            )
        else:
            lines.append(
                "You have {:,.0f} kg aboard and need {:,.0f}. **Short by "
                "{:,.0f} kg** — and that is before the reserve is touched."
                .format(state.fuel_kg, costing.required_kg, -costing.spare_kg)
            )
        return "\n".join(lines)

    def report(self, readout, title=None):
        blocks = [dashboard.render(self.sim, readout, title=title), ""]
        if self.finished:
            blocks.append(self.narrator.ending(self.sim, readout))
            blocks.append("")
            blocks.append("---")
            blocks.append("")
            blocks.append(navigation.debrief(self.sim))
        else:
            blocks.append(self.narrator.describe(self.sim, readout))
        return "\n".join(blocks)

    # -- the loop ------------------------------------------------------

    def execute(self, raw):
        """Run one pilot command. Returns (text, finished).

        Parse failures and no-time commands are handled here so that the REPL
        and the one-shot CLI behave identically.
        """
        if self.finished:
            return ("The simulation has already ended.", True)

        try:
            command = cmd.parse(raw)
        except cmd.ParseError as error:
            return (str(error), False)

        if command.kind == "help":
            return (cmd.HELP_TEXT, False)

        if command.kind == "status":
            return (dashboard.render(self.sim, self.sim.readout()), False)

        if command.kind == "map":
            return (mapview.render(self.sim, self.sim.readout()), False)

        if command.kind == "airfields":
            return (self.airfield_list(), False)

        if command.kind == "spec":
            craft = (
                fleet.resolve(command.target) if command.target else self.sim.aircraft
            )
            if craft is None:
                return (
                    "No such type: `{}`. `fleet` lists all {}.".format(
                        command.target, len(fleet.FLEET)
                    ),
                    False,
                )
            return (dashboard.spec_card(craft), False)

        if command.kind == "fleet":
            return (dashboard.fleet_menu(), False)

        if command.kind == "show_law":
            return (dashboard.law_card(self.sim), False)

        if command.kind == "set_route":
            return (self.set_route(command.target), False)
        if command.kind == "add_waypoint":
            return (self.add_waypoint(command.target), False)
        if command.kind == "remove_waypoint":
            return (self.remove_waypoint(command.target), False)
        if command.kind == "direct_to":
            return (self.set_destination(command.target), False)

        if command.kind == "show_plan":
            return (self.plan_text(), False)

        if command.kind == "clear_route":
            self.sim.route.clear()
            self.sim.sync_route()
            # Through `record_plan` like every other route change, or the
            # cancelled plan's block fuel stays on the state and the debrief
            # grades the flight against a plan the pilot threw away.
            self.record_plan()
            return ("Route cleared. No destination set.", False)

        if command.kind == "debrief":
            return (navigation.debrief(self.sim), False)

        if command.kind == "quit":
            self.sim.state.status = physics.ENDED_BY_PILOT
            readout = self.sim.readout()
            self.sim._record_impact()
            return (self.report(readout), True)

        cmd.apply(self.sim, command)

        # Respect the command's own declaration rather than the if-chain above.
        # Anything that only changes a setting -- the clock, say -- must not
        # burn ten seconds of flight just because nobody remembered to add it
        # to the list of exceptions.
        if not command.advances_time:
            return (
                "{}\n\n{}".format(
                    self._acknowledge(command),
                    dashboard.render(self.sim, self.sim.readout()),
                ),
                False,
            )

        seconds = command.seconds or physics.TICK_SECONDS
        readout = self.sim.step_tick(seconds)
        return (self.report(readout), self.finished)

    def _acknowledge(self, command):
        """A one-line confirmation for a command that costs no simulation time."""
        if command.kind == "time_of_day":
            return "Local time set to **{}** — {}.".format(
                dashboard._local_clock(self.sim.state.time_of_day_h),
                wx.light_phase(self.sim.state.time_of_day_h),
            )
        if command.kind == "control_law":
            return dashboard.law_card(self.sim)
        return "Acknowledged."


# ---------------------------------------------------------------------------
# Interactive setup and REPL
# ---------------------------------------------------------------------------


def _prompt(text):
    try:
        return input(text)
    except EOFError:
        return "quit"


def choose_aircraft():
    print(dashboard.fleet_menu())
    while True:
        raw = _prompt("Select an aircraft (1-9, or type the name) > ")
        stripped = raw.strip().lower()
        if stripped in ("quit", "exit"):
            return None
        # Let the pilot read a full card before committing to a type.
        if stripped.startswith("spec"):
            wanted = stripped[4:].strip()
            craft = fleet.resolve(wanted) if wanted else None
            if craft is None:
                print(
                    "`spec a350` — name one of the {}.\n".format(len(fleet.FLEET))
                )
            else:
                print(dashboard.spec_card(craft))
                print()
            continue
        craft = fleet.resolve(raw)
        if craft:
            return craft
        print(
            "Not one of the {}. Try `1`, `A320neo`, `a350-1000`, `BelugaXL`, "
            "or `spec a380` to see a full card.\n".format(len(fleet.FLEET))
        )


def choose_weather():
    print(dashboard.weather_menu())
    while True:
        raw = _prompt("Choose your weather (1-4, or type the name) > ")
        if raw.strip().lower() in ("quit", "exit"):
            return None
        profile = wx.resolve(raw)
        if profile:
            return profile
        print("Not recognised. Try `1`, `clear`, `stormy`, `foggy`, `crosswind`.\n")


def choose_start():
    """Airborne or on the runway."""
    print("### Where do you want to begin?\n")
    print("| # | Start |")
    print("| --- | --- |")
    print("| 1 | **On the runway** — lined up at ANFL, brakes set, flap 1 |")
    print("| 2 | **Airborne** — 5,000 ft, trimmed and level |")
    print()
    while True:
        raw = _prompt("Choose (1-2) > ").strip().lower()
        if raw in ("quit", "exit"):
            return None
        if raw in ("1", "runway", "ground", "takeoff"):
            return physics.RUNWAY_START
        if raw in ("2", "airborne", "air", "cruise", ""):
            return physics.AIRBORNE_START
        print("Not recognised. Try `1` or `2`.\n")


def run_interactive(seed=20260905):
    """Full Phase 1 + Phase 2 experience in the terminal."""
    print("=" * 72)
    print("  AIRBUS TEXT FLIGHT SIMULATOR")
    print("=" * 72)
    print()

    craft = choose_aircraft()
    if craft is None:
        return 0
    print()
    profile = choose_weather()
    if profile is None:
        return 0
    print()
    start = choose_start()
    if start is None:
        return 0
    print()

    session = Session.new(craft.key, profile.key, seed=seed, start=start)
    print(session.initial_report())
    print()
    print("---")
    print("Type `help` for commands, `quit` to end the simulation.")
    print()

    while not session.finished:
        raw = _prompt("FLIGHT COMMAND > ")
        print()
        text, finished = session.execute(raw)
        print(text)
        print()
        if finished:
            break
    return 0
