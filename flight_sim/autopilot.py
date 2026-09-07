"""The autopilot.

Hand-flying every ten seconds is fine for a valley run and tedious for a cruise
leg. These are controllers, not a bypass: each one writes the same commanded
pitch, bank and throttle a pilot would, so everything downstream -- the control
law, the rate limits, the stall behaviour -- is unchanged. An autopilot that
cheated by writing straight to the state would fly an aeroplane the pilot cannot.

Channels engage independently, and a manual input on a channel disengages it.
That matters: an autopilot silently fighting the pilot for the elevator is worse
than no autopilot at all.
"""

import math

from . import atmosphere as atm
from . import landing

# Channel names, as the panel and the disengage logic use them.
ALTITUDE = "ALT"
VERTICAL_SPEED = "V/S"
HEADING = "HDG"
SPEED = "SPD"
APPROACH = "APPR"
NAV = "NAV"

# Limits the autopilot flies within. It is deliberately gentler than the pilot:
# a passenger-carrying autopilot does not use 60 degrees of bank.
MAX_AP_VS_FPM = 2000.0
MAX_AP_BANK_DEG = 25.0
MAX_AP_PITCH_TRIM_DEG = 3.0

# Gains
ALTITUDE_TO_VS = 2.4  # fpm of climb demanded per foot of error
VS_TO_PITCH = 1.0 / 600.0  # degrees of pitch trim per fpm of error
HEADING_TO_BANK = 1.5  # degrees of bank per degree of heading error
SPEED_TO_THROTTLE = 1.6  # percent of throttle per knot of error
GLIDESLOPE_TO_VS = 1.9  # fpm of correction per foot off the path
LOCALISER_TO_HEADING = 0.045  # degrees of heading per foot off the centreline
MAX_LOCALISER_INTERCEPT_DEG = 25.0

# How long the box stays round a Flight Mode Annunciator column after that
# column changes. Ten seconds is the real one.
FMA_BOX_S = 10.0

# Where the altitude channel stops being a climb, starts being a capture, and
# finally becomes a hold. Only the annunciator reads these: one controller flies
# all three, and these say which part of it the pilot is watching.
ALT_HOLD_FT = 120.0
ALT_CAPTURE_FT = 900.0

# How often the approach channel re-solves its guidance, in seconds.
APPROACH_UPDATE_S = 0.5

# The elevator comes back to the pilot here, and the thrust levers here.
HANDOVER_AGL_FT = 55.0
RETARD_AGL_FT = 28.0
# How close to the threshold the aircraft must be for handover to mean the flare.
HANDOVER_RANGE_FT = 1500.0


def channels(state):
    """The channels currently engaged, in panel order."""
    if not state.ap_engaged:
        return []
    active = []
    if state.ap_approach:
        active.append(APPROACH)
    else:
        if state.ap_altitude_ft is not None:
            active.append(ALTITUDE)
        elif state.ap_vs_fpm is not None:
            active.append(VERTICAL_SPEED)
        # Managed lateral wins over selected lateral, which is what pushing the
        # heading knob in means: hand the aeroplane back to the route.
        if state.ap_nav:
            active.append(NAV)
        elif state.ap_heading_deg is not None:
            active.append(HEADING)
    if state.ap_speed_kt is not None:
        active.append(SPEED)
    return active


def disengage_for(state, command_kind):
    """Drop the channels a manual input has just overridden.

    The pilot taking the controls wins, immediately and without argument.
    """
    if command_kind in ("pitch_set", "pitch_delta", "level"):
        state.ap_altitude_ft = None
        state.ap_vs_fpm = None
        state.ap_approach = False
    elif command_kind in ("bank_set", "heading", "heading_delta"):
        # A heading command re-targets the heading channel rather than killing
        # it; a raw bank command is hand-flying and drops it. Either way managed
        # lateral goes: asking for a heading *is* asking not to follow the route.
        if command_kind == "bank_set":
            state.ap_heading_deg = None
        state.ap_nav = False
        state.ap_approach = False
    elif command_kind in ("throttle_set", "throttle_delta"):
        state.ap_speed_kt = None


