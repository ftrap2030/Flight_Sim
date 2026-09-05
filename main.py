#!/usr/bin/env python3
"""Airbus text flight simulator.

Two ways to fly:

    python main.py
        Full interactive session -- aircraft menu, weather menu, then the loop.

    python main.py --state-file flight.json --new --aircraft a350 --weather stormy
    python main.py --state-file flight.json --command "turn left heading 180"
        One command per invocation, state persisted to JSON in between. Useful
        for scripting, for replaying a flight, and for driving the simulator
        from somewhere other than a terminal.
"""

import argparse
import json
import sys

from flight_sim import aircraft as fleet
from flight_sim import dashboard, game
from flight_sim import weather as wx


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Airbus text flight simulator.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--state-file",
        help="Path to persist the flight between invocations (one-shot mode).",
    )
    parser.add_argument(
        "--new",
        action="store_true",
        help="Start a new flight, overwriting any existing state file.",
    )
    parser.add_argument(
        "--aircraft",
        help="Aircraft key: {}".format(", ".join(a.key for a in fleet.FLEET)),
    )
    parser.add_argument(
        "--weather",
        help="Weather key: {}".format(", ".join(w.key for w in wx.WEATHER_OPTIONS)),
    )
    parser.add_argument("--seed", type=int, default=20260905, help="World seed.")
    parser.add_argument(
        "--altitude", type=float, default=5000.0, help="Starting altitude in feet."
    )
    parser.add_argument(
        "--command",
        action="append",
        default=[],
        help="A flight command. May be repeated to run several in order.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Also emit the raw instrument readout as JSON on stderr.",
    )
    parser.add_argument(
        "--list", action="store_true", help="Print the fleet and weather menus and exit."
    )
    return parser


def emit_json(session):
    readout = session.sim.readout()
    payload = {
        "status": session.sim.state.status,
        "tick": session.sim.state.tick,
        "elapsed_s": round(session.sim.state.elapsed_s, 1),
        "readout": {
            key: (round(value, 3) if isinstance(value, float) else value)
            for key, value in vars(readout).items()
        },
    }
    print(json.dumps(payload, indent=1), file=sys.stderr)


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.list:
        print(dashboard.fleet_menu())
        print(dashboard.weather_menu())
        return 0

    # Interactive mode: no state file, no scripted commands.
    if not args.state_file and not args.command:
        return game.run_interactive(seed=args.seed)

    if not args.state_file:
        print("--command requires --state-file.", file=sys.stderr)
        return 2

    starting_new = args.new or not _exists(args.state_file)
    if starting_new:
        craft = fleet.resolve(args.aircraft or "a320neo")
        profile = wx.resolve(args.weather or "clear")
        if craft is None:
            print("Unknown aircraft: {}".format(args.aircraft), file=sys.stderr)
            return 2
        if profile is None:
            print("Unknown weather: {}".format(args.weather), file=sys.stderr)
            return 2
        session = game.Session.new(
            craft.key, profile.key, seed=args.seed, altitude_ft=args.altitude
        )
        print(session.initial_report())
    else:
        session = game.Session.load(args.state_file)

    for raw in args.command:
        if session.finished:
            break
        print()
        print("---")
        print()
        print("**> {}**".format(raw.strip()))
        print()
        text, _finished = session.execute(raw)
        print(text)

    session.save(args.state_file)
    if args.json:
        emit_json(session)
    return 0


def _exists(path):
    import os

    return os.path.exists(path)


if __name__ == "__main__":
    raise SystemExit(main())
