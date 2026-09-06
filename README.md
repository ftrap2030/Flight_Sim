# Flight_Sim

A text-based Airbus flight simulator with a real point-mass physics model and a
cinematic description engine. Pick an aircraft, pick your weather, and you start
at 5,000 feet in straight and level flight. Everything after that is up to you.

No dependencies — Python 3.9+ and the standard library.

```
python main.py
```

## The fleet

Nine Airbus airliners. The differences between them are *emergent*: the A320neo
climbs better and burns less because its sharklets raise the aspect ratio and
its LEAP engines have a lower TSFC, not because a "nimbleness" number was typed
into a table.

| # | Aircraft | Type | Engines | MTOW | Wing / AR | Cruise | Ceiling | Range | Seats | Roll |
|---|---|---|---|---:|---|---|---|---:|---:|---:|
| 1 | A319neo | A19N | 2× LEAP-1A24 | 76 t | 122.6 m² / 10.5 | M0.78 | FL398 | 3,750 nm | 140 | 16°/s |
| 2 | A320-200 | A320 | 2× CFM56-5B4 | 78 t | 122.6 m² / 9.5 | M0.78 | FL390 | 3,300 nm | 150 | 15°/s |
| 3 | A320neo | A20N | 2× LEAP-1A26 | 79 t | 122.6 m² / **10.5** | M0.78 | FL398 | 3,500 nm | 165 | 15°/s |
| 4 | A321neo | A21N | 2× LEAP-1A32 | 97 t | 122.6 m² / 10.5 | M0.78 | FL398 | 4,000 nm | 180 | 12°/s |
| 5 | A321XLR | A21N | 2× LEAP-1A35 | 101 t | 122.6 m² / 10.5 | M0.78 | FL398 | 4,700 nm | 180 | 11.5°/s |
| 6 | A330-900neo | A339 | 2× Trent 7000 | 251 t | 361.6 m² / **11.3** | M0.82 | FL414 | 7,200 nm | 287 | 10.2°/s |
| 7 | A350-900 | A359 | 2× Trent XWB-84 | 280 t | 442 m² / 9.5 | M0.85 | FL431 | 8,300 nm | 315 | 10°/s |
| 8 | A350-1000 | A35K | 2× Trent XWB-97 | 319 t | 464.3 m² / 9.0 | M0.85 | FL431 | 8,700 nm | 350 | 9°/s |
| 9 | A380-800 | A388 | 4× Trent 970 | 575 t | 845 m² / 7.5 | M0.85 | FL431 | 8,000 nm | 525 | 7°/s |

`python main.py --spec a350-1000` prints any type's full card. In flight, `spec`
does the same for the aircraft you are in and `fleet` shows all nine.

The whole A320 family shares a wing *area*; only the A320 ceo lacks sharklets,
which is why it alone has the lower aspect ratio and pays for it in induced drag.
The A321neo is nineteen tonnes heavier on that same area, so it stalls sixteen
knots faster than the A320 and rolls three degrees a second slower — the engines
did nothing for its inertia. The A330-900 has the highest aspect ratio in the
fleet at 11.3, a long slender wing on a comparatively small area, and at medium
weights very little else holds an altitude so effortlessly.

### They are drawn from their own dimensions

Nothing in the artwork is hand-drawn. The fuselage is as long as `length_m`, the
fin reaches `height_m`, the cabin has `cabin_decks` rows of windows, the pods are
counted from `engine_arms_m`, and the number of main gear bogies follows MTOW
because pavement loading is what decides it on the real aeroplane.

```
                                                                         /____
                                                                        /    |
                                                                       /     |
                                                                      /      |
                                                                     /       |
                                                                    /        |
          /__________________________________________________________________|__
       /__ \  o o o o o o o o o o o o o o o o o o o o o o o o o o o  ----------
     /_                                                                   /_
    /         o o o o o o o o o o o o o o o o o o o o o o o o o o o     /_
   _                                                                  /_
   ___________________________________________________________________
                |              (######)  (######)  |    |    |
                o                                 ooo  ooo  ooo
----------------------------------------------------------------------------------
   |<-------------------------------- 72.72 m --------------------------------->|
   span 79.75 m   ·   height 24.09 m   ·   wing 845.0 m²   ·   aspect ratio 7.5
```

Scale is fixed across the fleet, so the pictures are comparable: an A380 is drawn
very nearly twice the length of an A319neo because it very nearly is, and a test
asserts the drawn ratio matches the real one to within 8%.