def update(sim, dt):
    """Run the engaged channels for one substep."""
    state = sim.state
    if not state.ap_engaged or state.on_ground:
        return

    # Approach guidance is expensive -- terrain scans and runway geometry -- and
    # NAV needs the drift angle, which needs a readout too. Both change on a
    # scale of seconds, not hundredths, so they are re-solved a few times a
    # second and the demand is held in between, as the real box does.
    if state.ap_approach or state.ap_nav:
        sim._ap_clock = getattr(sim, "_ap_clock", 0.0) + dt
        if sim._ap_clock >= APPROACH_UPDATE_S or getattr(sim, "_ap_readout", None) is None:
            sim._ap_clock = 0.0
            sim._ap_readout = sim.readout()

    if state.ap_approach:
        _fly_approach(sim, sim._ap_readout)
    else:
        if state.ap_altitude_ft is not None:
            _hold_altitude(sim)
        elif state.ap_vs_fpm is not None:
            _hold_vertical_speed(sim, state.ap_vs_fpm)
        # Managed lateral first, so that leaving a selected heading set behind
        # does not quietly fight the route.
        if state.ap_nav:
            _fly_leg(sim, sim._ap_readout)
        elif state.ap_heading_deg is not None:
            state.cmd_heading_deg = state.ap_heading_deg

    if state.ap_speed_kt is not None:
        _hold_speed(sim)


def _current_vs_fpm(sim):
    state = sim.state
    return state.tas_ms * math.sin(math.radians(state.gamma_deg)) * atm.FPM_PER_MS


def _hold_altitude(sim):
    """Convert an altitude error into a vertical speed, then fly that."""
    state = sim.state
    error = state.ap_altitude_ft - state.altitude_ft
    target_vs = max(-MAX_AP_VS_FPM, min(MAX_AP_VS_FPM, error * ALTITUDE_TO_VS))
    _hold_vertical_speed(sim, target_vs)


def _hold_vertical_speed(sim, target_fpm):
    """Pitch for a vertical speed, trimmed around the level-flight attitude."""
    state = sim.state
    target_fpm = max(-MAX_AP_VS_FPM, min(MAX_AP_VS_FPM, target_fpm))
    speed_ms = max(state.tas_ms, 20.0)
    ratio = max(-0.35, min(0.35, (target_fpm / atm.FPM_PER_MS) / speed_ms))
    gamma_target = math.degrees(math.asin(ratio))

    error = target_fpm - _current_vs_fpm(sim)
    trim = max(
        -MAX_AP_PITCH_TRIM_DEG,
        min(MAX_AP_PITCH_TRIM_DEG, error * VS_TO_PITCH),
    )
    state.cmd_pitch_deg = sim.level_flight_pitch_deg() + gamma_target + trim


def _hold_speed(sim, _readout=None):
    """Autothrottle: the thrust for the current path, corrected for speed error.

    Driving throttle from the speed error alone is a positive feedback loop in a
    descent -- less thrust means a steeper path means more speed -- so the
    baseline is the thrust that actually holds the flight path.
    """
    state = sim.state
    # Airspeed only, computed directly. Building a full readout here -- terrain
    # scans, approach guidance, navigation -- ran ten times a second of flight
    # and cost more than the entire rest of the integrator.
    ias_kt = (
        atm.tas_to_ias(max(state.tas_ms, 1.0), state.altitude_ft) * atm.KT_PER_MS
    )
    baseline = sim.throttle_for_flight_path(state.gamma_deg)
    error = ias_kt - state.ap_speed_kt
    state.throttle_pct = max(
        0.0, min(100.0, baseline - error * SPEED_TO_THROTTLE)
    )


