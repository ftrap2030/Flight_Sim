"""Airbus fleet definitions.

Each aircraft is a frozen dataclass of published geometry, mass and engine data
plus the handful of aerodynamic coefficients the point-mass model needs. The
handling differences between variants are *emergent*: the A320neo climbs better
and burns less because its sharklets raise the aspect ratio and its LEAP engines
have a lower TSFC, not because a "nimbleness" number was typed in.
"""

import math
from dataclasses import dataclass, field

# TSFC values are in kg/(N*s). Multiply by 35,306 for the more familiar
# lb/(lbf*hr): the CFM56 works out at 0.59, the LEAP at 0.51, the Trents at
# ~0.43, all within the published range for those engines at cruise. They were
# derived by solving for the value that reproduces each type's published block
# fuel flow at its cruise altitude and Mach, then checked against the real
# engine figures -- so the fuel page agrees with the aircraft's actual numbers
# and the constants remain physically meaningful rather than fudge factors.


@dataclass(frozen=True)
class Aircraft:
    """Performance model for one airliner."""

    key: str
    name: str
    engines: str

    # Geometry
    wing_area_m2: float
    wing_span_m: float
    length_m: float

    # Mass, kg
    oew_kg: float
    mtow_kg: float
    payload_kg: float
    fuel_capacity_kg: float
    start_fuel_kg: float

    # Propulsion
    thrust_sl_n: float  # total static thrust, all engines, sea level
    tsfc: float  # kg of fuel per newton of thrust per second
    idle_thrust_fraction: float = 0.05

    # Aerodynamics
    cd_0: float = 0.022
    oswald_e: float = 0.80
    cl_0: float = 0.20
    cl_max_clean: float = 1.45
    alpha_crit_deg: float = 15.0
    mach_crit: float = 0.79
    wave_drag_k: float = 18.0

    # Envelope
    vmo_kt: float = 350.0  # max operating IAS
    mmo: float = 0.82  # max operating Mach
    ceiling_ft: float = 39000.0
    cruise_mach: float = 0.78

    # Control response -- what the pilot feels
    roll_rate_deg_s: float = 15.0
    pitch_rate_deg_s: float = 3.0

    handling: str = ""

    @property
    def aspect_ratio(self):
        """Span^2 / area. The single most important number for induced drag."""
        return self.wing_span_m ** 2 / self.wing_area_m2

    @property
    def cl_alpha(self):
        """Lift-curve slope per radian, corrected for finite span.

        Helmbold's equation for a finite wing; approaches the 2*pi thin-aerofoil
        result only as aspect ratio goes to infinity.
        """
        ar = self.aspect_ratio
        return (2.0 * math.pi * ar) / (2.0 + math.sqrt(ar ** 2 + 4.0))

    @property
    def induced_drag_factor(self):
        """k in CD = CD_0 + k * CL^2."""
        return 1.0 / (math.pi * self.aspect_ratio * self.oswald_e)

    @property
    def start_mass_kg(self):
        """Ramp mass for a typical mission: empty + payload + fuel."""
        return self.oew_kg + self.payload_kg + self.start_fuel_kg

    @property
    def cruise_speed_kt(self):
        """Published cruise TAS in knots, at a nominal FL350."""
        from . import atmosphere

        return atmosphere.mach_to_tas(self.cruise_mach, 35000.0) * atmosphere.KT_PER_MS

    def stall_speed_ias_ms(self, mass_kg, load_factor=1.0, flap_setting=0):
        """1g (or n-g) stall speed as an indicated airspeed, in m/s.

        Closed form from L = W: V = sqrt(2 * n * m * g / (rho0 * S * CL_max)).
        Because it is expressed as IAS, sea-level density is the correct term --
        that is exactly why stall speed reads the same on the ASI at any altitude.
        """
        from . import atmosphere

        cl_max = self.cl_max_for_flaps(flap_setting)
        numerator = 2.0 * load_factor * mass_kg * atmosphere.G0
        denominator = atmosphere.RHO0 * self.wing_area_m2 * cl_max
        return math.sqrt(numerator / denominator)

    def cl_max_for_flaps(self, flap_setting):
        """Maximum lift coefficient at a given flap detent (0-4)."""
        return self.cl_max_clean + FLAP_CL_BONUS[_clamp_flap(flap_setting)]

    def cd_0_for_config(self, flap_setting, gear_down, spoilers):
        """Parasite drag for the current configuration."""
        cd = self.cd_0 + FLAP_CD_PENALTY[_clamp_flap(flap_setting)]
        if gear_down:
            cd += 0.018
        if spoilers:
            cd += 0.030
        return cd


