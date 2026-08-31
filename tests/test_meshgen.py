"""Unit tests for the pure-Python mesh primitive generator.

Runs OUTSIDE Fusion:
    python -m unittest discover -s tests -v
"""

import math
import os
import sys
import unittest
from collections import Counter

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "LureMoldGenerator")
)

from lure_mold import meshgen  # noqa: E402


def triangles(indices):
    return [tuple(indices[i : i + 3]) for i in range(0, len(indices), 3)]


def assert_watertight(test, coords, indices):
    """A closed, consistently-wound surface uses every directed edge exactly once.

    Each undirected edge must therefore appear exactly twice, once in each
    direction. This catches holes, duplicated faces and flipped winding.
    """
    directed = Counter()
    for a, b, c in triangles(indices):
        test.assertEqual(len({a, b, c}), 3, "degenerate triangle")
        directed[(a, b)] += 1
        directed[(b, c)] += 1
        directed[(c, a)] += 1

    for (a, b), count in directed.items():
        test.assertEqual(count, 1, f"directed edge {(a, b)} used {count} times")
        test.assertEqual(
            directed[(b, a)], 1, f"edge {(a, b)} has no matching opposite"
        )


def bounds(coords):
    xs, ys, zs = coords[0::3], coords[1::3], coords[2::3]
    return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


def signed_volume(coords, indices):
    """Volume via the divergence theorem. Positive when wound outward."""
    total = 0.0
    for a, b, c in triangles(indices):
        ax, ay, az = coords[3 * a : 3 * a + 3]
        bx, by, bz = coords[3 * b : 3 * b + 3]
        cx, cy, cz = coords[3 * c : 3 * c + 3]
        total += (
            ax * (by * cz - bz * cy)
            - ay * (bx * cz - bz * cx)
            + az * (bx * cy - by * cx)
        )
    return total / 6.0


class TestBox(unittest.TestCase):
    def test_box_has_eight_corners_and_twelve_triangles(self):
        coords, indices = meshgen.box(0, 0, 0, 2, 4, 6)
        self.assertEqual(len(coords), 8 * 3)
        self.assertEqual(len(indices), 12 * 3)

    def test_box_is_watertight_and_outward_wound(self):
        coords, indices = meshgen.box(0, 0, 0, 2, 4, 6)
        assert_watertight(self, coords, indices)
        self.assertAlmostEqual(signed_volume(coords, indices), 48.0)

    def test_box_is_centred_on_the_requested_point(self):
        coords, _ = meshgen.box(10, -5, 3, 2, 4, 6)
        (x0, x1), (y0, y1), (z0, z1) = bounds(coords)
        self.assertAlmostEqual((x0 + x1) / 2, 10.0)
        self.assertAlmostEqual((y0 + y1) / 2, -5.0)
        self.assertAlmostEqual((z0 + z1) / 2, 3.0)
        self.assertAlmostEqual(x1 - x0, 2.0)
        self.assertAlmostEqual(y1 - y0, 4.0)
        self.assertAlmostEqual(z1 - z0, 6.0)


