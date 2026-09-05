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
        if state.ap_heading_deg is not None:
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
        # it; a raw bank command is hand-flying and drops it.
        if command_kind == "bank_set":
            state.ap_heading_deg = None
        state.ap_approach = False
    elif command_kind in ("throttle_set", "throttle_delta"):
        state.ap_speed_kt = None


def update(sim, dt):
    """Run the engaged channels for one substep."""
    state = sim.state
    if not state.ap_engaged or state.on_ground:
        return

    readout = None
    if state.ap_approach:
        # Approach guidance is expensive -- terrain scans and runway geometry --
        # and changes on a scale of seconds, not hundredths. Re-solve it a few
        # times a second and hold the demand in between, as the real box does.
        sim._ap_clock = getattr(sim, "_ap_clock", 0.0) + dt
        if sim._ap_clock >= APPROACH_UPDATE_S or getattr(sim, "_ap_readout", None) is None:
            sim._ap_clock = 0.0
            sim._ap_readout = sim.readout()
        readout = sim._ap_readout
        _fly_approach(sim, readout)
    else:
        if state.ap_altitude_ft is not None:
            _hold_altitude(sim)
        elif state.ap_vs_fpm is not None:
            _hold_vertical_speed(sim, state.ap_vs_fpm)
        if state.ap_heading_deg is not None:
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
