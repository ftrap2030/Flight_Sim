# The browser build

`anfell.html` is the whole thing: one file, no build step, no dependencies.
Open it and fly.

```bash
python3 -m http.server -d web 8000   # then http://localhost:8000/anfell.html
```

It also runs straight off the filesystem — `file:///…/web/anfell.html` — because
nothing in it is fetched.

## What it is

A WebGL2 renderer over the same flight model as the Python simulator, with a
takeoff, an ILS approach, a landing and synthesised sound.

* **Terrain** — a geometry clipmap: seven nested squares centred on the
  aircraft, each with twice the spacing of the one inside it, 29 ft between
  vertices where you are looking and 1,830 ft at the horizon. 714k triangles.
* **Height** — generated on the GPU into two RGBA8 textures, 24 bits of height
  packed across RGB. Coarse reaches 60 nm; fine follows the aircraft across 5 nm
  at 29.7 ft per texel and is re-baked every mile and a half.
* **Sound** — oscillators and filtered noise. No audio files. Radio-altitude
  callouts use the browser's own speech synthesiser.
* **Air** — the wind slows and backs near the ground, so a descent changes your
  drift; it breaks up in the lee of a ridge; and it goes up the windward face
  and down the other side, taking the aeroplane with it. Conditions drift over
  the flight. All of it from `weather.py`'s constants.

## The flight model is a *port*, not a second implementation

The atmosphere, the drag polar, the thrust lapse, the TSFC figures, the weather
and the control-law protections in this file are the same numbers as
`flight_sim/`. An A350-900 trimmed at FL370 and M0.85 at 252.4 t burns
5,793 kg/h here and there. The terrain is the same ridged multifractal over the
same 32-bit integer hash, which is why a seed produces the same mountains in
both — and the turbulence is drawn from that same hash, so the two builds fly
into the same gusts rather than merely into gusts of the same size.

**They must not drift.** `tools/cruise_check.js` holds this file to
`tests/test_physics.py::CRUISE_TARGETS`, which is the guard:

```bash
node web/tools/cruise_check.js "$PWD/web/anfell.html"
```

Every type must come back inside 5% of its published block fuel flow, at the
mass that figure belongs to — and a coverage guard fails the run if any type in
`FLEET` has no case at all, because a guard that is silent about what it is not
looking at is not a guard.

The BelugaXL has no published block fuel flow to be held to, so it gets a second
kind of case rather than an exemption: bisect for the altitude where the thrust
margin actually goes to zero, and compare that against the published service
ceiling. Both builds answer 35,575 ft against a published 35,000.

`tools/parity_check` guards the other half — what the glass cockpit puts on the
glass:

```bash
node web/tools/parity_check.js "$PWD/web/anfell.html" > /tmp/web.json
python web/tools/parity_check.py /tmp/web.json
```

A hundred and twenty-five states across five types — four airliners and the
BelugaXL, whose numbers are the least like anything else in the fleet: every
speed mark, every V-speed, all five Flight Mode Annunciator columns, the
per-engine N1/N2/EGT/fuel flow and every ECAM line with its colour must match
`flight_sim/`. This is the easier half to get wrong, because a speed tape with
its marks in the wrong place still looks exactly like a speed tape, and an E/WD
announcing the failure of the engine that is still running still looks exactly
like an E/WD.

It also compares **ten flight plans** and **five debriefs**, which are worse
still: a block fuel figure that is 6% out looks exactly like a block fuel
figure and there is nothing on the screen to check it against. Every phase,
every leg, the reserve, the block total and `enough` — the one a build could get
backwards and tell a pilot they can make it. Two of the routes start at cruise
level on purpose, because every other case files a level low enough that the
climb and descent fill the distance, and two miles of cruise cannot tell a mass
model from a constant; a Python-side assertion fails the run if none of them
genuinely cruises.

The debrief rows are compared on the rendered *string* as well as the numbers,
which is how the guard found that Python rounds halves to even and JavaScript
rounds them away from zero — a touchdown at 140.5 knots printing 140 in one
build and 141 in the other.

It covers the weather too -- the evolved conditions, the wind through the
friction layer, the rotor and the mountain wave over a hundred and twenty points
of terrain, and the gusts, which agree exactly because both builds draw them
from the same lattice hash.

`tools/shots.js` puts the aircraft at fixed places in the world and photographs
them, which is how the renderer is checked:

```bash
node web/tools/shots.js "$PWD/web/anfell.html" /tmp/shots
```

Both need Playwright and a Chromium; both take the paths as arguments because
where those live is a property of the machine, not of the simulator. **Both also
run in CI on every push** -- until they did, they ran when somebody remembered,
which is how the weather came to differ on all four profiles and an EGT
threshold came to be owned by the model and read only by the display.

## What the browser build has that the Python does not

**Anything you can see**: terrain, weather, the aircraft itself, time of day,
and the glass cockpit. The takeoff used to be on this list; `flight_sim/` has
one now, and `tools/parity_check` holds the two to the same V-speeds.

## What the Python has that this does not

The narrator, the route and debrief machinery, saving and loading, and the
command parser. This is a cockpit; that is a flight.

## Two places the picture and the physics deliberately disagree

Both are in the rendering only, and neither touches `naturalElevation`, which is
shared with the airfield search and with the Python.

* **Sub-kilometre relief is carved into the fine height texture**, because the
  terrain function's finest octave has a wavelength of 4,560 ft and 29 ft
  triangles were resolving velvet. It only ever cuts *downwards*, by at most
  165 ft, so no hill can appear that the aeroplane would not collide with — but
  off an airfield's graded ground the rendered surface can sit up to 165 ft
  below what the radio altimeter is reading. On a graded site the carve is
  flattened away entirely, so the runway is exactly as level as the touchdown
  model believes.
* **Lakes are filled to a level surface**, up to 900 ft above their bed. This is
  the one place the rendered ground sits *above* the collision ground, which is
  the right way round: an aircraft that meets a lake goes through the surface and
  hits the bottom. Over water the radio altimeter reads height above the bed, not
  above the water.
