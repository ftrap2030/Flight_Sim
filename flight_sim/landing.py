"""Approach guidance, touchdown assessment and the rollout.

Until now a flight could only end badly: the terminal states were a crash, a
break-up, or quitting. This is the other ending.

Ground contact is no longer one question ("am I below the terrain?") but two:
where did I touch, and how. Touching down anywhere but a runway is still a
crash. Touching down *on* one is judged the way a real arrival is judged --
sink rate first, then speed against Vref, then how straight and how centred --
and it can still go wrong.
"""

import math
from dataclasses import dataclass

from . import atmosphere as atm
from . import failures
from . import fbw
from .airfield import FT_PER_NM

# Sink rate bands at touchdown, in feet per minute.
GREASER_FPM = 60.0
NORMAL_FPM = 240.0
FIRM_FPM = 600.0
HARD_FPM = 900.0  # beyond this the gear does not survive

# Everything else that can spoil an arrival.
MAX_TOUCHDOWN_BANK_DEG = 8.0  # further and a wingtip or a pod strikes first
MAX_TOUCHDOWN_CRAB_DEG = 20.0  # landing sideways collapses the gear
FAST_APPROACH_FACTOR = 1.35  # of Vref: survivable, but you will need the runway

# The glidepath every instrument approach flies.
GLIDESLOPE_DEG = 3.0
APPROACH_RANGE_NM = 20.0
# A real glidepath does not aim at the threshold -- it aims at a touchdown point
# some way down the runway, which is what gives a threshold crossing height of
# about fifty feet. Aiming at the threshold makes the guidance run out exactly
# where the aircraft still has the last fifty feet to descend, and it floats.
AIM_POINT_FT = 1000.0
MAX_LOCALISER_DEG = 12.0
MAX_APPROACH_CRAB_DEG = 45.0

# Rollout
BRAKING_FRICTION = 0.30  # dry runway, full braking
ROLLING_FRICTION = 0.02  # wheels free
REVERSE_THRUST_FRACTION = 0.45
STOPPED_KT = 25.0


@dataclass(frozen=True)
class Touchdown:
    """What happened at the moment the wheels met the runway."""

    field_ident: str
    field_name: str
    grade: str
    survivable: bool
    sink_rate_fpm: float
    ias_kt: float
    vref_kt: float
    speed_ratio: float
    centreline_ft: float
    crab_deg: float
    bank_deg: float
    along_ft: float
    remaining_ft: float
    reason: str = ""

    def summary(self):
        return (
            "{} at {} — {:,.0f} fpm, {:,.0f} kt ({:.0f}% of Vref), "
            "{:,.0f} ft off the centreline, {:.0f}° of crab, "
            "{:,.0f} ft of runway left".format(
                self.grade,
                self.field_name,
                self.sink_rate_fpm,
                self.ias_kt,
                self.speed_ratio * 100.0,
                abs(self.centreline_ft),
                abs(self.crab_deg),
                self.remaining_ft,
            )
        )


def vref_kt(sim):
    """Reference approach speed, in the current configuration.

    The number itself lives in `fbw.characteristic_speeds`, with the rest of the
    speed tape, because the grader and the PFD must not be able to disagree
    about how fast an approach was flown.
    """
    return fbw.characteristic_speeds(sim).vref


def grade_touchdown(sim, field, readout, on_runway=True):
    """Judge an arrival. Returns a Touchdown.

    Order matters: the things that destroy the aircraft outright are checked
    before the things that merely embarrass the pilot.
    """
    state = sim.state
    direction = field.landing_direction_for_heading(state.heading_deg)
    along, across = field.frame_for(state.x_nm, state.y_nm, direction)
    crab = _wrap180(state.heading_deg - direction)
    sink = abs(readout.vertical_speed_fpm)
    reference = vref_kt(sim)
    ratio = readout.ias_kt / max(reference, 1.0)
    remaining = max(0.0, field.runway_length_ft - along)

    def make(grade, survivable, reason=""):
        return Touchdown(
            field_ident=field.ident,
            field_name=field.name,
            grade=grade,
            survivable=survivable,
            sink_rate_fpm=sink,
            ias_kt=readout.ias_kt,
            vref_kt=reference,
            speed_ratio=ratio,
            centreline_ft=across,
            crab_deg=crab,
            bank_deg=state.bank_deg,
            along_ft=along,
            remaining_ft=remaining,
            reason=reason,
        )

    if not on_runway:
        # Off the paving but on the airfield. Survivable if it was gentle;
        # otherwise the gear digs into unprepared ground and folds.
        gentle = sink < FIRM_FPM and abs(state.bank_deg) <= MAX_TOUCHDOWN_BANK_DEG
        return make(
            "runway excursion",
            gentle,
            "" if gentle else
            "left the paved surface at {:,.0f} feet a minute and {:.0f} degrees "
            "of bank; the gear folded in the soft ground".format(
                sink, abs(state.bank_deg)
            ),
        )

    if sink >= HARD_FPM:
        return make(
            "gear collapse", False,
            "arrived at {:,.0f} feet a minute; the gear is rated for nothing "
            "like it".format(sink),
        )
    if abs(state.bank_deg) > MAX_TOUCHDOWN_BANK_DEG:
        return make(
            "wingtip strike", False,
            "{:.0f} degrees of bank at touchdown put the wingtip into the "
            "runway before the wheels".format(abs(state.bank_deg)),
        )
    if abs(crab) > MAX_TOUCHDOWN_CRAB_DEG:
        return make(
            "gear collapse", False,
            "touched down {:.0f} degrees out of line with the runway; the gear "
            "is not built to be dragged sideways".format(abs(crab)),
        )

    if sink < GREASER_FPM:
        grade = "greaser"
    elif sink < NORMAL_FPM:
        grade = "normal landing"
    elif sink < FIRM_FPM:
        grade = "firm landing"
    else:
        grade = "hard landing"
    return make(grade, True)


