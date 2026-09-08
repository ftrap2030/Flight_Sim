"""Things going wrong, and the ECAM that tells you about them.

The aeroplane could only ever succeed. The *physics* of an engine failure has
been here since the yaw axis was written -- asymmetric thrust, the moment from
the live engine's arm, Vmca, the rudder travel limiter -- but nothing else could
break, and there was no display that would have told you if it had.

**What is here is only what a point-mass model can honestly represent.** That is
the same line `fbw.py` already draws when it says the two modelled control-law
reversions are the only two an aeroplane without systems can claim. Every
failure below changes a number the flight model already reads, so the aircraft
flies differently because something is different, not because a failure flag was
consulted:

* an engine that stops still makes the yaw moment it always did,
* a jammed flap moves VLS and Vref, so the approach must be flown faster,
* a fuel leak drains the tanks the range calculation was reading,
* lost hydraulics slow the control rates the integrator steps the attitude with.

Deliberately absent: electrical, pressurisation, air data, inertial reference.
None of them has an analogue in a point-mass model, and inventing one would mean
a failure whose only consequence is a message about itself.

## The ECAM

`ecam(sim)` turns whatever is wrong into the lines an Airbus puts on its
Engine/Warning Display -- the title, and beneath it what to do about it. Both
front ends read it, so the text simulator and the glass one cannot disagree
about what the aeroplane is complaining about. The colours are the Airbus
grammar and are not decoration: red is a warning, amber a caution, cyan an
action still to take, green one already done.
"""

from dataclasses import dataclass, field

from . import aircraft as fleet

RED, AMBER, CYAN, GREEN, WHITE = "red", "amber", "cyan", "green", "white"

# How fast a leaking tank empties, beyond whatever the engines are burning.
FUEL_LEAK_KG_S = 3.5

# What is left of the roll and pitch rates on the remaining hydraulics.
HYDRAULIC_RATE_FACTOR = 0.45

# Braking with a degraded system, as a fraction of the normal coefficient.
BRAKE_FAILURE_FACTOR = 0.35


@dataclass(frozen=True)
class Failure:
    """One thing that can go wrong, and what the crew should do about it."""

    key: str
    name: str  # how a pilot asks for it
    title: str  # as the ECAM announces it
    colour: str  # red for a warning, amber for a caution
    actions: tuple = ()
    per_engine: bool = False


CATALOGUE = (
    Failure(
        key="engine",
        name="engine failure",
        title="ENG {n} FAIL",
        colour=RED,
        actions=("THR LEVER {n} . . . IDLE", "ENG MASTER {n} . . . OFF"),
        per_engine=True,
    ),
    Failure(
        key="fire",
        name="engine fire",
        title="ENG {n} FIRE",
        colour=RED,
        actions=(
            "THR LEVER {n} . . . IDLE",
            "ENG MASTER {n} . . . OFF",
            "AGENT 1 . . . DISCHARGE",
        ),
        per_engine=True,
    ),
    Failure(
        key="fuel",
        name="fuel leak",
        title="FUEL LEAK",
        colour=AMBER,
        actions=("FUEL X FEED . . . OFF", "LAND ASAP"),
    ),
    Failure(
        key="flaps",
        name="flap jam",
        title="F/CTL FLAPS LOCKED",
        colour=AMBER,
        actions=("FLAP LEVER . . . DO NOT MOVE", "APPR SPEED . . . INCREASE"),
    ),
    Failure(
        key="gear",
        name="gear jam",
        title="L/G GEAR NOT DOWNLOCKED",
        colour=AMBER,
        actions=("L/G . . . GRAVITY EXTN", "APPR . . . FLAP 3"),
    ),
    Failure(
        key="hydraulics",
        name="hydraulic failure",
        title="HYD SYS LO PR",
        colour=AMBER,
        actions=("MAX SPEED . . . 320 KT", "F/CTL . . . SLOW"),
    ),
    Failure(
        key="brakes",
        name="brake failure",
        title="BRAKES DEGRADED",
        colour=AMBER,
        actions=("BRK . . . ALTN", "LDG DIST . . . INCREASED"),
    ),
)

BY_KEY = {f.key: f for f in CATALOGUE}


def resolve(text):
    """Find a failure by key or by the words a pilot would use."""
    wanted = (text or "").strip().lower()
    if not wanted:
        return None
    for failure in CATALOGUE:
        if wanted == failure.key or wanted == failure.name:
            return failure
    for failure in CATALOGUE:
        if wanted in failure.name or failure.key in wanted:
            return failure
    return None


