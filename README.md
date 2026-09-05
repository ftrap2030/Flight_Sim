# Flight_Sim

A text-based Airbus flight simulator with a real point-mass physics model and a
cinematic description engine. Pick an aircraft, pick your weather, and you start
at 5,000 feet in straight and level flight. Everything after that is up to you.

No dependencies — Python 3.8+ and the standard library.

```
python main.py
```

## The fleet

Five Airbus airliners. The differences between them are *emergent*: the A320neo
climbs better and burns less because its sharklets raise the aspect ratio and
its LEAP engines have a lower TSFC, not because a "nimbleness" number was typed
into a table.

| | A320 | A320neo | A321 | A350-900 | A380-800 |
|---|---|---|---|---|---|
| Engines | 2× CFM56-5B4 | 2× LEAP-1A26 | 2× CFM56-5B3 | 2× Trent XWB-84 | 4× Trent 970 |
| MTOW | 78 t | 79 t | 93.5 t | 280 t | 575 t |
| Wing / aspect ratio | 122.6 m² / 9.5 | 122.6 m² / **10.5** | 122.6 m² / 9.5 | 442 m² / 9.5 | 845 m² / 7.5 |
| Cruise | M0.78 | M0.78 | M0.78 | M0.85 | M0.85 |
| Ceiling | FL390 | FL390 | FL390 | FL431 | FL431 |
| Roll rate | 15°/s | 15°/s | 12°/s | 10°/s | 7°/s |

The A321 is modelled as the *ceo* (CFM56-5B3), to pair with the A320 ceo listed
alongside the neo.

## Weather

**Clear Skies**, **Heavy Crosswinds**, **Stormy**, **Foggy**. Each one feeds real
numbers into the simulation rather than only into the prose: turbulence amplitude
perturbs attitude every substep, the wind vector displaces your ground track (so
the terrain beneath you diverges from your heading, and you fly the whole leg
crabbed), and visibility decides whether you can see the mountain coming.

## Flying it

Commands are typed the way a pilot would say them:

| Intent | Examples |
| --- | --- |
| Throttle | `increase throttle 10%`, `throttle 85`, `full power`, `idle`, `climb power` |
| Pitch | `pitch nose down 5 degrees`, `pitch up 3`, `set pitch 10`, `level off` |
| Turning | `turn left heading 180`, `heading 090`, `bank right 25`, `roll level` |
| Rudder | `rudder left 10`, `full right rudder`, `centre rudder` |
| Engines | `engine failure`, `shutdown engine 2`, `restart engines` |
| Configuration | `flaps 2`, `flaps full`, `gear down`, `speedbrakes out` |
| Time | `hold`, `wait 60 seconds`, `wait 2 minutes` |
| Other | `map`, `status`, `help`, `quit` |

Each command advances the simulation ten seconds. `status` and `help` cost no
time, and an unrecognised command costs no time either — you get a hint instead.

## What the model actually does

**Atmosphere** — the ISA troposphere and the isothermal stratosphere above
36,089 ft, giving density, speed of sound, and the IAS/TAS/Mach relationships.
Densities agree with the published ISA tables to four decimal places.

**Forces** — a point-mass aircraft with a finite-wing lift curve
(`CL = CL₀ + CLα·α`, capped at `CL_max`), a drag polar
`CD = CD₀ + CL²/(π·AR·e)` plus a wave-drag term above the critical Mach, and a
thrust model that lapses with density, faster above the tropopause, falls off
with forward speed as ram drag rises, and fades out across the certified
ceiling. Fuel burns, mass drops, and the aircraft climbs better as it lightens.

Cruise fuel flow for every type lands within 3% of its published block figure,
at an L/D between 17 and 19, on TSFC values that correspond to the real engines
(0.59 lb/lbf/hr for the CFM56, 0.51 for the LEAP).

**Integration** — one command advances ten seconds, integrated semi-implicitly
at 0.1 s substeps. The substepping matters: turn rate and flight path angle are
coupled through airspeed, and a single ten-second Euler step on that coupling
diverges badly at low speed.

**Three axes, and the third one is real.** Sideslip is genuine state, not a
derived wind angle. The rudder yaws the aircraft against weathercock stability;
sideslip costs drag and rolls you through dihedral effect, which is why rudder
and roll go the same way and why a rudder-only turn works. Rudder travel is
limited with airspeed, as on the real aircraft -- without it, full rudder at
cruise produces a fin-detaching slip.

Lose an engine and the thrust asymmetry yaws you toward the dead one, from the
engine's real lateral offset: 5.75 m on the A320 family, 10.6 m on the A350,
21.6 m for an A380 outer. **Vmc is not a coded number** -- the thrust moment is
normalised by dynamic pressure, so as you slow down the same dead engine demands
ever more rudder until the available travel simply runs out. For the A320neo
that crossing lands near 110 kt, against a real Vmca of about 115. The A380 is
the most controllable of the five on an engine failure, because losing one of
four is half the asymmetry of losing one of two, against far more fin and wing.

**The envelope is real.** The wing stalls past its critical angle of attack and
lift collapses progressively, which is what makes a stall self-reinforcing —
less lift, steeper descent, higher alpha still. A departed wing stops obeying
the sidestick and drops its nose, so a stall is recoverable if you have the
height and the discipline to unload it. You can also overspeed past Vmo,
overstress the airframe, and run the tanks dry, at which point you are flying a
very large glider.

**The terrain map** — a track-up ASCII plan view (`map`) drawing the ground
*relative to your own altitude*, because at low level the only question is what
you can hit. Ground far below and ground about to kill you should not look alike.

**Terrain** — ridged multifractal noise: `1 - |value noise|` folded at the
midline and squared, four octaves, modulated by a slow massif field so the world
has plains, foothills and high country rather than uniform corrugation. Peaks
reach ~9,600 ft, which is squarely in the path of an airliner at 5,000 ft. The
opening position is chosen to give you room — the mountains are something you
fly *toward*.

Ridges are named from a hash of their lattice cell, so the same peak keeps the
same name across the whole flight.

## The description engine

Prose is composed, not selected. Each altitude band and weather profile owns
separate corpora for sky, terrain, motion, sensory and threat clauses; one is
weighted-sampled from each, live instrument values are spliced in, and a
16-entry deque suppresses recently used clauses so a long flight does not loop.

Altitude bands select on height above *ground*, which is what actually decides
whether you are looking at a horizon or at rock:

- **above 2,000 ft AGL** — the panoramic high-altitude vista
- **1,000–2,000 ft** — the landscape resolves; ridges and valleys rushing beneath
- **below 1,000 ft** — imminent terrain, and the GPWS

## Scripting and resuming

The simulator can be driven one command per invocation, with state persisted to
JSON in between:

```bash
python main.py --state-file flight.json --new --aircraft a350 --weather stormy
python main.py --state-file flight.json --command "turn left heading 180"
python main.py --state-file flight.json --command "descend 8" --json
```

Seeded determinism means a flight replays exactly, and a session resumed from
disk continues identically to one flown straight through — turbulence included.

Other flags: `--seed` picks the world, `--altitude` the starting height,
`--list` prints the menus, `--json` dumps the raw readout to stderr.

## Tests

```bash
python -m unittest discover -s tests -t .
```

149 tests, no dependencies. They check the atmosphere against published ISA
tables, stall speed against its closed form, cruise fuel flow and service
ceiling against published figures for all five aircraft, terrain determinism,
save/load fidelity, that every prose template renders against a live context,
that the artificial horizon is not upside down, and that Vmc falls out of the
engine geometry rather than being asserted.

CI runs them on Python 3.9, 3.11 and 3.12.
