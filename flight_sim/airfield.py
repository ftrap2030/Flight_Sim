"""Airfields: somewhere to go, and somewhere to land.

Two sources behind one interface, so nothing downstream cares which produced a
given field:

* **Procedural** -- infinite, seeded, generated on demand. Fields are placed by
  searching for genuinely flat ground rather than by dropping a runway wherever
  a hash says so, and the runway is aligned with the flattest axis, which in
  mountainous country means it naturally follows the valley.
* **Authored** -- a hand-designed home region with named fields of deliberately
  varied difficulty. Their identities are authored; their exact positions still
  snap to flat ground found by the same search, so they sit in the same terrain
  as everything else with no seam and no separate elevation function.

Runways are graded flat, because real airports are bulldozed and a runway laid
over raw ridged noise has a two-hundred-foot hump in it. Site selection reads
`Terrain.natural_elevation` and grading is applied afterwards, so the search that
places a runway never sees the flattening it will cause.
"""

import math
from dataclasses import dataclass

from .terrain import _hash01

FT_PER_NM = 6076.12


@dataclass(frozen=True)
class Airfield:
    """One airfield with a single runway."""

    ident: str
    name: str
    x_nm: float
    y_nm: float
    elevation_ft: float
    runway_heading_deg: float
    runway_length_ft: float
    runway_width_ft: float = 150.0
    has_ils: bool = False
    note: str = ""

    @property
    def runway_length_nm(self):
        return self.runway_length_ft / FT_PER_NM

    def runway_ends(self):
        """(threshold, far end) positions in world coordinates.

        The stored position is the runway midpoint; the threshold is half a
        length back along the runway heading.
        """
        half = self.runway_length_nm / 2.0
        rad = math.radians(self.runway_heading_deg)
        dx, dy = math.sin(rad) * half, math.cos(rad) * half
        return (self.x_nm - dx, self.y_nm - dy), (self.x_nm + dx, self.y_nm + dy)

    def runway_frame(self, x_nm, y_nm):
        """Position relative to the runway, in feet.

        Returns (along, across): distance from the threshold along the runway,
        and lateral offset from the centreline with positive to the right of the
        landing direction. This is the frame a touchdown is judged in.
        """
        (thresh_x, thresh_y), _far = self.runway_ends()
        dx = (x_nm - thresh_x) * FT_PER_NM
        dy = (y_nm - thresh_y) * FT_PER_NM
        rad = math.radians(self.runway_heading_deg)
        along = dx * math.sin(rad) + dy * math.cos(rad)
        across = dx * math.cos(rad) - dy * math.sin(rad)
        return along, across

    def is_over_runway(self, x_nm, y_nm):
        along, across = self.runway_frame(x_nm, y_nm)
        return (
            0.0 <= along <= self.runway_length_ft
            and abs(across) <= self.runway_width_ft / 2.0
        )

    def distance_nm(self, x_nm, y_nm):
        return math.hypot(x_nm - self.x_nm, y_nm - self.y_nm)

    def bearing_from(self, x_nm, y_nm):
        """Compass bearing from a position to the airfield."""
        return math.degrees(
            math.atan2(self.x_nm - x_nm, self.y_nm - y_nm)
        ) % 360.0

    def describe(self):
        surface = "{:,.0f} ft".format(self.runway_length_ft)
        ils = "ILS" if self.has_ils else "visual only"
        return "{} ({}) — elev {:,.0f} ft, runway {:02.0f} {}, {}".format(
            self.name,
            self.ident,
            self.elevation_ft,
            self.runway_heading_deg / 10.0,
            surface,
            ils,
        )


# ---------------------------------------------------------------------------
# Site selection
# ---------------------------------------------------------------------------

HEADING_CANDIDATES = 8
ROUGHNESS_SAMPLES = 7

# How much natural relief along the runway axis we are willing to bulldoze. Real
# airports do substantial cut and fill; this only rejects sites that would need
# a viaduct.
MAX_GRADEABLE_ROUGHNESS_FT = 420.0

