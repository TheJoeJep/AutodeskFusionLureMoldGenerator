"""Unit tests for the shape-following parting-face relief.

The first attempt used a bounding box around each feature. On a figure with
spread limbs that keeps roughly twice the flat area it needs -- one real model
filled only 52% of its own bounding box -- so the slope started nowhere near
the shape and the gaps between the limbs stayed flat.

This builds the relief as a height field instead: distance to the nearest
feature, sampled on a grid, turned into a surface that is flat near features
and ramps down away from them.

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

from lure_mold import meshgen, relief  # noqa: E402


def assert_watertight(test, coords, indices):
    directed = Counter()
    for t in range(0, len(indices), 3):
        a, b, c = indices[t], indices[t + 1], indices[t + 2]
        test.assertEqual(len({a, b, c}), 3, "degenerate triangle")
        directed[(a, b)] += 1
        directed[(b, c)] += 1
        directed[(c, a)] += 1
    for (a, b), count in directed.items():
        test.assertEqual(count, 1, f"directed edge {(a, b)} used {count} times")
        test.assertEqual(directed[(b, a)], 1, f"edge {(a, b)} has no opposite")


def signed_volume(coords, indices):
    total = 0.0
    for t in range(0, len(indices), 3):
        a, b, c = indices[t], indices[t + 1], indices[t + 2]
        ax, ay, az = coords[3 * a : 3 * a + 3]
        bx, by, bz = coords[3 * b : 3 * b + 3]
        cx, cy, cz = coords[3 * c : 3 * c + 3]
        total += (
            ax * (by * cz - bz * cy)
            - ay * (bx * cz - bz * cx)
            + az * (bx * cy - by * cx)
        )
    return total / 6.0


class TestGrid(unittest.TestCase):
    def test_a_grid_covers_the_requested_area(self):
        grid = relief.make_grid(-10.0, -5.0, 10.0, 5.0, 1.0)
        self.assertLessEqual(grid.min_x, -10.0)
        self.assertLessEqual(grid.min_y, -5.0)
        self.assertGreaterEqual(grid.min_x + grid.cell * grid.nx, 10.0)
        self.assertGreaterEqual(grid.min_y + grid.cell * grid.ny, 5.0)

    def test_a_finer_cell_gives_more_nodes(self):
        coarse = relief.make_grid(0, 0, 20, 20, 2.0)
        fine = relief.make_grid(0, 0, 20, 20, 0.5)
        self.assertGreater(fine.nx, coarse.nx)

    def test_the_node_count_is_capped(self):
        # An absurdly fine cell on a big block must not explode.
        grid = relief.make_grid(0, 0, 400, 400, 0.01)
        self.assertLessEqual((grid.nx + 1) * (grid.ny + 1), relief.MAX_NODES)


class TestMarking(unittest.TestCase):
    def test_a_disc_marks_nodes_inside_it(self):
        grid = relief.make_grid(-10, -10, 10, 10, 1.0)
        mask = relief.new_mask(grid)
        relief.mark_disc(grid, mask, 0.0, 0.0, 3.0)
        self.assertTrue(relief.marked_at(grid, mask, 0.0, 0.0))
        self.assertTrue(relief.marked_at(grid, mask, 2.0, 0.0))
        self.assertFalse(relief.marked_at(grid, mask, 8.0, 0.0))

    def test_a_rectangle_marks_its_own_area(self):
        grid = relief.make_grid(-10, -10, 10, 10, 0.5)
        mask = relief.new_mask(grid)
        relief.mark_rect(grid, mask, 0.0, 0.0, 6.0, 2.0)
        self.assertTrue(relief.marked_at(grid, mask, 2.5, 0.5))
        self.assertFalse(relief.marked_at(grid, mask, 2.5, 4.0))

    def test_triangles_mark_the_silhouette_not_the_bounding_box(self):
        # A thin diagonal bar: its bounding box is a big square, the shape is
        # not. The corners of the box must stay unmarked.
        grid = relief.make_grid(-12, -12, 12, 12, 0.5)
        mask = relief.new_mask(grid)
        coords = [-10, -10, 0, 10, 10, 0, -10, -8, 0, 10, 12, 0]
        indices = [0, 1, 2, 1, 3, 2]
        relief.mark_triangles(grid, mask, coords, indices)
        self.assertTrue(relief.marked_at(grid, mask, 0.0, 0.0))
        self.assertFalse(relief.marked_at(grid, mask, 9.0, -9.0))
        self.assertFalse(relief.marked_at(grid, mask, -9.0, 9.0))


class TestDistanceField(unittest.TestCase):
    def test_distance_is_zero_on_a_marked_node(self):
        grid = relief.make_grid(-10, -10, 10, 10, 1.0)
        mask = relief.new_mask(grid)
        relief.mark_disc(grid, mask, 0.0, 0.0, 2.0)
        field = relief.distance_field(grid, mask)
        self.assertAlmostEqual(relief.sample(grid, field, 0.0, 0.0), 0.0)

    def test_distance_grows_with_separation(self):
        grid = relief.make_grid(-20, -20, 20, 20, 0.5)
        mask = relief.new_mask(grid)
        relief.mark_disc(grid, mask, 0.0, 0.0, 2.0)
        field = relief.distance_field(grid, mask)
        near = relief.sample(grid, field, 4.0, 0.0)
        far = relief.sample(grid, field, 10.0, 0.0)
        self.assertGreater(far, near)

    def test_distance_approximates_the_true_euclidean_distance(self):
        grid = relief.make_grid(-20, -20, 20, 20, 0.25)
        mask = relief.new_mask(grid)
        relief.mark_disc(grid, mask, 0.0, 0.0, 2.0)
        field = relief.distance_field(grid, mask)
        # A point 10mm out from a disc of radius 2 is about 8mm from it.
        measured = relief.sample(grid, field, 10.0, 0.0)
        self.assertLess(abs(measured - 8.0), 0.6)

    def test_an_empty_mask_gives_a_large_distance_everywhere(self):
        grid = relief.make_grid(-5, -5, 5, 5, 1.0)
        mask = relief.new_mask(grid)
        field = relief.distance_field(grid, mask)
        self.assertGreater(relief.sample(grid, field, 0.0, 0.0), 100.0)


class TestHeights(unittest.TestCase):
    def test_inside_the_land_the_face_stays_flat(self):
        self.assertAlmostEqual(relief.height_at(0.0, 4.0, 2.0, 1.68), 0.0)
        self.assertAlmostEqual(relief.height_at(3.9, 4.0, 2.0, 1.68), 0.0)

    def test_beyond_the_land_the_face_drops(self):
        self.assertLess(relief.height_at(5.0, 4.0, 2.0, 1.68), 0.0)

    def test_the_drop_bottoms_out_at_the_recess_depth(self):
        self.assertAlmostEqual(relief.height_at(100.0, 4.0, 2.0, 1.68), -2.0)

    def test_the_ramp_follows_the_requested_slope(self):
        # Halfway along the ramp it should be halfway down.
        run = 1.68
        self.assertAlmostEqual(
            relief.height_at(4.0 + run / 2, 4.0, 2.0, run), -1.0, places=6
        )

    def test_a_zero_run_gives_a_vertical_step(self):
        self.assertAlmostEqual(relief.height_at(4.001, 4.0, 2.0, 0.0), -2.0)


class TestTerrainMesh(unittest.TestCase):
    def build(self, sign=-1.0):
        grid = relief.make_grid(-20, -20, 20, 20, 1.0)
        mask = relief.new_mask(grid)
        relief.mark_disc(grid, mask, 0.0, 0.0, 5.0)
        field = relief.distance_field(grid, mask)
        return grid, relief.terrain(grid, field, 4.0, 2.0, 1.68, sign, 12.0)

    def test_the_cutter_is_watertight(self):
        _, (coords, indices) = self.build()
        assert_watertight(self, coords, indices)

    def test_the_cutter_is_outward_wound(self):
        _, (coords, indices) = self.build()
        self.assertGreater(signed_volume(coords, indices), 0)

    def test_the_bottom_half_cutter_sits_above_the_face(self):
        _, (coords, _) = self.build(sign=-1.0)
        zs = coords[2::3]
        # It reaches the cap well above, and dips only to the recess depth.
        self.assertGreater(max(zs), 10.0)
        self.assertAlmostEqual(min(zs), -2.0, places=6)

    def test_the_top_half_cutter_is_the_mirror_image(self):
        _, (coords, indices) = self.build(sign=1.0)
        assert_watertight(self, coords, indices)
        self.assertGreater(signed_volume(coords, indices), 0)
        zs = coords[2::3]
        self.assertLess(min(zs), -10.0)
        self.assertAlmostEqual(max(zs), 2.0, places=6)

    def test_the_surface_is_flat_over_the_feature(self):
        # Directly above the marked disc the cutter must start at z = 0, so
        # nothing is taken off the sealing land.
        grid, (coords, _) = self.build(sign=-1.0)
        near_axis = [
            coords[i + 2]
            for i in range(0, len(coords), 3)
            if abs(coords[i]) < 0.6 and abs(coords[i + 1]) < 0.6
            and coords[i + 2] < 5.0
        ]
        self.assertTrue(near_axis)
        for z in near_axis:
            self.assertAlmostEqual(z, 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
