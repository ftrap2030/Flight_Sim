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
| On the ground | `max brakes`, `release brakes`, `reverse thrust`, `stow reversers` |
| Time | `hold`, `wait 60 seconds`, `wait 2 minutes` |
| Navigation | `direct to KEBR`, `show plan`, `clear route`, `airfields`, `debrief` |
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

## Somewhere to go

`direct to KEBR` sets a destination and the panel gains a navigation strip:
distance, bearing, how far off the nose it sits, ETA, and — the number that
turns a route into a decision — **fuel on arrival**, computed from the live burn
and the actual ground speed. Fly into a headwind or detour round a ridge and it
falls in front of you. Set a destination you cannot reach and it says so.

Every flight ends with a **debrief**: distance flown, fuel burned and average
burn, maximum altitude and speed, the closest you came to the ground, the
highest load factor you pulled, your touchdown numbers if you got any, and every
warning you triggered along the way. Some of that cannot be reconstructed
afterwards — how near the ground you came is only knowable while it is
happening — so it is gathered tick by tick as you fly.

## Landing

A flight can now end well. Ground contact asks two questions rather than one:
where did you touch down, and how.

Off an airfield it is a crash, as it always was. On the runway you are graded
the way a real arrival is graded — **sink rate first**, then speed against Vref,
then how straight and how centred:

| Sink rate | Verdict |
|---|---|
| under 60 fpm | greaser |
| under 240 fpm | normal landing |
| under 600 fpm | firm landing |
| under 900 fpm | hard landing — and a report to write |
| 900 fpm or more | the gear does not survive it |

More than 8° of bank puts a wingtip in first; more than 20° of crab drags the
gear sideways until it folds. Touch down just off the paving and it is a runway
excursion — survivable if it was gentle, which is not the same thing as a crash
into a mountain, and does not read like one.

**The flare is the skill.** A 3° glidepath at 150 knots is about 800 feet a
minute, so flying the glideslope all the way to the concrete earns a hard
landing every time. Flare too high or too hard and the aircraft balloons, bleeds
forty knots and drops in worse than if you had done nothing:

| Technique | Result |
|---|---|
| No flare | 826 fpm — hard |
| Flare at 30 ft, +1.5° | **181 fpm — normal** |
| Flare at 50 ft, +2.5° | 538 fpm — firm |
| Flare at 60 ft, +3.0° | 677 fpm — hard |

Then the rollout: wheel braking, spoilers and reverse thrust against the far
end of the runway. Run out of concrete and it is an overrun.

**Approaches are protected.** Every runway end carries an obstacle limitation
surface — a corridor that cuts high ground down to below the glidepath, the same
thing a real airport has. Without one, half the fields in a mountain range would
have a ridge sitting in short final and could not be used at all. It only ever
cuts terrain down, never fills a valley, so the landscape keeps its character.

## Airfields

Five hand-designed fields in the authored home region, **the Anfell Valleys**,
where every flight begins: a long instrument-equipped international, a regional
strip, a short narrow visual-only field back in the hills, a high one where thin
air lengthens the roll, and one whose approach threads between two ridges.
Beyond the region, airfields are generated procedurally and infinitely.

Both kinds are placed by searching for genuinely flat ground and aligning the
runway with the flattest axis — which in mountainous country means it follows
the valley, the same answer a surveyor would give, with no explicit notion of a
valley anywhere in the code. Runways are then graded flat, because real airports
are bulldozed and raw ridged noise puts a two-hundred-foot hump in a runway.

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

254 tests, no dependencies. They check the atmosphere against published ISA
tables, stall speed against its closed form, cruise fuel flow and service
ceiling against published figures for all five aircraft, terrain determinism,
save/load fidelity, that every prose template renders against a live context,
that the artificial horizon is not upside down, that Vmc falls out of the engine
geometry rather than being asserted, that every authored runway has a clear
3-degree approach from both ends, and that a stopped aircraft reads zero on the
airspeed indicator.

CI runs them on Python 3.9, 3.11 and 3.12.