That constraint is the point. A hand-drawn fleet would let the A321's 6.9 m
stretch go unnoticed; this cannot. It also means the A320 and A320neo draw the
*same* picture — they differ only in span, and a side view has no way to show
span. Their captions differ, because those quote it. Where a difference is
genuinely visible it is drawn: the A380's two decks and four engines in two pods,
and the A321XLR's belly fairing over its rear centre tank, which is the one
external feature that tells an XLR from an A321neo from directly abeam.

## Weather

**Clear Skies**, **Heavy Crosswinds**, **Stormy**, **Foggy** — and none of them
sits still. Wind veers and backs, visibility drifts and turbulence rises and
falls across a flight, seeded so the same flight replays identically while no
two "Stormy" days are alike. Each profile keeps its character: Stormy never
quietly becomes a calm afternoon.

Three things make the weather *local* rather than global:

**Wind shear.** Surface friction slows the wind and backs it by up to 30°, so
descending through the friction layer changes your drift and your groundspeed —
which matters most on an approach, exactly where you can least afford it.

**Rotor turbulence.** Wind pouring over a ridge breaks up in its lee. The
turbulence you feel is the weather's plus whatever the terrain adds, computed
from the ground gradient upwind, the wind strength and your height. In a 45-knot
wind, the lee of a crest reaches near-extreme turbulence while the same air over
flat ground is smooth. *This was a bug fix as much as a feature* — the crosswind
prose had been promising "rotor turbulence off the ridge" since the first
version, while turbulence was a per-weather constant that had no idea where the
terrain was.

**Mountain wave.** Air flowing over sloping ground has to go up the windward
face and down the lee one. Windward gives you a few hundred feet a minute of
free lift; the lee side takes it back and more, and in the mountains that is not
always survivable.

**Time of day** runs too — `time 0530`, `dawn`, `dusk`, `night`. The sun rises,
crosses and sets, and at night the horizon does not merely dim, it is *absent*,
leaving the instruments as the only attitude reference you have.

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
| Time of day | `time 0530`, `dawn`, `midday`, `dusk`, `night` |
| Autopilot | `autopilot on/off`, `set altitude 12000`, `set speed 280`, `vertical speed 1500`, `approach mode` |
| Navigation | `direct to KEBR`, `show plan`, `clear route`, `airfields`, `debrief` |
| Flight controls | `law`, `normal law`, `alternate law`, `direct law` |
| Reference | `spec`, `spec a380`, `fleet` |
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

Cruise fuel flow for every type lands within 2.2% of its published block figure,
at an L/D between 17 and 19.3, on TSFC values that were *solved* rather than
looked up — and which then come out on the real engines: 0.586 lb/(lbf·hr) for
the CFM56 against a published 0.59, 0.505 for the LEAP against 0.51, 0.501 for
the Trent 7000 against 0.50, 0.434 for the Trent XWB against 0.44, and 0.427 for
the Trent 970 against 0.43. That agreement is the evidence the numbers are
physical rather than fudge factors, and it is a test.

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
the most controllable of the nine on an engine failure, because losing one of
four is half the asymmetry of losing one of two, against far more fin and wing.

**The envelope is real.** The wing stalls past its critical angle of attack and
lift collapses progressively, which is what makes a stall self-reinforcing —
less lift, steeper descent, higher alpha still. A departed wing stops obeying
the sidestick and drops its nose, so a stall is recoverable if you have the
height and the discipline to unload it. You can also overspeed past Vmo,
overstress the airframe, and run the tanks dry, at which point you are flying a
very large glider.

## The flight control laws

An Airbus sidestick does not move a control surface. It asks for a load factor,
and a computer decides what the surfaces do about it — subject to protections
you cannot override by pulling harder. That is the most important single thing
about how these aircraft fly.

| | Normal | Alternate | Direct |
|---|---|---|---|
| Angle of attack | protected at α max | stability only | — |
| Load factor | +2.5 / −1.0 g | +2.5 / −1.0 g | — |
| Bank angle | 67° limit | — | — |
| Pitch attitude | +30° / −15° | — | — |
| High speed | protected at VMO/MMO | stability only | — |
| Alpha floor | TOGA above 100 ft AGL | — | — |

What that means in the aeroplane, measured, at full back stick and a commanded
80° of bank:

| | Normal | Alternate | Direct |
|---|---|---|---|
| Stall attempt | α held at 14.5°, **no stall** | **stalls** | **stalls** |
| 80° of bank | 67°, n capped at **2.50** | 80°, 2.55 g | 80°, 3.82 g, breaks up |
| Dive at full power | 382 kt, still flying | 415 kt, breaks up | 415 kt, breaks up |