# The graded apron, as a multiple of runway length: flat out to INNER, blended
# back to natural ground by OUTER.
GRADE_INNER_FACTOR = 0.62
GRADE_BLEND_NM = 1.2


def runway_roughness(terrain, x_nm, y_nm, heading_deg, length_ft):
    """Peak-to-peak elevation change along a prospective runway, in feet."""
    half_nm = length_ft / FT_PER_NM / 2.0
    rad = math.radians(heading_deg)
    lowest, highest = math.inf, -math.inf
    for i in range(ROUGHNESS_SAMPLES):
        t = -half_nm + (2.0 * half_nm) * i / (ROUGHNESS_SAMPLES - 1)
        elevation = terrain.natural_elevation(
            x_nm + math.sin(rad) * t, y_nm + math.cos(rad) * t
        )
        lowest = min(lowest, elevation)
        highest = max(highest, elevation)
    return highest - lowest


def best_runway_heading(terrain, x_nm, y_nm, length_ft):
    """The flattest axis through a point.

    In mountainous country the flattest line through a site runs along the
    valley, so aligning the runway this way gives the same answer a surveyor
    would -- without any explicit notion of a valley.
    """
    best_heading, best_roughness = 0.0, math.inf
    for i in range(HEADING_CANDIDATES):
        heading = 180.0 * i / HEADING_CANDIDATES
        roughness = runway_roughness(terrain, x_nm, y_nm, heading, length_ft)
        if roughness < best_roughness:
            best_heading, best_roughness = heading, roughness
    return best_heading, best_roughness


def find_flat_site(terrain, centre_x, centre_y, box_nm, length_ft, seed, attempts=48):
    """Search a box for ground flat enough to take a runway.

    Two passes, because the full heading search is expensive: score candidates
    cheaply on local relief first, then run the heading search only on the best
    handful. Deterministic for a given seed.

    Returns (x, y, heading, elevation, roughness) or None.
    """
    candidates = []
    for i in range(attempts):
        x = centre_x + (_hash01(i, 3, seed) - 0.5) * 2.0 * box_nm
        y = centre_y + (_hash01(i, 7, seed + 31) - 0.5) * 2.0 * box_nm
        # Cheap local relief probe: the centre against four points around it.
        here = terrain.natural_elevation(x, y)
        spread = max(
            abs(terrain.natural_elevation(x + d, y + e) - here)
            for d, e in ((0.7, 0), (-0.7, 0), (0, 0.7), (0, -0.7))
        )
        candidates.append((spread, x, y, here))

    candidates.sort(key=lambda c: c[0])
    best = None
    for _spread, x, y, elevation in candidates[:5]:
        heading, roughness = best_runway_heading(terrain, x, y, length_ft)
        if best is None or roughness < best[4]:
            best = (x, y, heading, elevation, roughness)
    return best


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def _graded(terrain, airfield):
    """Register an airfield's graded site with the terrain, and return it.

    Called at the moment a field is created, so any elevation query near it --
    the map, the GPWS, the ground-contact check -- sees flat ground.
    """
    inner = airfield.runway_length_nm * GRADE_INNER_FACTOR
    terrain.add_graded_site(
        airfield.x_nm,
        airfield.y_nm,
        airfield.elevation_ft,
        inner,
        inner + GRADE_BLEND_NM,
    )
    return airfield


