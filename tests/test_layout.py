"""Unit tests for the pure-math mold layout core.

These run OUTSIDE Fusion with plain stdlib unittest:
    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "LureMoldGenerator")
)

from lure_mold.layout import (  # noqa: E402
    LureDims,
    MoldSettings,
    compute_layout,
    relief_run,
)  # noqa: E402


def default_lure():
    """A 100mm long, 30mm tall, 12mm thick lure."""
    return LureDims(length=100.0, height=30.0, thickness=12.0)


class TestBlockDimensions(unittest.TestCase):
    def test_single_cavity_block_is_lure_plus_margin_on_every_side(self):
        layout = compute_layout(default_lure(), MoldSettings())
        self.assertAlmostEqual(layout.block_x, 120.0)  # 100 + 2*10
        self.assertAlmostEqual(layout.block_y, 50.0)  # 30 + 2*10
        self.assertAlmostEqual(layout.block_z, 32.0)  # 12 + 2*10

    def test_grid_multiplies_block_footprint_but_not_height(self):
        layout = compute_layout(default_lure(), MoldSettings(columns=3, rows=2))
        self.assertAlmostEqual(layout.block_x, 360.0)  # 3 * 120
        self.assertAlmostEqual(layout.block_y, 100.0)  # 2 * 50
        self.assertAlmostEqual(layout.block_z, 32.0)  # unchanged by grid

    def test_halves_are_equal_when_the_parting_plane_is_centred(self):
        layout = compute_layout(default_lure(), MoldSettings())
        self.assertAlmostEqual(layout.top_thickness, 16.0)  # 12/2 + 10
        self.assertAlmostEqual(layout.bottom_thickness, 16.0)

    def test_margins_are_independent_per_axis(self):
        layout = compute_layout(
            default_lure(), MoldSettings(margin_x=5.0, margin_y=20.0, margin_z=3.0)
        )
        self.assertAlmostEqual(layout.block_x, 110.0)
        self.assertAlmostEqual(layout.block_y, 70.0)
        self.assertAlmostEqual(layout.block_z, 18.0)


class TestCavityPlacement(unittest.TestCase):
    def test_single_cavity_sits_at_the_origin(self):
        layout = compute_layout(default_lure(), MoldSettings())
        self.assertEqual(len(layout.cavities), 1)
        self.assertAlmostEqual(layout.cavities[0].center.x, 0.0)
        self.assertAlmostEqual(layout.cavities[0].center.y, 0.0)

    def test_grid_produces_columns_times_rows_cavities(self):
        layout = compute_layout(default_lure(), MoldSettings(columns=3, rows=2))
        self.assertEqual(len(layout.cavities), 6)

    def test_two_columns_straddle_the_origin_one_cell_apart(self):
        layout = compute_layout(default_lure(), MoldSettings(columns=2))
        xs = sorted(c.center.x for c in layout.cavities)
        self.assertAlmostEqual(xs[0], -60.0)  # -240/2 + 120*0.5
        self.assertAlmostEqual(xs[1], 60.0)
        for cavity in layout.cavities:
            self.assertAlmostEqual(cavity.center.y, 0.0)

    def test_rows_are_spaced_along_y(self):
        layout = compute_layout(default_lure(), MoldSettings(rows=2))
        ys = sorted(c.center.y for c in layout.cavities)
        self.assertAlmostEqual(ys[0], -25.0)
        self.assertAlmostEqual(ys[1], 25.0)

    def test_cavities_stay_inside_the_block(self):
        lure = default_lure()
        layout = compute_layout(lure, MoldSettings(columns=4, rows=3))
        for cavity in layout.cavities:
            self.assertLessEqual(
                abs(cavity.center.x) + lure.length / 2, layout.block_x / 2 + 1e-9
            )
            self.assertLessEqual(
                abs(cavity.center.y) + lure.height / 2, layout.block_y / 2 + 1e-9
            )


class TestPegPlacement(unittest.TestCase):
    def test_two_pegs_go_to_diagonally_opposite_corners(self):
        # Corners, not edge midpoints: an edge sprue and vent break out at the
        # middle of the end faces, which is where midpoint pegs used to land.
        layout = compute_layout(default_lure(), MoldSettings(peg_count=2))
        pegs = sorted(layout.pegs, key=lambda p: p.x)
        self.assertEqual(len(pegs), 2)
        self.assertAlmostEqual(pegs[0].x, -55.5)
        self.assertAlmostEqual(pegs[0].y, -20.5)
        self.assertAlmostEqual(pegs[1].x, 55.5)
        self.assertAlmostEqual(pegs[1].y, 20.5)

    def test_two_pegs_stay_diagonal_on_a_tall_block(self):
        layout = compute_layout(default_lure(), MoldSettings(rows=4, peg_count=2))
        pegs = sorted(layout.pegs, key=lambda p: p.x)
        self.assertEqual(len(pegs), 2)
        self.assertLess(pegs[0].x, 0)
        self.assertLess(pegs[0].y, 0)
        self.assertGreater(pegs[1].x, 0)
        self.assertGreater(pegs[1].y, 0)

    def test_four_pegs_go_to_the_block_corners(self):
        layout = compute_layout(default_lure(), MoldSettings(peg_count=4))
        self.assertEqual(len(layout.pegs), 4)
        self.assertEqual(
            sorted((round(p.x, 3), round(p.y, 3)) for p in layout.pegs),
            [(-55.5, -20.5), (-55.5, 20.5), (55.5, -20.5), (55.5, 20.5)],
        )

    def test_pegs_never_overlap_a_cavity(self):
        lure = default_lure()
        layout = compute_layout(lure, MoldSettings(columns=3, rows=2, peg_count=8))
        for peg in layout.pegs:
            for cavity in layout.cavities:
                overlaps_x = abs(peg.x - cavity.center.x) < lure.length / 2
                overlaps_y = abs(peg.y - cavity.center.y) < lure.height / 2
                self.assertFalse(
                    overlaps_x and overlaps_y,
                    f"peg {peg} lands inside cavity {cavity.center}",
                )

    def test_pegs_stay_inside_the_block(self):
        layout = compute_layout(default_lure(), MoldSettings(columns=2, peg_count=6))
        for peg in layout.pegs:
            self.assertLessEqual(abs(peg.x) + 2.5, layout.block_x / 2 + 1e-9)
            self.assertLessEqual(abs(peg.y) + 2.5, layout.block_y / 2 + 1e-9)

    def test_thin_margins_leave_no_room_and_this_is_reported(self):
        # 3mm margins: every candidate position is swallowed by the cavity.
        layout = compute_layout(
            default_lure(),
            MoldSettings(margin_x=3.0, margin_y=3.0, peg_count=4),
        )
        self.assertEqual(len(layout.pegs), 0)
        self.assertTrue(
            any("peg" in w.lower() for w in layout.warnings),
            f"expected a peg warning, got {layout.warnings}",
        )

    def test_asking_for_more_pegs_than_fit_places_as_many_as_possible(self):
        layout = compute_layout(default_lure(), MoldSettings(peg_count=99))
        self.assertLess(len(layout.pegs), 99)
        self.assertTrue(any("peg" in w.lower() for w in layout.warnings))


class TestSprueAndVents(unittest.TestCase):
    def test_sprue_sits_inboard_of_the_nose_tip(self):
        # Nose at +X. Tip is at +50; inset is 15% of the 100mm length.
        layout = compute_layout(default_lure(), MoldSettings())
        self.assertAlmostEqual(layout.cavities[0].sprue.x, 35.0)
        self.assertAlmostEqual(layout.cavities[0].sprue.y, 0.0)

    def test_vent_sits_inboard_of_the_tail_tip(self):
        # Tail at -X. Tip is at -50; inset is 5% of the 100mm length.
        layout = compute_layout(default_lure(), MoldSettings())
        self.assertAlmostEqual(layout.cavities[0].vent.x, -45.0)
        self.assertAlmostEqual(layout.cavities[0].vent.y, 0.0)

    def test_flip_setting_swaps_which_end_gets_the_sprue(self):
        layout = compute_layout(default_lure(), MoldSettings(flip_lure=True))
        self.assertAlmostEqual(layout.cavities[0].sprue.x, -35.0)
        self.assertAlmostEqual(layout.cavities[0].vent.x, 45.0)

    def test_detected_nose_direction_is_respected(self):
        lure = LureDims(
            length=100.0, height=30.0, thickness=12.0, nose_at_positive_x=False
        )
        layout = compute_layout(lure, MoldSettings())
        self.assertAlmostEqual(layout.cavities[0].sprue.x, -35.0)

    def test_disabling_vents_removes_them_entirely(self):
        layout = compute_layout(default_lure(), MoldSettings(vents_enabled=False))
        self.assertIsNone(layout.cavities[0].vent)

    def test_every_cavity_in_a_grid_gets_its_own_sprue(self):
        layout = compute_layout(default_lure(), MoldSettings(columns=3, rows=2))
        self.assertEqual(len(layout.cavities), 6)
        for cavity in layout.cavities:
            self.assertAlmostEqual(cavity.sprue.x, cavity.center.x + 35.0)
            self.assertAlmostEqual(cavity.sprue.y, cavity.center.y)

    def test_sprue_stays_over_the_lure_for_a_stubby_lure(self):
        lure = LureDims(length=20.0, height=18.0, thickness=16.0)
        layout = compute_layout(lure, MoldSettings())
        cavity = layout.cavities[0]
        self.assertLessEqual(abs(cavity.sprue.x - cavity.center.x), lure.length / 2)


class TestPrintabilityWarnings(unittest.TestCase):
    def test_defaults_produce_no_warnings(self):
        layout = compute_layout(default_lure(), MoldSettings())
        self.assertEqual(layout.warnings, ())

    def test_thin_walls_are_flagged(self):
        layout = compute_layout(default_lure(), MoldSettings(margin_y=1.0))
        self.assertTrue(
            any("margin" in w.lower() for w in layout.warnings),
            f"expected a margin warning, got {layout.warnings}",
        )

    def test_unprintably_small_vents_are_flagged(self):
        layout = compute_layout(default_lure(), MoldSettings(vent_diameter=0.5))
        self.assertTrue(
            any("vent" in w.lower() for w in layout.warnings),
            f"expected a vent warning, got {layout.warnings}",
        )

    def test_disabled_vents_are_never_flagged_for_size(self):
        layout = compute_layout(
            default_lure(), MoldSettings(vent_diameter=0.1, vents_enabled=False)
        )
        self.assertFalse(any("vent" in w.lower() for w in layout.warnings))


class TestFlatLayout(unittest.TestCase):
    """Both halves end up sitting on z=0 with their cavities facing up."""

    def test_bottom_half_is_not_flipped(self):
        layout = compute_layout(default_lure(), MoldSettings())
        self.assertFalse(layout.bottom_placement.flip)

    def test_top_half_is_flipped_so_its_cavity_faces_up(self):
        layout = compute_layout(default_lure(), MoldSettings())
        self.assertTrue(layout.top_placement.flip)

    def test_both_halves_are_lifted_to_sit_on_the_ground_plane(self):
        layout = compute_layout(default_lure(), MoldSettings())
        # Each half is lifted by its own thickness so both rest on z = 0.
        self.assertAlmostEqual(layout.bottom_placement.dz, layout.bottom_thickness)
        self.assertAlmostEqual(layout.top_placement.dz, layout.top_thickness)

    def test_halves_are_separated_along_y_without_overlapping(self):
        layout = compute_layout(default_lure(), MoldSettings())
        separation = abs(layout.top_placement.dy - layout.bottom_placement.dy)
        self.assertGreater(separation, layout.block_y)

    def test_halves_are_placed_symmetrically_about_the_origin(self):
        layout = compute_layout(default_lure(), MoldSettings())
        self.assertAlmostEqual(
            layout.bottom_placement.dy, -layout.top_placement.dy
        )
        self.assertAlmostEqual(layout.bottom_placement.dx, 0.0)
        self.assertAlmostEqual(layout.top_placement.dx, 0.0)

    def test_separation_grows_with_a_bigger_block(self):
        small = compute_layout(default_lure(), MoldSettings())
        big = compute_layout(default_lure(), MoldSettings(rows=3))
        self.assertGreater(big.top_placement.dy, small.top_placement.dy)


class TestInjectionModes(unittest.TestCase):
    """Real soft-plastic molds inject through the edge, along the parting line."""

    def test_edge_injection_is_the_default(self):
        self.assertEqual(MoldSettings().injection_mode, "edge")

    def test_edge_mode_runs_a_channel_out_to_the_block_edge(self):
        layout = compute_layout(default_lure(), MoldSettings())
        cavity = layout.cavities[0]
        self.assertIsNotNone(cavity.sprue_entry)
        # Nose is at +X, so the channel leaves through the +X face.
        self.assertAlmostEqual(cavity.sprue_entry.x, layout.block_x / 2)
        self.assertAlmostEqual(cavity.sprue_entry.y, cavity.center.y)

    def test_edge_channel_still_meets_the_cavity_at_the_nose(self):
        layout = compute_layout(default_lure(), MoldSettings())
        self.assertAlmostEqual(layout.cavities[0].sprue.x, 35.0)

    def test_flipping_the_lure_moves_the_entry_to_the_other_face(self):
        layout = compute_layout(default_lure(), MoldSettings(flip_lure=True))
        self.assertAlmostEqual(
            layout.cavities[0].sprue_entry.x, -layout.block_x / 2
        )

    def test_none_mode_leaves_no_injection_hole_at_all(self):
        layout = compute_layout(
            default_lure(), MoldSettings(injection_mode="none")
        )
        for cavity in layout.cavities:
            self.assertIsNone(cavity.sprue)
            self.assertIsNone(cavity.sprue_entry)

    def test_none_mode_does_not_disable_vents(self):
        layout = compute_layout(
            default_lure(), MoldSettings(injection_mode="none")
        )
        self.assertIsNotNone(layout.cavities[0].vent)

    def test_top_mode_has_no_edge_entry(self):
        layout = compute_layout(
            default_lure(), MoldSettings(injection_mode="top")
        )
        cavity = layout.cavities[0]
        self.assertIsNotNone(cavity.sprue)
        self.assertIsNone(cavity.sprue_entry)

    def test_a_single_column_always_reaches_the_edge(self):
        layout = compute_layout(default_lure(), MoldSettings(rows=5))
        self.assertEqual(len(layout.cavities), 5)
        for cavity in layout.cavities:
            self.assertIsNotNone(cavity.sprue_entry)
        self.assertEqual(layout.warnings, ())

    def test_interior_columns_cannot_reach_the_edge_and_say_so(self):
        # With the nose at +X only the last column has a clear run out.
        layout = compute_layout(default_lure(), MoldSettings(columns=3))
        reached = [c for c in layout.cavities if c.sprue_entry is not None]
        blocked = [c for c in layout.cavities if c.sprue_entry is None]
        self.assertEqual(len(reached), 1)
        self.assertEqual(len(blocked), 2)
        self.assertTrue(
            any("inject" in w.lower() for w in layout.warnings),
            f"expected an injection warning, got {layout.warnings}",
        )

    def test_blocked_cavities_still_get_a_top_sprue_rather_than_nothing(self):
        layout = compute_layout(default_lure(), MoldSettings(columns=3))
        for cavity in layout.cavities:
            self.assertIsNotNone(cavity.sprue)


class TestVentRouting(unittest.TestCase):
    """Vents follow the injection mode: edge channels, or top risers."""

    def test_edge_mode_vents_break_out_of_the_opposite_face(self):
        layout = compute_layout(default_lure(), MoldSettings())
        cavity = layout.cavities[0]
        self.assertIsNotNone(cavity.vent_entry)
        # Nose exits +X, so the tail vents through -X.
        self.assertAlmostEqual(cavity.vent_entry.x, -layout.block_x / 2)
        self.assertAlmostEqual(cavity.vent_entry.y, cavity.center.y)

    def test_sprue_and_vent_leave_through_opposite_faces(self):
        layout = compute_layout(default_lure(), MoldSettings())
        cavity = layout.cavities[0]
        self.assertAlmostEqual(
            cavity.sprue_entry.x, -cavity.vent_entry.x
        )

    def test_flipping_swaps_both_ends_together(self):
        layout = compute_layout(default_lure(), MoldSettings(flip_lure=True))
        cavity = layout.cavities[0]
        self.assertAlmostEqual(cavity.sprue_entry.x, -layout.block_x / 2)
        self.assertAlmostEqual(cavity.vent_entry.x, layout.block_x / 2)

    def test_vents_stay_on_the_parting_line_even_with_top_injection(self):
        # The injection mode governs the sprue, not the vent: a vent on the
        # split face can be cleaned out, a riser cannot.
        layout = compute_layout(
            default_lure(), MoldSettings(injection_mode="top")
        )
        cavity = layout.cavities[0]
        self.assertIsNotNone(cavity.vent)
        self.assertIsNotNone(cavity.vent_entry)

    def test_disabled_vents_have_no_entry_either(self):
        layout = compute_layout(
            default_lure(), MoldSettings(vents_enabled=False)
        )
        for cavity in layout.cavities:
            self.assertIsNone(cavity.vent)
            self.assertIsNone(cavity.vent_entry)

    def test_a_single_column_vents_every_cavity_through_the_edge(self):
        layout = compute_layout(default_lure(), MoldSettings(rows=4))
        for cavity in layout.cavities:
            self.assertIsNotNone(cavity.vent_entry)

    def test_only_the_tail_side_column_vents_through_an_x_face(self):
        # Nose at +X means tails face -X, so only column 0 has a clear run
        # that way. The rest now vent sideways instead of upwards.
        layout = compute_layout(default_lure(), MoldSettings(columns=3, rows=1))
        through_x = [
            c for c in layout.cavities
            if c.vent_entry is not None
            and abs(c.vent_entry.x - c.vent.x) > abs(c.vent_entry.y - c.vent.y)
        ]
        self.assertEqual(len(through_x), 1)
        for cavity in layout.cavities:
            self.assertIsNotNone(cavity.vent)


class TestPegsAvoidPorts(unittest.TestCase):
    """A peg landing on the fill port makes the mold unusable."""

    def _channel_clear(self, layout, peg, inner, entry, radius, peg_radius):
        lo, hi = sorted([inner.x, entry.x])
        near_x = lo - peg_radius <= peg.x <= hi + peg_radius
        near_y = abs(peg.y - entry.y) <= radius + peg_radius
        return not (near_x and near_y)

    def test_no_peg_lands_on_an_edge_sprue_channel(self):
        for count in (2, 4, 6, 8):
            layout = compute_layout(default_lure(), MoldSettings(peg_count=count))
            for peg in layout.pegs:
                for cavity in layout.cavities:
                    if cavity.sprue_entry is None:
                        continue
                    self.assertTrue(
                        self._channel_clear(
                            layout, peg, cavity.sprue, cavity.sprue_entry,
                            MoldSettings().funnel_diameter / 2,
                            MoldSettings().peg_diameter / 2,
                        ),
                        f"peg {peg} sits on the sprue with {count} pegs",
                    )

    def test_no_peg_lands_on_an_edge_vent_channel(self):
        for count in (2, 4, 6, 8):
            layout = compute_layout(default_lure(), MoldSettings(peg_count=count))
            for peg in layout.pegs:
                for cavity in layout.cavities:
                    if cavity.vent_entry is None:
                        continue
                    self.assertTrue(
                        self._channel_clear(
                            layout, peg, cavity.vent, cavity.vent_entry,
                            MoldSettings().vent_diameter / 2,
                            MoldSettings().peg_diameter / 2,
                        ),
                        f"peg {peg} sits on the vent with {count} pegs",
                    )

    def test_a_very_wide_funnel_pushes_pegs_out_of_the_way(self):
        # A 30mm funnel sweeps a wide band; pegs must still find clear spots.
        layout = compute_layout(
            default_lure(),
            MoldSettings(peg_count=2, funnel_diameter=30.0, margin_y=25.0),
        )
        for peg in layout.pegs:
            for cavity in layout.cavities:
                if cavity.sprue_entry is None:
                    continue
                self.assertGreater(abs(peg.y - cavity.sprue_entry.y), 15.0)

    def test_pegs_still_avoid_cavities_as_well(self):
        lure = default_lure()
        layout = compute_layout(lure, MoldSettings(columns=2, rows=2, peg_count=8))
        for peg in layout.pegs:
            for cavity in layout.cavities:
                inside = (
                    abs(peg.x - cavity.center.x) < lure.length / 2
                    and abs(peg.y - cavity.center.y) < lure.height / 2
                )
                self.assertFalse(inside)


class TestPartingOffset(unittest.TestCase):
    """The split does not have to sit at the middle of the lure.

    On a turtle the correct plane is at the fins, near the top of the shell.
    Splitting through the middle strands the fins in the bottom half where
    material closes over them and they can never release.
    """

    def test_no_offset_gives_two_equal_halves(self):
        layout = compute_layout(default_lure(), MoldSettings())
        self.assertAlmostEqual(layout.top_thickness, layout.bottom_thickness)

    def test_a_positive_offset_thins_the_top_and_thickens_the_bottom(self):
        # Lure is 12 thick, margin 10. Offset +4 leaves 2 above, 10 below.
        layout = compute_layout(default_lure(), MoldSettings(parting_offset=4.0))
        self.assertAlmostEqual(layout.top_thickness, 12.0)  # 2 + 10
        self.assertAlmostEqual(layout.bottom_thickness, 20.0)  # 10 + 10

    def test_a_negative_offset_does_the_opposite(self):
        layout = compute_layout(default_lure(), MoldSettings(parting_offset=-4.0))
        self.assertAlmostEqual(layout.top_thickness, 20.0)
        self.assertAlmostEqual(layout.bottom_thickness, 12.0)

    def test_the_overall_block_height_never_changes(self):
        for offset in (-5.0, -2.0, 0.0, 3.0, 5.5):
            layout = compute_layout(
                default_lure(), MoldSettings(parting_offset=offset)
            )
            self.assertAlmostEqual(layout.block_z, 32.0)
            self.assertAlmostEqual(
                layout.top_thickness + layout.bottom_thickness, 32.0
            )

    def test_the_offset_is_clamped_inside_the_lure(self):
        # Beyond half the lure thickness there would be nothing to split.
        layout = compute_layout(default_lure(), MoldSettings(parting_offset=99.0))
        self.assertAlmostEqual(layout.parting_offset, 6.0)  # 12 / 2
        self.assertGreater(layout.top_thickness, 0.0)

    def test_offset_halves_still_land_on_the_ground_plane(self):
        layout = compute_layout(default_lure(), MoldSettings(parting_offset=4.0))
        self.assertAlmostEqual(layout.bottom_placement.dz, layout.bottom_thickness)
        self.assertAlmostEqual(layout.top_placement.dz, layout.top_thickness)

    def test_the_resolved_offset_is_reported_back(self):
        layout = compute_layout(default_lure(), MoldSettings(parting_offset=3.5))
        self.assertAlmostEqual(layout.parting_offset, 3.5)


class TestCentralRunner(unittest.TestCase):
    """One sprue feeding a channel down the middle, cavities gated off it.

    This is how real multi-cavity soft-plastic molds are built: two columns of
    lures with their heads facing a central runner, one big sprue at one end,
    and the whole shot pulls out as a single tree.
    """

    def runner_settings(self, **kw):
        base = dict(injection_mode="runner", columns=2, rows=3)
        base.update(kw)
        return MoldSettings(**base)

    def test_a_runner_is_produced(self):
        layout = compute_layout(default_lure(), self.runner_settings())
        self.assertIsNotNone(layout.runner)

    def test_the_runner_lies_down_the_middle(self):
        layout = compute_layout(default_lure(), self.runner_settings())
        self.assertAlmostEqual(layout.runner.x, 0.0)

    def test_the_runner_breaks_out_of_one_end_only(self):
        layout = compute_layout(default_lure(), self.runner_settings())
        half = layout.block_y / 2
        # The sprue enters at y_to, on the face.
        self.assertAlmostEqual(layout.runner.y_to, half)
        # The far end stops short so it is capped, not open.
        self.assertGreater(layout.runner.y_from, -half)

    def test_the_capped_end_still_reaches_the_outermost_cavity(self):
        for rows in (1, 2, 3, 5):
            layout = compute_layout(default_lure(), self.runner_settings(rows=rows))
            lowest = min(c.center.y for c in layout.cavities)
            self.assertLessEqual(
                layout.runner.y_from, lowest,
                f"runner stops short of the end cavity with {rows} rows",
            )

    def test_the_cap_is_a_full_wall_thick(self):
        layout = compute_layout(default_lure(), self.runner_settings(margin_y=12.0))
        self.assertAlmostEqual(
            layout.runner.y_from + layout.block_y / 2, 12.0
        )

    def test_every_cavity_gates_into_the_runner_not_the_block_face(self):
        layout = compute_layout(default_lure(), self.runner_settings())
        self.assertEqual(len(layout.cavities), 6)
        for cavity in layout.cavities:
            self.assertIsNotNone(cavity.sprue_entry)
            self.assertAlmostEqual(cavity.sprue_entry.x, 0.0)
            self.assertAlmostEqual(cavity.sprue_entry.y, cavity.center.y)

    def test_the_two_columns_face_each_other(self):
        layout = compute_layout(default_lure(), self.runner_settings())
        left = [c for c in layout.cavities if c.center.x < 0]
        right = [c for c in layout.cavities if c.center.x > 0]
        self.assertEqual(len(left), 3)
        self.assertEqual(len(right), 3)
        # Each cavity's sprue sits between its centre and the middle.
        for cavity in left:
            self.assertGreater(cavity.sprue.x, cavity.center.x)
        for cavity in right:
            self.assertLess(cavity.sprue.x, cavity.center.x)

    def test_one_column_is_turned_around(self):
        layout = compute_layout(default_lure(), self.runner_settings())
        turned = [c for c in layout.cavities if c.rotated]
        straight = [c for c in layout.cavities if not c.rotated]
        self.assertEqual(len(turned), 3)
        self.assertEqual(len(straight), 3)
        # All the turned ones are in the same column.
        self.assertEqual(len({round(c.center.x, 6) for c in turned}), 1)

    def test_vents_still_escape_through_the_outer_faces(self):
        layout = compute_layout(default_lure(), self.runner_settings())
        half = layout.block_x / 2
        for cavity in layout.cavities:
            self.assertIsNotNone(cavity.vent_entry)
            self.assertAlmostEqual(abs(cavity.vent_entry.x), half)
            # A cavity vents away from the middle, not into it: the sprue
            # and the vent leave on opposite sides of the cavity.
            to_sprue = cavity.sprue_entry.x - cavity.center.x
            to_vent = cavity.vent_entry.x - cavity.center.x
            self.assertLess(to_sprue * to_vent, 0.0)

    def test_the_runner_diameter_is_configurable(self):
        layout = compute_layout(
            default_lure(), self.runner_settings(runner_diameter=9.0)
        )
        self.assertAlmostEqual(layout.runner.diameter, 9.0)

    def test_a_runner_needs_exactly_two_columns(self):
        for columns in (1, 3, 4):
            layout = compute_layout(
                default_lure(), self.runner_settings(columns=columns)
            )
            self.assertIsNone(layout.runner)
            self.assertTrue(
                any("runner" in w.lower() for w in layout.warnings),
                f"expected a runner warning for {columns} columns",
            )

    def test_falling_back_still_produces_a_usable_mold(self):
        layout = compute_layout(default_lure(), self.runner_settings(columns=1))
        for cavity in layout.cavities:
            self.assertIsNotNone(cavity.sprue)

    def test_other_modes_have_no_runner(self):
        for mode in ("edge", "top", "none"):
            layout = compute_layout(
                default_lure(), MoldSettings(injection_mode=mode, columns=2)
            )
            self.assertIsNone(layout.runner)

    def test_pegs_keep_clear_of_the_runner(self):
        layout = compute_layout(default_lure(), self.runner_settings(peg_count=4))
        for peg in layout.pegs:
            self.assertGreater(
                abs(peg.x - layout.runner.x),
                layout.runner.diameter / 2 + MoldSettings().peg_diameter / 2,
            )


class TestVentsRunAlongTheSplit(unittest.TestCase):
    """Vents should lie on the parting line, not bore up through the lid.

    A channel on the split face opens up when the mold is opened, so it can be
    cleaned out. A vertical riser is a blind hole full of set plastic.
    """

    def test_a_single_column_vents_horizontally(self):
        layout = compute_layout(default_lure(), MoldSettings(rows=3))
        for cavity in layout.cavities:
            self.assertIsNotNone(cavity.vent_entry)

    def test_a_runner_mold_vents_horizontally(self):
        layout = compute_layout(
            default_lure(),
            MoldSettings(injection_mode="runner", columns=2, rows=3),
        )
        for cavity in layout.cavities:
            self.assertIsNotNone(cavity.vent_entry)

    def test_a_blocked_tail_vents_sideways_rather_than_upwards(self):
        # 3 columns: only column 0 reaches an X face with its tail. The rest
        # should still vent horizontally, out of the nearest Y face.
        layout = compute_layout(default_lure(), MoldSettings(columns=3, rows=1))
        for cavity in layout.cavities:
            self.assertIsNotNone(
                cavity.vent_entry, "every cavity in a single row can reach a Y face"
            )

    def test_sideways_vents_leave_through_a_y_face(self):
        layout = compute_layout(default_lure(), MoldSettings(columns=3, rows=1))
        half = layout.block_y / 2
        sideways = [
            c for c in layout.cavities
            if abs(c.vent_entry.y - c.vent.y) > abs(c.vent_entry.x - c.vent.x)
        ]
        self.assertTrue(sideways)
        for cavity in sideways:
            self.assertAlmostEqual(abs(cavity.vent_entry.y), half)
            self.assertAlmostEqual(cavity.vent_entry.x, cavity.vent.x)

    def test_a_truly_boxed_in_cavity_falls_back_to_a_riser(self):
        # The middle of a 3x3 grid can reach no face at all.
        layout = compute_layout(default_lure(), MoldSettings(columns=3, rows=3))
        risers = [c for c in layout.cavities if c.vent is not None and c.vent_entry is None]
        self.assertTrue(risers)
        self.assertTrue(any("vent" in w.lower() for w in layout.warnings))


class TestReliefRamp(unittest.TestCase):
    """The parting face is flat only near features; the rest is recessed.

    A narrow sealing land prints far better than a whole face that has to be
    dead flat. The ramp between land and recess sits at the chosen angle.
    """

    def test_relief_is_on_by_default_at_fifty_degrees(self):
        settings = MoldSettings()
        self.assertTrue(settings.relief_enabled)
        self.assertAlmostEqual(settings.relief_angle, 50.0)

    def test_a_steeper_angle_gives_a_shorter_ramp(self):
        shallow = relief_run(2.0, 30.0)
        steep = relief_run(2.0, 70.0)
        self.assertGreater(shallow, steep)

    def test_forty_five_degrees_runs_as_far_as_it_drops(self):
        self.assertAlmostEqual(relief_run(3.0, 45.0), 3.0)

    def test_fifty_degrees_matches_the_trigonometry(self):
        import math
        self.assertAlmostEqual(
            relief_run(2.0, 50.0), 2.0 / math.tan(math.radians(50.0))
        )

    def test_a_deeper_relief_needs_a_longer_ramp(self):
        self.assertGreater(relief_run(4.0, 50.0), relief_run(2.0, 50.0))

    def test_absurd_angles_are_clamped_to_something_buildable(self):
        # 0 or 90 degrees would give an infinite or zero ramp.
        for angle in (0.0, -10.0, 90.0, 180.0):
            run = relief_run(2.0, angle)
            self.assertGreater(run, 0.0)
            self.assertLess(run, 1000.0)

    def test_zero_depth_gives_no_ramp(self):
        self.assertAlmostEqual(relief_run(0.0, 50.0), 0.0)

    def test_a_land_wider_than_the_margin_is_flagged(self):
        # Land plus ramp has to fit inside the wall, or there is no face left
        # to recess and the setting silently does nothing.
        layout = compute_layout(
            default_lure(), MoldSettings(margin_x=10.0, margin_y=10.0,
                                         relief_land=9.5)
        )
        self.assertTrue(
            any("relief" in w.lower() for w in layout.warnings),
            f"expected a relief warning, got {layout.warnings}",
        )

    def test_a_sensible_land_is_not_flagged(self):
        layout = compute_layout(default_lure(), MoldSettings())
        self.assertFalse(any("relief" in w.lower() for w in layout.warnings))

    def test_relief_off_is_never_flagged(self):
        layout = compute_layout(
            default_lure(), MoldSettings(relief_enabled=False, relief_land=99.0)
        )
        self.assertFalse(any("relief" in w.lower() for w in layout.warnings))



class TestPegsUseTheRealOutline(unittest.TestCase):
    """A bounding box rejects corners that are wide open in reality.

    A figure with spread limbs leaves its bounding-box corners completely
    empty, but the box test says they are inside the cavity, so every peg
    candidate gets thrown away and the mold comes out with none at all.
    """

    def wide_lure(self):
        return LureDims(length=100.0, height=60.0, thickness=12.0)

    def cross(self, dx, dy):
        """Distance to a plus-shaped lure with 8mm half-width bars."""
        if abs(dx) <= 8.0 or abs(dy) <= 8.0:
            return 0.0
        return min(abs(dx) - 8.0, abs(dy) - 8.0)

    def test_the_bounding_box_alone_blocks_every_corner(self):
        # Baseline: this is the failure being fixed.
        layout = compute_layout(
            self.wide_lure(), MoldSettings(margin_x=7.0, margin_y=7.0)
        )
        self.assertEqual(len(layout.pegs), 0)

    def test_the_real_outline_frees_the_corners(self):
        layout = compute_layout(
            self.wide_lure(),
            MoldSettings(margin_x=7.0, margin_y=7.0),
            cavity_distance=self.cross,
        )
        self.assertEqual(len(layout.pegs), 2)

    def test_pegs_still_keep_clear_of_the_real_shape(self):
        layout = compute_layout(
            self.wide_lure(),
            MoldSettings(margin_x=7.0, margin_y=7.0, peg_count=8),
            cavity_distance=self.cross,
        )
        radius = MoldSettings().peg_diameter / 2
        for peg in layout.pegs:
            for cavity in layout.cavities:
                gap = self.cross(peg.x - cavity.center.x, peg.y - cavity.center.y)
                self.assertGreaterEqual(gap, radius)

    def test_a_solid_shape_still_blocks_what_it_should(self):
        # A distance function for a shape filling its whole box must behave
        # exactly like the box test did.
        solid = lambda dx, dy: 0.0 if (
            abs(dx) <= 50.0 and abs(dy) <= 30.0
        ) else 99.0
        layout = compute_layout(
            self.wide_lure(),
            MoldSettings(margin_x=7.0, margin_y=7.0),
            cavity_distance=solid,
        )
        for peg in layout.pegs:
            self.assertGreater(abs(peg.x), 50.0)

    def test_a_rotated_cavity_is_queried_the_right_way_round(self):
        seen = []

        def record(dx, dy):
            seen.append((dx, dy))
            return 99.0

        compute_layout(
            self.wide_lure(),
            MoldSettings(injection_mode="runner", columns=2, rows=1),
            cavity_distance=record,
        )
        self.assertTrue(seen, "the distance function should have been consulted")

    def test_omitting_the_outline_keeps_the_old_behaviour(self):
        with_box = compute_layout(default_lure(), MoldSettings())
        self.assertEqual(len(with_box.pegs), 2)


if __name__ == "__main__":
    unittest.main()