def _fly_leg(sim, readout):
    """Steer to the active waypoint. This is LNAV.

    The thing that makes it different from holding a heading is one term: it
    aims the *track* at the waypoint rather than the nose, so a crosswind no
    longer pushes the aircraft off to one side over a long leg. Holding the
    bearing as a heading looks correct for the first minute and arrives some
    miles abeam.

    Direct-to, not leg-tracking: this points at the active waypoint rather than
    following the line from the last one. With no multi-leg route command that
    is the same thing, and when there is one this is where the cross-track term
    goes.

    NAV never disengages itself. Running out of waypoints is not a failure --
    the route is flown, and the aircraft holds the last heading it was given,
    which is what an aeroplane at the end of its flight plan does.
    """
    state = sim.state
    leg = readout.leg
    if leg is None:
        return
    state.cmd_heading_deg = (leg.bearing_deg - readout.drift_deg) % 360.0


def _fly_approach(sim, readout):
    """Track the glideslope and the localiser onto the runway.

    Disengages itself at the threshold: the flare and the touchdown are the
    pilot's, which is the part worth flying by hand.
    """
    state = sim.state
    approach = readout.approach

    # Handover is decided on height above the *runway*, not above whatever
    # happens to be underneath. Terrain in the approach corridor can rise to
    # within fifty feet of the glidepath several miles out, and handing over
    # there abandons the approach with the runway still ahead -- which the
    # aircraft then flies straight over.
    height_ft = (
        state.altitude_ft - approach.field.elevation_ft
        if approach is not None
        else readout.agl_ft
    )
    # "Committed" means in the flare region: close to the threshold and going
    # down. Handing over merely because the aircraft is low hands back a
    # nose-up attitude the AP had commanded to regain the glidepath, and the
    # aircraft then climbs away over the runway.
    committed = (
        approach is not None
        and approach.along_ft > -HANDOVER_RANGE_FT
        and readout.vertical_speed_fpm < 0.0
    )

    if committed and height_ft < HANDOVER_AGL_FT:
        state.ap_approach = False
        if height_ft < RETARD_AGL_FT:
            state.ap_speed_kt = None
            state.ap_engaged = False
            state.throttle_pct = 0.0
        return

    if approach is None or not approach.on_approach:
        # Lost the approach while still high -- off the localiser, or past the
        # aim point without landing. Give the aircraft back rather than hold a
        # frozen attitude and hope.
        state.ap_approach = False
        state.ap_altitude_ft = state.altitude_ft
        return

    # Vertical: the nominal descent for a 3-degree path, plus a correction.
    ground_speed_ms = max(readout.ground_speed_kt * atm.MS_PER_KT, 20.0)
    nominal_fpm = -(
        ground_speed_ms * math.tan(math.radians(landing.GLIDESLOPE_DEG))
        * atm.FPM_PER_MS
    )
    correction = -approach.glideslope_dev_ft * GLIDESLOPE_TO_VS
    _hold_vertical_speed(sim, nominal_fpm + correction)

    # Lateral: intercept the extended centreline.
    intercept = max(
        -MAX_LOCALISER_INTERCEPT_DEG,
        min(
            MAX_LOCALISER_INTERCEPT_DEG,
            -approach.across_ft * LOCALISER_TO_HEADING,
        ),
    )
    state.cmd_heading_deg = (approach.direction_deg + intercept) % 360.0


def status_text(state):
    """A short description of what the autopilot is doing."""
    if not state.ap_engaged:
        return "AP OFF"
    active = channels(state)
    if not active:
        return "AP ON (no mode)"
    parts = []
    for channel in active:
        if channel == ALTITUDE:
            parts.append("ALT {:,.0f}".format(state.ap_altitude_ft))
        elif channel == VERTICAL_SPEED:
            parts.append("V/S {:+,.0f}".format(state.ap_vs_fpm))
        elif channel == HEADING:
            parts.append("HDG {:03.0f}".format(state.ap_heading_deg))
        elif channel == SPEED:
            parts.append("SPD {:.0f}".format(state.ap_speed_kt))
        else:
            parts.append(channel)
    return "AP: " + " · ".join(parts)


