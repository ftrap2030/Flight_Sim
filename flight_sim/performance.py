"""Takeoff and landing performance, solved rather than assumed.

`fbw.takeoff_speeds` said this in its own comment, and was right:

    Real V-speeds come out of a performance chart that also knows the runway,
    the slope, the wind and the temperature; these are the stall-speed
    relationships underneath that chart, which is as much as a point-mass model
    can honestly claim.

It can claim more now, because everything a chart is computed *from* is already
here. `landing.ground_forces` owns thrust, drag and friction on the runway; the
roll already models spoilers destroying lift, brakes biting on the weight the
wheels are carrying, wind in the right currency, and the yaw and thrust loss of
an engine that has quit. What was missing was a solver that flies the two
hypothetical rolls **before** the brakes come off.

So V1 stops being 1.09 x the stall speed -- a number that had never once looked
at the runway -- and becomes the speed at which the distance to stop and the
distance to go are the same.

**This module owns no forces.** Every step calls `landing.ground_forces`, which
is the same call `Simulator._ground_substep` makes: one force model, read by two
integrators. That is the relationship `navigation.plan` has with `_aero_state`
through `_probe`, and it exists for the same reason -- a performance chart with
its own friction model would be a second aeroplane, and the runway you are told
you need would not be the runway you actually use.

Like `navigation.py`, this takes a `sim` rather than importing `physics`.
"""

import math
from dataclasses import dataclass

from . import aircraft as fleet
from . import atmosphere as atm
from . import fbw
from . import landing


# How long the aeroplane keeps going after V1 before anything happens to stop
# it. The certification definition of accelerate-stop distance includes a
# distance equivalent to two seconds at V1, flown at V1, between reaching the
# decision speed and the first stopping action -- it is not reaction time, it is
# a regulatory allowance, and leaving it out shortens every accelerate-stop
# distance by a couple of hundred feet.
RECOGNITION_S = 2.0

# Rotation: from VR to the wheels leaving the ground. Still accelerating, still
# on the failed engine if this is the go case.
ROTATION_S = 3.0

# The height the takeoff distance is measured to. Not a number anyone here
# chose: it is the screen height in the certification rules.
SCREEN_HEIGHT_FT = 35.0

# The all-engines distance carries a 15% margin in the field-length definition,
# because the case where nothing goes wrong is the one that happens and the
# regulator wants room in it. See `takeoff_field_length_ft` below.
ALL_ENGINES_MARGIN = 1.15

# Integration step. The roll is a smooth acceleration of tens of seconds, so a
# quarter of a second is far finer than the answer needs; the cost is that a
# solve is a few thousand force evaluations, which is why `Simulator` caches it.
STEP_S = 0.25
MAX_ROLL_S = 180.0

# How close the two distances have to be before the bisection stops. A tenth of
# a knot of V1 is already past what any chart prints.
V1_TOLERANCE_KT = 0.1

BALANCED = "balanced"
FLOOR_LIMITED = "minimum V1"
VR_LIMITED = "VR"
# Not a limit on V1 at all: the aeroplane cannot reach the screen height on the
# remaining engine at this weight and flap, so there is no V1 that works. It
# used to be reported as VR-limited, which is a lie an A380 at maximum weight
# and flaps 3 tells eleven times out of eleven.
UNFLYABLE = "cannot climb"


@dataclass(frozen=True)
class TakeoffPerformance:
    """What the runway will and will not allow, at this weight and wind.

    Display data has one owner and this is it: both front ends render these
    numbers and neither works any of them out. The same rule
    `fbw.characteristic_speeds` follows for the speed tape.
    """

    v1_kt: float
    vr_kt: float
    v2_kt: float
    # Which takeoff flap this is for. A performance calculation *chooses* it --
    # that is what the FLAP line on a real takeoff data card is.
    flaps: int
    # The two distances at the chosen V1, and the all-engines case behind them.
    accelerate_stop_ft: float
    accelerate_go_ft: float
    all_engines_ft: float
    # The greater of the three, which is what the runway has to hold.
    field_length_ft: float
    runway_ft: float
    limited_by: str

    @property
    def margin_ft(self):
        return self.runway_ft - self.field_length_ft

    @property
    def legal(self):
        return self.margin_ft >= 0.0

    def summary(self):
        verdict = (
            "{:,.0f} ft to spare".format(self.margin_ft)
            if self.legal
            else "{:,.0f} ft SHORT".format(-self.margin_ft)
        )
        return (
            "FLAP {}  V1 {:.0f}  VR {:.0f}  V2 {:.0f} — needs {:,.0f} ft "
            "of {:,.0f}, {}".format(
                self.flaps, self.v1_kt, self.vr_kt, self.v2_kt,
                self.field_length_ft, self.runway_ft, verdict,
            )
        )


