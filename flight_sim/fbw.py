"""Airbus flight control laws.

An Airbus sidestick does not move a control surface. It asks for a load factor
in pitch and a roll rate in roll, and a computer decides what the surfaces do
about it -- subject to a set of protections the pilot cannot override by pulling
harder. That is the single most important thing about how these aircraft fly,
and without it the fleet in this simulator would handle like a 1960s airliner
wearing an Airbus badge.

Three laws, in decreasing order of help:

* **Normal Law** -- every protection active. The aeroplane will not stall, will
  not exceed 2.5 g, will not roll past 67 degrees and will not let you fly it
  into the ground faster than VMO. Full back stick gives you alpha max and
  nothing more.
* **Alternate Law** -- load factor limiting survives; the hard protections do
  not. In their place is *stability*: a nose-down demand as speed decays and a
  nose-up demand near VMO, both of which the pilot can override by holding the
  stick against them. You can stall an aeroplane in Alternate Law.
* **Direct Law** -- the stick moves the surfaces. Nothing is protecting you.

The degradations modelled here are the two a point-mass simulator can honestly
represent: losing every engine drops the aircraft to Alternate Law, because on
RAT power that is where it goes, and lowering the gear in Alternate Law drops it
to Direct Law, which is what the real reversion does. Everything else that
degrades a real Airbus -- air data disagreements, inertial reference failures,
multiple computer faults -- has no analogue here, so the pilot can also select a
law directly, which is what the `law direct` command is for.

The protections are implemented as limits on the *commanded* attitude, not as a
separate path around the flight model. Nothing here bypasses the aerodynamics:
alpha protection works by refusing to command a pitch attitude that would put
the wing past alpha max, which is exactly what it does on the aeroplane.
"""

import math

from . import atmosphere as atm

NORMAL = "normal"
ALTERNATE = "alternate"
DIRECT = "direct"

LAWS = (NORMAL, ALTERNATE, DIRECT)
LAW_NAMES = {
    NORMAL: "NORMAL LAW",
    ALTERNATE: "ALTERNATE LAW",
    DIRECT: "DIRECT LAW",
}

# Angle-of-attack thresholds, as margins below the angle at which this model's
# wing lets go. Airbus quotes them as speeds -- alpha prot at about 1.13 Vs1g,
# alpha max at Vs1g itself -- but the aerodynamic quantity underneath is alpha,
# and expressing them that way keeps them correct at any weight and load factor,
# which is the entire reason the real system uses alpha rather than airspeed.
ALPHA_MAX_MARGIN_DEG = 0.5
ALPHA_FLOOR_MARGIN_DEG = 2.0
ALPHA_PROT_MARGIN_DEG = 3.5

# Load factor limits. The flapped pair is lower because extended high-lift
# devices are not stressed for manoeuvring loads.
N_MAX_CLEAN = 2.5
N_MAX_FLAPS = 2.0
N_MIN_CLEAN = -1.0
N_MIN_FLAPS = 0.0

# Attitude limits in Normal Law.
PITCH_MAX_DEG = 30.0
PITCH_MIN_DEG = -15.0
BANK_LIMIT_DEG = 67.0
BANK_LIMIT_HIGH_SPEED_DEG = 45.0
# Direct Law has no bank protection; this is a structural bound that also keeps
# the point-mass equations away from the cos(bank) singularity at 90 degrees.
BANK_LIMIT_DIRECT_DEG = 85.0
# The bank a real Airbus holds hands-off. Past it the aircraft rolls back here
# when the stick is released -- which is not modelled, because a typed bank
# command is a stick held there, not released. Quoted on the panel so the pilot
# knows where the aeroplane stops helping.
BANK_NEUTRAL_DEG = 33.0

# Alpha floor is inhibited close to the ground: it exists to save an aircraft
# that is running out of energy in the air, not to firewall the thrust levers in
# the flare.
ALPHA_FLOOR_MIN_AGL_FT = 100.0

# High-speed protection: nose-up demand proportional to the exceedance.
HIGH_SPEED_PITCH_GAIN = 0.30  # degrees of pitch per knot over VMO
HIGH_SPEED_PITCH_MAX_DEG = 10.0
# A Mach exceedance converted to an equivalent overspeed in knots, so one gain
# can serve both limits. 0.01 Mach is worth roughly 5 kt at cruise altitude.
MACH_OVERSPEED_KT = 500.0