class ProceduralAirfields:
    """Infinite seeded airfields, one per populated grid cell."""

    CELL_NM = 55.0
    POPULATED_FRACTION = 0.62

    def __init__(self, terrain, seed=None):
        self.terrain = terrain
        self.seed = terrain.seed if seed is None else seed
        self._cache = {}

    def _cell_airfield(self, cell_x, cell_y):
        if (cell_x, cell_y) in self._cache:
            return self._cache[(cell_x, cell_y)]

        airfield = None
        if _hash01(cell_x, cell_y, self.seed + 6151) <= self.POPULATED_FRACTION:
            centre_x = (cell_x + 0.5) * self.CELL_NM
            centre_y = (cell_y + 0.5) * self.CELL_NM
            cell_seed = self.seed + cell_x * 7919 + cell_y * 104729
            length_ft = _runway_length_for(cell_seed)
            site = find_flat_site(
                self.terrain,
                centre_x,
                centre_y,
                self.CELL_NM * 0.42,
                length_ft,
                cell_seed,
            )
            # Reject sites the search could not make flat enough to land on.
            if site is not None and site[4] < MAX_GRADEABLE_ROUGHNESS_FT:
                x, y, heading, elevation, _roughness = site
                airfield = _graded(self.terrain, Airfield(
                    ident=_procedural_ident(cell_x, cell_y, self.seed),
                    name=self.terrain.feature_name(x, y) + " Field",
                    x_nm=x,
                    y_nm=y,
                    elevation_ft=elevation,
                    runway_heading_deg=heading,
                    runway_length_ft=length_ft,
                    has_ils=_hash01(cell_x, cell_y, self.seed + 991) < 0.45,
                ))

        self._cache[(cell_x, cell_y)] = airfield
        return airfield

    def near(self, x_nm, y_nm, radius_nm=90.0):
        """Every airfield within `radius_nm`, nearest first."""
        reach = int(radius_nm / self.CELL_NM) + 1
        centre_x = int(math.floor(x_nm / self.CELL_NM))
        centre_y = int(math.floor(y_nm / self.CELL_NM))
        found = []
        for cell_x in range(centre_x - reach, centre_x + reach + 1):
            for cell_y in range(centre_y - reach, centre_y + reach + 1):
                airfield = self._cell_airfield(cell_x, cell_y)
                if airfield and airfield.distance_nm(x_nm, y_nm) <= radius_nm:
                    found.append(airfield)
        found.sort(key=lambda a: a.distance_nm(x_nm, y_nm))
        return found


def _runway_length_for(seed_value):
    roll = _hash01(int(seed_value) & 0xFFFF, 17, 4441)
    if roll < 0.25:
        return 5200.0  # short: a real problem for the widebodies
    if roll < 0.72:
        return 7800.0
    return 11500.0


_IDENT_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def _procedural_ident(cell_x, cell_y, seed):
    """A four-letter code, X-prefixed so it cannot be read as a real ICAO one."""
    letters = "".join(
        _IDENT_LETTERS[
            int(_hash01(cell_x, cell_y, seed + 200 + i * 37) * len(_IDENT_LETTERS))
        ]
        for i in range(3)
    )
    return "X" + letters


# ---------------------------------------------------------------------------
# The authored home region
# ---------------------------------------------------------------------------

HOME_REGION_NAME = "The Anfell Valleys"
HOME_CENTRE_NM = (0.0, 0.0)
HOME_RADIUS_NM = 70.0

# Authored identity -- name, character, runway, ILS -- with the position given
# only as an intent. The flat-ground search decides where each one actually
# sits, so an authored field is as terrain-consistent as a procedural one.
_HOME_SPEC = [
    {
        "ident": "ANFL",
        "name": "Anfell International",
        "offset": (-18.0, 6.0),
        "length_ft": 12200.0,
        "width_ft": 200.0,
        "has_ils": True,
        "note": "The easy one. Long, wide, instrument-equipped, low ground all "
                "around. Everything in the fleet fits, including the A380.",
    },
    {
        "ident": "KEBR",
        "name": "Kettlebridge",
        "offset": (14.0, 22.0),
        "length_ft": 7600.0,
        "has_ils": True,
        "note": "A working regional field. Fine for the narrowbodies, tight for "
                "an A350 and out of the question for an A380.",
    },
    {
        "ident": "HRWD",
        "name": "Harrow Deep",
        "offset": (30.0, -16.0),
        "length_ft": 5400.0,
        "width_ft": 120.0,
        "has_ils": False,
        "note": "Short, narrow, no ILS, and set well back in the hills. Visual "
                "approach only, and you will want to be slow and stable early.",
    },
    {
        "ident": "VSPR",
        "name": "Vesper Shelf",
        "offset": (-8.0, -34.0),
        "length_ft": 6400.0,
        "has_ils": False,
        "note": "High ground. Thin air means a longer roll and a faster true "
                "airspeed for the same indicated -- the numbers on the panel lie "
                "to you about how fast you are really arriving.",
    },
    {
        "ident": "CROW",
        "name": "Crowmarsh",
        "offset": (42.0, 30.0),
        "length_ft": 8800.0,
        "has_ils": True,
        "note": "Long enough to be comfortable, but the approach threads between "
                "two ridges. In fog this is the interesting one.",
    },
]


