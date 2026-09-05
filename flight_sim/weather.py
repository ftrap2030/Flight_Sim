"""Weather profiles.

Weather is not decoration -- each profile feeds real numbers into the physics
step (turbulence perturbations, a wind vector that displaces the ground track)
and into the narrator (visibility, cloud deck, light).
"""

import math
from dataclasses import dataclass, field


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