@dataclass(frozen=True)
class LandingPerformance:
    """The other end of the same integrator."""

    vref_kt: float
    ground_roll_ft: float
    # The roll plus the air distance from the threshold, and then the factor a
    # despatch calculation applies to it.
    landing_distance_ft: float
    required_ft: float
    runway_ft: float

    @property
    def margin_ft(self):
        return self.runway_ft - self.required_ft

    @property
    def legal(self):
        return self.margin_ft >= 0.0


# Factored landing distance: a despatch calculation requires the runway to be
# 1/0.6 times the demonstrated distance for a dry runway. It is the regulator's
# number, not a guess at one.
LANDING_FACTOR = 1.0 / 0.6

# Threshold crossing height, feet: the aeroplane is still fifty feet up when it
# reaches the paving, and that distance counts against the runway.
THRESHOLD_HEIGHT_FT = 50.0


# --------------------------------------------------------------------------
# The integrator
# --------------------------------------------------------------------------

# Everything a hypothetical roll disturbs, put back in a `finally` so that an
# exception midway cannot leave the aeroplane braking on a runway it is not on.
_ROLL_FIELDS = (
    "tas_ms", "altitude_ft", "mass_kg", "pitch_deg", "cmd_pitch_deg",
    "gamma_deg", "bank_deg", "flaps", "gear_down", "spoilers", "throttle_pct",
    "engine_n1_pct", "sideslip_deg", "rudder_deg", "on_ground", "brakes",
    "reverse_thrust", "engines_failed", "engines_running",
)


