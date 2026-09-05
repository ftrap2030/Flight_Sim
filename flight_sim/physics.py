"""Point-mass flight dynamics.

One pilot command advances the simulation by TICK_SECONDS, integrated with
semi-implicit Euler at 0.1 s substeps. The substepping matters: turn rate and
flight path angle are coupled through airspeed, and a 10-second Euler step on
that coupling diverges badly at low speed.

The state is deliberately small and fully serialisable, so a session can be
persisted to JSON between turns and resumed exactly.
"""

import math
import random
from collections import namedtuple
from dataclasses import dataclass, field, asdict

from . import aircraft as fleet
from . import atmosphere as atm
from . import autopilot
from . import landing
from . import navigation
from . import weather as wx
from . import airfield
from .airfield import Airfields
from .terrain import Terrain

TICK_SECONDS = 10.0
SUBSTEP_S = 0.1

# Flight envelope thresholds
OVERSTRESS_G = 2.5
STRUCTURAL_FAILURE_G = 3.8
STALL_WARNING_MARGIN_DEG = 3.0
# Once the wing departs, the centre of pressure moves aft and the nose falls
# whatever the pilot asks for; control authority collapses with it.
STALL_NOSE_DROP_DEG_S = 4.5
STALL_CONTROL_AUTHORITY = 0.30

# Lateral-directional
MAX_SIDESLIP_DEG = 25.0
SIDESLIP_DRAG_K = 8.0e-5  # added CD per degree of beta squared
RUDDER_DRAG_K = 1.2e-4  # added CD per degree of rudder deflection
# Rudder travel limiter: full deflection is only available at low speed. Real
# airliners do exactly this, and without it full rudder at cruise produces an
# absurd (and fin-detaching) sideslip.
RUDDER_LIMIT_REF_KT = 160.0
MIN_RUDDER_TRAVEL_DEG = 4.0
VMC_SIDESLIP_DEG = 12.0  # beta beyond this with an engine out is losing it
LOW_FUEL_FRACTION = 0.05
GPWS_HARD_FT = 500.0
GPWS_SOFT_FT = 1000.0

# How far the aircraft may travel before the surrounding airfields are refreshed.
AIRFIELD_RELOAD_NM = 40.0

# Status values
FLYING = "flying"
ROLLOUT = "rollout"  # on the runway, still moving -- not yet an ending
LANDED = "landed"
OVERRUN = "overrun"
CRASHED_TERRAIN = "crashed_terrain"
STRUCTURAL_FAILURE = "structural_failure"
ENDED_BY_PILOT = "ended_by_pilot"

# The two statuses in which the simulation is still running.
LIVE_STATUSES = (FLYING, ROLLOUT)


def wrap180(degrees_value):
    """Normalise an angle to (-180, 180]."""
    return (degrees_value + 180.0) % 360.0 - 180.0


def wrap360(degrees_value):
    return degrees_value % 360.0


def clamp(value, low, high):
    return max(low, min(high, value))


@dataclass
class FlightState:
    """Everything needed to resume a flight."""

    aircraft_key: str
    weather_key: str
    seed: int

    tas_ms: float
    altitude_ft: float
    pitch_deg: float
    bank_deg: float
    heading_deg: float
    gamma_deg: float  # flight path angle
    throttle_pct: float
    fuel_kg: float
    mass_kg: float
    x_nm: float
    y_nm: float

    flaps: int = 0
    gear_down: bool = False
    spoilers: bool = False

    # Lateral-directional. sideslip_deg is beta: positive means the nose points
    # right of the flight path through the air mass.
    sideslip_deg: float = 0.0
    rudder_deg: float = 0.0
    engines_failed: list = field(default_factory=list)

    # Ground handling
    on_ground: bool = False
    brakes: float = 0.0  # 0 = off, 1 = full
    reverse_thrust: bool = False
    touchdown: dict = None  # the graded arrival, once there is one
    landing_field_ident: str = ""

    cmd_pitch_deg: float = 0.0
    cmd_bank_deg: float = 0.0
    cmd_heading_deg: float = None

    elapsed_s: float = 0.0
    tick: int = 0
    status: str = FLYING
    engines_running: bool = True

    # Turbulence filter state. Persisted so that a session resumed from disk
    # keeps its gust correlation instead of snapping back to still air.
    turb: list = field(default_factory=lambda: [0.0, 0.0, 0.0])

    # Autopilot. Each channel is independent and None when disengaged.
    ap_engaged: bool = False
    ap_altitude_ft: float = None
    ap_vs_fpm: float = None
    ap_heading_deg: float = None
    ap_speed_kt: float = None
    ap_approach: bool = False

    # Local time in hours, for the sun and the narrator's sense of light.
    time_of_day_h: float = 10.0

    # The route, serialised. Held as a dict so FlightState stays plain data.
    route: dict = None

    # The flight record, accumulated tick by tick for the debrief. There is no
    # way to reconstruct "closest you ever came to the ground" after the fact,
    # so it has to be gathered while it happens.
    initial_fuel_kg: float = 0.0
    distance_flown_nm: float = 0.0
    max_altitude_ft: float = 0.0
    min_agl_ft: float = 1e9
    max_ias_kt: float = 0.0
    max_mach: float = 0.0
    max_load_factor: float = 1.0
    warnings_seen: list = field(default_factory=list)

    # Populated at the moment of a terminal event, for the ending narration.
    impact_ias_kt: float = 0.0
    impact_vs_fpm: float = 0.0
    impact_elevation_ft: float = 0.0
    impact_feature: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)


