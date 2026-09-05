"""Weather: the immutable profile, and the living state on top of it.

Weather is not decoration -- each profile feeds real numbers into the physics
step and into the narrator. A `Weather` is the fixed character of a day; a
`WeatherState` is what it is actually doing right now, which changes as the
flight goes on and as the aircraft moves through the landscape.

Three things make it local rather than global:

* **Wind shear.** Surface friction slows and backs the wind near the ground, so
  a descent changes your drift and your airspeed.
* **Mechanical turbulence.** Wind pouring over a ridge breaks up in its lee.
  This is what the crosswind prose was already promising -- "rotor turbulence
  off the ridge arrives in hard, irregular slams" -- while turbulence was a
  per-weather constant with no idea where the terrain was.
* **Mountain wave.** Air flowing over sloping ground has to go up the windward
  face and down the lee one, which is a real and sometimes unsurvivable
  vertical velocity in the mountains.
"""

import math
from dataclasses import dataclass, field

from .terrain import _value_noise


@dataclass(frozen=True)
class Weather:
    key: str
    name: str

    turbulence: float  # 0-1, drives per-tick attitude and speed perturbation
    wind_speed_kt: float
    wind_dir_deg: float  # direction the wind is coming FROM
    gust_kt: float
    vertical_gust_fpm: float  # peak up/downdraft
    visibility_sm: float
    cloud_base_ft: float
    cloud_tops_ft: float
    lightning: bool = False
    precipitation: str = "none"
    summary: str = ""

    def turbulence_label(self):
        if self.turbulence < 0.10:
            return "NIL"
        if self.turbulence < 0.25:
            return "LIGHT"
        if self.turbulence < 0.50:
            return "MODERATE"
        if self.turbulence < 0.75:
            return "SEVERE"
        return "EXTREME"

    def wind_components(self, heading_deg):
        """Headwind and crosswind components in knots for a given heading.

        Positive headwind means wind on the nose; positive crosswind means wind
        from the right.
        """
        angle = math.radians(self.wind_dir_deg - heading_deg)
        headwind = self.wind_speed_kt * math.cos(angle)
        crosswind = -self.wind_speed_kt * math.sin(angle)
        return headwind, crosswind


CLEAR = Weather(
    key="clear",
    name="Clear Skies",
    turbulence=0.05,
    wind_speed_kt=8.0,
    wind_dir_deg=270.0,
    gust_kt=0.0,
    vertical_gust_fpm=60.0,
    visibility_sm=40.0,
    cloud_base_ft=12000.0,
    cloud_tops_ft=14000.0,
    summary=(
        "Severe clear. A high thin deck of cirrus at FL380 and nothing else "
        "between you and the ground. Visibility better than forty miles."
    ),
)

CROSSWIND = Weather(
    key="crosswind",
    name="Heavy Crosswinds",
    turbulence=0.35,
    wind_speed_kt=45.0,
    wind_dir_deg=290.0,
    gust_kt=65.0,
    vertical_gust_fpm=350.0,
    visibility_sm=25.0,
    cloud_base_ft=6500.0,
    cloud_tops_ft=11000.0,
    precipitation="none",
    summary=(
        "Forty-five knots from 290, gusting sixty-five. Ragged stratocumulus "
        "tearing downwind. You will fly this whole leg slightly sideways."
    ),
)

STORMY = Weather(
    key="stormy",
    name="Stormy",
    turbulence=0.85,
    wind_speed_kt=55.0,
    wind_dir_deg=210.0,
    gust_kt=80.0,
    vertical_gust_fpm=1800.0,
    visibility_sm=2.0,
    cloud_base_ft=2200.0,
    cloud_tops_ft=41000.0,
    lightning=True,
    precipitation="heavy rain",
    summary=(
        "A line of mature cells with tops above FL400. Severe turbulence, "
        "up- and downdraughts past 1,800 feet a minute, heavy rain, continuous "
        "lightning. The altimeter will not sit still."
    ),
)

FOGGY = Weather(
    key="foggy",
    name="Foggy",
    turbulence=0.10,
    wind_speed_kt=5.0,
    wind_dir_deg=180.0,
    gust_kt=0.0,
    vertical_gust_fpm=40.0,
    visibility_sm=0.3,
    cloud_base_ft=200.0,
    cloud_tops_ft=7200.0,
    precipitation="mist",
    summary=(
        "Dead calm air inside a radiation fog that has filled every valley to "
        "7,200 feet. Three hundred metres of visibility. The terrain is still "
        "there -- you simply will not see it coming."
    ),
)


