"""Engine state: fan speed, the spool, and the numbers an ECAM shows.

Until now the thrust levers produced an instant change in everything. Moving
one rewrote the thrust in the same substep, which makes an airliner feel like a
model aeroplane: a go-around is a keystroke, and a late one is no different from
an early one. On a real high-bypass turbofan the fan takes several seconds to
come up, and almost everything that makes a jet feel heavy to fly follows from
that one lag.

So N1 is state. The levers ask for a fan speed, the fan chases it -- **slower up
than down**, which is what a turbofan does and what makes a late go-around
frightening -- and thrust follows the fan rather than the lever.

The constraint that makes this safe: at equilibrium the fan has caught up with
the levers, so *steady-state thrust is unchanged* and the calibrated cruise
figures are exactly as they were. Only the transient moves.

Three kinds of number live here and must not be confused, which is the same
distinction `aircraft.py` draws between published data and calibrated
coefficients:

* **N1** is *state*. It flies the aeroplane, because thrust is derived from it.
* **N2** is *derived*, and is shown because a real E/WD shows it.
* **EGT** is *derived and decorative*. It is a plausible function of fan speed
  and the air going in, it is not published data for any of these types, and it
  must never feed a force. If it ever does, it has become a fudge factor with a
  temperature's name on it.
"""

from dataclasses import dataclass

from . import atmosphere as atm

# How long the fan takes to chase the levers, as a first-order time constant.
# Spooling up is the slow direction: the engine has to accelerate its own
# rotating mass against the air it is pumping, where closing the throttle simply
# stops feeding it.
SPOOL_UP_S = 3.2
SPOOL_DOWN_S = 1.6

# The fan speed range, in percent. Idle is what the engine turns at with the
# levers closed; 100 is the takeoff rating.
N1_IDLE_PCT = 21.0
N1_MAX_PCT = 100.0

# Thrust does not go as N1 -- it goes roughly as the square of it, because
# thrust is momentum flux and the fan is moving air proportionally to its speed
# in two ways at once. The exponent is what makes the bottom half of the lever
# range feel dead, which is also true of the real thing.
N1_THRUST_EXPONENT = 2.0

# Derived, for display only. Idle is around 60% N2 and takeoff around 100%.
N2_IDLE_PCT = 58.0

# EGT: ambient plus a rise that is steep off idle and flattens toward takeoff,
# which is the shape a turbofan's exhaust temperature actually has. Fitted so
# that flight idle sits near 400 C and the takeoff rating near 850 C, over the
# air going in -- so the same lever setting reads cooler in cold air, as it does.
EGT_RISE_C = 835.0
EGT_EXPONENT = 0.815
EGT_CAUTION_C = 875.0
EGT_LIMIT_C = 950.0

# What the two thresholds above mean, as a name the displays can read. They had
# no reader in Python at all: the model declared the limits and the browser
# re-declared them as its own literals and made the decision, which is the
# ownership rule backwards. A band rather than a colour because the colour is a
# display's choice -- the E/WD paints it amber, a text panel has no colour and
# marks it some other way -- while *where* the thresholds sit is the model's.
EGT_NORMAL, EGT_CAUTION, EGT_OVER_LIMIT = "normal", "caution", "limit"


def commanded_n1_pct(craft, throttle_pct):
    """The fan speed the levers are asking for.

    The idle stop is expressed as a thrust fraction on the type, so it is
    converted through the same curve that turns fan speed into thrust -- which
    keeps one relationship between the two rather than two that must agree.
    """
    demand = max(craft.idle_thrust_fraction, throttle_pct / 100.0)
    return n1_for_thrust_fraction(demand)


def thrust_fraction_for_n1(n1_pct):
    """What fraction of the takeoff rating this fan speed is making."""
    span = N1_MAX_PCT - N1_IDLE_PCT
    above_idle = max(0.0, n1_pct - N1_IDLE_PCT) / span
    return above_idle ** N1_THRUST_EXPONENT