@dataclass
class Readout:
    """Derived instrument values for one moment in time."""

    ias_kt: float
    tas_kt: float
    ground_speed_kt: float
    mach: float
    altitude_ft: float
    agl_ft: float
    terrain_ft: float
    vertical_speed_fpm: float
    pitch_deg: float
    bank_deg: float
    heading_deg: float
    track_deg: float
    drift_deg: float
    alpha_deg: float
    sideslip_deg: float
    wind_drift_deg: float
    rudder_deg: float
    rudder_limit_deg: float
    engines_running_count: int
    load_factor: float
    throttle_pct: float
    thrust_n: float
    fuel_kg: float
    fuel_pct: float
    fuel_flow_kgh: float
    mass_kg: float
    stall_ias_kt: float
    stalled: bool
    warnings: list = field(default_factory=list)
    terrain_ahead_nm: float = 0.0
    terrain_ahead_ft: float = 0.0
    terrain_ahead_name: str = ""
    approach: object = None
    vref_kt: float = 0.0
    leg: object = None
    wind_speed_kt: float = 0.0
    wind_dir_deg: float = 0.0
    rotor_turbulence: float = 0.0
    orographic_fpm: float = 0.0


Aero = namedtuple(
    "Aero", "v mach alpha_deg cl cd stalled lift drag thrust"
)


_WORLDS = {}


def world_for_seed(seed):
    """The Terrain and Airfields for a seed, built once.

    Both are deterministic functions of the seed alone, so every Simulator on a
    given seed should see the same world -- and generating airfields is the most
    expensive thing in the whole simulator, since each one searches for flat
    ground. Sharing turns that from per-session work into per-seed work.
    """
    if seed not in _WORLDS:
        terrain = Terrain(seed=seed)
        _WORLDS[seed] = (terrain, Airfields(terrain))
    return _WORLDS[seed]


def forget_worlds():
    """Drop the cached worlds. For tests that need a pristine terrain."""
    _WORLDS.clear()