# ---------------------------------------------------------------------------
# The Flight Mode Annunciator
# ---------------------------------------------------------------------------

# The five columns, in the order they sit across the top of the PFD.
FMA_COLUMNS = ("thrust", "vertical", "lateral", "capability", "engagement")

# Colours as the aeroplane uses them, not as a stylesheet does: green is
# engaged and doing it now, blue is armed and waiting its turn, amber is
# something you are meant to look at, white is a statement of fact.
GREEN, BLUE, AMBER, WHITE = "green", "blue", "amber", "white"


def _fma_columns(sim, readout=None):
    """The engaged and armed text for each column, before the box is worked out.

    Returns {column: (engaged, armed)}, either of which may be None. This is
    where the aeroplane's state is turned into the words an Airbus uses for it,
    and it is the only place that mapping exists -- a display picks the font.

    The readout is optional because working out whether the localiser is
    captured needs one, and building a readout costs a terrain scan. Callers
    that already have one pass it; callers that do not get an uncaptured
    approach, which is the safe way to be wrong -- LOC armed rather than LOC
    engaged.
    """
    s = sim.state
    on = s.ap_engaged
    captured = bool(
        s.ap_approach
        and readout is not None
        and readout.approach is not None
        and readout.approach.on_approach
    )

    # --- thrust ---
    if s.alpha_floor_latched:
        # A.FLOOR is the one mode the pilot did not ask for, so it says so
        # loudly and stays said until the protection is reset.
        thrust = ("A.FLOOR", AMBER)
    elif s.ap_speed_kt is not None:
        thrust = ("SPEED", GREEN)
    elif s.on_ground and s.throttle_pct > 80.0:
        thrust = ("MAN TOGA", WHITE)
    else:
        thrust = ("MAN THR", WHITE)

    # --- vertical ---
    # An approach armed but not captured leaves the aircraft in whatever
    # vertical mode it was already in -- climbing, holding a level -- with G/S
    # armed underneath it. So the armed value is decided first and held: letting
    # the altitude channel fill in "ALT armed" over the top would announce that
    # the aeroplane is about to level off when what it is about to do is
    # intercept the glideslope.
    vertical, vertical_armed = None, None
    if captured:
        vertical = ("G/S", GREEN)
    elif s.ap_approach:
        vertical_armed = ("G/S", BLUE)
    if vertical is None and on:
        if s.ap_altitude_ft is not None:
            # Three modes, not one, because that is what the aircraft is
            # actually doing and the difference is what a pilot reads. Far from
            # the selected level it is climbing or descending openly; near it,
            # ALT* is the capture; on it, ALT is the hold. One altitude channel
            # drives all three -- the annunciator only says which part of the
            # manoeuvre it is in.
            error_ft = s.ap_altitude_ft - s.altitude_ft
            if abs(error_ft) < ALT_HOLD_FT:
                vertical = ("ALT", GREEN)
            elif abs(error_ft) < ALT_CAPTURE_FT:
                vertical = ("ALT*", GREEN)
            else:
                vertical = ("OP CLB" if error_ft > 0 else "OP DES", GREEN)
                if vertical_armed is None:
                    vertical_armed = ("ALT", BLUE)
        elif s.ap_vs_fpm is not None:
            vertical = ("V/S {:+,.0f}".format(s.ap_vs_fpm), GREEN)

    # --- lateral ---
    lateral, lateral_armed = None, None
    if captured:
        lateral = ("LOC", GREEN)
    elif s.ap_approach:
        lateral, lateral_armed = None, ("LOC", BLUE)
    if lateral is None and on:
        if s.ap_nav:
            lateral = ("NAV", GREEN)
        elif s.ap_heading_deg is not None:
            lateral = ("HDG", GREEN)

    # --- approach capability ---
    # One ILS quality is modelled, so CAT 1 is the only honest thing to claim,
    # and only once the beams are actually being tracked. Announcing CAT 3 for
    # an approach the simulator cannot fly would be a lie told in green.
    capability = ("CAT 1", WHITE) if captured else None

    # --- what is flying the aeroplane ---
    engaged = []
    if on:
        engaged.append("AP1")
    if s.ap_speed_kt is not None:
        engaged.append("A/THR")
    engagement = (" ".join(engaged), WHITE) if engaged else None

    return {
        "thrust": (thrust, None),
        "vertical": (vertical, vertical_armed),
        "lateral": (lateral, lateral_armed),
        "capability": (capability, None),
        "engagement": (engagement, None),
    }