WEATHER_OPTIONS = [CLEAR, CROSSWIND, STORMY, FOGGY]
WEATHER_BY_KEY = {w.key: w for w in WEATHER_OPTIONS}

_ALIASES = {
    "1": "clear",
    "2": "crosswind",
    "3": "stormy",
    "4": "foggy",
    "clearskies": "clear",
    "clear skies": "clear",
    "sunny": "clear",
    "heavycrosswinds": "crosswind",
    "heavy crosswinds": "crosswind",
    "crosswinds": "crosswind",
    "wind": "crosswind",
    "windy": "crosswind",
    "storm": "stormy",
    "thunderstorm": "stormy",
    "storms": "stormy",
    "fog": "foggy",
    "mist": "foggy",
}


# ---------------------------------------------------------------------------
# Living weather
# ---------------------------------------------------------------------------

# How fast conditions drift. One unit of noise time is a quarter of an hour.
EVOLUTION_SCALE_S = 900.0

# The surface friction layer: below this AGL the wind is slowed and backed.
FRICTION_LAYER_FT = 2000.0
SURFACE_WIND_FRACTION = 0.40
SURFACE_BACKING_DEG = 30.0

# Mechanical turbulence
ROTOR_SAMPLE_NM = 1.6
ROTOR_REFERENCE_WIND_KT = 40.0
ROTOR_DECAY_FT = 4500.0

# Mountain wave
WAVE_SAMPLE_NM = 2.2
WAVE_DECAY_FT = 8000.0