class _Roll:
    """A ground roll flown somewhere the aeroplane is not.

    Saves the state on entry and restores it on exit, so the solve is invisible
    to everything that reads the aeroplane afterwards.
    """

    def __init__(self, sim, flaps, headwind_ms, elevation_ft, mass_kg):
        self.sim = sim
        self.state = sim.state
        self._saved = {
            name: _copy(getattr(self.state, name)) for name in _ROLL_FIELDS
        }
        s = self.state
        s.altitude_ft = elevation_ft
        s.mass_kg = mass_kg
        s.flaps = flaps
        s.gear_down = True
        s.spoilers = False
        s.brakes = 0.0
        s.reverse_thrust = False
        s.engines_failed = []
        s.engines_running = True
        s.on_ground = True
        s.bank_deg = s.gamma_deg = 0.0
        s.sideslip_deg = s.rudder_deg = 0.0
        s.pitch_deg = s.cmd_pitch_deg = 0.0
        s.throttle_pct = 100.0
        # A performance chart assumes the engines are standing at the takeoff
        # rating when the brakes come off, which is also what a real crew does
        # and why they do it. Spooling from idle inside the solve would price a
        # different takeoff from the one the chart describes.
        sim.settle_engines()
        self.headwind_ms = headwind_ms
        # Airspeed at rest is the headwind, not zero -- the same currency
        # `_ground_substep` keeps and the reason a headwind shortens the roll.
        s.tas_ms = headwind_ms
        self.distance_ft = 0.0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for name, value in self._saved.items():
            setattr(self.state, name, value)
        return False

    @property
    def ias_kt(self):
        return (
            atm.tas_to_ias(max(self.state.tas_ms, 0.0), self.state.altitude_ft)
            * atm.KT_PER_MS
        )

    @property
    def ground_ms(self):
        return max(0.0, self.state.tas_ms - self.headwind_ms)

    def step(self, dt, ceiling_ms=None):
        """One substep of the same physics the real roll uses.

        `ceiling_ms` shortens the step so the roll finishes *at* a speed rather
        than at the first step past it -- see `run_to_speed`. It returns the
        time actually flown, which is not always `dt` for that reason.
        """
        s = self.state
        thrust, drag, friction = landing.ground_forces(self.sim)
        accel = (thrust - drag - friction) / s.mass_kg
        if ceiling_ms is not None and accel > 0.0:
            dt = min(dt, max(0.0, (ceiling_ms - s.tas_ms) / accel))
        s.tas_ms = max(self.headwind_ms, s.tas_ms + accel * dt)
        self.distance_ft += self.ground_ms * dt / atm.M_PER_FT
        return dt

    def run_to_speed(self, target_kt, dt=STEP_S):
        """Accelerate until the airspeed indicator reads `target_kt`.

        **The last step is shortened to land exactly on the target**, and that
        is load-bearing rather than tidy. Left to run in whole quarter-seconds
        the roll stops at the first step *past* V1, which at takeoff
        acceleration is about a knot beyond it -- so the distance is a staircase
        in V1 with a tread far wider than the tenth of a knot the bisection
        converges to, and `_takeoff_at_flap` ends up bisecting a step function.
        The symptom is a "balanced" V1 whose two distances are a hundred and
        fifty feet apart, which is not balanced. It is the same family as
        truncating a clock: an expression that is right at every sample and
        wrong between them.

        Returns False if it never gets there, which is how a takeoff that
        cannot be made at all is reported rather than looping.
        """
        target_ms = atm.ias_to_tas(
            target_kt * atm.MS_PER_KT, self.state.altitude_ft
        )
        flown = 0.0
        while self.state.tas_ms < target_ms:
            before = self.state.tas_ms
            flown += self.step(dt, ceiling_ms=target_ms)
            if flown > MAX_ROLL_S or self.state.tas_ms - before <= 1e-6:
                return False
        return True

    def run_for(self, seconds, dt=STEP_S):
        flown = 0.0
        while flown < seconds:
            self.step(min(dt, seconds - flown))
            flown += dt

    def run_to_stop(self, dt=STEP_S):
        flown = 0.0
        while self.ground_ms > 0.5:
            self.step(dt)
            flown += dt
            if flown > MAX_ROLL_S:
                break


def _copy(value):
    return list(value) if isinstance(value, list) else value


# Where to look for the minimum control speed. Below the first figure no
# airliner is controllable on one engine; above the second every one of them is,
# so a crossing outside this range would mean the geometry had changed rather
# than the weather.
VMCG_SEARCH_KT = (40.0, 200.0)


def vmcg_kt(sim, elevation_ft, mass_kg, flaps):
    """The slowest speed the rudder can still hold the aeroplane straight at.

    Not a number typed in. `_update_sideslip` normalises the dead engine's yaw
    moment by dynamic pressure, so as the speed falls the same engine demands
    ever more rudder while `max_rudder_deg` gives ever less; somewhere the two
    cross, and that crossing is Vmc. `tests/test_yaw.py` has asserted for
    several phases that it emerges from the engine arm, the thrust and the
    dynamic pressure rather than being coded, which is exactly what makes it
    usable as a floor here.

    It is the ground case that is wanted -- Vmcg, where the aeroplane may not
    bank into the live engine and the nosewheel is still doing something -- and
    this is the airborne crossing standing in for it. The honest difference is
    that a real Vmcg is a little higher; the honest similarity is that both are
    decided by the same three numbers.
    """
    s = sim.state
    craft = sim.aircraft
    saved = {name: _copy(getattr(s, name)) for name in _ROLL_FIELDS}
    try:
        s.altitude_ft = elevation_ft
        s.mass_kg = mass_kg
        s.flaps = flaps
        s.gear_down = True
        s.spoilers = False
        s.brakes = 0.0
        s.reverse_thrust = False
        s.on_ground = False
        s.engines_failed = [0]
        s.engines_running = True
        s.throttle_pct = 100.0
        s.bank_deg = s.gamma_deg = s.sideslip_deg = s.rudder_deg = 0.0
        s.pitch_deg = s.cmd_pitch_deg = 0.0
        sim.settle_engines()

        def short_of_rudder(ias_kt):
            """True below the crossing: more rudder needed than there is."""
            s.tas_ms = atm.ias_to_tas(ias_kt * atm.MS_PER_KT, elevation_ft)
            v = max(s.tas_ms, 25.0)
            reference = (
                0.5 * atm.density(elevation_ft) * v * v
                * craft.wing_area_m2 * craft.wing_span_m
            )
            needed = abs(
                sim._asymmetric_yaw_moment() / max(reference, 1.0)
                / craft.rudder_power
            )
            return needed > sim.max_rudder_deg()

        lo, hi = VMCG_SEARCH_KT
        if not short_of_rudder(lo):
            return lo
        if short_of_rudder(hi):
            return hi
        while hi - lo > V1_TOLERANCE_KT:
            mid = 0.5 * (lo + hi)
            if short_of_rudder(mid):
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)
    finally:
        for name, value in saved.items():
            setattr(s, name, value)