def n1_for_thrust_fraction(fraction):
    """The inverse: the fan speed that makes this fraction of the rating."""
    span = N1_MAX_PCT - N1_IDLE_PCT
    return N1_IDLE_PCT + span * max(0.0, fraction) ** (1.0 / N1_THRUST_EXPONENT)


def settled_n1(craft, throttle_pct):
    """Fan speed with the spool already caught up.

    Used to start a flight, and to set the engines when a state is placed rather
    than flown into -- an aeroplane trimmed at cruise has engines that got there
    some time ago.
    """
    return commanded_n1_pct(craft, throttle_pct)


def spool(state, craft, dt):
    """Advance each engine's fan speed one substep.

    A failed engine runs down to nothing; a running one chases the levers.
    """
    if not state.engine_n1_pct or len(state.engine_n1_pct) != craft.engine_count:
        state.engine_n1_pct = [
            settled_n1(craft, state.throttle_pct)
        ] * craft.engine_count

    failed = set(state.engines_failed or ())
    fuel_starved = not state.engines_running or state.fuel_kg <= 0.0
    target_running = commanded_n1_pct(craft, state.throttle_pct)

    for index in range(craft.engine_count):
        stopped = index in failed or fuel_starved
        target = 0.0 if stopped else target_running
        now = state.engine_n1_pct[index]
        # A windmilling engine winds down on the fast constant: nothing is
        # driving it, so it is the same case as closing the throttle.
        tau = SPOOL_UP_S if target > now else SPOOL_DOWN_S
        state.engine_n1_pct[index] = now + (target - now) * min(1.0, dt / tau)


def total_thrust_fraction(state, craft):
    """The whole aeroplane's thrust as a fraction of its takeoff rating.

    Summed over engines rather than multiplied by a count, so a failed engine is
    absent because its fan has stopped rather than because it was subtracted.
    """
    if not state.engine_n1_pct:
        return 0.0
    return sum(thrust_fraction_for_n1(n1) for n1 in state.engine_n1_pct) / (
        craft.engine_count
    )


def egt_band(egt_value_c):
    """Which side of the caution and the limit this exhaust temperature is."""
    if egt_value_c > EGT_LIMIT_C:
        return EGT_OVER_LIMIT
    if egt_value_c > EGT_CAUTION_C:
        return EGT_CAUTION
    return EGT_NORMAL


def egt_c(n1_pct, altitude_ft):
    """Exhaust gas temperature. Derived, and for display only -- see the module
    docstring. Ambient plus a rise that climbs steeply with fan speed."""
    ambient_c = atm.temperature_k(altitude_ft) - 273.15
    fraction = max(0.0, n1_pct / N1_MAX_PCT)
    return ambient_c + EGT_RISE_C * fraction ** EGT_EXPONENT


@dataclass(frozen=True)
class EngineReadout:
    """One engine, as the E/WD shows it."""

    index: int
    n1_pct: float
    n2_pct: float
    egt_c: float
    egt_band: str
    fuel_flow_kgh: float
    thrust_n: float
    failed: bool
    fire: bool


def readouts(sim):
    """Every engine's instruments.

    Both front ends read this, so neither works out what N1 is -- the same
    arrangement as the speed tape and the annunciator.
    """
    s = sim.state
    craft = sim.aircraft
    failed = set(s.engines_failed or ())
    fires = set(getattr(s, "engines_on_fire", None) or ())
    available = sim._thrust_available_n()
    out = []
    for index in range(craft.engine_count):
        n1 = s.engine_n1_pct[index] if s.engine_n1_pct else 0.0
        thrust = available * thrust_fraction_for_n1(n1) / craft.engine_count
        out.append(
            EngineReadout(
                index=index,
                n1_pct=n1,
                n2_pct=N2_IDLE_PCT + (100.0 - N2_IDLE_PCT) * max(0.0, n1) / N1_MAX_PCT
                if n1 > 0.5
                else 0.0,
                egt_c=egt_c(n1, s.altitude_ft),
                egt_band=egt_band(egt_c(n1, s.altitude_ft)),
                fuel_flow_kgh=craft.tsfc * thrust * 3600.0,
                thrust_n=thrust,
                failed=index in failed,
                fire=index in fires,
            )
        )
    return out
