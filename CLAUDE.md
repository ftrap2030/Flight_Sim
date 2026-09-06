# Flight_Sim — notes for working on this codebase

A text-based Airbus flight simulator: a real point-mass physics model with a
procedural prose engine on top. Stdlib only, `unittest`, no dependencies.

```bash
python main.py                                  # play it
python -m unittest discover -s tests -t .       # 314 tests, ~50 s
python main.py --list                           # fleet and weather menus
```

## Layout

Dependencies flow one way. `atmosphere` knows nothing; `physics` is the hub;
`game` orchestrates. Nothing below imports `physics`.

```
main.py            CLI: interactive REPL, and a one-shot --command mode
flight_sim/
  atmosphere.py    ISA, density, TAS/IAS/Mach.       Imports nothing.
  aircraft.py      The five-type fleet as frozen dataclasses.
  terrain.py       Ridged-fBm world, graded sites, approach surfaces.
  weather.py       Immutable profiles + a mutable WeatherState.
  airfield.py      Airfield geometry; procedural and authored sources.
  landing.py       Approach guidance, touchdown grading, rollout physics.
  navigation.py    Routes, leg guidance, the end-of-flight debrief.
  autopilot.py     ALT / V/S / HDG / SPD / APPR controllers.
  physics.py       FlightState, Readout, Simulator. The integrator.
  narrator.py      The 251-clause prose engine.
  dashboard.py     Markdown instrument panel.
  mapview.py       Track-up ASCII terrain plan view.
  commands.py      Natural-language command parsing.
  game.py          Session: setup, the loop, persistence.
```

## Conventions

**Units.** Feet, knots and degrees at every interface — that is what a cockpit
uses. SI inside the force equations. `atmosphere.py` holds every conversion
constant; do not redefine them locally.

**`Simulator._aero_state()` is the single source of truth for forces.** The trim
solvers, the integrator and the instrument readout all go through it. They once
did not: `throttle_for_level_flight` computed available thrust with a different
lapse law from `_thrust_n`, so the aircraft trimmed to a throttle setting that
did not hold its speed, and the panel reported an L/D of 24 for an aeroplane
whose drag polar says 16. If you add a force term, add it there.

**`FlightState` is plain serialisable data.** Anything that must survive a
save/load lives on it — including things that look like implementation detail,
such as the turbulence filter state (`turb`) and the route (`route`). A session
resumed from disk must continue *identically* to one flown straight through;
`tests/test_game.py` asserts exactly that.

**Worlds are shared per seed** via `physics.world_for_seed`. Terrain and
airfields are pure functions of the seed, and generating airfields is the most
expensive thing in the simulator, so sharing is correctness-preserving rather
than a cache. A test that depends on a pristine, unexplored world must use a
**distinct seed** — or call `physics.forget_worlds()`.

## Where the calibrated constants come from

This is the part that is invisible in the code and easy to break.

`tsfc`, `cd_0` and `oswald_e` in `aircraft.py` were **solved**, not looked up.
For each type: trim at its published cruise altitude and Mach, then find the
TSFC that reproduces its published block fuel flow. The drag polar was tuned
first so that cruise L/D lands in the real 17–19 band, because an L/D that is
wrong makes the required TSFC absorb the error.

The result is cross-checked against reality: multiply TSFC by 35,306 for
lb/(lbf·hr) and the CFM56 comes out at 0.59, the LEAP at 0.51, the Trents at
~0.43 — all within published ranges for those engines. That agreement is the
evidence the numbers are physical rather than fudge factors.

**Therefore: changing a drag polar without re-solving the TSFC makes the fuel
page quietly lie.** `tests/test_physics.py::CRUISE_TARGETS` holds every type to
5% of its published flow, which is the guard.

Thrust lapse is three effects (density, steeper above the tropopause; ram drag
with Mach; a fade across the certified ceiling). Without the last one an A320
climbs to 50,000 ft.

## Bug families that have bitten more than once

**Float truncation in displayed times.** A ten-second tick accumulates to
9.99999 over 100 substeps. Splitting that into minutes and seconds
independently gives "1 min 60 s"; truncating gives "T+00:09". Round to whole
seconds *first*, then divide. Bitten twice — `dashboard._clock` and
`navigation.debrief`.

**Regex matcher ordering in `commands.py`.** `_MATCHERS` is tried in order and
the first hit wins, so a broad pattern registered early swallows a specific one
registered later. "fly to heading 270" became a destination named "heading 270"
until the lateral matcher was moved ahead of navigation. Alternation inside one
pattern is ordered too: `direct|direct to` matches the bare `direct` first.
**Add a parse test whenever you add a matcher.**

**`__getattr__` delegation on `WeatherState`.** It forwards anything not set on
the instance to the immutable profile — which means a *method* reached that way
binds to the profile and reports the profile's fixed values. `turbulence_label`
and `wind_components` are explicitly overridden for this reason; a
`WeatherState` in extreme turbulence otherwise politely reported `NIL`.

**Commands listed in two places.** `commands.apply` has a no-op tuple for
commands that only meta-signal, and an `elif` chain for those that mutate state.
Listing a command in both means the tuple wins and the handler never runs —
`autopilot on` silently did nothing. `Session.execute` respects
`Command.advances_time` rather than keeping its own list, which fixed the
related class of bug.

## Testing patterns

- One test file per module, named for it. 314 tests, ~50 s.
- Assert against **published figures** where they exist: ISA density tables,
  cruise fuel flow, service ceilings, Vmca. These catch calibration drift that
  self-consistent tests never would.
- Assert **relative properties** where absolutes are brittle: which side of the
  map is more dangerous, not which glyph appears; that the neo burns less than
  the ceo, not that it burns exactly N kg.
- `weather.hold(**overrides)` pins conditions so a comparison is not fighting
  the weather evolving underneath it.
- `tests/test_narrator.py::all_pools()` collects every clause pool. A new
  corpus must be added there — it is the only thing standing between a
  placeholder typo and prose silently vanishing at runtime.

## Things deliberately not modelled

No takeoff — every flight begins airborne at 5,000 ft, and `on_ground` exists
only for the rollout after landing. No failures beyond engines. No multi-leg
route command, though `navigation.Route` fully supports one. The A321 is the
neo; there is no A321ceo.
