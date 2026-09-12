"""A controller who expects you to do what you are told.

The rule this module is written against is `failures.py`'s: *a failure whose
only consequence is a message about itself is not a failure, it is a label*.
The same is true of a clearance. So nothing here merely prints:

* the assigned level is **measured against** -- drift off it and you are told,
  and the drift is counted;
* descent is **withheld** until you are cleared for it, so a managed descent
  armed too early sits in ALT CRZ with the controller's reason for it;
* the approach sequence is read off the **traffic model**, so "number two,
  follow the A330" is a real aeroplane that is really ahead of you;
* every one of those ends up in the debrief, which is where being told off
  turns into something you can do better next time.

The controller is state rather than a pure function -- it responds to you, so
it has to remember what it last said -- and that state lives on `FlightState`
like the turbulence filter and the route, because a session resumed from disk
must not forget that it was told to maintain FL230.

What is deliberately absent: a voice, a frequency, a handoff between sectors,
and anything resembling a real phraseology parser. Those would be scenery. The
clearances here are the ones that change what the aeroplane may do.
"""

import math

from . import atmosphere as atm
from . import traffic as traffic_module

# How far off an assigned level counts as having left it. Real tolerance is
# 300 ft; this is the same, and the ten seconds is so that a gust does not
# earn a rebuke.
LEVEL_TOLERANCE_FT = 300.0
LEVEL_GRACE_S = 10.0
# Longer, for an instruction that has not been acted on at all: a heavy
# aeroplane takes a while to get going, and chasing it after ten seconds
# would be chasing physics rather than the pilot.
INSTRUCTION_GRACE_S = 40.0
# How many times a controller will chase the same clearance before leaving it.
# Without a cap it said the same thing every fifty seconds for the whole
# flight, which is the cry-wolf failure the terrain warning already taught:
# a call that never stops is a call nobody hears. The deviation is still
# counted -- it goes in the debrief, which is where it belongs.
MAX_CHASES = 3

# Descent is cleared this far before the profile needs it, so the clearance
# arrives in time to be flown rather than the moment it is already late.
DESCENT_CLEARANCE_MARGIN_NM = 12.0

# Inside this, you are on the approach as far as the sequence is concerned.
SEQUENCE_RANGE_NM = 35.0

# A controller does not repeat itself every tick.
REPEAT_INTERVAL_S = 45.0

LEVEL, DESCENT, APPROACH, SEQUENCE = "level", "descent", "approach", "sequence"


class Message:
    """One transmission, with what it is about and when it was said."""

    def __init__(self, kind, text, elapsed_s, urgent=False):
        self.kind = kind
        self.text = text
        self.elapsed_s = elapsed_s
        self.urgent = urgent

    def to_dict(self):
        return {"kind": self.kind, "text": self.text,
                "elapsed_s": self.elapsed_s, "urgent": self.urgent}

    @classmethod
    def from_dict(cls, data):
        return cls(data["kind"], data["text"], data["elapsed_s"],
                   data.get("urgent", False))

    def __repr__(self):
        return "<{} {!r}>".format(self.kind, self.text)


def semicircular_level_ft(track_deg, wanted_ft):
    """The nearest level this track is allowed to use.

    Eastbound odd, westbound even -- the oldest rule in the air, and the reason
    two aeroplanes converging head-on are a thousand feet apart without anybody
    having to arrange it. Below the transition it does not apply, and neither
    does this.
    """
    if wanted_ft < 5000.0:
        return round(wanted_ft / 500.0) * 500.0
    eastbound = 0.0 <= (track_deg % 360.0) < 180.0
    # Odd thousands eastbound, even thousands westbound.
    base = math.floor(wanted_ft / 1000.0)
    if (base % 2 == 1) != eastbound:
        base -= 1
    return max(5000.0, base * 1000.0)


def callsign_for(sim):
    """What the controller calls you. Derived, so both builds say the same."""
    craft = sim.aircraft
    return "{}{:02d}".format(craft.icao_type, (sim.state.seed % 89) + 10)