def _takeoff_flaps(state):
    """Nobody departs with a clean wing, and quoting the clean stall speed
    would put every V-speed some thirty knots high."""
    return max(state.flaps, 1)


def _climb_out_ft(sim, v2_ms, engines_out):
    """Lift-off to the thirty-five foot screen, in feet of ground covered.

    The gradient is whatever the excess thrust buys at V2 with the gear still
    down -- so an aeroplane that cannot climb on the remaining engine reports
    that it cannot, rather than quietly covering the distance anyway.
    """
    s = sim.state
    saved = {name: _copy(getattr(s, name)) for name in _ROLL_FIELDS}
    try:
        s.on_ground = False
        s.tas_ms = v2_ms
        s.spoilers = False
        s.brakes = 0.0
        s.engines_failed = list(engines_out)
        s.gamma_deg = 0.0
        s.pitch_deg = s.cmd_pitch_deg = sim.level_flight_pitch_deg()
        s.throttle_pct = 100.0
        sim.settle_engines()
        aero = sim._aero_state()
        excess = aero.thrust - aero.drag
        weight = s.mass_kg * atm.G0
        if excess <= 0.0:
            return None
        gamma = math.asin(min(1.0, excess / weight))
        if gamma <= 1e-6:
            return None
        return SCREEN_HEIGHT_FT / math.tan(gamma)
    finally:
        for name, value in saved.items():
            setattr(s, name, value)


# --------------------------------------------------------------------------
# The two cases
# --------------------------------------------------------------------------

def accelerate_stop_ft(sim, v1_kt, headwind_ms, elevation_ft, mass_kg, flaps):
    """Accelerate to V1, then stop.

    No reverse thrust: a dry-runway accelerate-stop distance may not take
    credit for it, because a thrust reverser is not guaranteed to deploy. That
    is the regulator's rule and it is also why this distance is so long.
    """
    with _Roll(sim, flaps, headwind_ms, elevation_ft, mass_kg) as roll:
        if not roll.run_to_speed(v1_kt):
            return None
        # Two seconds at V1 before anything happens, which is the definition
        # rather than a model of a pilot.
        roll.distance_ft += roll.ground_ms * RECOGNITION_S / atm.M_PER_FT
        s = sim.state
        s.throttle_pct = 0.0
        sim.settle_engines()
        s.spoilers = True
        s.brakes = 1.0
        roll.run_to_stop()
        return roll.distance_ft


def accelerate_go_ft(sim, v1_kt, vr_kt, v2_kt, headwind_ms, elevation_ft,
                     mass_kg, flaps):
    """Accelerate to V1, lose the critical engine, and go anyway.

    Engine 0 is the most outboard on every type in the fleet -- `engine_arms_m`
    is ordered from port to starboard -- so it is the critical one, and the yaw
    it makes is the yaw the model already computes.
    """
    with _Roll(sim, flaps, headwind_ms, elevation_ft, mass_kg) as roll:
        if not roll.run_to_speed(v1_kt):
            return None
        sim.state.engines_failed = [0]
        if not roll.run_to_speed(vr_kt):
            return None
        # Rotation, still accelerating and still on the remaining engines.
        roll.run_for(ROTATION_S)
        air_ft = _climb_out_ft(sim, v2_kt * atm.MS_PER_KT, [0])
        if air_ft is None:
            return None
        return roll.distance_ft + air_ft