class TestCylinder(unittest.TestCase):
    def test_cylinder_is_watertight(self):
        coords, indices = meshgen.cylinder(0, 0, 0, 5, radius=2, segments=24)
        assert_watertight(self, coords, indices)

    def test_cylinder_volume_approaches_the_analytic_value_from_below(self):
        # The facets are chords, so an inscribed polygon always slightly
        # under-fills the true circle. Assert that, rather than exact equality.
        coords, indices = meshgen.cylinder(0, 0, 0, 5, radius=2, segments=256)
        actual = signed_volume(coords, indices)
        expected = math.pi * 4 * 5
        self.assertLess(actual, expected)
        self.assertLess((expected - actual) / expected, 0.001)

    def test_more_segments_give_a_closer_approximation(self):
        expected = math.pi * 4 * 5
        coarse = signed_volume(*meshgen.cylinder(0, 0, 0, 5, radius=2, segments=16))
        fine = signed_volume(*meshgen.cylinder(0, 0, 0, 5, radius=2, segments=256))
        self.assertLess(expected - fine, expected - coarse)

    def test_cylinder_spans_the_requested_height(self):
        coords, _ = meshgen.cylinder(0, 0, -3, 7, radius=2, segments=16)
        _, _, (z0, z1) = bounds(coords)
        self.assertAlmostEqual(z0, -3.0)
        self.assertAlmostEqual(z1, 7.0)

    def test_cylinder_is_positioned_in_xy(self):
        coords, _ = meshgen.cylinder(8, -4, 0, 1, radius=2, segments=64)
        (x0, x1), (y0, y1), _ = bounds(coords)
        self.assertAlmostEqual((x0 + x1) / 2, 8.0, places=6)
        self.assertAlmostEqual((y0 + y1) / 2, -4.0, places=6)


class TestCone(unittest.TestCase):
    def test_truncated_cone_is_watertight(self):
        coords, indices = meshgen.cone(0, 0, 0, 4.0, 10, 2.0, segments=32)
        assert_watertight(self, coords, indices)

    def test_cone_with_a_zero_radius_end_is_watertight(self):
        coords, indices = meshgen.cone(0, 0, 0, 3.0, 6, 0.0, segments=32)
        assert_watertight(self, coords, indices)

    def test_truncated_cone_volume_matches_the_frustum_formula(self):
        r0, r1, h = 4.0, 2.0, 10.0
        coords, indices = meshgen.cone(0, 0, 0, r0, h, r1, segments=256)
        expected = math.pi * h / 3 * (r0 * r0 + r0 * r1 + r1 * r1)
        self.assertAlmostEqual(signed_volume(coords, indices), expected, places=1)

    def test_cone_radii_apply_to_the_correct_ends(self):
        coords, _ = meshgen.cone(0, 0, 0, 5.0, 10, 1.0, segments=64)
        bottom = [
            math.hypot(coords[i], coords[i + 1])
            for i in range(0, len(coords), 3)
            if abs(coords[i + 2]) < 1e-9
        ]
        self.assertAlmostEqual(max(bottom), 5.0, places=6)


class TestTransforms(unittest.TestCase):
    def test_translate_shifts_every_vertex(self):
        coords, _ = meshgen.box(0, 0, 0, 2, 2, 2)
        moved = meshgen.translate(coords, 5, -3, 1)
        (x0, x1), (y0, y1), (z0, z1) = bounds(moved)
        self.assertAlmostEqual((x0 + x1) / 2, 5.0)
        self.assertAlmostEqual((y0 + y1) / 2, -3.0)
        self.assertAlmostEqual((z0 + z1) / 2, 1.0)

    def test_translate_leaves_the_original_untouched(self):
        coords, _ = meshgen.box(0, 0, 0, 2, 2, 2)
        meshgen.translate(coords, 100, 100, 100)
        (x0, x1), _, _ = bounds(coords)
        self.assertAlmostEqual((x0 + x1) / 2, 0.0)

    def test_scale_multiplies_uniformly(self):
        coords, _ = meshgen.box(0, 0, 0, 2, 2, 2)
        scaled = meshgen.scale(coords, 3.0)
        (x0, x1), _, _ = bounds(scaled)
        self.assertAlmostEqual(x1 - x0, 6.0)