def _say(sim, kind, text, urgent=False):
    """Add a transmission, unless the same thing was said a moment ago."""
    state = sim.state
    for message in reversed(state.atc_messages[-6:]):
        if message.get("kind") != kind:
            continue
        # By kind, not by wording. The deviation call carries the number of
        # feet in it, which changes every tick -- so matching on the text
        # matched nothing and the controller read the same rebuke out forty
        # times in a row. A warning that fires constantly is one nobody reads,
        # which is the same reason the terrain warning is suppressed on a
        # configured approach.
        if state.elapsed_s - message.get("elapsed_s", 0.0) < REPEAT_INTERVAL_S:
            return None
        break
    message = Message(kind, text, state.elapsed_s, urgent)
    state.atc_messages.append(message.to_dict())
    del state.atc_messages[:-40]
    return message


def inbound_sequence(sim, field_ident, radius_nm=SEQUENCE_RANGE_NM):
    """Who else is going to the same runway, and how many are ahead.

    Read straight off the traffic model -- so "number two" is a real aeroplane
    that is really closer to the field than you are, and looking out of the
    window finds it.
    """
    state = sim.state
    field = sim.airfields.by_ident(field_ident, state.x_nm, state.y_nm)
    if field is None:
        return (1, None)
    mine = math.hypot(field.x_nm - state.x_nm, field.y_nm - state.y_nm)
    if mine > radius_nm:
        return (1, None)

    ahead = []
    for contact in sim.traffic_near(radius_nm=radius_nm * 2.0):
        if contact.destination != field_ident:
            continue
        theirs = math.hypot(field.x_nm - contact.x_nm, field.y_nm - contact.y_nm)
        if theirs < mine:
            ahead.append((theirs, contact))
    ahead.sort(key=lambda pair: pair[0])
    return (len(ahead) + 1, ahead[-1][1] if ahead else None)


def update(sim, tick_s=10.0):
    """Run the controller for one tick. Returns any new transmissions."""
    state = sim.state
    fresh = []

    destination = sim.route.destination if sim.route else None
    if destination is None or state.on_ground:
        # Nobody to talk to about nothing. Forget the clearances rather than
        # keep enforcing a level for a flight that no longer has a plan.
        state.atc_cleared_altitude_ft = None
        state.atc_descent_cleared = False
        state.atc_sequence = 0
        return fresh

    plan = sim.descent_guidance()
    callsign = callsign_for(sim)

    # --- the cruise level -------------------------------------------------
    if state.atc_cleared_altitude_ft is None:
        wanted = state.ap_altitude_ft or state.altitude_ft
        level = semicircular_level_ft(state.heading_deg, wanted)
        state.atc_cleared_altitude_ft = level
        state.atc_level_reached = abs(state.altitude_ft - level) <= LEVEL_TOLERANCE_FT
        if state.atc_level_reached:
            text = "{}, maintain {}.".format(callsign, _level_text(level))
        else:
            # The semicircular rule may well not be where the aeroplane is:
            # westbound at 23,000 the legal level is 22,000. Tell it to move
            # rather than book it for being where it already was.
            going = "climb" if level > state.altitude_ft else "descend"
            text = "{}, {} to {}.".format(callsign, going, _level_text(level))
        message = _say(sim, LEVEL, text)
        if message:
            fresh.append(message)

    # --- staying on it ----------------------------------------------------
    error = state.altitude_ft - state.atc_cleared_altitude_ft
    if abs(error) <= LEVEL_TOLERANCE_FT:
        # Reaching it once is what makes leaving it a deviation. Before that
        # the aeroplane is on its way there, and the controller knows it.
        state.atc_level_reached = True

    if state.atc_descent_cleared:
        # "Descend at your discretion" makes the clearance a *floor*, not a
        # target: anywhere between the level and the field is where you are
        # allowed to be, and only going below it is a deviation. Treated as a
        # target instead, an aeroplane correctly flying its profile was
        # permanently "1,668 feet above its cleared level".
        deviating = error < -LEVEL_TOLERANCE_FT
        complaint = "{}, you are below your cleared altitude.".format(callsign)
    elif state.atc_level_reached:
        deviating = abs(error) > LEVEL_TOLERANCE_FT
        complaint = "{}, you are {:,.0f} feet {} your cleared level. Say " \
                    "intentions.".format(callsign, abs(error),
                                         "above" if error > 0 else "below")
    else:
        # Still on the way to it. Only a complaint if the aeroplane is not
        # actually going there -- an instruction ignored is a deviation too,
        # and staying quiet about it would make the clearance a label.
        going = state.tas_ms * math.sin(math.radians(state.gamma_deg)) * atm.FPM_PER_MS
        toward = going * (1.0 if error < 0.0 else -1.0)
        deviating = toward < 100.0
        complaint = "{}, confirm you are {} to {}.".format(
            callsign, "climbing" if error < 0 else "descending",
            _level_text(state.atc_cleared_altitude_ft))

    if deviating:
        state.atc_off_level_s += tick_s
        grace = LEVEL_GRACE_S if state.atc_level_reached else INSTRUCTION_GRACE_S
        if state.atc_off_level_s > grace and state.atc_chases < MAX_CHASES:
            message = _say(sim, LEVEL, complaint, urgent=True)
            if message:
                state.atc_chases += 1
                fresh.append(message)
        # Counted whether or not anything was said, because the debrief's
        # question is how long the aeroplane was off its clearance, not how
        # many times it was told.
        state.atc_deviation_s += tick_s
    else:
        state.atc_off_level_s = 0.0

    # --- descent ----------------------------------------------------------
    if plan is not None and not state.atc_descent_cleared:
        due = plan.distance_to_go_nm <= plan.top_of_descent_nm + DESCENT_CLEARANCE_MARGIN_NM
        if due:
            state.atc_descent_cleared = True
            state.atc_chases = 0
            # To a round figure a controller would actually say, and never
            # below the ground: the field elevation rounded up to the next
            # five hundred feet.
            field_ft = destination.elevation_ft or 0.0
            state.atc_cleared_altitude_ft = math.ceil(field_ft / 500.0) * 500.0
            message = _say(sim, DESCENT, "{}, descend at your discretion, "
                           "{} nautical miles to run.".format(
                               callsign, int(round(plan.distance_to_go_nm))))
            if message:
                fresh.append(message)

    # --- the sequence -----------------------------------------------------
    position, ahead = inbound_sequence(sim, destination.ident)
    if position != state.atc_sequence:
        state.atc_sequence = position
        if position == 1:
            text = "{}, you are number one for {}.".format(
                callsign, destination.ident)
        elif ahead is not None:
            text = "{}, number {} for {}, follow the {} ahead of you.".format(
                callsign, position, destination.ident,
                _type_name(sim, ahead.aircraft_key))
        else:
            text = "{}, number {} for {}.".format(
                callsign, position, destination.ident)
        message = _say(sim, SEQUENCE, text)
        if message:
            fresh.append(message)

    return fresh


