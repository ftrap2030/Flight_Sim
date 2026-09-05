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
    def new(cls, aircraft_key, weather_key, seed=20260905, altitude_ft=5000.0):
        sim = physics.Simulator.new_flight(
            aircraft_key, weather_key, seed=seed, altitude_ft=altitude_ft
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
        return self.sim.state.status != physics.FLYING

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

    def report(self, readout, title=None):
        blocks = [dashboard.render(self.sim, readout, title=title), ""]
        if self.finished:
            blocks.append(self.narrator.ending(self.sim, readout))
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

        if command.kind == "quit":
            self.sim.state.status = physics.ENDED_BY_PILOT
            readout = self.sim.readout()
            self.sim._record_impact()
            return (self.report(readout), True)

        cmd.apply(self.sim, command)
        seconds = command.seconds or physics.TICK_SECONDS
        readout = self.sim.step_tick(seconds)
        return (self.report(readout), self.finished)


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
        raw = _prompt("Select an aircraft (1-5, or type the name) > ")
        if raw.strip().lower() in ("quit", "exit"):
            return None
        craft = fleet.resolve(raw)
        if craft:
            return craft
        print("Not one of the five. Try `1`, `A320neo`, `a350`, and so on.\n")


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

    session = Session.new(craft.key, profile.key, seed=seed)
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
