"""Airbus fleet definitions.

Each aircraft is a frozen dataclass of published geometry, mass and engine data
plus the handful of aerodynamic coefficients the point-mass model needs. The
handling differences between variants are *emergent*: the A320neo climbs better
and burns less because its sharklets raise the aspect ratio and its LEAP engines
have a lower TSFC, not because a "nimbleness" number was typed in.

Two classes of number live here and they must not be confused:

* **Published data** -- dimensions, masses, seat counts, thrust ratings, tank
  volumes. These come from the manufacturer's figures and are quoted as
  published. They exist to be *shown*: the spec card and the artwork are drawn
  from them, so a change here changes what the pilot sees.
* **Calibrated coefficients** -- ``tsfc``, ``cd_0``, ``oswald_e``,
  ``mach_crit``. These were solved so that the model reproduces each type's
  published cruise fuel flow, then cross-checked against the real engine's SFC.
  They exist to be *flown*. Changing one without re-solving the others makes the
  fuel page quietly wrong.

TSFC values are in kg/(N*s). Multiply by 35,306 for the more familiar
lb/(lbf*hr): the CFM56 works out at 0.59, the LEAP at 0.51, the Trents at
0.43-0.51, all within the published range for those engines at cruise. They were
derived by solving for the value that reproduces each type's published block
fuel flow at its cruise altitude and Mach, then checked against the real engine
figures -- so the fuel page agrees with the aircraft's actual numbers and the
constants remain physically meaningful rather than fudge factors.
"""

import math
from dataclasses import dataclass, field

# Jet A-1 at 15 degrees C. Tanks are certified by volume and the mass they hold
# follows from the density, which is why every published capacity is in litres
# and every flight plan is in kilograms.
JET_A1_KG_PER_L = 0.804


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
    start_fuel_kg: float

    # Propulsion
    thrust_sl_n: float  # total static thrust, all engines, sea level
    tsfc: float  # kg of fuel per newton of thrust per second
    idle_thrust_fraction: float = 0.05

    # -- published identity and dimensions, for the spec card and the artwork --
    icao_type: str = ""
    engine_options: str = ""  # the alternative powerplant offered on the type
    entry_service: int = 0
    height_m: float = 0.0  # overall height, fin tip to ground
    fuselage_width_m: float = 0.0  # external diameter, at the widest
    fuselage_height_m: float = 0.0  # external cross-section height
    wing_sweep_deg: float = 25.0  # quarter-chord
    wingtip: str = "sharklets"  # the device on the tip, as fitted
    cabin_decks: int = 1
    # A bulged belly fairing over an extra centre tank. Only the XLR has one,
    # and it is the single external feature that tells it from an A321neo.
    belly_fairing: bool = False
    mlw_kg: float = 0.0  # maximum landing weight
    mzfw_kg: float = 0.0  # maximum zero-fuel weight
    fuel_capacity_l: float = 0.0
    seats_typical: int = 0  # manufacturer's two- or three-class layout
    seats_max: int = 0  # exit-limit, single class
    range_nm: float = 0.0

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

    # Lateral-directional. The engine arms are real lateral offsets from the
    # centreline, so the yawing moment after an engine failure -- and therefore
    # Vmc -- comes out of the geometry instead of being typed in.
    engine_arms_m: tuple = (-5.75, 5.75)
    rudder_power: float = 0.0022  # yaw coefficient per degree of rudder
    directional_stability: float = 0.0035  # weathercock restoring, per degree
    dihedral_effect: float = 0.50  # bank degrees held per degree of sideslip
    yaw_tau_s: float = 2.2  # sideslip response time constant
    max_rudder_deg: float = 30.0

    handling: str = ""

    @property
    def engine_count(self):
        return len(self.engine_arms_m)

    @property
    def thrust_per_engine_n(self):
        return self.thrust_sl_n / self.engine_count

    @property
    def fuel_capacity_kg(self):
        """What the published tank volume actually holds.

        Derived rather than declared: the tanks are a fixed volume and the mass
        follows from the fuel density, so quoting both independently would let
        them drift apart.
        """
        return self.fuel_capacity_l * JET_A1_KG_PER_L

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

    @property
    def tsfc_lb_per_lbf_hr(self):
        """The same TSFC in the units engine makers publish."""
        return self.tsfc * 35306.0

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

    def cl_0_for_flaps(self, flap_setting):
        """Zero-alpha lift coefficient at a given flap detent.

        Flaps work mostly by adding camber, which lifts the whole lift curve
        rather than extending it to a higher stall angle. Raising CL_max without
        raising CL_0 would mean the flapped maximum could only be reached at an
        absurd angle of attack -- an aircraft at Vref with full flaps would sit
        permanently on the edge of the stall, which is the opposite of what
        flaps are for.
        """
        return self.cl_0 + FLAP_CL_BONUS[_clamp_flap(flap_setting)]

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
#
# Ordered by family and then by size, which is also roughly the order of
# increasing inertia -- but not exactly, and the tests check the physical claim
# (roll rate falls as the aeroplane gets heavier) rather than this listing.
# ---------------------------------------------------------------------------

