"""International Standard Atmosphere model and airspeed conversions.

Implements the ISA troposphere (sea level to 36,089 ft) and the lower
stratosphere (isothermal above the tropopause), which comfortably covers the
service ceiling of every airliner in the fleet.

All functions take altitude in feet -- the unit the cockpit actually works in --
and return SI, which is the unit the force equations work in.
"""

import math

# ---------------------------------------------------------------------------
# ISA constants
# ---------------------------------------------------------------------------

T0_K = 288.15  # sea-level standard temperature
P0_PA = 101325.0  # sea-level standard pressure
RHO0 = 1.225  # sea-level standard density, kg/m^3
R_SPECIFIC = 287.05287  # specific gas constant for dry air, J/(kg*K)
GAMMA_AIR = 1.4  # ratio of specific heats
G0 = 9.80665  # standard gravity, m/s^2

LAPSE_K_PER_FT = 1.98120e-3  # 6.5 K/km expressed per foot
TROPOPAUSE_FT = 36089.24  # 11,000 m
TROPOPAUSE_M = 11000.0
T_TROPOPAUSE_K = 216.65
P_TROPOPAUSE_PA = 22632.06

# The pressure exponent g0 / (lapse * R) for the troposphere.
_PRESSURE_EXPONENT = 5.25588

# ---------------------------------------------------------------------------
# Unit conversions
# ---------------------------------------------------------------------------

M_PER_FT = 0.3048
FT_PER_M = 1.0 / M_PER_FT
MS_PER_KT = 0.514444444
KT_PER_MS = 1.0 / MS_PER_KT
FPM_PER_MS = 196.850393701
NM_PER_M = 1.0 / 1852.0
M_PER_NM = 1852.0


def temperature_k(altitude_ft):
    """Static air temperature in kelvin at a given pressure altitude."""
    if altitude_ft < TROPOPAUSE_FT:
        return T0_K - LAPSE_K_PER_FT * altitude_ft
    return T_TROPOPAUSE_K


def pressure_pa(altitude_ft):
    """Static pressure in pascals at a given pressure altitude."""
    if altitude_ft < TROPOPAUSE_FT:
        return P0_PA * (temperature_k(altitude_ft) / T0_K) ** _PRESSURE_EXPONENT
    # Isothermal layer: pressure decays exponentially with geopotential height.
    height_above_trop_m = (altitude_ft - TROPOPAUSE_FT) * M_PER_FT
    return P_TROPOPAUSE_PA * math.exp(
        -G0 * height_above_trop_m / (R_SPECIFIC * T_TROPOPAUSE_K)
    )


def density(altitude_ft):
    """Air density in kg/m^3 from the ideal gas law."""
    return pressure_pa(altitude_ft) / (R_SPECIFIC * temperature_k(altitude_ft))


def density_ratio(altitude_ft):
    """Sigma -- density relative to sea level. Drives thrust lapse and IAS."""
    return density(altitude_ft) / RHO0


def speed_of_sound_ms(altitude_ft):
    """Speed of sound in m/s; a function of temperature alone."""
    return math.sqrt(GAMMA_AIR * R_SPECIFIC * temperature_k(altitude_ft))


def tas_to_ias(tas_ms, altitude_ft):
    """True airspeed to indicated airspeed.

    Uses the equivalent-airspeed relation IAS = TAS * sqrt(sigma). This ignores
    compressibility correction, which is a fraction of a knot at the speeds and
    altitudes an airliner actually flies, and keeps the model transparent.
    """
    return tas_ms * math.sqrt(density_ratio(altitude_ft))


def ias_to_tas(ias_ms, altitude_ft):
    """Indicated airspeed to true airspeed."""
    return ias_ms / math.sqrt(density_ratio(altitude_ft))


def mach(tas_ms, altitude_ft):
    """Mach number for a true airspeed at altitude."""
    return tas_ms / speed_of_sound_ms(altitude_ft)


def mach_to_tas(mach_number, altitude_ft):
    """True airspeed in m/s for a given Mach number at altitude."""
    return mach_number * speed_of_sound_ms(altitude_ft)
