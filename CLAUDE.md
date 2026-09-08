# Flight_Sim — notes for working on this codebase

An Airbus flight simulator in two front ends over one physics model: a text
simulator with a procedural prose engine, and a WebGL cockpit in the browser.
Python is stdlib only, `unittest`, no dependencies.

```bash
python main.py                                  # play it
python -m unittest discover -s tests -t .       # 499 tests, ~100 s
python main.py --list                           # fleet and weather menus
python main.py --spec a350-1000                 # one type's card and drawing
open web/anfell.html                            # the browser build
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
  landing.py       Approach guidance, touchdown grading, ground forces.
  navigation.py    Routes, leg guidance, the end-of-flight debrief.
  autopilot.py     ALT / V/S / HDG / SPD / NAV / APPR, and the FMA.
  fbw.py           Normal / alternate / direct law and the protections.
  engines.py       N1 as state, the spool, and the E/WD's numbers.
  failures.py      Seven failures, the V1 cut, and the ECAM lines.
  physics.py       FlightState, Readout, Simulator. The integrator.
  narrator.py      The 251-clause prose engine.
  dashboard.py     Markdown instrument panel, spec cards, the law card.
  mapview.py       Track-up ASCII terrain plan view.
  commands.py      Natural-language command parsing.
  game.py          Session: setup, the loop, persistence.
web/
  anfell.html      The whole browser build. One file, no build step.
  tools/           Playwright checks: cruise flow, model parity, screenshots.
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

**Two write paths to one piece of state.** `commands.apply` appended to
`engines_failed` itself instead of calling `failures.trigger`, so only one of
the two routes kept the invariants. Four bugs came out of that single fact, and
the worst made the two front ends contradict each other on screen: `restart
engines` cleared the failed list and not `engines_on_fire`, so the ECAM fell
silent while `engines.readouts` went on reporting a fire on a running engine and
the E/WD painted it red. **If a module owns a piece of state, everything writes
through it** — `failures.trigger`, `failures.clear` and
`failures.restore_engines` are that owner now.

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

- One test file per module, named for it. 499 tests, ~100 s.
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

## Numbers a display shows have exactly one owner

A speed mark, a mode name and the moment a mode changed are all **model data**,
not display data — because there are two front ends and they must not be able to
disagree. `fbw.characteristic_speeds` owns the whole speed tape (VLS, the alpha
marks, green dot, Vref, Vmax) and `landing.vref_kt` calls it rather than keeping
its own factor; `autopilot.fma` owns the five annunciator columns and
`autopilot.note_mode_changes`, called once a tick from `Simulator.readout`, owns
when each last changed. A display picks fonts and colours. It does not work out
*where a mark goes*.

The bug this prevents is the one that was already latent: Vref was computed in
`landing` with a `1.3` factor, and a speed tape computing its own VLS would have
put a different number on the PFD from the one grading the landing.

Two conventions live side by side there and are not a mistake: VLS is
1.23 · Vs1g, the modern certification number, and Vref is 1.3 · Vs1g, the older
one the touchdown grader has always used. Vref therefore sits a little *above*
VLS, which is the right way round — VLS is a floor and Vref is a target.

`engines.readouts` and `failures.ecam` join them: N1, N2, EGT and fuel flow per
engine, and every warning line with its colour. The E/WD picks the font.

## N1 is state; N2 and EGT are not

The one rule in `engines.py` that everything else follows from. **Thrust is
derived from the fan speed, and the fan speed chases the levers** — slower up
(3.2 s) than down (1.6 s), which is what a turbofan does and what makes a late
go-around a commitment rather than a keystroke. Before this, moving a lever
rewrote the thrust in the same substep.

What makes the change safe is the constraint it was built under: **at
equilibrium the fan has caught up with the levers, so steady-state thrust is
unchanged** and the nine calibrated cruise figures are exactly as they were.
Only the transient moved. A standing start with the levers slammed forward now
costs an A320neo about 150 ft more runway, 3% — which is roughly right, and is
also why a real crew stands the engines up before releasing the brakes.

The consequence to remember: **anywhere `throttle_pct` is assigned from outside
the integrator, `Simulator.settle_engines()` belongs immediately after it** —
the trim solvers, a state placed rather than flown into, a test setting up a
condition. Without it the aeroplane is at cruise thrust levers with idle fans.
This was found the honest way: cruise fuel flow fell 65% the moment thrust
started following N1, because `trimmed_at_cruise` never told the engines.

N2 and EGT are **derived, and shown, and nothing else**. EGT in particular is a
plausible function of fan speed and the air going in; it is not published data
for any of these types, and if it ever feeds a force it has become a fudge
factor with a temperature's name on it.

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

## The ground roll is one function, read in two directions

`Simulator._ground_substep` does both the takeoff roll and the landing rollout,
because it is the same physics either way: thrust against friction and drag,
with the wing taking more of the weight the faster it goes. Which one it is
depends on `touchdown` — a roll that has not landed from anywhere is a
departure. Splitting them into two functions would mean two copies of the
friction model, and they would drift.

Friction acts on the weight the **wheels** are carrying, not the aeroplane's.
At touchdown the wing is still doing most of the work and the brakes have almost
nothing to bite on; the load transfers as speed decays and lift falls with its
square. That is why you cannot stop a fast aeroplane on the brakes alone.

Which makes the spoilers load-bearing, and they work the way the real ones do:
`GROUND_SPOILER_LIFT_FACTOR` **destroys the lift**, and the weight that lands on
the wheels is what lets the brakes work. It is gated on `on_ground`, so in the
air the same panels are a speedbrake and only cost drag. An earlier version
multiplied the friction by 1.25 instead, which asserted the effect rather than
explaining it. Forgetting the spoilers now costs about fifteen hundred feet of
runway, which is roughly right.

### On the ground, `tas_ms` is the signed along-runway *airspeed*

Not the groundspeed. Every force reads airspeed, so keeping it in that currency
leaves the whole force model untouched by the wind; the groundspeed the wheels
are doing is `tas_ms - headwind`, and it is that which moves the aeroplane.
Standing still in a twenty-knot headwind the airspeed indicator reads twenty and
the aeroplane does not move, which is what the real one does and is exactly why
the roll is shorter. Parked downwind `tas_ms` goes *negative* — a pitot tube in
reversed flow, which `readout()` floors at zero, as a real one does.

Two consequences that are easy to get wrong, and each has a test:

- **The integration floor is the headwind, not zero.** It says the wheels cannot
  turn backwards. Flooring the airspeed instead lets a parked aeroplane in a
  tailwind taxi itself downwind at twenty knots with the brakes set.
- **`STOPPED_KT` is a groundspeed.** In a twenty-knot tailwind an airspeed of
  twenty-four knots is forty-four over the ground, with the far end arriving.

A twenty-knot surface headwind takes an A320neo's roll from 5,600 ft to 4,467,
and a twenty-knot tailwind stretches it to 6,859. Both track `((v_lo ∓ w)/v_lo)²`
a couple of points shy, which is explainable rather than error: the engines
spool as a function of *time*, so the wind does not shorten the thrust-limited
first seconds proportionally.

### The lateral wind is not added to the aeroplane's motion

On wheels the side force goes into the tyres — an airliner does not slide
sideways down a runway at forty-five knots — so adding a lateral term would
assert a drift rather than explain one, which is the `×1.25` mistake again. What
a crosswind does instead is two things that were already nearly here:

1. It **weathervanes** the nose, which the code's own comment promised and
   nothing implemented. Where it settles comes from `directional_stability /
   rudder_power`, per type and already calibrated, so only the *rate*
   (`WEATHERVANE_RATE_DEG_S`) is invented.
2. The aeroplane travels along its **heading**, not along `roll_direction_deg`.
   That stays the runway's frame, which is what the centreline offset and the
   overrun test are measured in. Before this, steering did nothing: full rudder
   held through a takeoff roll swung the heading a hundred and seventy degrees
   off the runway and left the aircraft exactly on the centreline, accelerating.

The drift then falls out rather than being modelled. A 38 kt crosswind — the
A320's demonstrated figure — is holdable on 18° of the 30° of rudder available,
and costs about 3% of the roll; left alone the aeroplane weathervanes off the
side, which is why the pedals are not optional.

`_touch_down` straightens the aeroplane on contact, because the main gear does.
The crab is graded first and is therefore a verdict, not a state that survives
the wheels — without that a legal eight-degree crab would drive the aircraft off
a runway in about two seconds now that a heading moves it.

## `web/` is a port of this model, not a second one

`web/anfell.html` carries the atmosphere, the drag polar, the thrust lapse, the
TSFC figures, the control laws and the terrain function again, in JavaScript,
because a browser cannot import Python. They are the *same numbers*. An A350-900
trimmed at FL370 and M0.85 at 252.4 t burns 5,793 kg/h in both, and a seed grows
the same mountains in both, down to the 32-bit lattice hash.

**Change one and you must change the other**, or the two quietly diverge and the
figures in `tests/test_physics.py::CRUISE_TARGETS` only catch it on one side.
`web/tools/cruise_check.js` holds the browser build to those same targets, which
is the guard; run it after touching `aircraft.py` or `physics.py`.

`web/tools/parity_check.js` and `.py` are the other guard, and they cover what
the glass cockpit puts on the glass: the speed marks, the V-speeds, the five
Flight Mode Annunciator columns, the per-engine N1/N2/EGT/fuel flow with its
band, and every ECAM line with its colour, across a hundred states and four
types. That is the easier half to get wrong — a speed tape with its marks in the
wrong place still looks exactly like a speed tape, and an E/WD announcing the
failure of the engine that is still running still looks exactly like an E/WD.

**Some of those states are transitions, not resting states**, and they are there
because a resting state cannot catch an un-setting. A fire that survived a
restart left the ECAM silent while `engines.readouts` went on reporting it, so
the E/WD painted FIRE over a running engine — through sixty-eight states that
only ever broke the aeroplane and never repaired it. Four of the EGT states sit
one degree either side of a threshold, for the same reason: a sample
comfortably inside a band compares two builds that happen to agree.

Four of those states are broken on purpose, and they carry a fixed number of
spool substeps so N1 is compared **mid-decay**. A fan at idle or at the takeoff
rating agrees in both builds whatever the two time constants are; a fan that is
one second into running down does not.

The two builds are also flown against each other rather than only sampled: the
same world (seed 20260905 grows the same ANFL, to every printed digit), the same
A320neo at 71.19 t, levers to 100%. In still air that is 5,600 ft of ground roll
in the Python and 5,620 in the browser, and the agreement holds across the wind
— head, tail and cross, from 5 to 30 knots — to **0.38%**, with the residue
explained by the two scripts' control cadences.

The weather itself agrees to 4.8e-4 across four profiles, four elapsed times,
five heights and a hundred and twenty points of terrain, and the turbulence
agrees to one ULP because both builds draw it from the same lattice hash.

Only rendering exists solely in the browser now. Nothing in `flight_sim/` may
import from or depend on `web/`.

Two places the picture and the physics deliberately disagree, both rendering
only and both bounded: sub-kilometre relief is *carved down* into the height
texture by at most 165 ft, because the terrain function's finest octave is
4,560 ft long and the mesh was finer than the function it sampled; and lakes are
filled to a level surface up to 900 ft above their bed. `natural_elevation` —
which the airfield search reads — is untouched by both.

## Things deliberately not modelled

No multi-leg route command, though `navigation.Route` fully supports one. The
A321 is the neo; there is no A321ceo.

Only the two control-law reversions a point-mass model can honestly represent
are implemented: all engines out, and gear down in alternate law. Air data and
inertial reference failures have no analogue here, which is why the law can also
be selected by hand.

`failures.py` draws the same line, and its docstring says so: electrical,
pressurisation, air data and inertial reference are absent because **every
failure that is present changes a number the flight model already reads**. A
failure whose only consequence is a message about itself is not a failure, it is
a label. The ECAM has the E/WD but no system synoptic pages (ENG, FUEL, F/CTL,
WHEEL) — those would be drawings of systems that do not exist.