A319NEO = Aircraft(
    key="a319neo",
    name="A319neo",
    icao_type="A19N",
    engines="2 x CFM LEAP-1A24 (107.6 kN each)",
    engine_options="Pratt & Whitney PW1124G-JM",
    entry_service=2019,
    wing_area_m2=122.6,
    wing_span_m=35.80,  # sharklets, as across the neo family
    length_m=33.84,
    height_m=11.76,
    fuselage_width_m=3.95,
    fuselage_height_m=4.14,
    wing_sweep_deg=25.0,
    wingtip="sharklets",
    oew_kg=42600.0,
    mtow_kg=75500.0,
    mlw_kg=62500.0,
    mzfw_kg=58500.0,
    payload_kg=12000.0,
    fuel_capacity_l=23859.0,
    start_fuel_kg=10000.0,
    seats_typical=140,
    seats_max=160,
    range_nm=3750.0,
    thrust_sl_n=215200.0,
    tsfc=1.43e-5,  # the same LEAP core as the A320neo
    cd_0=0.0190,  # shortest fuselage on the family wing: least wetted area
    oswald_e=0.82,
    mach_crit=0.795,
    vmo_kt=350.0,
    mmo=0.82,
    ceiling_ft=39800.0,
    cruise_mach=0.78,
    roll_rate_deg_s=16.0,
    pitch_rate_deg_s=3.4,
    engine_arms_m=(-5.75, 5.75),
    dihedral_effect=0.62,
    yaw_tau_s=1.9,
    handling=(
        "The shrink, and it behaves like one. Same wing and very nearly the same "
        "thrust as the A320 carrying seven tonnes less aeroplane, so it climbs "
        "like nothing else in the fleet and rolls the instant you think about "
        "it. The short fuselage costs it directional damping -- it hunts a "
        "little in turbulence where the A321 simply ploughs on."
    ),
)

A320 = Aircraft(
    key="a320",
    name="A320-200",
    icao_type="A320",
    engines="2 x CFM56-5B4 (120.1 kN each)",
    engine_options="IAE V2527-A5",
    entry_service=1988,
    wing_area_m2=122.6,
    wing_span_m=34.10,  # wingtip fences, no sharklets
    length_m=37.57,
    height_m=11.76,
    fuselage_width_m=3.95,
    fuselage_height_m=4.14,
    wing_sweep_deg=25.0,
    wingtip="wingtip fences",
    oew_kg=42600.0,
    mtow_kg=78000.0,
    mlw_kg=66000.0,
    mzfw_kg=62500.0,
    payload_kg=15000.0,
    fuel_capacity_l=23859.0,
    start_fuel_kg=12000.0,
    seats_typical=150,
    seats_max=180,
    range_nm=3300.0,
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
    engine_arms_m=(-5.75, 5.75),
    dihedral_effect=0.60,
    yaw_tau_s=2.0,
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
    icao_type="A20N",
    engines="2 x CFM LEAP-1A26 (120.6 kN each)",
    engine_options="Pratt & Whitney PW1127G-JM",
    entry_service=2016,
    wing_area_m2=122.6,
    wing_span_m=35.80,  # sharklets: same area, more span
    length_m=37.57,
    height_m=11.76,
    fuselage_width_m=3.95,
    fuselage_height_m=4.14,
    wing_sweep_deg=25.0,
    wingtip="sharklets",
    oew_kg=44300.0,
    mtow_kg=79000.0,
    mlw_kg=67400.0,
    mzfw_kg=64300.0,
    payload_kg=15000.0,
    fuel_capacity_l=23859.0,
    start_fuel_kg=12000.0,
    seats_typical=165,
    seats_max=194,
    range_nm=3500.0,
    thrust_sl_n=241200.0,
    tsfc=1.43e-5,  # ~0.51 lb/(lbf*hr): ~14% better than the CFM56
    cd_0=0.0195,
    oswald_e=0.82,
    mach_crit=0.795,
    vmo_kt=350.0,
    mmo=0.82,
    ceiling_ft=39800.0,
    cruise_mach=0.78,
    roll_rate_deg_s=15.0,
    pitch_rate_deg_s=3.2,
    engine_arms_m=(-5.75, 5.75),
    dihedral_effect=0.60,
    yaw_tau_s=2.0,
    handling=(
        "Same airframe as the A320, transformed by the engines and sharklets. "
        "Aspect ratio 10.5 against the ceo's 9.5 means noticeably less induced "
        "drag, so it climbs harder, holds speed better in a steep turn, and burns "
        "appreciably less fuel over a long leg. Quiet enough to hear the airframe."
    ),
)

