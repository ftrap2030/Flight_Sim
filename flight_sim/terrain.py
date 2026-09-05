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

    def relief(self, x_nm, y_nm):
        """Local relief scale in feet -- how mountainous this region is."""
        massif = _value_noise(
            x_nm / MASSIF_SCALE_NM, y_nm / MASSIF_SCALE_NM, self.seed + 7717
        )
        return MIN_RELIEF_FT + (MAX_RELIEF_FT - MIN_RELIEF_FT) * _smoothstep(massif)

    def elevation(self, x_nm, y_nm):
        """Ground elevation in feet MSL at a point."""
        ridges = _ridged_fbm(x_nm / RIDGE_SCALE_NM, y_nm / RIDGE_SCALE_NM, self.seed)
        return BASE_ELEVATION_FT + ridges * self.relief(x_nm, y_nm)

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
            x = (_hash01(attempt, 11, self.seed + 4409) - 0.5) * 3000.0
            y = (_hash01(attempt, 22, self.seed + 8821) - 0.5) * 3000.0
            if self.elevation(x, y) > max_ground_ft:
                continue
            _distance, highest = self.highest_ahead(
                x, y, heading_deg, max_nm=corridor_nm, step_nm=1.0
            )
            if highest > max_ridge_ft:
                continue
            return x, y
        return 0.0, 0.0

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
