"""The drawings must be generated from the data, not decorated onto it.

Every assertion here is of the form "the picture says what the numbers say".
That is the whole value of generating the artwork: if a dimension changes and
the drawing does not, one of them is lying.
"""

import unittest

from flight_sim import aircraft as fleet
from flight_sim import artwork
from flight_sim import dashboard


def width_of(craft):
    """Columns from the nose to the tail of the drawn aircraft."""
    rows = artwork.side_profile(craft)
    # The ground line spans the whole drawing, so measure the aircraft itself.
    return max(len(row) for row in rows[:-1])


def glyph_count(craft, glyph):
    return sum(row.count(glyph) for row in artwork.side_profile(craft))


class TestGeometryDrivesTheDrawing(unittest.TestCase):
    def test_a_longer_aircraft_is_drawn_longer(self):
        for shorter, longer in (
            (fleet.A319NEO, fleet.A320),
            (fleet.A320NEO, fleet.A321),
            (fleet.A330NEO, fleet.A350),
            (fleet.A350, fleet.A350K),
        ):
            self.assertLess(
                width_of(shorter), width_of(longer),
                "{} is drawn no shorter than {}".format(shorter.name, longer.name),
            )

    def test_a_taller_aircraft_is_drawn_taller(self):
        heights = [(len(artwork.side_profile(c)), c.height_m) for c in fleet.FLEET]
        for rows, metres in heights:
            self.assertGreater(rows, 4)
        by_metres = sorted(heights, key=lambda pair: pair[1])
        drawn = [rows for rows, _m in by_metres]
        self.assertEqual(drawn, sorted(drawn))

    def test_length_ratio_survives_into_the_drawing(self):
        """The A380 is drawn about twice the A319neo because it is."""
        real = fleet.A380.length_m / fleet.A319NEO.length_m
        drawn = width_of(fleet.A380) / width_of(fleet.A319NEO)
        self.assertLess(abs(drawn - real) / real, 0.08)

    def test_the_scale_is_shared_across_the_fleet(self):
        """Columns per metre must be the same for every type, or the pictures
        are not comparable and the size difference means nothing."""
        scales = [width_of(c) / c.length_m for c in fleet.FLEET]
        self.assertLess(max(scales) - min(scales), 0.12)

    def test_four_engines_are_drawn_as_two_pods_and_two_as_one(self):
        for craft in fleet.FLEET:
            pods = glyph_count(craft, "(")
            self.assertEqual(
                pods, craft.engine_count // 2,
                "{} has {} engines but {} pods drawn".format(
                    craft.name, craft.engine_count, pods
                ),
            )

    def test_the_a380_is_the_only_double_decker(self):
        double = [c for c in fleet.FLEET if c.cabin_decks == 2]
        self.assertEqual(double, [fleet.A380])
        # Two window rows on the A380, one on every other airliner. Matched on
        # the spaced-out window pattern rather than a bare count of "o", so
        # that a bogie drawn as "ooo ooo" is not mistaken for a cabin. The
        # freighter has a main deck and nobody on it, which is the next test.
        for craft in fleet.FLEET:
            if not craft.carries_passengers:
                continue
            rows = [r for r in artwork.side_profile(craft) if r.count("o o") > 3]
            self.assertEqual(
                len(rows), craft.cabin_decks,
                "{} drew {} cabin rows".format(craft.name, len(rows)),
            )

    def test_gear_bogies_follow_weight(self):
        """Pavement loading, drawn: one bogie, two, or the A380's three."""
        self.assertEqual(artwork._Layout(fleet.A320).gear_groups, 1)
        self.assertEqual(artwork._Layout(fleet.A350).gear_groups, 2)
        self.assertEqual(artwork._Layout(fleet.A380).gear_groups, 3)

    def test_the_main_gear_is_drawn_behind_the_engines(self):
        for craft in fleet.FLEET:
            layout = artwork._Layout(craft)
            self.assertGreater(
                layout.main_gear, layout.pod_end,
                "{} draws its gear through its engines".format(craft.name),
            )

    def test_only_the_xlr_carries_a_belly_fairing(self):
        with_fairing = [c for c in fleet.FLEET if c.belly_fairing]
        self.assertEqual(with_fairing, [fleet.A321XLR])
        # And it is the one thing that tells the two A321s apart on a side view.
        neo = artwork.side_profile(fleet.A321)
        xlr = artwork.side_profile(fleet.A321XLR)
        self.assertNotEqual(neo, xlr)
        self.assertEqual(glyph_count(fleet.A321, "="), 0)
        self.assertGreater(glyph_count(fleet.A321XLR, "="), 3)

    def test_only_the_beluga_carries_a_cargo_lobe(self):
        with_lobe = [c for c in fleet.FLEET if c.cargo_lobe]
        self.assertEqual(with_lobe, [fleet.BELUGA_XL])

    def test_the_lobe_steps_up_from_a_flight_deck_an_a330_would_recognise(self):
        """The step's height is the two published cross-sections, differenced.

        A BelugaXL's lower lobe *is* an A330's fuselage, and a circular section
        is as tall as it is wide -- so the flight deck's roof has to come out at
        exactly the height an A330's whole fuselage does. Nothing about the lobe
        is drawn from a number invented for the drawing.
        """
        beluga = artwork._Layout(fleet.BELUGA_XL)
        donor = artwork._Layout(fleet.A330_800)
        self.assertEqual(fleet.BELUGA_XL.fuselage_width_m, fleet.A330_800.fuselage_width_m)
        self.assertEqual(beluga.deck_rows, donor.fus_rows)
        # And the lobe is everything the taller cross-section adds above it.
        self.assertGreater(beluga.lobe_rows, 0)
        self.assertEqual(beluga.deck_top - beluga.lobe_rows, beluga.fus_top)
        for craft in fleet.FLEET:
            if craft.cargo_lobe:
                continue
            layout = artwork._Layout(craft)
            self.assertEqual(
                (layout.deck_top, layout.lobe_rows), (layout.fus_top, 0),
                "{} grew a lobe it does not have".format(craft.name),
            )

    def test_the_belugas_roof_is_low_at_the_nose_and_high_over_the_wing(self):
        """The feature, as the drawing sees it: a roof that steps up aft.

        Every airliner's roof is one flat line the length of the cabin. The
        Beluga's is two, and the pilots sit under the lower one.
        """
        layout = artwork._Layout(fleet.BELUGA_XL)
        rows = artwork.side_profile(fleet.BELUGA_XL)

        def roof_row(col):
            """The topmost fuselage row at this column, ignoring the fin."""
            for row in range(layout.fus_top, layout.ground):
                if rows[row][artwork.LEFT_MARGIN + col] != " ":
                    return row
            return layout.ground

        deck = int(round(layout.nose_end)) - 1  # over the flight deck
        crown = int(round(layout.lobe_crown_from + 2))  # over the hold
        self.assertEqual(roof_row(deck), layout.deck_top)
        self.assertEqual(roof_row(crown), layout.fus_top)
        self.assertLess(roof_row(crown), roof_row(deck))

    def test_a_freighter_draws_no_cabin_windows(self):
        """No passengers, no window rows -- and the flight deck still glazed."""
        self.assertFalse(fleet.BELUGA_XL.carries_passengers)
        self.assertEqual(glyph_count(fleet.BELUGA_XL, "o o"), 0)
        for craft in fleet.FLEET:
            if craft.carries_passengers:
                self.assertGreater(
                    glyph_count(craft, "o o"), 0,
                    "{} lost its cabin".format(craft.name),
                )

    def test_only_the_beluga_grows_fins_on_its_tailplane(self):
        """The endplate fins, which exist because the lobe blankets the fin."""
        layout = artwork._Layout(fleet.BELUGA_XL)
        rows = artwork.side_profile(fleet.BELUGA_XL)
        tail = artwork.LEFT_MARGIN + layout.length
        self.assertIn("/|", rows[layout.deck_top][tail:])
        for craft in fleet.FLEET:
            if craft.cargo_lobe:
                continue
            other = artwork._Layout(craft)
            self.assertNotIn(
                "/|",
                artwork.side_profile(craft)[other.deck_top][
                    artwork.LEFT_MARGIN + other.length :
                ],
                "{} grew tailplane fins".format(craft.name),
            )

    def test_the_a330_800_is_the_900_shortened(self):
        """The whole claim the model makes, in a picture.

        The two A330neos share a wing, a fin and an engine; the only thing that
        distinguishes them is 4.8 m of fuselage. So the -800 must draw shorter
        and must draw nothing else differently.
        """
        short = artwork.side_profile(fleet.A330_800)
        long = artwork.side_profile(fleet.A330NEO)
        self.assertLess(fleet.A330_800.length_m, fleet.A330NEO.length_m)
        self.assertLess(len(long[-1].rstrip()), 88)
        self.assertLess(len(short[-1].rstrip()), len(long[-1].rstrip()))
        self.assertEqual(fleet.A330_800.wing_span_m, fleet.A330NEO.wing_span_m)
        # Same cross-section, same gear, same pods: same rows to draw them in.
        self.assertEqual(
            artwork._Layout(fleet.A330_800).fus_rows,
            artwork._Layout(fleet.A330NEO).fus_rows,
        )

    def test_types_with_identical_dimensions_are_drawn_identically(self):
        """The A320 and A320neo differ in span, which a side view cannot show.

        Asserting this keeps the drawing honest: it must not invent a difference
        it has no way of seeing.
        """
        self.assertEqual(fleet.A320.length_m, fleet.A320NEO.length_m)
        self.assertEqual(
            artwork.side_profile(fleet.A320), artwork.side_profile(fleet.A320NEO)
        )
        # The caption, which quotes the span, does distinguish them.
        self.assertNotEqual(
            artwork.profile_block(fleet.A320), artwork.profile_block(fleet.A320NEO)
        )