def _clamp_flap(setting):
    return max(0, min(len(FLAP_CL_BONUS) - 1, int(setting)))


# Flap detents 0 (clean), 1, 2, 3, FULL.
FLAP_CL_BONUS = [0.00, 0.40, 0.75, 1.05, 1.35]
FLAP_CD_PENALTY = [0.000, 0.008, 0.020, 0.038, 0.062]
FLAP_NAMES = ["UP", "1", "2", "3", "FULL"]

# Maximum indicated airspeed for each flap detent, knots (VFE).
FLAP_LIMIT_KT = [999.0, 230.0, 200.0, 185.0, 177.0]


# ---------------------------------------------------------------------------
# The fleet
# ---------------------------------------------------------------------------

A320 = Aircraft(
    key="a320",
    name="A320-200",
    engines="2 x CFM56-5B4 (120 kN each)",
    wing_area_m2=122.6,
    wing_span_m=34.10,  # wingtip fences, no sharklets
    length_m=37.57,
    oew_kg=42600.0,
    mtow_kg=78000.0,
    payload_kg=15000.0,
    fuel_capacity_kg=15200.0,
    start_fuel_kg=12000.0,
    thrust_sl_n=240200.0,
    tsfc=1.66e-5,  # ~0.59 lb/(lbf*hr) cruise
    cd_0=0.0199,
    oswald_e=0.82,
    mach_crit=0.790,
    vmo_kt=350.0,
    mmo=0.82,
    ceiling_ft=39000.0,
    cruise_mach=0.78,
    roll_rate_deg_s=15.0,
    pitch_rate_deg_s=3.2,
    handling=(
        "The classic narrowbody. Light, eager and honest -- rolls into a turn the "
        "instant you ask and holds an altitude with almost no attention. Shortest "
        "legs and the thirstiest of the A320 family, but the most forgiving thing "
        "in the fleet to hand-fly down a valley."
    ),
)

A320NEO = Aircraft(
    key="a320neo",
    name="A320neo",
    engines="2 x CFM LEAP-1A26 (121 kN each)",
    wing_area_m2=122.6,
    wing_span_m=35.80,  # sharklets: same area, more span
    length_m=37.57,
    oew_kg=44300.0,
    mtow_kg=79000.0,
    payload_kg=15000.0,
    fuel_capacity_kg=15200.0,
    start_fuel_kg=12000.0,
    thrust_sl_n=241200.0,
    tsfc=1.43e-5,  # ~0.51 lb/(lbf*hr): ~14% better than the CFM56
    cd_0=0.0195,
    oswald_e=0.82,
    mach_crit=0.795,
    vmo_kt=350.0,
    mmo=0.82,
    ceiling_ft=39000.0,
    cruise_mach=0.78,
    roll_rate_deg_s=15.0,
    pitch_rate_deg_s=3.2,
    handling=(
        "Same airframe as the A320, transformed by the engines and sharklets. "
        "Aspect ratio 10.5 against the ceo's 9.5 means noticeably less induced "
        "drag, so it climbs harder, holds speed better in a steep turn, and burns "
        "appreciably less fuel over a long leg. Quiet enough to hear the airframe."
    ),
)

A321 = Aircraft(
    key="a321",
    name="A321-200",
    engines="2 x CFM56-5B3 (133 kN each)",
    wing_area_m2=122.6,
    wing_span_m=34.10,
    length_m=44.51,  # 6.9 m longer than the A320
    oew_kg=48500.0,
    mtow_kg=93500.0,
    payload_kg=18000.0,
    fuel_capacity_kg=18700.0,
    start_fuel_kg=14000.0,
    thrust_sl_n=266800.0,
    tsfc=1.63e-5,  # ~0.57 lb/(lbf*hr) cruise
    cd_0=0.0206,
    oswald_e=0.82,
    mach_crit=0.788,
    vmo_kt=350.0,
    mmo=0.82,
    ceiling_ft=39000.0,
    cruise_mach=0.78,
    roll_rate_deg_s=12.0,
    pitch_rate_deg_s=2.7,
    handling=(
        "The stretch. Fifteen tonnes heavier on the same wing, so wing loading "
        "and every speed that depends on it -- stall, approach, manoeuvre margin "
        "-- climbs with it. Rolls a beat later than the A320 and takes a beat "
        "longer to stop rolling. Deeply stable, but it does not like being rushed."
    ),
)