# Low-energy ("SPEED SPEED SPEED") threshold, as a multiple of the stall speed.
LOW_ENERGY_RATIO = 1.18

# How much of a commanded excess over the alpha ceiling Alternate Law's
# low-speed stability takes back. Below 1.0 by definition: it is a demand the
# pilot can hold the stick against, not a limit, so a determined pull still
# reaches the stall.
ALTERNATE_STABILITY = 0.55


def clamp(value, low, high):
    return max(low, min(high, value))


def alpha_thresholds(craft):
    """(alpha_prot, alpha_floor, alpha_max) in degrees, for one type."""
    alpha_max = craft.alpha_crit_deg - ALPHA_MAX_MARGIN_DEG
    return (
        craft.alpha_crit_deg - ALPHA_PROT_MARGIN_DEG,
        craft.alpha_crit_deg - ALPHA_FLOOR_MARGIN_DEG,
        alpha_max,
    )


def load_factor_limits(state):
    """(n_min, n_max) for the current configuration."""
    if state.flaps > 0:
        return (N_MIN_FLAPS, N_MAX_FLAPS)
    return (N_MIN_CLEAN, N_MAX_CLEAN)


# A bound on the alpha the load factor law may ask for, so that a demand made
# at very low dynamic pressure produces a large number rather than an infinite
# one. Well past the stall by design -- see alpha_for_load_factor.
MAX_DEMANDED_ALPHA_DEG = 40.0


def alpha_for_load_factor(sim, load_factor):
    """The angle of attack the load factor law asks for to make this many g.

    Inverts L = n*m*g through the lift curve, and deliberately does *not* stop
    at CL_max. The load factor law limits the g that is demanded; it has no idea
    what the wing can actually deliver. Ask for 2.5 g at an airspeed where the
    wing can only produce 1.2 and it will happily command the angle of attack
    the arithmetic calls for -- which is how an aeroplane in alternate law gets
    stalled by its own flight control computer.

    Clamping to CL_max here would have made the load factor limit into a second,
    accidental angle-of-attack protection, silently active in alternate and
    direct law where there is supposed to be none.

    The consequence is that the two limits swap places with speed. Fast, the
    alpha for 2.5 g is small and the load factor limit is what stops the pull;
    slow, it is far past the stall and irrelevant, and only normal law's alpha
    max stands between the pilot and a departure.
    """
    s = sim.state
    q = 0.5 * atm.density(s.altitude_ft) * max(s.tas_ms, 25.0) ** 2
    return _alpha_for_load_factor(sim.aircraft, s, q, load_factor)


def _alpha_for_load_factor(craft, state, q, load_factor):
    """The same, with dynamic pressure passed in.

    Runs twice per substep -- once for each end of the load factor envelope --
    and the atmosphere lookup is not free, so the caller computes q once.
    """
    cl_needed = load_factor * state.mass_kg * atm.G0 / max(
        q * craft.wing_area_m2, 1.0
    )
    alpha_deg = math.degrees(
        (cl_needed - craft.cl_0_for_flaps(state.flaps)) / craft.cl_alpha
    )
    return clamp(alpha_deg, -MAX_DEMANDED_ALPHA_DEG, MAX_DEMANDED_ALPHA_DEG)


def overspeed_kt(sim):
    """How far past the speed envelope the aircraft is, in knots.

    Zero inside the envelope. The Mach limit is converted to an equivalent so
    that one number covers both ends of the coffin corner.
    """
    s = sim.state
    craft = sim.aircraft
    ias_kt = atm.tas_to_ias(max(s.tas_ms, 1.0), s.altitude_ft) * atm.KT_PER_MS
    mach = atm.mach(max(s.tas_ms, 1.0), s.altitude_ft)
    return max(
        0.0,
        ias_kt - craft.vmo_kt,
        (mach - craft.mmo) * MACH_OVERSPEED_KT,
    )


# ---------------------------------------------------------------------------
# Law selection
# ---------------------------------------------------------------------------