class AuthoredAirfields:
    """The hand-designed home region."""

    def __init__(self, terrain):
        self.terrain = terrain
        self._fields = None

    @property
    def fields(self):
        if self._fields is None:
            self._fields = [self._build(spec) for spec in _HOME_SPEC]
        return self._fields

    def _build(self, spec):
        target_x = HOME_CENTRE_NM[0] + spec["offset"][0]
        target_y = HOME_CENTRE_NM[1] + spec["offset"][1]
        length_ft = spec["length_ft"]
        site = find_flat_site(
            self.terrain,
            target_x,
            target_y,
            8.0,
            length_ft,
            self.terrain.seed + sum(ord(c) for c in spec["ident"]),
        )
        x, y, heading, elevation, _roughness = site
        return _graded(self.terrain, Airfield(
            ident=spec["ident"],
            name=spec["name"],
            x_nm=x,
            y_nm=y,
            elevation_ft=elevation,
            runway_heading_deg=heading,
            runway_length_ft=length_ft,
            runway_width_ft=spec.get("width_ft", 150.0),
            has_ils=spec["has_ils"],
            note=spec["note"],
        ))

    def near(self, x_nm, y_nm, radius_nm=90.0):
        found = [
            a for a in self.fields if a.distance_nm(x_nm, y_nm) <= radius_nm
        ]
        found.sort(key=lambda a: a.distance_nm(x_nm, y_nm))
        return found


def in_home_region(x_nm, y_nm):
    return (
        math.hypot(x_nm - HOME_CENTRE_NM[0], y_nm - HOME_CENTRE_NM[1])
        <= HOME_RADIUS_NM
    )


class Airfields:
    """Authored fields inside the home region, procedural ones outside it."""

    def __init__(self, terrain):
        self.terrain = terrain
        self.authored = AuthoredAirfields(terrain)
        self.procedural = ProceduralAirfields(terrain)

    def near(self, x_nm, y_nm, radius_nm=90.0):
        found = list(self.authored.near(x_nm, y_nm, radius_nm))
        # Procedural cells overlapping the home region would drop runways on top
        # of the authored ones, so they are suppressed there.
        for airfield in self.procedural.near(x_nm, y_nm, radius_nm):
            if not in_home_region(airfield.x_nm, airfield.y_nm):
                found.append(airfield)
        found.sort(key=lambda a: a.distance_nm(x_nm, y_nm))
        return found

    def ensure_loaded(self, x_nm, y_nm, radius_nm=95.0):
        """Generate (and so grade) every field near a position.

        Grading is registered as fields are created, so anything that queries
        terrain near an airfield -- the map, the GPWS, ground contact -- must be
        sure that field exists first. Calling this keeps elevation queries
        consistent regardless of the order the world was explored in.
        """
        self.near(x_nm, y_nm, radius_nm)

    def nearest(self, x_nm, y_nm, radius_nm=120.0):
        found = self.near(x_nm, y_nm, radius_nm)
        return found[0] if found else None

    def by_ident(self, ident, x_nm=0.0, y_nm=0.0, radius_nm=250.0):
        wanted = (ident or "").strip().upper()
        for airfield in self.near(x_nm, y_nm, radius_nm):
            if airfield.ident == wanted:
                return airfield
        return None

    def over_runway(self, x_nm, y_nm):
        """The airfield whose runway this position is on, if any."""
        for airfield in self.near(x_nm, y_nm, radius_nm=6.0):
            if airfield.is_over_runway(x_nm, y_nm):
                return airfield
        return None