A321 = Aircraft(
    key="a321",
    name="A321neo",
    icao_type="A21N",
    engines="2 x CFM LEAP-1A32 (143.1 kN each)",
    engine_options="Pratt & Whitney PW1133G-JM",
    entry_service=2017,
    wing_area_m2=122.6,
    wing_span_m=35.80,  # sharklets, as on the A320neo: same area, more span
    length_m=44.51,  # 6.9 m longer than the A320
    height_m=11.76,
    fuselage_width_m=3.95,
    fuselage_height_m=4.14,
    wing_sweep_deg=25.0,
    wingtip="sharklets",
    oew_kg=50100.0,
    mtow_kg=97000.0,
    mlw_kg=79200.0,
    mzfw_kg=75600.0,
    payload_kg=20000.0,
    fuel_capacity_l=23700.0,
    start_fuel_kg=15000.0,
    seats_typical=180,
    seats_max=244,
    range_nm=4000.0,
    thrust_sl_n=286200.0,
    tsfc=1.43e-5,  # ~0.51 lb/(lbf*hr): the same LEAP as the A320neo
    cd_0=0.0200,  # the A320neo's polar, plus a little for the longer fuselage
    oswald_e=0.82,
    mach_crit=0.792,
    vmo_kt=350.0,
    mmo=0.82,
    ceiling_ft=39800.0,
    cruise_mach=0.78,
    roll_rate_deg_s=12.0,
    pitch_rate_deg_s=2.7,
    engine_arms_m=(-5.75, 5.75),
    dihedral_effect=0.55,
    yaw_tau_s=2.2,
    handling=(
        "The long one. Nineteen tonnes heavier than the A320 on the same wing "
        "area, so wing loading and every speed that depends on it -- stall, "
        "approach, manoeuvre margin -- climbs with it, and it stalls some "
        "twelve knots faster at operating weight. The sharklets and the LEAPs "
        "make it the most efficient thing in the fleet per seat, but they do "
        "nothing for its inertia: it rolls a beat later than the A320 and takes "
        "a beat longer to stop rolling. Deeply stable, and it does not like "
        "being rushed."
    ),
)

A321XLR = Aircraft(
    key="a321xlr",
    name="A321XLR",
    icao_type="A21N",
    engines="2 x CFM LEAP-1A35 (143.1 kN each)",
    engine_options="Pratt & Whitney PW1133G-JM",
    entry_service=2024,
    wing_area_m2=122.6,
    wing_span_m=35.80,
    length_m=44.51,
    height_m=11.76,
    fuselage_width_m=3.95,
    fuselage_height_m=4.14,
    wing_sweep_deg=25.0,
    wingtip="sharklets",
    belly_fairing=True,  # the rear centre tank's fairing, aft of the wing box
    oew_kg=52300.0,  # the rear centre tank and the strengthening it needs
    mtow_kg=101000.0,
    mlw_kg=79200.0,
    mzfw_kg=75600.0,
    payload_kg=20000.0,
    fuel_capacity_l=36600.0,  # +12,900 L in the permanent rear centre tank
    start_fuel_kg=22000.0,
    seats_typical=180,
    seats_max=244,
    range_nm=4700.0,
    thrust_sl_n=286200.0,
    tsfc=1.43e-5,  # the same LEAP as the A321neo
    cd_0=0.0202,  # the RCT's belly fairing, on the A321neo's polar
    oswald_e=0.82,
    mach_crit=0.792,
    vmo_kt=350.0,
    mmo=0.82,
    ceiling_ft=39800.0,
    cruise_mach=0.78,
    roll_rate_deg_s=11.5,
    pitch_rate_deg_s=2.6,
    engine_arms_m=(-5.75, 5.75),
    dihedral_effect=0.55,
    yaw_tau_s=2.3,
    handling=(
        "A narrowbody with a widebody's legs. Thirteen tonnes of extra fuel over "
        "the A321neo, all of it in a permanent tank behind the wing, and the "
        "aeroplane feels every kilogram of it: heavier in roll, slower to "
        "accelerate, and reluctant to climb until several hours have burned off "
        "the difference. Fly it like a long-haul aircraft, because that is what "
        "it is."
    ),
)

