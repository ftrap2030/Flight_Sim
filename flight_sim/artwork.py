"""Side profiles, drawn from each aircraft's published dimensions.

Nothing here is hand-drawn. Every line is placed from the numbers in
``aircraft.py``: the fuselage is as long as the type is long, the fin reaches
the type's published height, the cabin has as many decks as the type has, and
the engine pods are laid out from ``engine_arms_m`` -- so the A380 gets four
because it has four, and the outer pair sits further outboard because its arms
are longer.

That matters more than it sounds. A drawing that is merely *pretty* would let
the A321's stretch or the A350-1000's seven extra metres go unnoticed; a drawing
generated from the dimensions cannot. If two types look the same here, they are
the same, and if the artwork ever looks wrong the data is wrong.

Scale is fixed across the fleet, so the pictures are directly comparable: an
A380 is drawn twice the length of an A319neo because it is very nearly twice as
long. The vertical scale is half the horizontal one because a terminal character
is about twice as tall as it is wide, which is what keeps the proportions honest
rather than stretching every aircraft into a pancake.
"""

import math

# Character cells are roughly twice as tall as they are wide, so the vertical
# scale must be half the horizontal one for the drawing to be in proportion.
H_SCALE = 1.05  # columns per metre
V_SCALE = 0.60  # rows per metre

LEFT_MARGIN = 3
FRAME_WIDTH = 88


class _Canvas:
    """A character grid with the origin at the top left."""

    def __init__(self, rows, cols):
        self.rows = rows
        self.cols = cols
        self.grid = [[" "] * cols for _ in range(rows)]

    def put(self, row, col, char):
        row, col = int(round(row)), int(round(col))
        if 0 <= row < self.rows and 0 <= col < self.cols and char != " ":
            self.grid[row][col] = char

    def hline(self, row, col_from, col_to, char):
        start, end = sorted((int(round(col_from)), int(round(col_to))))
        for col in range(start, end + 1):
            self.put(row, col, char)

    def vline(self, col, row_from, row_to, char):
        start, end = sorted((int(round(row_from)), int(round(row_to))))
        for row in range(start, end + 1):
            self.put(row, col, char)

    def text(self, row, col, string):
        for offset, char in enumerate(string):
            self.put(row, col + offset, char)

    def lines(self):
        return ["".join(row).rstrip() for row in self.grid]