def _signature(engaged, armed):
    return "{}|{}".format(engaged[0] if engaged else "", armed[0] if armed else "")


def note_mode_changes(sim, readout=None):
    """Record when each annunciator column last changed.

    Called once a tick, from `Simulator.readout`, because *when* a mode changed
    is a fact about the flight and not about the display -- two front ends
    drawing the same aeroplane must box the same column at the same moment, and
    they cannot if each is timing it from its own frame rate.

    Idempotent: called twice with nothing changed it writes nothing, which is
    what lets the readout be rebuilt within a tick without restarting the box.
    """
    s = sim.state
    if s.fma_changed_s is None:
        s.fma_changed_s = {}
    columns = _fma_columns(sim, readout)
    for name in FMA_COLUMNS:
        engaged, armed = columns[name]
        signature = _signature(engaged, armed)
        if s.fma_changed_s.get(name + ":text") == signature:
            continue
        seen_before = (name + ":text") in s.fma_changed_s
        s.fma_changed_s[name + ":text"] = signature
        # Do not box the first look at a column, or every flight would begin
        # with all five boxed for having come into existence.
        if seen_before:
            s.fma_changed_s[name] = s.elapsed_s


def fma(sim, readout=None):
    """The Flight Mode Annunciator: five columns of what is flying this thing.

    Returns {column: {"engaged": (text, colour) | None,
                      "armed":   (text, colour) | None,
                      "boxed":   bool}}.

    The box is the point. A mode change on an Airbus is announced by a white
    rectangle drawn round the column for ten seconds, and a pilot's scan is
    built around noticing it -- so a mode that changes without one has, as far
    as the crew is concerned, changed silently. The moment of the change is
    recorded by `note_mode_changes`; this only reads it, so a display may call
    it as often as it likes.
    """
    s = sim.state
    columns = _fma_columns(sim, readout)
    changed = s.fma_changed_s or {}
    out = {}
    for name in FMA_COLUMNS:
        engaged, armed = columns[name]
        since = changed.get(name)
        # A column that has changed since the box was last timed is boxed
        # whatever the clock says -- otherwise a display that runs ahead of the
        # recorder shows the new mode without the box announcing it.
        stale = changed.get(name + ":text") != _signature(engaged, armed)
        out[name] = {
            "engaged": engaged,
            "armed": armed,
            "boxed": stale or (since is not None and (s.elapsed_s - since) < FMA_BOX_S),
        }
    return out


def fma_text(sim, readout=None):
    """The annunciator as one line, for the front end that has no pixels."""
    annunciator = fma(sim, readout)
    parts = []
    for name in FMA_COLUMNS:
        column = annunciator[name]
        engaged, armed = column["engaged"], column["armed"]
        if engaged is None and armed is None:
            continue
        # Both rows, because an armed mode is the annunciator saying what
        # happens next, and dropping it leaves the pilot to guess.
        text = " ".join(
            bit for bit in (
                engaged[0] if engaged else "",
                "({})".format(armed[0]) if armed else "",
            ) if bit
        )
        parts.append("[{}]".format(text) if column["boxed"] else text)
    return "  ".join(parts) if parts else "—"