def all_engines_ft(sim, vr_kt, v2_kt, headwind_ms, elevation_ft, mass_kg, flaps):
    """The takeoff nothing goes wrong on, to the same screen height."""
    with _Roll(sim, flaps, headwind_ms, elevation_ft, mass_kg) as roll:
        if not roll.run_to_speed(vr_kt):
            return None
        roll.run_for(ROTATION_S)
        air_ft = _climb_out_ft(sim, v2_kt * atm.MS_PER_KT, [])
        if air_ft is None:
            return None
        return roll.distance_ft + air_ft


# --------------------------------------------------------------------------
# The solve
# --------------------------------------------------------------------------

def takeoff(sim, field=None, mass_kg=None, flaps=None):
    """What the takeoff *being made* needs, in the configuration it is in.

    Not the best available one. The aeroplane rotates at the VR for the flap
    the lever is on, so a card quoting a different flap's VR would be the
    display and the model disagreeing about the same aeroplane -- which is the
    fault the speed tape and the flight mode annunciator each exist to prevent.
    `best_flap` below is the separate question, and a display asks it
    separately.
    """
    s = sim.state
    field = field or sim.airfields.by_ident(
        s.landing_field_ident, s.x_nm, s.y_nm, radius_nm=15.0
    )
    mass_kg = s.mass_kg if mass_kg is None else mass_kg
    return _takeoff_at_flap(
        sim, field, mass_kg, _takeoff_flaps(s) if flaps is None else flaps
    )


def best_flap(sim, field=None, mass_kg=None):
    """Which takeoff setting needs least runway, and its card.

    A real takeoff data card has a FLAP line on it because the setting is
    *chosen*: more flap is more lift and a slower, shorter roll, but more drag
    and a worse climb-out afterwards, and which wins depends on the weight and
    the elevation. So every setting is solved and the shortest wins -- and on
    Anfell's four and a half thousand feet of elevation it is not always the
    same one, which is the whole reason this is a calculation rather than a
    convention.

    Landing flap is not a takeoff setting and is not offered.
    """
    s = sim.state
    field = field or sim.airfields.by_ident(
        s.landing_field_ident, s.x_nm, s.y_nm, radius_nm=15.0
    )
    mass_kg = s.mass_kg if mass_kg is None else mass_kg
    best = None
    for setting in TAKEOFF_FLAPS:
        candidate = _takeoff_at_flap(sim, field, mass_kg, setting)
        if best is None or candidate.field_length_ft < best.field_length_ft:
            best = candidate
    return best


# The settings an aeroplane departs on. FULL is a landing configuration -- its
# drag would ruin the climb-out the screen height is measured to.
TAKEOFF_FLAPS = (1, 2, 3)