A330NEO = Aircraft(
    key="a330neo",
    name="A330-900neo",
    icao_type="A339",
    engines="2 x Rolls-Royce Trent 7000-72 (324.0 kN each)",
    entry_service=2018,
    wing_area_m2=361.6,
    wing_span_m=64.00,  # new composite sharklets: +3.7 m over the ceo
    length_m=63.66,
    height_m=16.79,
    fuselage_width_m=5.64,
    fuselage_height_m=5.64,
    wing_sweep_deg=30.0,
    wingtip="composite sharklets",
    oew_kg=137000.0,
    mtow_kg=251000.0,
    mlw_kg=191000.0,
    mzfw_kg=181000.0,
    payload_kg=40000.0,
    fuel_capacity_l=139090.0,
    start_fuel_kg=58000.0,
    seats_typical=287,
    seats_max=440,
    range_nm=7200.0,
    thrust_sl_n=648000.0,
    tsfc=1.42e-5,  # ~0.50 lb/(lbf*hr): the Trent 7000, off the Trent 1000-TEN
    cd_0=0.0190,  # metal airframe -- clean, but not the A350's composite skin
    oswald_e=0.82,  # sharklets on an older wing: good, not A350 good
    mach_crit=0.835,
    vmo_kt=330.0,
    mmo=0.86,
    ceiling_ft=41450.0,
    cruise_mach=0.82,
    # Nearly the A350-900's span on thirty tonnes less aeroplane, so it is
    # marginally the more responsive of the two in every axis.
    roll_rate_deg_s=10.2,
    pitch_rate_deg_s=2.35,
    engine_arms_m=(-10.0, 10.0),
    rudder_power=0.0024,
    dihedral_effect=0.50,
    yaw_tau_s=2.75,
    handling=(
        "The highest aspect ratio in the fleet at 11.3 -- a long, slender wing on "
        "a comparatively small area, which is exactly the recipe for low induced "
        "drag and a superb climb at weight. Older aerodynamics than the A350 and "
        "an aluminium skin, so it pays more in parasite drag and cruises a shade "
        "slower, but at medium weights very little else holds an altitude so "
        "effortlessly."
    ),
)

A350 = Aircraft(
    key="a350",
    name="A350-900",
    icao_type="A359",
    engines="2 x Rolls-Royce Trent XWB-84 (374.5 kN each)",
    entry_service=2015,
    wing_area_m2=442.0,
    wing_span_m=64.75,
    length_m=66.80,
    height_m=17.08,
    fuselage_width_m=5.96,
    fuselage_height_m=6.09,
    wing_sweep_deg=31.9,
    wingtip="curved sabre tips",
    oew_kg=142400.0,
    mtow_kg=280000.0,
    mlw_kg=207000.0,
    mzfw_kg=195700.0,
    payload_kg=40000.0,
    fuel_capacity_l=138000.0,
    start_fuel_kg=70000.0,
    seats_typical=315,
    seats_max=440,
    range_nm=8300.0,
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
    engine_arms_m=(-10.6, 10.6),
    rudder_power=0.0024,
    dihedral_effect=0.50,
    yaw_tau_s=2.8,
    handling=(
        "Long-legged and beautifully damped. The composite wing flexes visibly in "
        "turbulence and soaks up gusts the narrowbodies would pass straight to "
        "you. Cruises above FL400 where it is Mach-limited rather than "
        "speed-limited -- the airspeed needle falls as you climb but Mach rises."
    ),
)