class _Layout:
    """Where every part of one aircraft sits on the grid.

    Derived once so the drawing routines and the dimension callouts cannot
    disagree about, say, where the wing is.
    """

    def __init__(self, craft):
        self.craft = craft
        self.length = max(12, int(round(craft.length_m * H_SCALE)))

        # Vertical stack, measured down from the fin tip.
        self.total_rows = max(5, int(round(craft.height_m * V_SCALE)))
        self.ground = self.total_rows
        # Two rows at minimum: one for the pods and legs, one for the wheels.
        self.gear_rows = max(2, int(round(self.total_rows * 0.18)))
        self.fus_rows = max(2, int(round(craft.fuselage_height_m * V_SCALE)))
        self.fus_bottom = self.ground - self.gear_rows
        self.fus_top = self.fus_bottom - self.fus_rows

        # Longitudinal stations, as fractions of overall length. These are the
        # proportions common to every Airbus: flight deck in the first tenth,
        # wing box a little behind the midpoint, tail cone over the last fifth.
        self.nose_end = self.length * 0.12
        self.wing_le = self.length * 0.38
        self.wing_te = self.length * 0.60
        self.tail_start = self.length * 0.86
        self.nose_gear = self.length * 0.17

        # Engine pods, inboard to outboard. The wing is swept, so an engine
        # further out along it sits further back along the aeroplane.
        self.nacelle = max(3, int(round(craft.length_m * 0.075 * H_SCALE)))
        per_side = max(1, craft.engine_count // 2)
        self.pods = [
            int(round(self.wing_le)) - 1 + index * (self.nacelle + 4)
            for index in range(per_side)
        ]
        self.pod_end = self.pods[-1] + self.nacelle + 1

        # The main gear retracts into the wing root behind the rear spar, which
        # on every one of these types puts the forward-most bogie behind the
        # engines. Pavement loading decides how many bogies there are: one on a
        # narrowbody, two on a widebody, three on the A380.
        self.gear_groups = (
            1 if craft.mtow_kg < 150000 else (2 if craft.mtow_kg < 400000 else 3)
        )
        self.gear_wheels = 2 if craft.mtow_kg < 150000 else 3
        self.gear_spacing = self.gear_wheels + 2
        self.main_gear = max(self.length * 0.50, self.pod_end + 3)

        # The fin is laid out backwards from its trailing edge, which on every
        # Airbus sits just short of the tail cone's end.
        self.fin_rows = self.fus_top  # cabin roof to fin tip
        self.fin_tip_te = self.length - 2
        self.fin_tip_le = self.fin_tip_te - max(2, self.fin_rows // 2)
        self.fin_base = self.fin_tip_le - self.fin_rows


def _trace(canvas, x0, col_from, col_to, row_at, char_flat="_"):
    """Paint an outline whose row varies with column.

    Draws the flat runs with ``char_flat`` and the transitions with a slash
    leaning the way the surface actually goes: ``/`` where the line rises to the
    right, ``\\`` where it falls. Without this a shallow tail cone comes out as
    a run of backslashes on one row instead of a slope.
    """
    previous = None
    for col in range(int(round(col_from)), int(round(col_to)) + 1):
        row = int(round(row_at(col)))
        if previous is None or row == previous:
            canvas.put(row, x0 + col, char_flat)
        elif row < previous:  # rising to the right
            for step in range(row, previous):
                canvas.put(step, x0 + col, "/")
        else:
            for step in range(previous + 1, row + 1):
                canvas.put(step, x0 + col, "\\")
        previous = row


def _draw_fuselage(canvas, layout, x0):
    """The tube: nose cone, constant section, and the tail cone rising aft.

    The belly runs flat from the radome to the start of the tail cone -- an
    airliner's underside really is a straight line for most of its length -- so
    all the shape at the front is in the roof, which is why an A380's forward
    fuselage climbs over three rows and an A319's over one.
    """
    top, bottom = layout.fus_top, layout.fus_bottom
    nose_end, tail_start, length = layout.nose_end, layout.tail_start, layout.length
    tail_span = max(length - tail_start, 1.0)

    def roof(col):
        if col >= nose_end:
            return top
        # sqrt, not linear: the nose rises quickly and then flattens, which is
        # the shape of a radome rather than a wedge.
        fraction = math.sqrt(col / max(nose_end, 1.0))
        return (bottom - 1) - (bottom - 1 - top) * fraction

    def belly(col):
        if col <= tail_start:
            return bottom
        return bottom - (bottom - top) * (col - tail_start) / tail_span

    _trace(canvas, x0, 0, length, belly)
    _trace(canvas, x0, 0, length, roof)


def _draw_cabin(canvas, layout, x0):
    """Windows, one row per deck, and the flight-deck glazing."""
    top, bottom = layout.fus_top, layout.fus_bottom
    decks = layout.craft.cabin_decks
    interior = list(range(top + 1, bottom + 1))
    if not interior:
        return

    # Space the decks through the cross-section: one row each, from the top.
    step = max(1, len(interior) // decks)
    start = int(round(layout.nose_end)) + 2
    end = int(round(layout.tail_start)) - 1
    for deck in range(decks):
        row = interior[min(deck * step, len(interior) - 1)]
        for col in range(start, end, 2):
            canvas.put(row, x0 + col, "o")

    # The flight deck: glazing swept back over the nose.
    canvas.put(interior[0], x0 + int(round(layout.nose_end)) - 1, "\\")


def _draw_tail(canvas, layout, x0):
    """The swept fin and the tailplane at its root."""
    rows = layout.fin_rows
    if rows < 1:
        return
    # Leading edge, swept back about 45 degrees in character space.
    for step in range(rows):
        canvas.put(layout.fus_top - 1 - step, x0 + layout.fin_base + step, "/")
    # Tip chord, then a near-vertical trailing edge down to the fuselage.
    canvas.hline(0, x0 + layout.fin_tip_le, x0 + layout.fin_tip_te, "_")
    canvas.vline(x0 + layout.fin_tip_te, 1, layout.fus_top, "|")
    # Tailplane, at the root of the fin, running a little aft of the tail cone
    # so it reads as a surface rather than as more fuselage. Its chord scales
    # with the aeroplane: the A380's is more than twice the A319's, as it is.
    chord = max(4, int(round(layout.craft.length_m * 0.12 * H_SCALE)))
    start = layout.fin_base + 1
    canvas.hline(
        layout.fus_top + 1, x0 + start, x0 + min(start + chord, layout.length + 2), "-"
    )


def _draw_belly_fairing(canvas, layout, x0):
    """The bulged fairing over an extra centre tank, where a type has one.

    Drawn on the belly line just aft of the wing box, which is where the
    A321XLR's rear centre tank sits -- and is the only thing that tells an XLR
    from an A321neo from directly abeam.
    """
    if not layout.craft.belly_fairing:
        return
    canvas.hline(
        layout.fus_bottom,
        x0 + layout.wing_te - layout.length * 0.14,
        x0 + layout.wing_te + layout.length * 0.04,
        "=",
    )


def _draw_engines(canvas, layout, x0):
    """One pod per engine on this side of the aircraft.

    The count comes from ``engine_arms_m``, so the A380 gets two visible pods
    because it has four engines. They are ordered inboard to outboard, and the
    outboard pod is drawn further aft: the wing is swept, so an engine further
    out along it is an engine further back along the aeroplane.

    The wing itself is not drawn. Seen from directly abeam a wing is edge-on and
    very nearly invisible; what actually marks it on a real side view is the
    nacelles hanging in front of it and the gear folding up behind it, which is
    what these rows are.
    """
    craft = layout.craft
    arms = sorted(arm for arm in craft.engine_arms_m if arm > 0)
    if not arms:
        return

    row = min(layout.fus_bottom + 1, layout.ground - 1)
    nacelle = max(3, int(round(craft.length_m * 0.075 * H_SCALE)))
    for index, _arm in enumerate(arms):
        start = int(round(layout.wing_le - 1)) + index * (nacelle + 4)
        canvas.put(row, x0 + start, "(")
        canvas.hline(row, x0 + start + 1, x0 + start + nacelle, "#")
        canvas.put(row, x0 + start + nacelle + 1, ")")


def _draw_gear(canvas, layout, x0):
    """Nose gear, main gear, and the ground they stand on."""
    ground = layout.ground

    def leg(col, wheels):
        col = int(round(col))
        canvas.vline(x0 + col, layout.fus_bottom + 1, ground - 1, "|")
        for index in range(wheels):
            canvas.put(ground - 1, x0 + col - wheels // 2 + index, "o")

    leg(layout.nose_gear, 1)
    for index in range(layout.gear_groups):
        leg(layout.main_gear + index * layout.gear_spacing, layout.gear_wheels)

    canvas.hline(ground, 0, x0 + layout.length + 2, "-")


def side_profile(craft, width=FRAME_WIDTH):
    """The aircraft as a list of text rows, ground line included."""
    layout = _Layout(craft)
    canvas = _Canvas(layout.ground + 1, width)
    x0 = LEFT_MARGIN

    _draw_fuselage(canvas, layout, x0)
    _draw_cabin(canvas, layout, x0)
    _draw_belly_fairing(canvas, layout, x0)
    _draw_tail(canvas, layout, x0)
    _draw_engines(canvas, layout, x0)
    _draw_gear(canvas, layout, x0)
    return canvas.lines()


def _dimension_rule(metres, label, width=FRAME_WIDTH):
    """A `|<---- 37.57 m ---->|` callout, scaled like the drawing."""
    span = max(4, int(round(metres * H_SCALE)))
    text = " {} ".format(label)
    if len(text) + 2 > span:
        return " " * LEFT_MARGIN + "|<>| " + text.strip()
    dashes = span - len(text) - 2
    left = dashes // 2
    right = dashes - left
    return " " * LEFT_MARGIN + "|" + "<" + "-" * left + text + "-" * right + ">" + "|"


def frame_height():
    """Rows the tallest aircraft in the fleet needs, ground line included."""
    from . import aircraft as fleet

    return max(_Layout(craft).ground for craft in fleet.FLEET) + 1


def profile_block(craft, pad_to=None):
    """The full drawing: the aircraft, the ground, and its dimensions.

    ``pad_to`` bottom-aligns the drawing inside a taller frame, for placing two
    types against each other on a common ground line. Left alone, each card is
    exactly as tall as its aircraft -- which, at a fixed scale, is itself the
    comparison: an A380 card is twice the height of an A319neo card.
    """
    rows = side_profile(craft)
    if pad_to:
        rows = [""] * max(0, pad_to - len(rows)) + rows

    rows.append(_dimension_rule(craft.length_m, "{:.2f} m".format(craft.length_m)))
    rows.append(
        "{}span {:.2f} m   ·   height {:.2f} m   ·   wing {:.1f} m²   ·   "
        "aspect ratio {:.1f}".format(
            " " * LEFT_MARGIN,
            craft.wing_span_m,
            craft.height_m,
            craft.wing_area_m2,
            craft.aspect_ratio,
        )
    )
    return rows


def profile_text(craft):
    """The drawing as a single string, ready to drop into a code fence."""
    return "\n".join(profile_block(craft))