@dataclass(frozen=True)
class Approach:
    """Where you are relative to a runway you might land on."""

    field: object
    direction_deg: float
    distance_nm: float
    along_ft: float
    across_ft: float
    glideslope_dev_ft: float
    localiser_dev_deg: float
    target_altitude_ft: float
    on_approach: bool

    @property
    def high(self):
        return self.glideslope_dev_ft > 0


def approach_guidance(sim, field=None):
    """Guidance for the runway being approached, or None if none is.

    Chooses the runway direction the aircraft is actually lined up to use, so a
    field can be landed from either end without the pilot declaring which.
    """
    state = sim.state
    if field is None:
        field = sim.airfields.nearest(state.x_nm, state.y_nm, APPROACH_RANGE_NM)
    if field is None:
        return None

    direction = field.landing_direction_for_heading(state.heading_deg)
    along, across = field.frame_for(state.x_nm, state.y_nm, direction)

    # Distance to the threshold: negative `along` means still short of it.
    to_threshold_ft = -along
    distance_nm = math.hypot(to_threshold_ft, across) / FT_PER_NM

    # Height is flown against the aim point, so the path keeps descending across
    # the threshold and meets the runway where it should.
    to_aim_ft = AIM_POINT_FT - along
    target = field.elevation_ft + max(0.0, to_aim_ft) * math.tan(
        math.radians(GLIDESLOPE_DEG)
    )
    deviation = state.altitude_ft - target
    # Guard the singularity over the threshold itself, where a foot of lateral
    # offset would otherwise read as tens of degrees.
    localiser = math.degrees(
        math.atan2(across, max(abs(to_threshold_ft), 500.0))
    )

    return Approach(
        field=field,
        direction_deg=direction,
        distance_nm=distance_nm,
        along_ft=along,
        across_ft=across,
        glideslope_dev_ft=deviation,
        localiser_dev_deg=localiser,
        target_altitude_ft=target,
        # "On approach" has to mean *positioned to land on this runway*, not
        # merely somewhere near it. A cruising aircraft five miles abeam a field
        # is not on its approach, and showing it guidance is noise.
        # Guidance runs until the far end of the runway, not until the aim
        # point. Ending it at the aim point is a race the aircraft can lose: a
        # touch high on short final and the guidance quits with fifty feet still
        # to descend, leaving whatever catches it next to float the length of
        # the runway. Past the aim point the target is simply runway level, so
        # the guidance keeps flying it down onto the concrete.
        on_approach=(
            along < field.runway_length_ft
            and distance_nm < APPROACH_RANGE_NM
            and abs(localiser) < MAX_LOCALISER_DEG
            and abs(_wrap180(state.heading_deg - direction)) < MAX_APPROACH_CRAB_DEG
        ),
    )


def ground_forces(sim):
    """(thrust, drag, friction) on the runway, in newtons.

    One place, because the takeoff roll and the landing rollout are the same
    physics read in opposite directions, and two copies of it would drift.

    Friction acts on the weight the *wheels* are carrying, not on the weight of
    the aeroplane. At touchdown the wing is still doing most of the work and the
    brakes have very little to bite on; the load transfers to the wheels as the
    speed decays and lift falls with the square of it. That is why you cannot
    stop a fast aeroplane on the brakes alone, and why the spoilers come out
    first -- they destroy the lift, which is what puts the weight where the
    brakes can use it. Forgetting them costs about fifteen hundred feet.
    """
    state = sim.state
    aero = sim._aero_state()
    braking = BRAKING_FRICTION * failures.braking_factor(state)
    mu = ROLLING_FRICTION + (braking - ROLLING_FRICTION) * state.brakes
    on_wheels = max(0.0, state.mass_kg * atm.G0 - aero.lift)

    thrust = aero.thrust
    if state.reverse_thrust:
        thrust = -REVERSE_THRUST_FRACTION * aero.thrust
    return thrust, aero.drag, mu * on_wheels


def rollout_deceleration(sim):
    """How hard the aircraft is slowing on the ground, in m/s^2.

    The rollout view of `ground_forces`: everything that takes energy out,
    reverse thrust counted as a positive contribution to stopping.
    """
    thrust, drag, friction = ground_forces(sim)
    decel = (drag + friction) / sim.state.mass_kg
    if sim.state.reverse_thrust:
        decel += -thrust / sim.state.mass_kg
    return decel


def _wrap180(degrees_value):
    return (degrees_value + 180.0) % 360.0 - 180.0
