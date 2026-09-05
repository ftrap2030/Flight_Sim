"""Track-up ASCII plan view of the surrounding terrain.

The instrument panel answers "what is directly ahead of me?" with a single
number. That is not enough to fly a valley: at 1,500 ft in cloud you need to
know whether the ground opens to the left or the right, and the panel cannot
tell you. This does.

Terrain is drawn *relative to the aircraft's own altitude*, not as absolute
elevation, because the only question that matters at low level is what you can
hit. Ground far below you and ground about to kill you should not look alike.

The view is track-up -- the nose always points up the page -- since that is how
a pilot thinks in the moment, and the aircraft sits low in the grid so most of
the display is the ground you are flying toward rather than the ground you have
already survived.
"""

import math

WIDTH = 41
HEIGHT = 15
# The aircraft sits below centre: 10 rows of terrain ahead, 4 behind.
AIRCRAFT_ROW = 10

DEFAULT_SPAN_NM = 15.0

# A terminal character cell is roughly twice as tall as it is wide, so a row
# must span twice the distance of a column for the map to keep its shape.
ROW_TO_COL_ASPECT = 2.0

AIRCRAFT_SYMBOL = "@"
AIRFIELD_SYMBOL = "A"
WAYPOINT_SYMBOL = "*"

# Terrain height relative to the aircraft, in feet, and the glyph for each band.
# Ordered from safest to most dangerous.
TERRAIN_BANDS = [
    (-2000.0, "."),   # more than 2,000 ft below
    (-1000.0, "-"),   # 1,000 to 2,000 ft below
    (0.0, "~"),       # within 1,000 ft below
    (1000.0, "^"),    # up to 1,000 ft above
]
TERRAIN_ABOVE = "#"   # more than 1,000 ft above -- this is what kills you

_COMPASS_ARROWS = ["|^", "/^", "->", "\\v", "|v", "/v", "<-", "\\^"]


def _band_glyph(relative_ft):
    for threshold, glyph in TERRAIN_BANDS:
        if relative_ft < threshold:
            return glyph
    return TERRAIN_ABOVE


def _arrow_for_bearing(bearing_deg):
    """A crude eight-point arrow for a relative bearing."""
    index = int(((bearing_deg % 360.0) + 22.5) // 45.0) % 8
    return _COMPASS_ARROWS[index]


def airfield_overlays(sim, span_nm=DEFAULT_SPAN_NM):
    """Every airfield that could fall inside the display."""
    state = sim.state
    source = getattr(sim, "airfields", None)
    if source is None:
        return []
    return [
        (a.x_nm, a.y_nm, AIRFIELD_SYMBOL)
        for a in source.near(state.x_nm, state.y_nm, span_nm * 2.0)
    ]


def render(sim, readout, span_nm=DEFAULT_SPAN_NM, overlays=None):
    """Draw the plan view.

    `overlays` is an optional iterable of (x_nm, y_nm, symbol) to stamp onto the
    map -- airfields and waypoints, once those exist.
    """
    state = sim.state
    heading_rad = math.radians(state.heading_deg)
    sin_hdg = math.sin(heading_rad)
    cos_hdg = math.cos(heading_rad)

    nm_per_col = 2.0 * span_nm / (WIDTH - 1)
    nm_per_row = nm_per_col * ROW_TO_COL_ASPECT

    centre_col = WIDTH // 2

    def to_world(col, row):
        """Screen cell -> world position, rotating the grid into track-up."""
        forward = (AIRCRAFT_ROW - row) * nm_per_row
        right = (col - centre_col) * nm_per_col
        return (
            state.x_nm + forward * sin_hdg + right * cos_hdg,
            state.y_nm + forward * cos_hdg - right * sin_hdg,
        )

    grid = []
    for row in range(HEIGHT):
        line = []
        for col in range(WIDTH):
            world_x, world_y = to_world(col, row)
            elevation = sim.terrain.elevation(world_x, world_y)
            line.append(_band_glyph(elevation - state.altitude_ft))
        grid.append(line)

    # Stamp overlays by projecting each back into grid space.
    if overlays is None:
        overlays = airfield_overlays(sim, span_nm)
    for entry in overlays:
        world_x, world_y, symbol = entry[0], entry[1], entry[2]
        dx = world_x - state.x_nm
        dy = world_y - state.y_nm
        forward = dx * sin_hdg + dy * cos_hdg
        right = dx * cos_hdg - dy * sin_hdg
        col = int(round(centre_col + right / nm_per_col))
        row = int(round(AIRCRAFT_ROW - forward / nm_per_row))
        if 0 <= col < WIDTH and 0 <= row < HEIGHT:
            grid[row][col] = symbol

    grid[AIRCRAFT_ROW][centre_col] = AIRCRAFT_SYMBOL

    north_arrow = _arrow_for_bearing(-state.heading_deg)
    forward_nm = AIRCRAFT_ROW * nm_per_row

    lines = ["```"]
    lines.append(
        "  TERRAIN  track-up   HDG {:03.0f}   north {}   {:.0f} nm ahead, "
        "{:.0f} nm wide".format(
            state.heading_deg, north_arrow, forward_nm, span_nm * 2
        )
    )
    lines.append("  " + "-" * WIDTH)
    for row in range(HEIGHT):
        lines.append("  " + "".join(grid[row]))
    lines.append("  " + "-" * WIDTH)
    lines.append(
        "  relative to you at {:,.0f} ft:  "
        ".  >2000 below   -  1-2000 below   ~  <1000 below".format(
            state.altitude_ft
        )
    )
    lines.append(
        "  {}  YOU   {}  airfield             ^  up to 1000 ABOVE"
        "   #  >1000 ABOVE".format(AIRCRAFT_SYMBOL, AIRFIELD_SYMBOL)
    )
    lines.append("```")
    return "\n".join(lines)