class TestRendering(unittest.TestCase):
    def test_every_type_draws_without_error_and_fits_the_frame(self):
        for craft in fleet.FLEET:
            rows = artwork.profile_block(craft)
            self.assertGreater(len(rows), 6)
            for row in rows:
                self.assertLessEqual(
                    len(row), artwork.FRAME_WIDTH,
                    "{} overflows the frame".format(craft.name),
                )

    def test_padding_bottom_aligns_without_changing_the_aircraft(self):
        tall = artwork.frame_height()
        padded = artwork.profile_block(fleet.A319NEO, pad_to=tall)
        plain = artwork.profile_block(fleet.A319NEO)
        self.assertEqual([r for r in padded if r.strip()],
                         [r for r in plain if r.strip()])
        self.assertGreater(len(padded), len(plain))

    def test_the_ground_line_is_the_last_line_of_the_aircraft(self):
        for craft in fleet.FLEET:
            rows = artwork.side_profile(craft)
            self.assertTrue(set(rows[-1]) <= {"-"})
            self.assertGreater(len(rows[-1]), 10)

    def test_dimension_rule_is_scaled_like_the_drawing(self):
        short = artwork._dimension_rule(30.0, "30 m")
        long = artwork._dimension_rule(70.0, "70 m")
        self.assertLess(len(short), len(long))
        self.assertIn("30 m", short)


