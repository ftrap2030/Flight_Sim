"""Procedural terrain.

Ridged multifractal noise over a hashed integer lattice. Ridged rather than
plain fractal Brownian motion because the brief calls for *jagged* ridges and
deep valleys: taking 1 - |noise| folds the field at its midline, turning smooth
hills into sharp crests, and squaring sharpens them further.

A slow, large-scale "massif" field modulates the local relief, so the world has
genuine plains, foothills and high country rather than uniform corrugation. Peak
elevations reach roughly 9,600 ft -- squarely in the path of an airliner at
5,000 ft, which is the point: the low-altitude band needs real teeth.

Everything is a pure function of (x, y, seed), so the world is infinite,
deterministic and needs no storage.
"""

import math

# Feet. The floor of the lowest valley and the ceiling of the highest peak.
BASE_ELEVATION_FT = 150.0
MIN_RELIEF_FT = 1100.0
MAX_RELIEF_FT = 8400.0

# Nautical miles per unit of noise space. Larger = broader landforms.
RIDGE_SCALE_NM = 6.0
MASSIF_SCALE_NM = 44.0

OCTAVES = 4


def _hash01(ix, iy, seed):
    """Deterministic 32-bit integer hash of a lattice point -> [0, 1)."""
    h = (ix * 374761393 + iy * 668265263 + seed * 1442695041) & 0xFFFFFFFF
    h = (h ^ (h >> 13)) & 0xFFFFFFFF
    h = (h * 1274126177) & 0xFFFFFFFF
    h = (h ^ (h >> 16)) & 0xFFFFFFFF
    return h / 4294967296.0


def _smoothstep(t):
    return t * t * (3.0 - 2.0 * t)


def _value_noise(x, y, seed):
    """Bilinearly interpolated value noise with a smoothstep fade, in [0, 1]."""
    ix = math.floor(x)
    iy = math.floor(y)
    fx = _smoothstep(x - ix)
    fy = _smoothstep(y - iy)

    ix = int(ix)
    iy = int(iy)

    n00 = _hash01(ix, iy, seed)
    n10 = _hash01(ix + 1, iy, seed)
    n01 = _hash01(ix, iy + 1, seed)
    n11 = _hash01(ix + 1, iy + 1, seed)

    top = n00 + (n10 - n00) * fx
    bottom = n01 + (n11 - n01) * fx
    return top + (bottom - top) * fy


def _ridged_fbm(x, y, seed, octaves=OCTAVES):
    """Ridged multifractal in [0, 1]. Sharp crests, wide valleys."""
    total = 0.0
    amplitude = 1.0
    frequency = 1.0
    normaliser = 0.0
    for octave in range(octaves):
        n = _value_noise(x * frequency, y * frequency, seed + octave * 1013)
        ridge = 1.0 - abs(2.0 * n - 1.0)  # fold at the midline -> crest
        ridge *= ridge  # sharpen
        total += ridge * amplitude
        normaliser += amplitude
        amplitude *= 0.5
        frequency *= 2.0
    return total / normaliser