def degrade_for_failures(sim):
    """Reconfigure the control law from the state of the aircraft.

    Two reversions, both real:

    * Every engine out means RAT power and Alternate Law. A windmilling
      relight, or fuel found again, does not put the protections back -- the
      reversion latches, as it does on the aeroplane.
    * Lowering the gear in Alternate Law reverts to Direct Law. This one
      surprises people: an aircraft that has been flying acceptably suddenly has
      no protections at all at the exact moment the pilot is busiest.

    Returns the name of the reversion if one happened, otherwise None.
    """
    s = sim.state
    if s.control_law == DIRECT:
        return None

    engines_out = not s.engines_running or len(sim._running_engines()) == 0
    if engines_out and s.control_law == NORMAL:
        s.control_law = ALTERNATE
        return "ALTERNATE LAW — all engines out"

    if s.control_law == ALTERNATE and s.gear_down:
        s.control_law = DIRECT
        return "DIRECT LAW — gear down in alternate law"

    return None


# ---------------------------------------------------------------------------
# The laws themselves
# ---------------------------------------------------------------------------


def apply(sim, dt):
    """Run the active law over the pilot's commands for one substep.

    Sets ``sim.law_pitch_target`` and ``sim.law_bank_target``: the attitude the
    flight controls will actually fly toward this substep. The pilot's own
    ``cmd_pitch_deg`` and ``cmd_bank_deg`` are never written back to, which is
    what makes the difference between the two low-speed behaviours real. Normal
    Law's protection is a hard ceiling the stick cannot argue with; Alternate
    Law's stability is a demand *against* the stick, and a pilot who holds it
    there wins -- and stalls. Overwriting the command would have quietly turned
    the second into the first, since a command taken away stays away.

    Returns the protections that fired, and accumulates them on the state so a
    momentary trigger inside a ten-second tick is still visible on the panel.
    """
    s = sim.state
    law = s.control_law
    sim.law_pitch_target = s.cmd_pitch_deg
    sim.law_bank_target = s.cmd_bank_deg

    if law == DIRECT:
        sim.law_bank_target = clamp(
            s.cmd_bank_deg, -BANK_LIMIT_DIRECT_DEG, BANK_LIMIT_DIRECT_DEG
        )
        s.alpha_floor_latched = False
        return []

    active = []
    alpha_prot, alpha_floor, alpha_max = alpha_thresholds(sim.aircraft)
    alpha_now = s.pitch_deg - s.gamma_deg
    n_min, n_max = load_factor_limits(s)
    over_kt = overspeed_kt(sim)

    # -- pitch: two separate limits that are easy to confuse -----------------
    # Load factor limiting is a hard ceiling in *both* normal and alternate law;
    # it is part of the basic pitch law and survives the reversion. Angle-of-
    # attack protection is normal law only, and what replaces it in alternate
    # law is low-speed stability, applied below as a soft demand.
    #
    # Which of the two is doing the stopping depends on speed. Fast, the alpha
    # for 2.5 g is small and the load factor limit binds; slow, it is far past
    # the stall and only alpha max is left.
    q = 0.5 * atm.density(s.altitude_ft) * max(s.tas_ms, 25.0) ** 2
    alpha_ceiling = _alpha_for_load_factor(sim.aircraft, s, q, n_max)
    limit_name = "LOAD n"
    if law == NORMAL and alpha_max < alpha_ceiling:
        alpha_ceiling = alpha_max
        limit_name = "α-MAX"
    alpha_bottom = _alpha_for_load_factor(sim.aircraft, s, q, n_min)

    if law == NORMAL and alpha_now > alpha_prot:
        active.append("α-PROT")

    commanded_alpha = s.cmd_pitch_deg - s.gamma_deg
    if commanded_alpha > alpha_ceiling:
        sim.law_pitch_target = s.gamma_deg + alpha_ceiling
        active.append(limit_name)
    elif commanded_alpha < alpha_bottom:
        sim.law_pitch_target = s.gamma_deg + alpha_bottom
        active.append("LOAD n")

    if law == ALTERNATE:
        # Low-speed stability: a nose-down demand as alpha climbs past where
        # normal law's protection would have been, and one the pilot can hold
        # the stick against. Only part of the excess is taken back, so a gentle
        # pull is resisted and a determined one still reaches the stall.
        demanded_alpha = sim.law_pitch_target - s.gamma_deg
        if demanded_alpha > alpha_max:
            excess = demanded_alpha - alpha_max
            sim.law_pitch_target -= excess * ALTERNATE_STABILITY
            active.append("LOW SPEED STAB")

    # -- pitch attitude limits ----------------------------------------------
    if law == NORMAL:
        limited = clamp(sim.law_pitch_target, PITCH_MIN_DEG, PITCH_MAX_DEG)
        if abs(limited - sim.law_pitch_target) > 1e-9:
            sim.law_pitch_target = limited
            active.append("PITCH LIM")

    # -- high speed ----------------------------------------------------------
    if over_kt > 0.0:
        bias = min(over_kt * HIGH_SPEED_PITCH_GAIN, HIGH_SPEED_PITCH_MAX_DEG)
        if law == ALTERNATE:
            bias *= 0.5  # stability, not protection
        wanted = sim.level_flight_pitch_deg() + bias
        if wanted > sim.law_pitch_target:
            sim.law_pitch_target = min(wanted, s.gamma_deg + alpha_ceiling)
            active.append("HIGH SPEED PROT" if law == NORMAL else "HIGH SPEED STAB")

    # -- bank ----------------------------------------------------------------
    # No spiral-stability rollback toward 33 degrees. On the aeroplane that
    # happens when the stick is *released*; a typed bank command is a stick held
    # there, so what applies is the hard limit and nothing else.
    if law == NORMAL:
        limit = BANK_LIMIT_HIGH_SPEED_DEG if over_kt > 0.0 else BANK_LIMIT_DEG
        if abs(s.cmd_bank_deg) > limit:
            sim.law_bank_target = math.copysign(limit, s.cmd_bank_deg)
            active.append("BANK LIM")
    else:
        sim.law_bank_target = clamp(
            s.cmd_bank_deg, -BANK_LIMIT_DIRECT_DEG, BANK_LIMIT_DIRECT_DEG
        )

    # -- alpha floor: the autothrust protection ------------------------------
    if law == NORMAL:
        _alpha_floor(sim, alpha_now, alpha_prot, alpha_floor, active)
    else:
        s.alpha_floor_latched = False

    _accumulate(s, active)
    return active