def _takeoff_at_flap(sim, field, mass_kg, flaps):
    """Where the two distances meet, at one flap setting.

    V1 lives inside a window and the solve never leaves it:

    * the **floor** is Vmcg, below which the rudder cannot hold the aeroplane
      straight on the remaining engine;
    * the **ceiling** is VR, because a decision taken after the nose has come
      up is not a decision.

    Inside the window the two distances cross and V1 is that crossing; outside
    it they never do and V1 clamps to whichever end.

    **`limited_by` is read off where V1 landed rather than chosen by a branch**,
    and that is deliberate. Only one of the two clamps is reachable: stopping
    from the minimum control speed is always far shorter than accelerating from
    it to VR on one engine and then climbing, so the floor never binds -- the
    narrowest margin anywhere in the fleet, an A319neo at maximum weight with
    the brakes degraded, still leaves twenty-six knots between Vmcg and the
    crossing. Written as an `if` the floor branch would be code that cannot
    run; derived from the answer it is a label that tells the truth if the
    numbers ever move, and costs nothing while they do not.
    """
    s = sim.state
    elevation_ft = field.elevation_ft if field else s.altitude_ft
    runway_ft = field.runway_length_ft if field else 0.0

    direction = (
        s.roll_direction_deg
        if s.roll_direction_deg is not None
        else (field.landing_direction_for_heading(s.heading_deg)
              if field else s.heading_deg)
    )
    headwind_ms, _cross = sim.ground_wind_ms(direction)

    _v1_was, vr_kt, v2_kt = _speeds_at(sim, mass_kg, flaps)
    # The floor is Vmcg, not `fbw`'s 1.09 x Vs. That factor was a balanced-V1
    # approximation standing in for the whole chart; using it as the lower bound
    # here would be the chart bounding itself, and it sits so far above the real
    # minimum control speed that every case clamps to it and the solve never
    # runs. Vmcg is the speed below which the rudder cannot hold the aeroplane
    # straight, which is the actual reason V1 has a floor.
    floor_kt = min(vmcg_kt(sim, elevation_ft, mass_kg, flaps), vr_kt)

    def stop(v1):
        return accelerate_stop_ft(
            sim, v1, headwind_ms, elevation_ft, mass_kg, flaps)

    def go(v1):
        return accelerate_go_ft(
            sim, v1, vr_kt, v2_kt, headwind_ms, elevation_ft, mass_kg, flaps)

    # The difference is *increasing* in V1: a later decision means more runway
    # used getting there and more to shed afterwards, while the go case gets
    # more of its acceleration on all engines and shortens. So it crosses zero
    # at most once, and bisection is the whole solve.
    #
    # Tested at VR rather than at the floor because neither `None` case depends
    # on V1 -- an engine-out climb that cannot make the screen height cannot
    # make it from any decision speed -- so one probe answers both questions and
    # saves two integrations.
    lo, hi = floor_kt, vr_kt
    s_hi, g_hi = stop(hi), go(hi)
    if s_hi is None or g_hi is None:
        return _unflyable(runway_ft, floor_kt, vr_kt, v2_kt, flaps)
    if s_hi <= g_hi:
        v1 = hi
    else:
        while hi - lo > V1_TOLERANCE_KT:
            mid = 0.5 * (lo + hi)
            if stop(mid) < go(mid):
                lo = mid
            else:
                hi = mid
        v1 = 0.5 * (lo + hi)

    stop_ft = stop(v1)
    go_ft = go(v1)
    clean_ft = all_engines_ft(
        sim, vr_kt, v2_kt, headwind_ms, elevation_ft, mass_kg, flaps)
    if stop_ft is None or go_ft is None or clean_ft is None:
        return _unflyable(runway_ft, floor_kt, vr_kt, v2_kt, flaps)

    limited_by = (
        VR_LIMITED if v1 >= vr_kt - V1_TOLERANCE_KT
        else FLOOR_LIMITED if v1 <= floor_kt + V1_TOLERANCE_KT
        else BALANCED
    )

    return TakeoffPerformance(
        v1_kt=v1, vr_kt=vr_kt, v2_kt=v2_kt, flaps=flaps,
        accelerate_stop_ft=stop_ft,
        accelerate_go_ft=go_ft,
        all_engines_ft=clean_ft,
        field_length_ft=takeoff_field_length_ft(stop_ft, go_ft, clean_ft),
        runway_ft=runway_ft,
        limited_by=limited_by,
    )


def takeoff_field_length_ft(stop_ft, go_ft, clean_ft):
    """The published figure is not the balanced field length.

    It is the greatest of the accelerate-stop distance, the engine-out distance
    to the screen, and the all-engines distance with 15% added -- and on a long
    runway it is that last one that wins, which is why a balanced field length
    alone comes out short of every published number.
    """
    return max(stop_ft, go_ft, clean_ft * ALL_ENGINES_MARGIN)


def _speeds_at(sim, mass_kg, flaps):
    """(V1 floor, VR, V2) at a weight the aeroplane may not currently be.

    VR and V2 stay exactly what `fbw` has always made them: certification
    defines both as minima against the stall speed, so they are honest as
    stall relationships and there is nothing here for a runway to say about
    them. Only V1 was pretending.
    """
    s = sim.state
    saved_mass, saved_flaps = s.mass_kg, s.flaps
    try:
        s.mass_kg, s.flaps = mass_kg, flaps
        v = fbw.takeoff_speeds(sim)
        return v.v1, v.vr, v.v2
    finally:
        s.mass_kg, s.flaps = saved_mass, saved_flaps