A350K = Aircraft(
    key="a350k",
    name="A350-1000",
    icao_type="A35K",
    engines="2 x Rolls-Royce Trent XWB-97 (431.5 kN each)",
    entry_service=2018,
    wing_area_m2=464.3,  # the -1000's extended trailing edge
    wing_span_m=64.75,
    length_m=73.79,  # seven metres of stretch over the -900
    height_m=17.08,
    fuselage_width_m=5.96,
    fuselage_height_m=6.09,
    wing_sweep_deg=31.9,
    wingtip="curved sabre tips",
    oew_kg=155000.0,
    mtow_kg=319000.0,
    mlw_kg=236000.0,
    mzfw_kg=223000.0,
    payload_kg=45000.0,
    fuel_capacity_l=158791.0,
    start_fuel_kg=85000.0,
    seats_typical=350,
    seats_max=480,
    range_nm=8700.0,
    thrust_sl_n=863000.0,
    tsfc=1.23e-5,  # the XWB-97: same core, same cruise SFC as the -84
    cd_0=0.0170,  # the -900's skin over a longer fuselage
    oswald_e=0.85,
    mach_crit=0.858,
    vmo_kt=340.0,
    mmo=0.89,
    ceiling_ft=43100.0,
    cruise_mach=0.85,
    roll_rate_deg_s=9.0,
    pitch_rate_deg_s=2.1,
    engine_arms_m=(-10.6, 10.6),
    rudder_power=0.0024,
    dihedral_effect=0.48,
    yaw_tau_s=3.0,
    handling=(
        "Seven metres longer than the -900 and thirty-nine tonnes heavier, on a "
        "wing enlarged only at the trailing edge -- so wing loading is up and "
        "everything that depends on it follows. The stretch shows most in pitch: "
        "the nose takes noticeably longer to come round, and a late correction "
        "on final becomes a long, slow oscillation instead of a nudge. Six-wheel "
        "main gear, and the widest turn radius of anything on two engines."
    ),
)

A380 = Aircraft(
    key="a380",
    name="A380-800",
    icao_type="A388",
    engines="4 x Rolls-Royce Trent 970 (310.7 kN each)",
    engine_options="Engine Alliance GP7270",
    entry_service=2007,
    wing_area_m2=845.0,
    wing_span_m=79.75,
    length_m=72.72,
    height_m=24.09,
    fuselage_width_m=7.14,
    fuselage_height_m=8.41,  # two full decks, and it shows
    wing_sweep_deg=33.5,
    wingtip="upswept winglets",
    cabin_decks=2,
    oew_kg=277000.0,
    mtow_kg=575000.0,
    mlw_kg=394000.0,
    mzfw_kg=361000.0,
    payload_kg=60000.0,
    fuel_capacity_l=320000.0,
    start_fuel_kg=160000.0,
    seats_typical=525,
    seats_max=853,
    range_nm=8000.0,
    thrust_sl_n=1242800.0,
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
    engine_arms_m=(-21.6, -12.4, 12.4, 21.6),
    rudder_power=0.0020,
    directional_stability=0.0038,
    dihedral_effect=0.40,
    yaw_tau_s=3.5,
    handling=(
        "Five hundred tonnes of deliberate calm. The lowest aspect ratio in the "
        "fleet at 7.5, so it pays for lift in induced drag, but the sheer inertia "
        "means turbulence arrives as a slow heave rather than a jolt. Roll rate of "
        "7 deg/s: begin every turn early, and begin rolling out earlier still."
    ),
)


FLEET = [
    A319NEO,
    A320,
    A320NEO,
    A321,
    A321XLR,
    A330NEO,
    A350,
    A350K,
    A380,
]
FLEET_BY_KEY = {a.key: a for a in FLEET}

# Accept the obvious things a pilot might type at the selection menu. The bare
# digits are the menu numbers and so must track the order of FLEET.
_ALIASES = {
    "1": "a319neo",
    "2": "a320",
    "3": "a320neo",
    "4": "a321",
    "5": "a321xlr",
    "6": "a330neo",
    "7": "a350",
    "8": "a350k",
    "9": "a380",
    "a19n": "a319neo",
    "a319": "a319neo",
    "319": "a319neo",
    "319neo": "a319neo",
    "a320-200": "a320",
    "320": "a320",
    "a320ceo": "a320",
    "neo": "a320neo",
    "a20n": "a320neo",
    "320neo": "a320neo",
    "a320 neo": "a320neo",
    "321": "a321",
    "a21n": "a321",
    "a321neo": "a321",
    "321neo": "a321",
    "xlr": "a321xlr",
    "321xlr": "a321xlr",
    "a330": "a330neo",
    "a339": "a330neo",
    "330": "a330neo",
    "330neo": "a330neo",
    "a330-900": "a330neo",
    "a330900": "a330neo",
    "a3309": "a330neo",
    "350": "a350",
    "a359": "a350",
    "a350-900": "a350",
    "a350900": "a350",
    "a35k": "a350k",
    "a350-1000": "a350k",
    "a3501000": "a350k",
    "3501000": "a350k",
    "380": "a380",
    "a388": "a380",
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