class TestSpecCard(unittest.TestCase):
    def test_the_card_quotes_the_published_data(self):
        card = dashboard.spec_card(fleet.A380)
        for expected in (
            "A388", "24.09 m", "79.75 m", "320,000 L", "575,000 kg",
            "853 max", "Trent 970", "8,000 nm", "Engine Alliance GP7270",
        ):
            self.assertIn(expected, card, "spec card omits {}".format(expected))

    def test_every_type_renders_a_card(self):
        for craft in fleet.FLEET:
            card = dashboard.spec_card(craft)
            self.assertIn(craft.name, card)
            self.assertIn(craft.icao_type, card)
            self.assertIn(craft.handling[:40], card)

    def test_the_card_and_the_drawing_cannot_disagree(self):
        """Both read the same field, so the length in the table is the length
        in the callout under the picture."""
        for craft in fleet.FLEET:
            card = dashboard.spec_card(craft)
            self.assertEqual(
                card.count("{:.2f} m".format(craft.length_m)), 2,
                "{}: drawing and table quote different lengths".format(craft.name),
            )

    def test_fleet_menu_lists_every_type_with_a_picture(self):
        menu = dashboard.fleet_menu()
        for index, craft in enumerate(fleet.FLEET, start=1):
            self.assertIn(craft.name, menu)
            self.assertIn("**{}**".format(index), menu)
        self.assertGreaterEqual(menu.count("```"), 18)

    def test_fleet_menu_can_leave_the_pictures_out(self):
        plain = dashboard.fleet_menu(artwork_included=False)
        self.assertNotIn("```", plain)
        for craft in fleet.FLEET:
            self.assertIn(craft.name, plain)


if __name__ == "__main__":
    unittest.main()