A350 = Aircraft(
    key="a350",
    name="A350-900",
    engines="2 x Rolls-Royce Trent XWB-84 (375 kN each)",
    wing_area_m2=442.0,
    wing_span_m=64.75,
    length_m=66.80,
    oew_kg=142400.0,
    mtow_kg=280000.0,
    payload_kg=40000.0,
    fuel_capacity_kg=110500.0,
    start_fuel_kg=70000.0,
    thrust_sl_n=749000.0,
    tsfc=1.23e-5,  # ~0.44 lb/(lbf*hr) cruise
    cd_0=0.0167,  # composite airframe, cleanest of the fleet
    oswald_e=0.85,
    mach_crit=0.860,
    vmo_kt=340.0,
    mmo=0.89,
    ceiling_ft=43100.0,
    cruise_mach=0.85,
    roll_rate_deg_s=10.0,
    pitch_rate_deg_s=2.3,
    handling=(
        "Long-legged and beautifully damped. The composite wing flexes visibly in "
        "turbulence and soaks up gusts the narrowbodies would pass straight to "
        "you. Cruises above FL400 where it is Mach-limited rather than "
        "speed-limited -- the airspeed needle falls as you climb but Mach rises."
    ),
)

A380 = Aircraft(
    key="a380",
    name="A380-800",
    engines="4 x Rolls-Royce Trent 970 (311 kN each)",
    wing_area_m2=845.0,
    wing_span_m=79.75,
    length_m=72.72,
    oew_kg=277000.0,
    mtow_kg=575000.0,
    payload_kg=60000.0,
    fuel_capacity_kg=254000.0,
    start_fuel_kg=160000.0,
    thrust_sl_n=1244000.0,
    tsfc=1.21e-5,  # ~0.43 lb/(lbf*hr) cruise
    cd_0=0.0145,
    oswald_e=0.84,
    mach_crit=0.858,
    vmo_kt=340.0,
    mmo=0.89,
    ceiling_ft=43100.0,
    cruise_mach=0.85,
    roll_rate_deg_s=7.0,
    pitch_rate_deg_s=1.8,
    handling=(
        "Five hundred tonnes of deliberate calm. The lowest aspect ratio in the "
        "fleet at 7.5, so it pays for lift in induced drag, but the sheer inertia "
        "means turbulence arrives as a slow heave rather than a jolt. Roll rate of "
        "7 deg/s: begin every turn early, and begin rolling out earlier still."
    ),
)


FLEET = [A320, A320NEO, A321, A350, A380]
FLEET_BY_KEY = {a.key: a for a in FLEET}

# Accept the obvious things a pilot might type at the selection menu.
_ALIASES = {
    "1": "a320",
    "2": "a320neo",
    "3": "a321",
    "4": "a350",
    "5": "a380",
    "a320-200": "a320",
    "320": "a320",
    "a320ceo": "a320",
    "neo": "a320neo",
    "320neo": "a320neo",
    "a320 neo": "a320neo",
    "321": "a321",
    "a321-200": "a321",
    "350": "a350",
    "a350-900": "a350",
    "a350900": "a350",
    "380": "a380",
    "a380-800": "a380",
    "a380800": "a380",
}


def resolve(text):
    """Look up an aircraft from menu input. Returns None if unrecognised."""
    if text is None:
        return None
    key = text.strip().lower().replace("_", "").replace("/", "")
    if key in FLEET_BY_KEY:
        return FLEET_BY_KEY[key]
    if key in _ALIASES:
        return FLEET_BY_KEY[_ALIASES[key]]
    compact = key.replace(" ", "").replace("-", "")
    if compact in FLEET_BY_KEY:
        return FLEET_BY_KEY[compact]
    if compact in _ALIASES:
        return FLEET_BY_KEY[_ALIASES[compact]]
    return None