class TestAxisConversion(unittest.TestCase):
    """Sprue channels run along X, but the primitives are built along Z."""

    def test_z_axis_cone_becomes_an_x_axis_cone(self):
        coords, indices = meshgen.cone(0, 0, 0, 2.0, 10, 1.0, segments=32)
        turned = meshgen.axis_z_to_x(coords)
        (x0, x1), _, (z0, z1) = bounds(turned)
        self.assertAlmostEqual(x1 - x0, 10.0)  # length now runs along X
        self.assertAlmostEqual(z1 - z0, 4.0)   # widest radius now spans Z

    def test_conversion_preserves_watertightness_and_winding(self):
        coords, indices = meshgen.cone(0, 0, 0, 2.0, 10, 1.0, segments=32)
        turned = meshgen.axis_z_to_x(coords)
        assert_watertight(self, turned, indices)
        # A rotation has determinant +1, so the volume must not flip sign.
        self.assertGreater(signed_volume(turned, indices), 0)
        self.assertAlmostEqual(
            signed_volume(turned, indices), signed_volume(coords, indices), places=9
        )

    def test_the_low_z_end_maps_to_the_low_x_end(self):
        coords, _ = meshgen.cone(0, 0, 0, 5.0, 10, 1.0, segments=48)
        turned = meshgen.axis_z_to_x(coords)
        wide = [
            math.hypot(turned[i + 1], turned[i + 2])
            for i in range(0, len(turned), 3)
            if abs(turned[i]) < 1e-9
        ]
        self.assertAlmostEqual(max(wide), 5.0, places=6)

    def test_conversion_leaves_the_original_untouched(self):
        coords, _ = meshgen.cone(0, 0, 0, 2.0, 10, 1.0, segments=16)
        before = list(coords)
        meshgen.axis_z_to_x(coords)
        self.assertEqual(coords, before)


class TestRunnerTransforms(unittest.TestCase):
    """A central runner needs a channel along Y and mirrored cavities."""

    def test_z_axis_cone_becomes_a_y_axis_cone(self):
        coords, indices = meshgen.cone(0, 0, 0, 2.0, 10, 1.0, segments=32)
        turned = meshgen.axis_z_to_y(coords)
        _, (y0, y1), (z0, z1) = bounds(turned)
        self.assertAlmostEqual(y1 - y0, 10.0)
        self.assertAlmostEqual(z1 - z0, 4.0)

    def test_y_conversion_keeps_the_mesh_sound(self):
        coords, indices = meshgen.cone(0, 0, 0, 2.0, 10, 1.0, segments=32)
        turned = meshgen.axis_z_to_y(coords)
        assert_watertight(self, turned, indices)
        self.assertAlmostEqual(
            signed_volume(turned, indices), signed_volume(coords, indices), places=9
        )

    def test_the_low_z_end_maps_to_the_low_y_end(self):
        coords, _ = meshgen.cone(0, 0, 0, 5.0, 10, 1.0, segments=48)
        turned = meshgen.axis_z_to_y(coords)
        wide = [
            math.hypot(turned[i], turned[i + 2])
            for i in range(0, len(turned), 3)
            if abs(turned[i + 1]) < 1e-9
        ]
        self.assertAlmostEqual(max(wide), 5.0, places=6)

    def test_half_turn_points_the_lure_the_other_way(self):
        coords, _ = meshgen.cone(0, 0, 0, 4.0, 10, 0.0, segments=24)
        along_x = meshgen.axis_z_to_x(coords)
        turned = meshgen.rotate_z_180(along_x)
        (x0, x1), _, _ = bounds(along_x)
        (t0, t1), _, _ = bounds(turned)
        # The span mirrors about the origin.
        self.assertAlmostEqual(t0, -x1)
        self.assertAlmostEqual(t1, -x0)

    def test_half_turn_is_a_rotation_not_a_mirror(self):
        # A mirror would flip the sign of the volume; a rotation must not.
        coords, indices = meshgen.box(3, 1, 0, 2, 4, 6)
        turned = meshgen.rotate_z_180(coords)
        assert_watertight(self, turned, indices)
        self.assertAlmostEqual(
            signed_volume(turned, indices), signed_volume(coords, indices), places=9
        )

    def test_half_turn_leaves_z_alone(self):
        coords, _ = meshgen.box(3, 1, 5, 2, 4, 6)
        turned = meshgen.rotate_z_180(coords)
        _, _, (z0, z1) = bounds(turned)
        self.assertAlmostEqual(z0, 2.0)
        self.assertAlmostEqual(z1, 8.0)