**You cannot stall an aircraft in normal law.** That is not a simplification, it
is the protection working, and it is why the original brief's vivid stall ending
now needs a degraded law to reach — which is exactly the trade the real aircraft
makes.

Reversions are the two a point-mass model can honestly represent, and both
latch: losing every engine drops you to alternate law, because on RAT power that
is where a real Airbus goes, and lowering the gear in alternate law reverts to
direct. That second one catches people out — an aircraft that has been flying
acceptably suddenly has no protections at all, at the exact moment the pilot is
busiest. Everything else that degrades a real Airbus has no analogue here, so
`direct law` selects one deliberately.

The laws sit between whoever is flying — pilot or autopilot — and the
aerodynamics. Nothing bypasses the flight model: alpha protection works by
refusing to command a pitch attitude that would put the wing past alpha max,
which is what it does on the aeroplane.

Two subtleties that were bugs first. The law computes a *target* and never
overwrites the pilot's command, because in this game a command is persistent
state — take it away to enforce a limit and it stays away, which silently turned
alternate law's resistible nose-down demand into a hard limit and made the
aircraft unstallable in the one law where it must be stallable. And the load
factor law does not stop at CL_max: it limits the g *demanded*, not the g the
wing can make, so asking for 2.5 g where the wing has 1.2 commands the angle of
attack the arithmetic calls for. That is how an aircraft in alternate law gets
stalled by its own flight control computer, and clamping it would have created a
second, accidental AoA protection in the two laws that must have none.

`law` prints the card: thresholds, what is armed, and what is holding you back
right now.

## The autopilot

Hand-flying every ten seconds is right for a valley run and tedious for a cruise
leg. `set altitude 12000`, `set speed 280`, `heading 200`, `vertical speed 1500`
— and `approach mode` to fly the ILS.

These are controllers, not a bypass: each writes the same commanded pitch, bank
and throttle a pilot would, so the control law, the rate limits and the stall
behaviour are all unchanged. An autopilot that wrote straight to the state could
fly an aeroplane the pilot cannot.

It captures 12,000 ft to within a few feet, holds a commanded speed exactly, and
settles a turn onto a heading without oscillating — including through extreme
turbulence, where it holds altitude to a couple of hundred feet.

Channels engage independently, and **a manual input on a channel disengages it**:
touch the sidestick and vertical guidance drops while the autothrottle keeps
working. An autopilot silently fighting the pilot for the elevator is worse than
no autopilot at all.

`approach mode` flies the glideslope and localiser down to about 50 feet above
the runway, then hands the elevator back — on the centreline, at Vref, descending
— and retards the thrust levers at 28 feet, as a real autoland does. **The flare
is still yours.** That is the part worth flying by hand, and it is the part that
decides whether you get a greaser or a hard landing.

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

**251 clauses**, weighted toward where a pilot actually spends attention: the
critical band — below 1,000 feet, where the writing matters most — went from two
or three clauses per weather to seven or eight. Time of day adds dawn, golden,
dusk and night registers on top. Flying an approach or rolling out swaps the
terrain clause for its own register, because on final the outside world stops
being scenery and becomes a set of cues.

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
`--list` prints the menus, `--spec TYPE` prints one aircraft's card and drawing,
`--json` dumps the raw readout to stderr.

## Tests

```bash
python -m unittest discover -s tests -t .
```

374 tests, no dependencies. They check the atmosphere against published ISA
tables, stall speed against its closed form, cruise fuel flow and service
ceiling against published figures for all nine aircraft, terrain determinism,
save/load fidelity, that every prose template renders against a live context,
that the artificial horizon is not upside down, that Vmc falls out of the engine
geometry rather than being asserted, that every authored runway has a clear
3-degree approach from both ends, and that a stopped aircraft reads zero on the
airspeed indicator.

Two families are worth calling out because they guard things that are easy to
break silently. Every solved TSFC is checked against its real engine's published
cruise SFC — the constants were solved against block fuel flow, not looked up, so
a wrong drag polar shows up as a TSFC that has drifted off its engine rather than
as a quietly wrong fuel page. And the artwork is asserted against the data it is
drawn from: a longer aircraft must be drawn longer, the drawn A380/A319neo length
ratio must match the real one to within 8%, the pod count must equal the engine
count, and the length in the spec table must be the length in the callout under
the picture.

CI runs them on Python 3.9, 3.11 and 3.12.