class Terrain:
    """An infinite, deterministic landscape addressed in nautical miles."""

    def __init__(self, seed=20260905):
        self.seed = int(seed) & 0x7FFFFFFF
        # Graded sites: airports are bulldozed flat, and a runway laid over raw
        # ridged noise would have a two-hundred-foot hump in it. Each entry is
        # (x_nm, y_nm, elevation_ft, inner_nm, outer_nm) -- flat within the
        # inner radius, blended back to natural ground by the outer one.
        self.graded_sites = []
        # Approach surfaces: the protected corridor off each runway end. Real
        # airports have an obstacle limitation surface, and terrain that would
        # penetrate it is either removed or the approach is not published.
        # Unlike grading, this only ever cuts high ground *down* -- it never
        # fills a valley, so the landscape keeps its character.
        self.approach_surfaces = []

    def add_approach_surface(
        self,
        threshold_x_nm,
        threshold_y_nm,
        direction_deg,
        base_elevation_ft,
        length_nm=6.0,
        half_width_nm=0.35,
        slope_deg=2.0,
    ):
        """Protect the final approach path to a runway threshold.

        The slope is shallower than the 3-degree glidepath, so an aircraft flown
        down the glideslope has clearance the whole way in.
        """
        self.approach_surfaces.append(
            (
                threshold_x_nm,
                threshold_y_nm,
                direction_deg,
                base_elevation_ft,
                length_nm,
                half_width_nm,
                math.tan(math.radians(slope_deg)),
            )
        )

    def add_graded_site(self, x_nm, y_nm, elevation_ft, inner_nm, outer_nm):
        for site in self.graded_sites:
            if abs(site[0] - x_nm) < 1e-6 and abs(site[1] - y_nm) < 1e-6:
                return
        self.graded_sites.append(
            (x_nm, y_nm, elevation_ft, inner_nm, max(outer_nm, inner_nm + 0.1))
        )

    def clear_graded_sites(self):
        self.graded_sites = []
        self.approach_surfaces = []

    def relief(self, x_nm, y_nm):
        """Local relief scale in feet -- how mountainous this region is."""
        massif = _value_noise(
            x_nm / MASSIF_SCALE_NM, y_nm / MASSIF_SCALE_NM, self.seed + 7717
        )
        return MIN_RELIEF_FT + (MAX_RELIEF_FT - MIN_RELIEF_FT) * _smoothstep(massif)

    def natural_elevation(self, x_nm, y_nm):
        """Ground elevation before any earthmoving.

        Site selection must use this rather than `elevation`, or a graded runway
        would feed back into the search that placed it.
        """
        ridges = _ridged_fbm(x_nm / RIDGE_SCALE_NM, y_nm / RIDGE_SCALE_NM, self.seed)
        return BASE_ELEVATION_FT + ridges * self.relief(x_nm, y_nm)

    def elevation(self, x_nm, y_nm):
        """Ground elevation in feet MSL, including graded airfield sites."""
        natural = self.natural_elevation(x_nm, y_nm)
        if not self.graded_sites:
            return self._apply_approach_surfaces(x_nm, y_nm, natural)
        for site_x, site_y, site_elev, inner_nm, outer_nm in self.graded_sites:
            # Bounding-box reject first: this runs for every graded site on
            # every elevation query, and the map makes six hundred of those.
            dx = x_nm - site_x
            if dx > outer_nm or dx < -outer_nm:
                continue
            dy = y_nm - site_y
            if dy > outer_nm or dy < -outer_nm:
                continue
            distance = math.hypot(dx, dy)
            if distance >= outer_nm:
                continue
            if distance <= inner_nm:
                return site_elev
            blend = _smoothstep((distance - inner_nm) / (outer_nm - inner_nm))
            natural = site_elev + (natural - site_elev) * blend
        return self._apply_approach_surfaces(x_nm, y_nm, natural)

    def _apply_approach_surfaces(self, x_nm, y_nm, elevation_ft):
        """Cut down anything poking through a protected approach corridor."""
        for (tx, ty, direction, base_ft, length_nm, half_width_nm,
             slope) in self.approach_surfaces:
            dx = x_nm - tx
            dy = y_nm - ty
            rad = math.radians(direction)
            # Along runs *back* from the threshold, out along the approach.
            along = -(dx * math.sin(rad) + dy * math.cos(rad))
            if along <= 0.0 or along >= length_nm:
                continue
            across = abs(dx * math.cos(rad) - dy * math.sin(rad))
            if across >= half_width_nm:
                continue

            ceiling = base_ft + along * slope * 6076.12
            if elevation_ft <= ceiling:
                continue
            # Taper at the edges of the corridor and at its far end, so the cut
            # blends into the surrounding country instead of leaving a trench.
            # Both tapers act only over the outer fifth: a taper spread across
            # the whole corridor would apply half the cut in the middle, which
            # is exactly where the glidepath is lowest and the protection
            # matters most.
            edge = _smoothstep(min(1.0, (1.0 - across / half_width_nm) / 0.2))
            far = _smoothstep(min(1.0, (1.0 - along / length_nm) / 0.2))
            strength = edge * far
            elevation_ft = elevation_ft * (1.0 - strength) + ceiling * strength
        return elevation_ft

    def height_above_ground(self, x_nm, y_nm, altitude_ft):
        """AGL in feet. Negative means you are inside the rock."""
        return altitude_ft - self.elevation(x_nm, y_nm)

    def ahead(self, x_nm, y_nm, heading_deg, distance_nm):
        """Position `distance_nm` along the current heading."""
        rad = math.radians(heading_deg)
        return (
            x_nm + math.sin(rad) * distance_nm,
            y_nm + math.cos(rad) * distance_nm,
        )

    def scan_ahead(self, x_nm, y_nm, heading_deg, distances=(1, 3, 5, 10)):
        """Terrain elevation at several ranges along the nose.

        Feeds both the GPWS logic and the narrator's sense of what is coming.
        """
        samples = []
        for d in distances:
            px, py = self.ahead(x_nm, y_nm, heading_deg, d)
            samples.append((d, self.elevation(px, py)))
        return samples

    def highest_ahead(self, x_nm, y_nm, heading_deg, max_nm=12.0, step_nm=0.5):
        """The worst terrain within `max_nm` of the nose.

        Returns (distance_nm, elevation_ft). Sampled at half-mile intervals,
        which is fine enough not to step over a ridge crest at this scale.
        """
        best_d = 0.0
        best_elev = -1.0
        d = step_nm
        while d <= max_nm:
            px, py = self.ahead(x_nm, y_nm, heading_deg, d)
            elev = self.elevation(px, py)
            if elev > best_elev:
                best_elev = elev
                best_d = d
            d += step_nm
        return best_d, best_elev

    def find_start(
        self,
        altitude_ft,
        heading_deg,
        min_clearance_ft=2800.0,
        corridor_nm=18.0,
        corridor_margin_ft=900.0,
        attempts=2000,
        centre=(0.0, 0.0),
        span_nm=1500.0,
    ):
        """Pick an opening position with room to fly.

        The world is mountainous enough that a randomly chosen origin can put a
        ridge inside a minute of the nose, which makes the first turn a death
        sentence rather than a choice. This searches for somewhere that opens
        over lower ground and keeps the terrain within `corridor_nm` of the
        initial track below the aircraft -- so the high country is something you
        fly *toward*, deliberately, rather than something you start inside.

        Deterministic for a given seed. Falls back to the origin if the search
        fails, which only happens with pathological parameters.
        """
        max_ground_ft = altitude_ft - min_clearance_ft
        max_ridge_ft = altitude_ft - corridor_margin_ft

        for attempt in range(attempts):
            x = centre[0] + (_hash01(attempt, 11, self.seed + 4409) - 0.5) * 2.0 * span_nm
            y = centre[1] + (_hash01(attempt, 22, self.seed + 8821) - 0.5) * 2.0 * span_nm
            if self.elevation(x, y) > max_ground_ft:
                continue
            _distance, highest = self.highest_ahead(
                x, y, heading_deg, max_nm=corridor_nm, step_nm=1.0
            )
            if highest > max_ridge_ft:
                continue
            return x, y
        return centre

    def feature_name(self, x_nm, y_nm):
        """A stable, procedurally generated name for the landform here.

        Names are keyed to a coarse lattice cell, so the same ridge keeps the
        same name across turns -- cheap continuity that makes the world feel
        surveyed rather than randomly generated each tick.
        """
        cx = int(math.floor(x_nm / 11.0))
        cy = int(math.floor(y_nm / 11.0))
        first = _PREFIXES[int(_hash01(cx, cy, self.seed + 313) * len(_PREFIXES))]
        second = _STEMS[int(_hash01(cx, cy, self.seed + 929) * len(_STEMS))]
        kind_pool = _HIGH_KINDS if self.relief(x_nm, y_nm) > 4500 else _LOW_KINDS
        kind = kind_pool[int(_hash01(cx, cy, self.seed + 5501) * len(kind_pool))]
        return "{} {} {}".format(first, second, kind).replace("  ", " ").strip()


_PREFIXES = [
    "Black", "Grey", "Iron", "Cold", "High", "Old", "White", "Red",
    "Broken", "Long", "Sharp", "Bitter", "Storm", "Ash", "Cairn", "Dark",
]

_STEMS = [
    "Anvil", "Hawk", "Kettle", "Spire", "Wolf", "Lantern", "Thorn", "Crow",
    "Marrow", "Falcon", "Tarn", "Sable", "Quarry", "Vesper", "Harrow", "Kestrel",
]

_HIGH_KINDS = ["Ridge", "Massif", "Spur", "Horn", "Crest", "Wall", "Pinnacle"]
_LOW_KINDS = ["Downs", "Fells", "Bluffs", "Rise", "Moor", "Shoulder", "Bench"]