class TestReversedPrimitives(unittest.TestCase):
    """Channels run in both directions, so the ends may arrive reversed.

    A cavity gating into a central runner from the right passes z1 < z0. If
    the primitive is built assuming z0 < z1 it comes out inside-out, and an
    inverted tool body does not cut -- the gate simply stays filled.
    """

    def test_a_cone_built_backwards_is_still_outward_facing(self):
        coords, indices = meshgen.cone(0, 0, 10, 2.0, 0, 4.0, segments=32)
        assert_watertight(self, coords, indices)
        self.assertGreater(
            signed_volume(coords, indices), 0, "backwards cone is inside-out"
        )

    def test_a_backwards_cone_matches_the_forward_one(self):
        forward = meshgen.cone(0, 0, 0, 4.0, 10, 2.0, segments=32)
        backward = meshgen.cone(0, 0, 10, 2.0, 0, 4.0, segments=32)
        self.assertAlmostEqual(
            signed_volume(*forward), signed_volume(*backward), places=9
        )
        self.assertEqual(bounds(forward[0]), bounds(backward[0]))

    def test_a_backwards_cylinder_is_still_outward_facing(self):
        coords, indices = meshgen.cylinder(0, 0, 7, -3, radius=2, segments=24)
        assert_watertight(self, coords, indices)
        self.assertGreater(signed_volume(coords, indices), 0)

    def test_a_backwards_cone_with_an_apex_is_sound(self):
        coords, indices = meshgen.cone(0, 0, 6, 0.0, 0, 3.0, segments=32)
        assert_watertight(self, coords, indices)
        self.assertGreater(signed_volume(coords, indices), 0)

    def test_radii_still_belong_to_the_right_ends(self):
        coords, _ = meshgen.cone(0, 0, 10, 1.0, 0, 5.0, segments=64)
        at_zero = [
            math.hypot(coords[i], coords[i + 1])
            for i in range(0, len(coords), 3)
            if abs(coords[i + 2]) < 1e-9
        ]
        self.assertAlmostEqual(max(at_zero), 5.0, places=6)


class TestBoxFrustum(unittest.TestCase):
    """A box whose XY size differs at each end - the mold relief ramp."""

    def test_a_frustum_is_watertight_and_outward_wound(self):
        coords, indices = meshgen.box_frustum(0, 0, 0, 10, 6, 4, 18, 14)
        assert_watertight(self, coords, indices)
        self.assertGreater(signed_volume(coords, indices), 0)

    def test_equal_ends_match_a_plain_box(self):
        frustum = meshgen.box_frustum(0, 0, -2, 4, 6, 2, 4, 6)
        box = meshgen.box(0, 0, 0, 4, 6, 4)
        self.assertAlmostEqual(
            signed_volume(*frustum), signed_volume(*box), places=9
        )

    def test_each_end_has_the_size_it_was_given(self):
        coords, _ = meshgen.box_frustum(0, 0, 0, 10, 6, 5, 20, 12)
        at_low = [
            (coords[i], coords[i + 1])
            for i in range(0, len(coords), 3)
            if abs(coords[i + 2]) < 1e-9
        ]
        at_high = [
            (coords[i], coords[i + 1])
            for i in range(0, len(coords), 3)
            if abs(coords[i + 2] - 5) < 1e-9
        ]
        self.assertAlmostEqual(max(x for x, _ in at_low) * 2, 10.0)
        self.assertAlmostEqual(max(y for _, y in at_low) * 2, 6.0)
        self.assertAlmostEqual(max(x for x, _ in at_high) * 2, 20.0)
        self.assertAlmostEqual(max(y for _, y in at_high) * 2, 12.0)

    def test_a_frustum_is_centred_where_asked(self):
        coords, _ = meshgen.box_frustum(7, -3, 0, 4, 4, 2, 8, 8)
        (x0, x1), (y0, y1), _ = bounds(coords)
        self.assertAlmostEqual((x0 + x1) / 2, 7.0)
        self.assertAlmostEqual((y0 + y1) / 2, -3.0)

    def test_reversed_ends_are_accepted(self):
        forward = meshgen.box_frustum(0, 0, 0, 10, 6, 4, 18, 14)
        backward = meshgen.box_frustum(0, 0, 4, 18, 14, 0, 10, 6)
        assert_watertight(self, *backward)
        self.assertAlmostEqual(
            signed_volume(*forward), signed_volume(*backward), places=9
        )

    def test_volume_matches_the_prismatoid_formula(self):
        # Prismatoid: h/6 * (A_bottom + 4*A_middle + A_top).
        lx0, ly0, lx1, ly1, h = 10.0, 6.0, 20.0, 12.0, 5.0
        coords, indices = meshgen.box_frustum(0, 0, 0, lx0, ly0, h, lx1, ly1)
        mid = ((lx0 + lx1) / 2) * ((ly0 + ly1) / 2)
        expected = h / 6 * (lx0 * ly0 + 4 * mid + lx1 * ly1)
        self.assertAlmostEqual(signed_volume(coords, indices), expected, places=6)