class WeatherState:
    """The live conditions: a profile, plus what it is doing at this moment.

    Delegates anything it does not override to the immutable profile, so the
    rest of the simulator can keep treating `sim.weather` as a Weather.
    """

    def __init__(self, profile, seed=0, elapsed_s=0.0):
        self.profile = profile
        self.seed = int(seed) & 0x7FFFFFFF
        self.wind_speed_kt = profile.wind_speed_kt
        self.wind_dir_deg = profile.wind_dir_deg
        self.visibility_sm = profile.visibility_sm
        self.turbulence = profile.turbulence
        self.frozen = False
        self.advance_to(elapsed_s)

    def hold(self, **overrides):
        """Pin the conditions and stop them drifting.

        Weather that evolves is the right default, but a test comparing two
        flights needs the air to stand still while it does so.
        """
        for name, value in overrides.items():
            setattr(self, name, value)
        self.frozen = True
        return self

    def __getattr__(self, name):
        # Only reached for attributes not set on the instance, so the live
        # values above always win over the profile's fixed ones.
        return getattr(self.profile, name)

    # -- evolution -----------------------------------------------------

    def advance_to(self, elapsed_s):
        """Drift the conditions to match the time elapsed.

        Deterministic in the seed and the clock, so a replayed flight sees the
        same weather at the same moment.
        """
        self.elapsed_s = elapsed_s
        if self.frozen:
            return
        t = elapsed_s / EVOLUTION_SCALE_S
        profile = self.profile

        self.wind_dir_deg = (
            profile.wind_dir_deg + (self._noise(t, 11) - 0.5) * 70.0
        ) % 360.0
        self.wind_speed_kt = max(
            0.0, profile.wind_speed_kt * (0.65 + 0.7 * self._noise(t, 23))
        )
        self.visibility_sm = max(
            0.05, profile.visibility_sm * (0.55 + 0.9 * self._noise(t, 37))
        )
        self.turbulence = min(
            1.0, max(0.0, profile.turbulence * (0.7 + 0.6 * self._noise(t, 53)))
        )

    def _noise(self, t, salt):
        return _value_noise(t, salt * 0.5, self.seed + salt)

    # These two must be overridden rather than delegated: reached through
    # __getattr__ they would bind to the profile and report its fixed values
    # instead of what the weather is actually doing now.
    def turbulence_label(self):
        return Weather.turbulence_label(self)

    def wind_components(self, heading_deg):
        return Weather.wind_components(self, heading_deg)

    # -- wind as a function of height ----------------------------------

    def wind_at(self, agl_ft):
        """(speed, direction) at a height above the ground.

        Surface friction both slows the wind and backs it; climbing out of the
        friction layer therefore changes drift as well as groundspeed.
        """
        fraction = min(1.0, max(0.0, agl_ft) / FRICTION_LAYER_FT) ** 0.3
        speed = self.wind_speed_kt * (
            SURFACE_WIND_FRACTION + (1.0 - SURFACE_WIND_FRACTION) * fraction
        )
        direction = (
            self.wind_dir_deg - SURFACE_BACKING_DEG * (1.0 - fraction)
        ) % 360.0
        return speed, direction

    # -- terrain coupling ----------------------------------------------

    def _upwind_slope(self, terrain, x_nm, y_nm, sample_nm):
        """Terrain gradient along the wind's direction of travel.

        Positive means the ground rises downwind of you -- you are on a windward
        face. Negative means it falls away, and you are in the lee.
        """
        travelling = math.radians(self.wind_dir_deg + 180.0)
        ahead_x = x_nm + math.sin(travelling) * sample_nm
        ahead_y = y_nm + math.cos(travelling) * sample_nm
        rise_ft = terrain.elevation(ahead_x, ahead_y) - terrain.elevation(x_nm, y_nm)
        return rise_ft / (sample_nm * 6076.12)

    def mechanical_turbulence(self, terrain, x_nm, y_nm, agl_ft):
        """Extra turbulence from wind breaking up over terrain, 0-1.

        Strongest in the lee of a ridge, in a strong wind, close to the ground.
        """
        if self.wind_speed_kt < 5.0 or agl_ft > ROTOR_DECAY_FT:
            return 0.0
        slope = self._upwind_slope(terrain, x_nm, y_nm, ROTOR_SAMPLE_NM)
        # Only the lee side is rough: descending ground downwind means the flow
        # has just come over a crest and is breaking up behind it.
        lee = max(0.0, -slope)
        wind_factor = min(2.0, self.wind_speed_kt / ROTOR_REFERENCE_WIND_KT)
        height_factor = max(0.0, 1.0 - max(0.0, agl_ft) / ROTOR_DECAY_FT)
        return min(1.0, lee * 9.0 * wind_factor * height_factor)

    def orographic_vertical_fpm(self, terrain, x_nm, y_nm, agl_ft):
        """Vertical air velocity from flow over sloping ground, in fpm.

        Air follows the terrain: up the windward face, down the lee. The wind
        component along the slope times the slope itself is the vertical speed,
        decaying with height above the ground.
        """
        if self.wind_speed_kt < 3.0:
            return 0.0
        slope = self._upwind_slope(terrain, x_nm, y_nm, WAVE_SAMPLE_NM)
        wind_fpm = self.wind_speed_kt * 101.269  # knots to feet per minute
        decay = math.exp(-max(0.0, agl_ft) / WAVE_DECAY_FT)
        return wind_fpm * slope * decay


# ---------------------------------------------------------------------------
# Time of day
# ---------------------------------------------------------------------------

def solar_elevation_deg(hour_of_day):
    """A simple solar elevation: up at 06, highest at 12, down at 18."""
    return 62.0 * math.sin(math.pi * (hour_of_day - 6.0) / 12.0)


def light_phase(hour_of_day):
    """The name of the light: what the narrator needs to know."""
    elevation = solar_elevation_deg(hour_of_day)
    if elevation < -6.0:
        return "night"
    if elevation < 3.0:
        return "dawn" if hour_of_day < 12.0 else "dusk"
    if elevation < 20.0:
        return "golden"
    return "day"


def resolve(text):
    """Look up a weather profile from menu input. None if unrecognised."""
    if text is None:
        return None
    key = text.strip().lower()
    if key in WEATHER_BY_KEY:
        return WEATHER_BY_KEY[key]
    if key in _ALIASES:
        return WEATHER_BY_KEY[_ALIASES[key]]
    compact = key.replace(" ", "").replace("-", "").replace("_", "")
    if compact in WEATHER_BY_KEY:
        return WEATHER_BY_KEY[compact]
    if compact in _ALIASES:
        return WEATHER_BY_KEY[_ALIASES[compact]]
    return None
