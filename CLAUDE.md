# Flight_Sim — notes for working on this codebase

A text-based Airbus flight simulator: a real point-mass physics model with a
procedural prose engine on top. Stdlib only, `unittest`, no dependencies.

```bash
python main.py                                  # play it
python -m unittest discover -s tests -t .       # 374 tests, ~60 s
python main.py --list                           # fleet and weather menus
python main.py --spec a350-1000                 # one type's card and drawing
```

## Layout

Dependencies flow one way. `atmosphere` knows nothing; `physics` is the hub;
`game` orchestrates. Nothing below imports `physics`.

```
main.py            CLI: interactive REPL, and a one-shot --command mode
flight_sim/
  atmosphere.py    ISA, density, TAS/IAS/Mach.       Imports nothing.
  aircraft.py      The nine-type fleet as frozen dataclasses.
  artwork.py       Side profiles generated from each type's dimensions.
  terrain.py       Ridged-fBm world, graded sites, approach surfaces.
  weather.py       Immutable profiles + a mutable WeatherState.
  airfield.py      Airfield geometry; procedural and authored sources.
  landing.py       Approach guidance, touchdown grading, rollout physics.
  navigation.py    Routes, leg guidance, the end-of-flight debrief.
  autopilot.py     ALT / V/S / HDG / SPD / APPR controllers.
  fbw.py           Normal / alternate / direct law and the protections.
  physics.py       FlightState, Readout, Simulator. The integrator.
  narrator.py      The 251-clause prose engine.
  dashboard.py     Markdown instrument panel, spec cards, the law card.
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

**Two kinds of number live in `aircraft.py` and must not be confused.**
*Published data* — dimensions, masses, seat counts, thrust ratings, tank volumes
— exists to be **shown**: the spec card and the artwork are drawn from it, so
changing it changes what the pilot sees. *Calibrated coefficients* — `tsfc`,
`cd_0`, `oswald_e`, `mach_crit` — exist to be **flown**, and were solved rather
than looked up. Editing one as though it were the other is the mistake to avoid.

`fuel_capacity_kg` is derived from `fuel_capacity_l` at Jet A-1 density rather
than declared. Tanks are certified by volume; quoting both independently lets
them drift apart.

## Where the calibrated constants come from

This is the part that is invisible in the code and easy to break.

`tsfc`, `cd_0` and `oswald_e` in `aircraft.py` were **solved**, not looked up.
For each type: trim at its published cruise altitude and Mach, then find the
TSFC that reproduces its published block fuel flow. The drag polar was tuned
first so that cruise L/D lands in the real 17–19 band, because an L/D that is
wrong makes the required TSFC absorb the error.

The result is cross-checked against reality: multiply TSFC by 35,306 for
lb/(lbf·hr) and the CFM56 comes out at 0.586, the LEAP at 0.505, the Trent 7000
at 0.501, the Trent XWB at 0.434 and the Trent 970 at 0.427 — all within the
published range for those engines. That agreement is the evidence the numbers
are physical rather than fudge factors, and
`tests/test_physics.py::test_tsfc_matches_the_real_engine` asserts it: if a drag
polar is wrong, the solved TSFC drifts off its engine and the test says so,
rather than the error hiding in the fuel page.

**Therefore: changing a drag polar without re-solving the TSFC makes the fuel
page quietly lie.** `tests/test_physics.py::CRUISE_TARGETS` holds every type to
5% of its published flow, which is the other guard.

Cruise fuel flow is strongly weight-dependent, so `CRUISE_TARGETS` records the
**mass each figure belongs to** and a test asserts the model still trims there.
A target quoted without a weight cannot be verified: the same A321neo burns
2,300 kg/h at 85 tonnes and under 2,000 late in a flight.

Thrust lapse is three effects (density, steeper above the tropopause; ram drag
with Mach; a fade across the certified ceiling). Without the last one an A320
climbs to 50,000 ft.

## The artwork is generated, and that is load-bearing

`artwork.py` draws nothing by hand. Fuselage length is `length_m`, the fin
reaches `height_m`, the cabin has `cabin_decks` window rows, the pods come from
`engine_arms_m`, and the gear bogie count follows `mtow_kg`. Scale is fixed
across the fleet, so the pictures are comparable.

The consequence to respect: **if two types look identical, they are identical.**
The A320 and A320neo draw the same picture because they differ only in span, and
a side view cannot show span — `tests/test_artwork.py` asserts that too, so the
drawing cannot invent a difference it has no way of seeing. Distinguishing data
that a profile cannot carry belongs in the caption or the spec card.

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

**A limiter that overwrites the command it is limiting.** Commanded attitude is
*persistent state*: `cmd_pitch_deg` survives until the pilot changes it. So a
limit applied by writing back to it is permanent — the command is gone, not
merely opposed. This silently converted alternate law's resistible nose-down
demand into a hard ceiling and made the aircraft unstallable in the one law
where it must be stallable. `fbw.apply` therefore computes
`sim.law_pitch_target` and never touches the pilot's command.

**The same limit in the parser and in the model.** `commands.py` clamped bank
to ±60°, which predated the control laws and made normal law's 67° protection
unreachable — a protection that cannot be reached cannot be felt or tested. The
parser now applies only the structural bound and the law narrows it. Before
adding a clamp in `commands.py`, check whether the thing being clamped already
has an owner.

## Testing patterns

- One test file per module, named for it. 374 tests, ~60 s.
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

## The control laws

`fbw.py` sits between whoever is flying — pilot or autopilot — and the
aerodynamics, and only ever *narrows* the commanded attitude. Nothing in it
bypasses the flight model: alpha protection works by refusing to command a pitch
attitude that would put the wing past alpha max.

Normal law makes the aircraft unstallable, so **a test that needs a stall must
select `direct law` first** — three in `tests/test_physics.py` do, with comments
saying why.

The one distinction to keep straight: *load factor limiting* survives into
alternate law, because it is part of the basic pitch law; *angle-of-attack
protection* does not, and what replaces it is a soft demand the pilot can hold
the stick against. `alpha_for_load_factor` therefore does **not** clamp at
CL_max — it limits the g demanded, not the g the wing can make. Clamping it made
it a second, accidental AoA protection, silently active in the two laws that are
supposed to have none.

## Things deliberately not modelled

No takeoff — every flight begins airborne at 5,000 ft, and `on_ground` exists
only for the rollout after landing. No failures beyond engines. No multi-leg
route command, though `navigation.Route` fully supports one. The A321 is the
neo; there is no A321ceo.

**No LNAV.** `direct to KEBR` sets a destination and the panel shows bearing,
ETA and fuel on arrival, but the autopilot holds a *heading* — it will not fly
the route unless you also command a heading toward it. Easy to mistake for a
bug when a scripted flight sails past its destination.

Only the two control-law reversions a point-mass model can honestly represent
are implemented: all engines out, and gear down in alternate law. Air data and
inertial reference failures have no analogue here, which is why the law can also
be selected by hand.