def _accumulate(state, active):
    """Merge this substep's protections into the tick's list, in order.

    A protection that fires for half a second inside a ten-second tick still
    happened, and the panel should say so.
    """
    seen = state.active_protections
    for label in active:
        if label not in seen:
            seen.append(label)


def _alpha_floor(sim, alpha_now, alpha_prot, alpha_floor, active):
    """TOGA, commanded by the aircraft rather than the pilot.

    Latches once it fires and holds the thrust up until alpha comes back below
    the protection threshold -- a simplification of TOGA LK, which on the real
    aircraft needs the pilot to disconnect the autothrust to clear.
    """
    s = sim.state
    agl_ft = s.altitude_ft - sim.terrain.elevation(s.x_nm, s.y_nm)
    if agl_ft < ALPHA_FLOOR_MIN_AGL_FT or s.on_ground:
        s.alpha_floor_latched = False
        return

    if alpha_now >= alpha_floor:
        s.alpha_floor_latched = True
    elif alpha_now < alpha_prot:
        s.alpha_floor_latched = False

    if s.alpha_floor_latched:
        s.throttle_pct = 100.0
        active.append("A.FLOOR TOGA")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def low_energy(sim, readout):
    """The "SPEED SPEED SPEED" call.

    Not a stall warning: the aircraft is flying, but the combination of speed,
    deceleration and thrust means it is running out of energy and needs thrust
    now rather than in five seconds' time.
    """
    s = sim.state
    if s.on_ground or s.control_law == DIRECT:
        return False
    if not (s.flaps >= 2 and s.gear_down):
        return False
    agl_ft = readout.agl_ft
    if agl_ft < 100.0 or agl_ft > 2000.0:
        return False
    return (
        readout.ias_kt < readout.stall_ias_kt * LOW_ENERGY_RATIO
        and s.throttle_pct < 60.0
    )


def status_text(state):
    """`NORMAL LAW` or `ALTERNATE LAW · α-PROT · A.FLOOR TOGA` for the panel."""
    name = LAW_NAMES.get(state.control_law, state.control_law.upper())
    protections = state.active_protections or []
    if not protections:
        return name
    return "{} · {}".format(name, " · ".join(protections))


def resolve(text):
    """Look up a law from pilot input. Returns None if unrecognised."""
    if not text:
        return None
    key = text.strip().lower().replace(" law", "").strip()
    aliases = {
        "normal": NORMAL, "norm": NORMAL, "n": NORMAL,
        "alternate": ALTERNATE, "alt": ALTERNATE, "altn": ALTERNATE,
        "direct": DIRECT, "dir": DIRECT, "d": DIRECT,
    }
    return aliases.get(key)