class TestLathe(unittest.TestCase):
    """A solid of revolution -- pegs and holes with lead-in chamfers."""

    def test_a_cylinder_profile_matches_the_cylinder_primitive(self):
        lathed = meshgen.lathe(0, 0, [(0, 0), (2, 0), (2, 5), (0, 5)], 64)
        plain = meshgen.cylinder(0, 0, 0, 5, radius=2, segments=64)
        self.assertAlmostEqual(
            signed_volume(*lathed), signed_volume(*plain), places=6
        )

    def test_a_lathed_solid_is_watertight_and_outward_wound(self):
        coords, indices = meshgen.lathe(
            0, 0, [(0, 0), (3, 0), (3, 8), (2, 10), (0, 10)], 32
        )
        assert_watertight(self, coords, indices)
        self.assertGreater(signed_volume(coords, indices), 0)

    def test_a_chamfered_peg_loses_volume_at_the_tip(self):
        straight = meshgen.lathe(0, 0, [(0, 0), (2.5, 0), (2.5, 5), (0, 5)], 64)
        chamfered = meshgen.lathe(
            0, 0, [(0, 0), (2.5, 0), (2.5, 4.4), (1.9, 5), (0, 5)], 64
        )
        self.assertLess(signed_volume(*chamfered), signed_volume(*straight))

    def test_the_profile_governs_the_radius_at_each_height(self):
        coords, _ = meshgen.lathe(
            0, 0, [(0, 0), (5, 0), (5, 4), (2, 6), (0, 6)], 64
        )
        at_base = [
            math.hypot(coords[i], coords[i + 1])
            for i in range(0, len(coords), 3)
            if abs(coords[i + 2]) < 1e-9
        ]
        at_top = [
            math.hypot(coords[i], coords[i + 1])
            for i in range(0, len(coords), 3)
            if abs(coords[i + 2] - 6.0) < 1e-9
        ]
        self.assertAlmostEqual(max(at_base), 5.0, places=6)
        self.assertAlmostEqual(max(at_top), 2.0, places=6)

    def test_a_profile_is_positioned_in_xy(self):
        coords, _ = meshgen.lathe(7, -3, [(0, 0), (2, 0), (2, 4), (0, 4)], 32)
        (x0, x1), (y0, y1), _ = bounds(coords)
        self.assertAlmostEqual((x0 + x1) / 2, 7.0, places=6)
        self.assertAlmostEqual((y0 + y1) / 2, -3.0, places=6)

    def test_a_profile_that_does_not_close_on_the_axis_is_refused(self):
        with self.assertRaises(ValueError):
            meshgen.lathe(0, 0, [(2, 0), (2, 5)], 16)


if __name__ == "__main__":
    unittest.main()