class Simulator:
    """Owns the aircraft, the weather, the world and the mutable flight state."""

    def __init__(self, state, terrain=None):
        self.state = state
        self.aircraft = fleet.FLEET_BY_KEY[state.aircraft_key]
        self.weather = wx.WeatherState(
            wx.WEATHER_BY_KEY[state.weather_key],
            seed=state.seed,
            elapsed_s=state.elapsed_s,
        )
        if terrain is None:
            self.terrain, self.airfields = world_for_seed(state.seed)
        else:
            self.terrain = terrain
            self.airfields = Airfields(self.terrain)
        # Generating a field grades its site, so the world near the aircraft has
        # to be realised before anything queries terrain there.
        self.airfields.ensure_loaded(state.x_nm, state.y_nm)
        self._loaded_at = (state.x_nm, state.y_nm)
        # Turbulence is a filtered random walk rather than white noise, so gusts
        # have believable duration instead of flickering every substep. The
        # filter state lives on FlightState so it survives serialisation.
        self._rng = random.Random(state.seed * 7919 + state.tick)
        self.route = navigation.Route.from_dict(state.route)
        self._mechanical_turbulence = 0.0
        self._orographic_fpm = 0.0
        self._refresh_terrain_effects()

    def sync_route(self):
        """Mirror the route back into the serialisable state."""
        self.state.route = self.route.to_dict()

    # -- construction --------------------------------------------------

    @classmethod
    def new_flight(cls, aircraft_key, weather_key, seed=20260905, altitude_ft=5000.0):
        """Phase 2 initial condition: 5,000 ft, straight and level, trimmed."""
        craft = fleet.FLEET_BY_KEY[aircraft_key]
        # Start at a sensible low-altitude manoeuvring speed for the type.
        ias_kt = 250.0 if craft.wing_area_m2 < 200 else 270.0
        tas_ms = atm.ias_to_tas(ias_kt * atm.MS_PER_KT, altitude_ft)

        heading_deg = 90.0
        world, _fields = world_for_seed(seed)
        # Start inside the authored home region, so the hand-designed fields are
        # somewhere a pilot will actually meet rather than a corner of an
        # infinite world they would never fly to.
        start_x, start_y = world.find_start(
            altitude_ft,
            heading_deg,
            centre=airfield.HOME_CENTRE_NM,
            span_nm=airfield.HOME_RADIUS_NM * 0.55,
        )

        state = FlightState(
            aircraft_key=aircraft_key,
            weather_key=weather_key,
            seed=seed,
            tas_ms=tas_ms,
            altitude_ft=altitude_ft,
            pitch_deg=0.0,
            bank_deg=0.0,
            heading_deg=heading_deg,
            gamma_deg=0.0,
            throttle_pct=60.0,
            fuel_kg=craft.start_fuel_kg,
            mass_kg=craft.start_mass_kg,
            x_nm=start_x,
            y_nm=start_y,
        )
        state.initial_fuel_kg = craft.start_fuel_kg
        state.max_altitude_ft = altitude_ft
        sim = cls(state)
        # Trim: set pitch to whatever holds level flight at this speed and mass,
        # and set thrust to match drag, so the aeroplane genuinely starts stable.
        trim_pitch = sim.level_flight_pitch_deg()
        state.pitch_deg = trim_pitch
        state.cmd_pitch_deg = trim_pitch
        state.throttle_pct = sim.throttle_for_level_flight()
        return sim

    # -- trim solutions ------------------------------------------------

    def level_flight_pitch_deg(self):
        """Pitch attitude that produces exactly 1g of lift at the current state.

        Solves L = W for CL, then inverts the lift curve for alpha. With a zero
        flight path angle, pitch equals alpha.
        """
        s = self.state
        craft = self.aircraft
        rho = atm.density(s.altitude_ft)
        v = max(s.tas_ms, 25.0)
        bank = math.radians(s.bank_deg)
        # A banked turn needs 1/cos(phi) times the lift to hold altitude.
        required_lift = s.mass_kg * atm.G0 / max(math.cos(bank), 0.2)
        cl_required = required_lift / (0.5 * rho * v * v * craft.wing_area_m2)
        cl_required = min(cl_required, craft.cl_max_for_flaps(s.flaps))
        alpha_rad = (
            cl_required - craft.cl_0_for_flaps(s.flaps)
        ) / craft.cl_alpha
        return clamp(math.degrees(alpha_rad), -10.0, craft.alpha_crit_deg - 0.5)

    def throttle_for_flight_path(self, gamma_deg):
        """Throttle that holds a steady flight path angle at the current speed.

        Thrust must beat drag by the component of weight along the path, so a
        descent needs less and a climb needs more. This is the primitive an
        approach actually needs: with full flaps and the gear down, holding a
        3-degree path takes *more* thrust than intuition suggests, because the
        aircraft's idle glide is far steeper than three degrees.
        """
        aero = self._aero_state()
        needed = aero.drag + self.state.mass_kg * atm.G0 * math.sin(
            math.radians(gamma_deg)
        )
        available = self._thrust_available_n()
        return clamp(100.0 * needed / max(available, 1.0), 0.0, 100.0)

    def idle_flight_path_deg(self):
        """The flight path angle the aircraft settles into with no thrust.

        The glide angle in the current configuration -- what you get if you do
        nothing. Negative.
        """
        aero = self._aero_state()
        return -math.degrees(math.atan2(aero.drag, aero.lift if aero.lift > 1.0 else 1.0))

    def throttle_for_level_flight(self):
        """Throttle setting whose thrust equals drag in level flight."""
        s = self.state
        craft = self.aircraft
        rho = atm.density(s.altitude_ft)
        v = max(s.tas_ms, 25.0)
        q = 0.5 * rho * v * v
        cl = (s.mass_kg * atm.G0) / (q * craft.wing_area_m2)
        cd = craft.cd_0_for_config(s.flaps, s.gear_down, s.spoilers)
        cd += craft.induced_drag_factor * cl * cl
        m = atm.mach(v, s.altitude_ft)
        if m > craft.mach_crit:
            cd += craft.wave_drag_k * (m - craft.mach_crit) ** 3
        drag = q * craft.wing_area_m2 * cd
        return clamp(100.0 * drag / max(self._thrust_available_n(), 1.0), 5.0, 100.0)

    # -- per-substep forces --------------------------------------------

    def _lift_coefficient(self, alpha_deg):
        """CL and whether the wing is stalled at this angle of attack.

        Below the critical angle the lift curve is linear and clipped at CL_max.
        Beyond it, lift collapses progressively -- which is what makes a stall
        self-reinforcing: less lift, steeper descent, higher alpha still.
        """
        craft = self.aircraft
        cl_max = craft.cl_max_for_flaps(self.state.flaps)
        cl_linear = craft.cl_0_for_flaps(
            self.state.flaps
        ) + craft.cl_alpha * math.radians(alpha_deg)
        cl = clamp(cl_linear, -cl_max, cl_max)
        if alpha_deg > craft.alpha_crit_deg:
            excess = alpha_deg - craft.alpha_crit_deg
            cl *= max(0.32, 1.0 - 0.055 * excess)
            return cl, True
        return cl, False

    def _thrust_n(self):
        """Net thrust available, all engines.

        Three effects, all of which matter for getting the service ceiling right:

        * Density lapse, steeper above the tropopause -- in the isothermal layer
          temperature stops falling while density keeps dropping, so thrust
          decays faster than the simple sigma**0.7 troposphere rule.
        * Ram drag: net thrust of a high-bypass turbofan falls off with forward
          speed, which is why static sea-level rating badly overstates what an
          engine gives you at cruise Mach.
        * The certified ceiling, faded in over the last 2,000 ft. Without it the
          model happily climbs an A320 to 50,000 ft.
        """
        return self._thrust_per_engine_n() * len(self._running_engines())

    def _running_engines(self):
        """Indices of the engines still turning."""
        failed = set(self.state.engines_failed or ())
        return [i for i in range(self.aircraft.engine_count) if i not in failed]

    def _thrust_per_engine_n(self):
        """Thrust from one running engine."""
        if not self.state.engines_running or self.state.fuel_kg <= 0.0:
            return 0.0
        fraction = max(
            self.aircraft.idle_thrust_fraction, self.state.throttle_pct / 100.0
        )
        return self._thrust_available_n() * fraction / self.aircraft.engine_count

    def _asymmetric_yaw_moment(self):
        """Yawing moment from thrust, in newton-metres.

        Zero with every engine running, since the arms cancel. An engine at a
        positive (right-hand) arm pushes the aircraft forward on the right and
        so yaws the nose left -- hence the negation, and hence the aircraft
        yawing *toward* the dead engine.
        """
        arms = self.aircraft.engine_arms_m
        live_arm_sum = sum(arms[i] for i in self._running_engines())
        return -self._thrust_per_engine_n() * live_arm_sum

    def max_rudder_deg(self):
        """Rudder travel available at the current airspeed."""
        ias_kt = max(
            atm.tas_to_ias(max(self.state.tas_ms, 1.0), self.state.altitude_ft)
            * atm.KT_PER_MS,
            1.0,
        )
        travel = self.aircraft.max_rudder_deg * min(
            1.0, (RUDDER_LIMIT_REF_KT / ias_kt) ** 2.0
        )
        return clamp(travel, MIN_RUDDER_TRAVEL_DEG, self.aircraft.max_rudder_deg)

    def _thrust_available_n(self):
        """Maximum thrust at the current altitude and Mach, all engines.

        Single source of truth for the lapse law -- the trim solver and the
        integrator must agree, or the aeroplane trims to a throttle setting that
        does not actually hold speed.
        """
        s = self.state
        craft = self.aircraft

        sigma = atm.density_ratio(s.altitude_ft)
        if s.altitude_ft <= atm.TROPOPAUSE_FT:
            lapse = sigma ** 0.7
        else:
            sigma_trop = atm.density_ratio(atm.TROPOPAUSE_FT)
            lapse = sigma_trop ** 0.7 * (sigma / sigma_trop) ** 1.2

        mach_number = atm.mach(max(s.tas_ms, 1.0), s.altitude_ft)
        lapse *= max(0.30, 1.0 - 0.45 * mach_number)

        above_ceiling = s.altitude_ft - craft.ceiling_ft
        if above_ceiling > 0.0:
            lapse *= max(0.0, 1.0 - above_ceiling / 2000.0)

        return craft.thrust_sl_n * lapse

    def _aero_state(self):
        """Forces and coefficients at the present instant.

        Shared by the integrator and the instrument readout so the panel can
        never disagree with the physics driving the aircraft.
        """
        s = self.state
        craft = self.aircraft
        v = max(s.tas_ms, 25.0)
        rho = atm.density(s.altitude_ft)
        mach_number = atm.mach(v, s.altitude_ft)
        alpha_deg = s.pitch_deg - s.gamma_deg

        cl, stalled = self._lift_coefficient(alpha_deg)
        cd = craft.cd_0_for_config(s.flaps, s.gear_down, s.spoilers)
        cd += craft.induced_drag_factor * cl * cl
        if mach_number > craft.mach_crit:
            cd += craft.wave_drag_k * (mach_number - craft.mach_crit) ** 3
        # Flying sideways is expensive: the fuselage presents its flank to the
        # airflow, and the deflected rudder adds its own profile drag.
        cd += SIDESLIP_DRAG_K * s.sideslip_deg ** 2
        cd += RUDDER_DRAG_K * abs(s.rudder_deg)

        q = 0.5 * rho * v * v
        return Aero(
            v=v,
            mach=mach_number,
            alpha_deg=alpha_deg,
            cl=cl,
            cd=cd,
            stalled=stalled,
            lift=q * craft.wing_area_m2 * cl,
            drag=q * craft.wing_area_m2 * cd,
            thrust=self._thrust_n(),
        )

    def _velocity_over_ground_ms(self, tas_ms):
        """Ground velocity components (east, north) in m/s.

        Air-relative motion is along (heading - beta), the flight path through
        the air mass, which is only the same as the nose when beta is zero.
        Wind is then added on top.
        """
        s = self.state
        horizontal_ms = tas_ms * math.cos(math.radians(s.gamma_deg))
        air_track_rad = math.radians(s.heading_deg - s.sideslip_deg)
        # Wind is a function of height: surface friction slows and backs it, so
        # a descent changes drift and groundspeed as well as altitude.
        wind_kt, wind_from_deg = self.weather.wind_at(
            s.altitude_ft - self.terrain.elevation(s.x_nm, s.y_nm)
        )
        wind_ms = wind_kt * atm.MS_PER_KT
        wind_to_rad = math.radians(wind_from_deg + 180.0)
        return (
            horizontal_ms * math.sin(air_track_rad) + wind_ms * math.sin(wind_to_rad),
            horizontal_ms * math.cos(air_track_rad) + wind_ms * math.cos(wind_to_rad),
        )

    def _update_sideslip(self, dt):
        """Advance beta toward the sideslip the yaw moments are asking for.

        Steady-state yaw balance: rudder moment plus asymmetric-thrust moment is
        opposed by weathercock stability, which is proportional to beta. Solving
        for equilibrium and lagging toward it with a first-order time constant
        gives the right feel without needing a yaw moment of inertia.

        Vmc falls out of this rather than being coded. The thrust moment is
        normalised by dynamic pressure, so as speed decays the same dead engine
        demands ever more rudder; below some speed the available travel simply
        cannot balance it, and the nose goes.
        """
        s = self.state
        craft = self.aircraft

        s.rudder_deg = clamp(
            s.rudder_deg, -self.max_rudder_deg(), self.max_rudder_deg()
        )

        v = max(s.tas_ms, 25.0)
        reference_moment = (
            0.5
            * atm.density(s.altitude_ft)
            * v * v
            * craft.wing_area_m2
            * craft.wing_span_m
        )
        cn_thrust = self._asymmetric_yaw_moment() / max(reference_moment, 1.0)
        cn_rudder = craft.rudder_power * s.rudder_deg

        target = (cn_rudder + cn_thrust) / craft.directional_stability
        target = clamp(target, -MAX_SIDESLIP_DEG, MAX_SIDESLIP_DEG)

        s.sideslip_deg += (target - s.sideslip_deg) * min(
            1.0, dt / craft.yaw_tau_s
        )
        s.sideslip_deg = clamp(s.sideslip_deg, -MAX_SIDESLIP_DEG, MAX_SIDESLIP_DEG)

    def _refresh_terrain_effects(self):
        """Re-sample the slow, terrain-scale weather effects.

        Rotor turbulence and orographic lift are features of the landscape at a
        scale of a mile or two; the aircraft covers about seven metres in a
        substep. Sampling them once per tick rather than a hundred times is
        both far cheaper and no less accurate.
        """
        s = self.state
        agl_ft = s.altitude_ft - self.terrain.elevation(s.x_nm, s.y_nm)
        self._mechanical_turbulence = self.weather.mechanical_turbulence(
            self.terrain, s.x_nm, s.y_nm, agl_ft
        )
        self._orographic_fpm = self.weather.orographic_vertical_fpm(
            self.terrain, s.x_nm, s.y_nm, agl_ft
        )

    def _local_turbulence(self):
        """Total turbulence here: the weather's, plus what the terrain adds.

        Wind pouring over a ridge breaks up in its lee, so the same weather is
        violent behind a crest and smooth over a plain.
        """
        return min(1.0, self.weather.turbulence + self._mechanical_turbulence)

    def _update_turbulence(self, dt):
        """Ornstein-Uhlenbeck-ish filtered noise: correlated, bounded gusts."""
        intensity = self._local_turbulence()
        if intensity <= 0.0:
            return
        decay = math.exp(-dt / 2.5)  # ~2.5 s correlation time
        turb = self.state.turb
        for i in range(3):
            turb[i] = turb[i] * decay + self._rng.gauss(0.0, 1.0) * (1.0 - decay) * 3.0
            turb[i] = clamp(turb[i], -3.0, 3.0)

    # -- the integrator ------------------------------------------------

    def step_tick(self, seconds=TICK_SECONDS):
        """Advance the simulation. Returns the Readout at the end of the tick."""
        s = self.state
        if s.status not in LIVE_STATUSES:
            return self.readout()

        # Re-seed per tick from the tick number, so a flight resumed from disk
        # produces exactly the same turbulence as one flown straight through.
        # Serialising the Mersenne Twister state would also work; deriving the
        # stream from the tick makes the run reproducible however it was reached.
        self._rng = random.Random(s.seed * 7919 + s.tick)

        substeps = max(1, int(round(seconds / SUBSTEP_S)))
        dt = seconds / substeps
        previous_x, previous_y = s.x_nm, s.y_nm
        self._refresh_terrain_effects()

        for _ in range(substeps):
            if s.on_ground:
                self._ground_substep(dt)
            else:
                self._substep(dt)
            if s.status not in LIVE_STATUSES:
                break

        s.time_of_day_h = (s.time_of_day_h + seconds / 3600.0) % 24.0
        self.weather.advance_to(s.elapsed_s)

        self._record_flight(previous_x, previous_y)

        if self.route.advance_if_reached(s.x_nm, s.y_nm):
            self.sync_route()

        # Keep the surrounding world realised as the aircraft moves.
        if math.hypot(
            s.x_nm - self._loaded_at[0], s.y_nm - self._loaded_at[1]
        ) > AIRFIELD_RELOAD_NM:
            self.airfields.ensure_loaded(s.x_nm, s.y_nm)
            self._loaded_at = (s.x_nm, s.y_nm)

        s.tick += 1
        return self.readout()

    def _substep(self, dt):
        s = self.state
        craft = self.aircraft
        self._update_turbulence(dt)

        # The autopilot writes the same commanded pitch, bank and throttle a
        # pilot would, so everything below is unchanged by its presence.
        autopilot.update(self, dt)

        # --- lateral-directional: sideslip, before the roll law reads it ---
        self._update_sideslip(dt)

        # A departed wing does not obey the sidestick. Establish that first,
        # because it governs how much of the pilot's command gets through.
        departed = (s.pitch_deg - s.gamma_deg) > craft.alpha_crit_deg
        authority = STALL_CONTROL_AUTHORITY if departed else 1.0

        # --- control laws: slew actual attitude toward commanded ---
        if s.cmd_heading_deg is not None:
            error = wrap180(s.cmd_heading_deg - s.heading_deg)
            if abs(error) < 1.0:
                s.cmd_bank_deg = 0.0
                if abs(s.bank_deg) < 1.0:
                    s.heading_deg = s.cmd_heading_deg
                    s.cmd_heading_deg = None
            else:
                # Bank proportionally to the error, capped at a normal 25 deg.
                target = clamp(abs(error) * 1.4, 5.0, 25.0)
                s.cmd_bank_deg = math.copysign(target, error)

        # Dihedral effect: sideslip rolls the aircraft. With the nose yawed
        # right, the left wing becomes the upwind wing, makes more lift, and
        # rolls you right -- which is why rudder and roll go the same way.
        #
        # Modelled as an offset on the bank the control law is holding, not as a
        # roll rate: a roll rate of a degree or two per second is simply erased
        # by 15 deg/s of roll authority on the very next substep, which would
        # make rudder produce no visible roll at all. As an offset it is what a
        # pilot actually sees -- a steady wing-down attitude the FBW trims to.
        bank_target = s.cmd_bank_deg + craft.dihedral_effect * s.sideslip_deg

        roll_step = craft.roll_rate_deg_s * dt * authority
        s.bank_deg += clamp(bank_target - s.bank_deg, -roll_step, roll_step)

        pitch_step = craft.pitch_rate_deg_s * dt * authority
        s.pitch_deg += clamp(s.cmd_pitch_deg - s.pitch_deg, -pitch_step, pitch_step)

        if departed:
            # The nose falls whether or not you want it to -- which is exactly
            # what eventually unstalls the wing, if there is height left.
            s.pitch_deg -= STALL_NOSE_DROP_DEG_S * dt

        # Turbulence perturbs attitude directly; the airframe is being shoved.
        turb_scale = self._local_turbulence()
        if turb_scale > 0.0:
            s.pitch_deg += s.turb[0] * turb_scale * 0.55 * dt
            s.bank_deg += s.turb[1] * turb_scale * 1.6 * dt

        s.bank_deg = clamp(s.bank_deg, -67.0, 67.0)
        s.pitch_deg = clamp(s.pitch_deg, -35.0, 35.0)

        # --- aerodynamics ---
        aero = self._aero_state()
        v = aero.v
        lift, drag, thrust = aero.lift, aero.drag, aero.thrust
        alpha_rad = math.radians(aero.alpha_deg)
        gamma_rad = math.radians(s.gamma_deg)
        bank_rad = math.radians(s.bank_deg)

        # --- equations of motion ---
        dv = (thrust * math.cos(alpha_rad) - drag) / s.mass_kg - atm.G0 * math.sin(
            gamma_rad
        )
        dgamma = (
            lift * math.cos(bank_rad)
            + thrust * math.sin(alpha_rad)
            - s.mass_kg * atm.G0 * math.cos(gamma_rad)
        ) / (s.mass_kg * v)
        dpsi = (lift * math.sin(bank_rad)) / (s.mass_kg * v)

        s.tas_ms = max(20.0, s.tas_ms + dv * dt)
        s.gamma_deg = clamp(s.gamma_deg + math.degrees(dgamma * dt), -89.0, 89.0)
        s.heading_deg = wrap360(s.heading_deg + math.degrees(dpsi * dt))

        # --- altitude, including vertical gusts ---
        v = max(s.tas_ms, 20.0)
        vs_ms = v * math.sin(math.radians(s.gamma_deg))
        gust_fpm = s.turb[2] * self.weather.vertical_gust_fpm * 0.33
        # Air flowing over sloping ground must go up the windward face and down
        # the lee one. In the mountains that is not a small number.
        gust_fpm += self._orographic_fpm
        s.altitude_ft += vs_ms * atm.FT_PER_M * dt + gust_fpm / 60.0 * dt

        # --- ground track: air mass velocity plus wind ---
        # The aircraft travels along its flight path, not along its nose: with
        # sideslip, those differ by beta.
        vx, vy = self._velocity_over_ground_ms(v)
        s.x_nm += vx * dt * atm.NM_PER_M
        s.y_nm += vy * dt * atm.NM_PER_M

        # --- fuel ---
        if thrust > 0.0:
            burn = craft.tsfc * thrust * dt
            burn = min(burn, s.fuel_kg)
            s.fuel_kg -= burn
            s.mass_kg -= burn
            if s.fuel_kg <= 0.0:
                s.fuel_kg = 0.0
                s.engines_running = False

        s.elapsed_s += dt

        # --- terminal conditions ---
        # True load factor, not the 1/cos(bank) approximation: a hard pull at
        # wings level loads the airframe just as surely as a steep turn does.
        load_factor = lift / (s.mass_kg * atm.G0)
        if load_factor > STRUCTURAL_FAILURE_G or (
            atm.tas_to_ias(v, s.altitude_ft) * atm.KT_PER_MS > craft.vmo_kt + 65.0
        ):
            self._record_impact()
            s.status = STRUCTURAL_FAILURE
            return

        ground_ft = self.terrain.elevation(s.x_nm, s.y_nm)
        if s.altitude_ft <= ground_ft:
            self._touch_down(ground_ft)

    def _touch_down(self, ground_ft):
        """The wheels have met the ground. Where, and how?

        Off a runway this is a crash, as it always was. On one it is an arrival,
        graded, and possibly still a disaster.
        """
        s = self.state
        field = self.airfields.over_runway(s.x_nm, s.y_nm)
        on_runway = field is not None
        if field is None:
            field = self.airfields.over_airfield_surface(s.x_nm, s.y_nm)
        if field is None:
            self._record_impact(ground_ft)
            s.status = CRASHED_TERRAIN
            return

        verdict = landing.grade_touchdown(self, field, self.readout(), on_runway)
        s.touchdown = asdict(verdict)
        if not verdict.survivable:
            self._record_impact(ground_ft)
            s.status = CRASHED_TERRAIN
            return

        # Down, and in one piece. Settle onto the runway and start the rollout.
        s.on_ground = True
        s.status = ROLLOUT
        s.altitude_ft = ground_ft
        s.gamma_deg = 0.0
        s.bank_deg = 0.0
        s.cmd_bank_deg = 0.0
        s.cmd_heading_deg = None
        s.pitch_deg = s.cmd_pitch_deg = 0.0
        s.sideslip_deg = 0.0
        s.throttle_pct = 0.0
        s.landing_field_ident = field.ident

    def _ground_substep(self, dt):
        """Rolling out: friction, reverse thrust and the end of the runway."""
        s = self.state
        field = self.airfields.by_ident(
            s.landing_field_ident, s.x_nm, s.y_nm, radius_nm=15.0
        )
        if field is None:
            s.status = LANDED
            return

        direction = field.landing_direction_for_heading(s.heading_deg)
        rad = math.radians(direction)

        decel = landing.rollout_deceleration(self)
        s.tas_ms = max(0.0, s.tas_ms - decel * dt)

        # Rolling straight down the runway; the nosewheel keeps it there.
        s.heading_deg = direction
        s.altitude_ft = self.terrain.elevation(s.x_nm, s.y_nm)
        s.x_nm += math.sin(rad) * s.tas_ms * dt * atm.NM_PER_M
        s.y_nm += math.cos(rad) * s.tas_ms * dt * atm.NM_PER_M

        if self._thrust_n() > 0.0:
            burn = min(
                self.aircraft.tsfc * self._thrust_n() * dt, s.fuel_kg
            )
            s.fuel_kg -= burn
            s.mass_kg -= burn
        s.elapsed_s += dt

        along, _across = field.frame_for(s.x_nm, s.y_nm, direction)
        if along > field.runway_length_ft:
            self._record_impact()
            s.status = OVERRUN
            return

        if s.tas_ms * atm.KT_PER_MS < landing.STOPPED_KT:
            s.tas_ms = 0.0
            s.status = LANDED

    def _record_flight(self, previous_x, previous_y):
        """Update the running flight record used by the debrief."""
        s = self.state
        readout = self.readout()
        s.distance_flown_nm += math.hypot(s.x_nm - previous_x, s.y_nm - previous_y)
        s.max_altitude_ft = max(s.max_altitude_ft, s.altitude_ft)
        s.min_agl_ft = min(s.min_agl_ft, readout.agl_ft)
        s.max_ias_kt = max(s.max_ias_kt, readout.ias_kt)
        s.max_mach = max(s.max_mach, readout.mach)
        s.max_load_factor = max(s.max_load_factor, readout.load_factor)
        for warning in readout.warnings:
            if warning not in s.warnings_seen:
                s.warnings_seen.append(warning)

    def _record_impact(self, ground_ft=None):
        s = self.state
        s.impact_ias_kt = atm.tas_to_ias(s.tas_ms, s.altitude_ft) * atm.KT_PER_MS
        s.impact_vs_fpm = (
            s.tas_ms * math.sin(math.radians(s.gamma_deg)) * atm.FPM_PER_MS
        )
        s.impact_elevation_ft = (
            ground_ft
            if ground_ft is not None
            else self.terrain.elevation(s.x_nm, s.y_nm)
        )
        s.impact_feature = self.terrain.feature_name(s.x_nm, s.y_nm)

    # -- instrumentation -----------------------------------------------

    def readout(self):
        s = self.state
        craft = self.aircraft
        aero = self._aero_state()
        # _aero_state clamps airspeed to a floor so the force equations cannot
        # divide by zero. That floor must not reach the instruments: an aircraft
        # stopped on the runway reported 48 knots before this distinction.
        v = max(s.tas_ms, 0.0)

        ias_kt = atm.tas_to_ias(v, s.altitude_ft) * atm.KT_PER_MS
        tas_kt = v * atm.KT_PER_MS
        mach_number = aero.mach
        alpha_deg = aero.alpha_deg
        stalled = aero.stalled

        terrain_ft = self.terrain.elevation(s.x_nm, s.y_nm)
        agl_ft = s.altitude_ft - terrain_ft
        vs_fpm = v * math.sin(math.radians(s.gamma_deg)) * atm.FPM_PER_MS

        # Ground track including both sideslip and wind.
        vx, vy = self._velocity_over_ground_ms(v)
        ground_speed_kt = math.hypot(vx, vy) * atm.KT_PER_MS
        track_deg = wrap360(math.degrees(math.atan2(vx, vy)))
        # Total angle between where the nose points and where the aircraft goes.
        # Sideslip is the part the pilot caused; the rest is wind.
        drift_deg = wrap180(track_deg - s.heading_deg)
        wind_drift_deg = wrap180(drift_deg + s.sideslip_deg)

        thrust = aero.thrust
        load_factor = aero.lift / (s.mass_kg * atm.G0)
        # Vs is quoted at the load factor needed to *hold altitude* at this bank
        # angle. Using the instantaneous load factor instead would report a
        # comfortingly low stall speed at the exact moment the wing gave up.
        manoeuvring_n = 1.0 / max(math.cos(math.radians(s.bank_deg)), 0.05)
        stall_ias_kt = (
            craft.stall_speed_ias_ms(s.mass_kg, manoeuvring_n, s.flaps) * atm.KT_PER_MS
        )

        local_wind_kt, local_wind_dir = self.weather.wind_at(agl_ft)

        ahead_nm, ahead_ft = self.terrain.highest_ahead(
            s.x_nm, s.y_nm, s.heading_deg, max_nm=12.0
        )
        ahead_pos = self.terrain.ahead(s.x_nm, s.y_nm, s.heading_deg, ahead_nm)

        readout = Readout(
            ias_kt=ias_kt,
            tas_kt=tas_kt,
            ground_speed_kt=ground_speed_kt,
            mach=mach_number,
            altitude_ft=s.altitude_ft,
            agl_ft=agl_ft,
            terrain_ft=terrain_ft,
            vertical_speed_fpm=vs_fpm,
            pitch_deg=s.pitch_deg,
            bank_deg=s.bank_deg,
            heading_deg=s.heading_deg,
            track_deg=track_deg,
            drift_deg=drift_deg,
            alpha_deg=alpha_deg,
            sideslip_deg=s.sideslip_deg,
            wind_drift_deg=wind_drift_deg,
            rudder_deg=s.rudder_deg,
            rudder_limit_deg=self.max_rudder_deg(),
            engines_running_count=len(self._running_engines()),
            load_factor=load_factor,
            throttle_pct=s.throttle_pct,
            thrust_n=thrust,
            fuel_kg=s.fuel_kg,
            fuel_pct=100.0 * s.fuel_kg / craft.fuel_capacity_kg,
            fuel_flow_kgh=craft.tsfc * thrust * 3600.0,
            mass_kg=s.mass_kg,
            stall_ias_kt=stall_ias_kt,
            stalled=stalled,
            terrain_ahead_nm=ahead_nm,
            terrain_ahead_ft=ahead_ft,
            terrain_ahead_name=self.terrain.feature_name(*ahead_pos),
            wind_speed_kt=local_wind_kt,
            wind_dir_deg=local_wind_dir,
            rotor_turbulence=self._mechanical_turbulence,
            orographic_fpm=self._orographic_fpm,
        )
        readout.approach = landing.approach_guidance(self)
        readout.vref_kt = landing.vref_kt(self)
        readout.leg = navigation.leg_for(self, readout)
        readout.warnings = self._warnings(readout)
        return readout

    def _warnings(self, r):
        s = self.state
        craft = self.aircraft
        out = []

        if r.stalled:
            out.append("STALL")
        elif r.alpha_deg > craft.alpha_crit_deg - STALL_WARNING_MARGIN_DEG:
            out.append("STALL WARNING")
        elif r.ias_kt < r.stall_ias_kt * 1.10:
            out.append("LOW SPEED")

        if r.ias_kt > craft.vmo_kt or r.mach > craft.mmo:
            out.append("OVERSPEED")

        if r.load_factor > OVERSTRESS_G:
            out.append("OVERSTRESS")

        # GPWS, suppressed when the aircraft is configured to land and tracking
        # a runway. A real system does the same: without it, every correct
        # approach sets off a terrain warning at 500 ft, and a warning that
        # fires on every landing is a warning nobody reads.
        landing_configured = (
            s.gear_down
            and s.flaps >= 2
            and r.approach is not None
            and r.approach.on_approach
            and r.approach.distance_nm < 10.0
        )
        if landing_configured:
            pass
        elif r.agl_ft < GPWS_HARD_FT:
            out.append("TERRAIN -- PULL UP")
        elif r.agl_ft < GPWS_SOFT_FT:
            out.append("TERRAIN")
        elif r.terrain_ahead_ft > s.altitude_ft and r.terrain_ahead_nm < 8.0:
            out.append("TERRAIN AHEAD")

        if s.engines_failed:
            out.append(
                "ENGINE FAILURE ({}/{})".format(
                    r.engines_running_count, craft.engine_count
                )
            )
            # Losing directional control on the remaining engines: either beta
            # has run away, or the rudder is on the stops and still not enough.
            if abs(r.sideslip_deg) > VMC_SIDESLIP_DEG or (
                abs(r.rudder_deg) >= r.rudder_limit_deg - 0.5
                and abs(r.sideslip_deg) > 6.0
            ):
                out.append("VMC -- DIRECTIONAL CONTROL")

        if not s.engines_running:
            out.append("ENGINES OUT")
        elif r.fuel_pct < LOW_FUEL_FRACTION * 100.0:
            out.append("LOW FUEL")

        if s.flaps > 0 and r.ias_kt > fleet.FLAP_LIMIT_KT[s.flaps]:
            out.append("FLAP OVERSPEED")

        if s.gear_down and r.ias_kt > 280.0:
            out.append("GEAR OVERSPEED")

        return out