def _unflyable(runway_ft, v1, vr, v2, flaps):
    """No V1 works -- the aeroplane cannot make the screen height at all.

    Reported as an infinite field length rather than as an exception, so a
    display can say "this does not fit" in the same words it uses for a runway
    that is merely too short. It is a real answer and not an error: an A380 at
    maximum weight with flaps 3 has too much drag to climb away on three
    engines, and the right response is to choose a different flap -- which is
    what `best_flap` does, since an infinity sorts last on its own.
    """
    return TakeoffPerformance(
        v1_kt=v1, vr_kt=vr, v2_kt=v2, flaps=flaps,
        accelerate_stop_ft=float("inf"),
        accelerate_go_ft=float("inf"),
        all_engines_ft=float("inf"),
        field_length_ft=float("inf"),
        runway_ft=runway_ft,
        limited_by=UNFLYABLE,
    )


# --------------------------------------------------------------------------
# The other end
# --------------------------------------------------------------------------

def landing_performance(sim, field=None, mass_kg=None, arrival_track_deg=None):
    """What it takes to stop, which is what makes a destination usable.

    The same integrator read the other way: over the threshold at fifty feet
    and Vref, touch down, spoilers out, full braking and reverse -- reverse is
    allowed here, where the accelerate-stop case may not have it.

    `arrival_track_deg` is the track the aeroplane will *arrive* on, which is
    what decides which end of the runway it uses and therefore whether the wind
    helps or hurts. A caller that leaves it out gets the aeroplane's own
    heading, which is right when it is on or near the field and meaningless
    three hundred miles away -- so `navigation.plan` passes the final leg's
    track rather than letting the answer depend on where the nose happens to
    be pointing when the plan is filed.
    """
    s = sim.state
    field = field or sim.airfields.by_ident(
        s.landing_field_ident, s.x_nm, s.y_nm, radius_nm=15.0
    )
    mass_kg = s.mass_kg if mass_kg is None else mass_kg
    # Full flap, which is what a landing distance is quoted at.
    flaps = len(fleet.FLAP_NAMES) - 1
    elevation_ft = field.elevation_ft if field else s.altitude_ft
    runway_ft = field.runway_length_ft if field else 0.0

    if arrival_track_deg is not None and field is not None:
        direction = field.landing_direction_for_heading(arrival_track_deg)
    elif s.roll_direction_deg is not None:
        direction = s.roll_direction_deg
    elif field is not None:
        direction = field.landing_direction_for_heading(s.heading_deg)
    else:
        direction = s.heading_deg
    headwind_ms, _cross = sim.ground_wind_ms(direction)

    saved_mass, saved_flaps = s.mass_kg, s.flaps
    try:
        s.mass_kg, s.flaps = mass_kg, flaps
        vref = fbw.characteristic_speeds(sim).vref
    finally:
        s.mass_kg, s.flaps = saved_mass, saved_flaps

    with _Roll(sim, flaps, headwind_ms, elevation_ft, mass_kg) as roll:
        roll_state = sim.state
        roll_state.tas_ms = atm.ias_to_tas(
            vref * atm.MS_PER_KT, elevation_ft)
        roll_state.throttle_pct = 0.0
        sim.settle_engines()
        roll_state.spoilers = True
        roll_state.brakes = 1.0
        roll_state.reverse_thrust = True
        roll.run_to_stop()
        ground_roll_ft = roll.distance_ft

    # The air distance from the threshold: fifty feet down a three-degree path.
    air_ft = THRESHOLD_HEIGHT_FT / math.tan(math.radians(landing.GLIDESLOPE_DEG))
    total = ground_roll_ft + air_ft
    return LandingPerformance(
        vref_kt=vref,
        ground_roll_ft=ground_roll_ft,
        landing_distance_ft=total,
        required_ft=total * LANDING_FACTOR,
        runway_ft=runway_ft,
    )