def _type_name(sim, aircraft_key):
    from . import aircraft as fleet
    craft = fleet.FLEET_BY_KEY.get(aircraft_key)
    return craft.name if craft else aircraft_key


def _level_text(altitude_ft):
    if altitude_ft >= 5000.0:
        return "flight level {:03.0f}".format(altitude_ft / 100.0)
    return "{:,.0f} feet".format(altitude_ft)


def clearance(sim):
    """What the aeroplane is currently cleared to do. Model data.

    Both front ends read this rather than each deciding what the controller
    must have meant -- the same rule the speed tape and the FMA follow.
    """
    state = sim.state
    if state.atc_cleared_altitude_ft is None:
        return None
    error = state.altitude_ft - state.atc_cleared_altitude_ft
    if state.atc_descent_cleared:
        # A floor once descent is cleared -- above it is where you are
        # supposed to be on the way down.
        on_clearance = error >= -LEVEL_TOLERANCE_FT
    elif state.atc_level_reached:
        on_clearance = abs(error) <= LEVEL_TOLERANCE_FT
    else:
        on_clearance = True  # still climbing or descending to it
    return {
        "callsign": callsign_for(sim),
        "cleared_altitude_ft": state.atc_cleared_altitude_ft,
        "level_text": _level_text(state.atc_cleared_altitude_ft),
        "descent_cleared": state.atc_descent_cleared,
        "sequence": state.atc_sequence,
        "deviation_ft": error,
        "on_clearance": on_clearance,
    }


def messages(sim, limit=6):
    """The last few transmissions, newest last."""
    return [Message.from_dict(m) for m in sim.state.atc_messages[-limit:]]