def menu():
    """The catalogue, for `failures`."""
    lines = ["### What can go wrong", ""]
    lines.append("| Command | On the ECAM |")
    lines.append("| --- | --- |")
    for failure in CATALOGUE:
        title = failure.title.format(n=1) if failure.per_engine else failure.title
        lines.append("| `fail {}` | {} |".format(failure.name, title))
    lines.append("")
    lines.append(
        "`arm engine failure` sets one to fire at V1 on the takeoff roll, which "
        "is the case worth practising."
    )
    lines.append("")
    lines.append(
        "Add an engine number to any of the engine ones -- `fail engine 3 fire` "
        "-- and undo any of them: `fix hydraulics`, or `fix all`."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Triggering
# ---------------------------------------------------------------------------


def trigger(sim, key, engine_index=0):
    """Break something. Returns what the ECAM will say about it."""
    s = sim.state
    failure = BY_KEY.get(key)
    if failure is None:
        return None

    if failure.per_engine:
        index = int(max(0, min(engine_index, sim.aircraft.engine_count - 1)))
        if index not in s.engines_failed:
            s.engines_failed.append(index)
        if key == "fire" and index not in s.engines_on_fire:
            s.engines_on_fire.append(index)
        return failure.title.format(n=index + 1)

    if key not in s.failures:
        s.failures.append(key)
    # A jam has to remember what it jammed *at*, or a later flap command would
    # move a surface that is supposed to be stuck.
    if key == "flaps":
        s.jammed_flaps = s.flaps
    if key == "gear":
        s.jammed_gear_down = s.gear_down
    return failure.title


def restore_engines(sim):
    """Everything is running again.

    The one owner of that statement, because it is three pieces of state that
    have to move together and did not: `restart engines` cleared the failed list
    and left `engines_on_fire` populated, so the ECAM fell silent while
    `engines.readouts` went on reporting a fire on an engine that was running --
    and the browser's E/WD paints that in red. It also never cleared
    `engines_running`, which is set when the tanks run dry and was a one-way
    latch, so the command could not do the one thing it exists for.
    """
    s = sim.state
    s.engines_failed = []
    s.engines_on_fire = []
    s.engines_running = True


def clear(sim, key=None):
    """Un-break something, or everything. Returns what was cleared.

    The inverse of `trigger`, and in the same place for the same reason: a jam
    remembers the setting it jammed at, and forgetting to forget that leaves the
    surface stuck at a position nothing is enforcing any more.
    """
    s = sim.state
    if key is None:
        cleared = [f.key for f, _index in active(s)]
        s.failures = []
        s.jammed_flaps = None
        s.jammed_gear_down = None
        s.armed_failure = None
        restore_engines(sim)
        return cleared

    if key not in BY_KEY:
        return []
    if BY_KEY[key].per_engine:
        restore_engines(sim)
        return [key]
    if key not in s.failures:
        return []
    s.failures.remove(key)
    if key == "flaps":
        s.jammed_flaps = None
    if key == "gear":
        s.jammed_gear_down = None
    return [key]


def active(state):
    """Every failure currently on the aircraft, engine ones included."""
    out = []
    for index in sorted(state.engines_failed or ()):
        on_fire = index in (state.engines_on_fire or ())
        out.append((BY_KEY["fire" if on_fire else "engine"], index))
    for key in state.failures or ():
        if key in BY_KEY:
            out.append((BY_KEY[key], None))
    return out


def has(state, key):
    return key in (state.failures or ())


# ---------------------------------------------------------------------------
# What they do to the aeroplane
# ---------------------------------------------------------------------------


def apply(sim, dt):
    """Let the failures act. Called once a substep, before the forces.

    Each of these writes a number the flight model already reads. Nothing
    downstream knows a failure happened -- it only sees a tank emptying faster
    than the burn explains, or a flap lever that will not move.
    """
    s = sim.state
    if has(s, "fuel") and s.fuel_kg > 0.0:
        leaked = min(FUEL_LEAK_KG_S * dt, s.fuel_kg)
        s.fuel_kg -= leaked
        s.mass_kg -= leaked
        if s.fuel_kg <= 0.0:
            s.fuel_kg = 0.0
            s.engines_running = False
    if has(s, "flaps") and s.jammed_flaps is not None:
        s.flaps = s.jammed_flaps
    if has(s, "gear") and s.jammed_gear_down is not None:
        s.gear_down = s.jammed_gear_down


def control_rate_factor(state):
    """How much of the roll and pitch rate the surfaces still have."""
    return HYDRAULIC_RATE_FACTOR if has(state, "hydraulics") else 1.0


def braking_factor(state):
    """How much of the brakes are left."""
    return BRAKE_FAILURE_FACTOR if has(state, "brakes") else 1.0


# ---------------------------------------------------------------------------
# The armed failure
# ---------------------------------------------------------------------------


def arm(sim, key, engine_index=0):
    """Set a failure to fire at V1 on the takeoff roll.

    The one worth practising, and the reason the rest of this exists: an engine
    that quits at the moment the takeoff can no longer be rejected. Everything
    it needs was already here -- the V-speeds, the ground roll, the asymmetric
    thrust and the rudder that has to hold it straight.
    """
    if key not in BY_KEY:
        return None
    sim.state.armed_failure = [key, int(engine_index)]
    return BY_KEY[key]


def check_armed(sim, readout):
    """Fire an armed failure once the aircraft passes V1. Returns its title."""
    from . import fbw

    s = sim.state
    if not s.armed_failure or not s.on_ground:
        return None
    if readout.ias_kt < fbw.takeoff_speeds(sim).v1:
        return None
    key, index = s.armed_failure
    s.armed_failure = None
    return trigger(sim, key, index)


# ---------------------------------------------------------------------------
# The ECAM
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EcamLine:
    text: str
    colour: str
    indent: bool = False


def ecam(sim):
    """What the Engine/Warning Display is saying, worst first.

    Warnings above cautions, because that is the order they have to be dealt
    with and the order the real one uses.
    """
    lines = []
    for failure, index in active(sim.state):
        title = (
            failure.title.format(n=index + 1) if failure.per_engine else failure.title
        )
        lines.append((failure.colour, EcamLine(title, failure.colour)))
        for action in failure.actions:
            text = action.format(n=index + 1) if failure.per_engine else action
            lines.append((failure.colour, EcamLine(text, CYAN, indent=True)))

    warnings = [line for colour, line in lines if colour == RED]
    cautions = [line for colour, line in lines if colour != RED]
    return warnings + cautions


def ecam_text(sim):
    """The same thing for the front end that has no pixels."""
    lines = ecam(sim)
    if not lines:
        return ""
    return "\n".join(
        ("    " if line.indent else "") + line.text for line in lines
    )
