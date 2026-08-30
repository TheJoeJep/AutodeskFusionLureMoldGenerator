"""Unit tests for triangle winding repair.

Real downloaded STLs are routinely watertight but inconsistently wound. Fusion
reports isOriented=False and volume=0.0 for those, and a mesh boolean against
one does not fail -- it silently produces a corrupt result. So the winding has
to be fixed before the mesh is used as a cutting tool.

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

from lure_mold import mesh_repair, meshgen  # noqa: E402


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


def is_consistent(indices):
    """Every directed edge must be used exactly once."""
    directed = Counter()
    for t in range(0, len(indices), 3):
        a, b, c = indices[t], indices[t + 1], indices[t + 2]
        directed[(a, b)] += 1
        directed[(b, c)] += 1
        directed[(c, a)] += 1
    return all(count == 1 for count in directed.values())


def flip_triangle(indices, n):
    out = list(indices)
    out[3 * n + 1], out[3 * n + 2] = out[3 * n + 2], out[3 * n + 1]
    return out


class TestWindingRepair(unittest.TestCase):
    def test_an_already_consistent_mesh_is_left_alone(self):
        coords, indices = meshgen.box(0, 0, 0, 2, 4, 6)
        fixed, flips = mesh_repair.make_consistent(coords, indices)
        self.assertEqual(flips, 0)
        self.assertEqual(list(fixed), list(indices))

    def test_a_single_flipped_face_is_repaired(self):
        coords, indices = meshgen.box(0, 0, 0, 2, 4, 6)
        broken = flip_triangle(indices, 5)
        self.assertFalse(is_consistent(broken))

        fixed, flips = mesh_repair.make_consistent(coords, broken)
        self.assertTrue(is_consistent(fixed))
        self.assertEqual(flips, 1)
        self.assertAlmostEqual(signed_volume(coords, fixed), 48.0)

    def test_many_scattered_flipped_faces_are_repaired(self):
        coords, indices = meshgen.cylinder(0, 0, 0, 5, radius=2, segments=32)
        broken = list(indices)
        for n in range(0, len(indices) // 3, 3):
            broken = flip_triangle(broken, n)
        self.assertFalse(is_consistent(broken))

        fixed, _ = mesh_repair.make_consistent(coords, broken)
        self.assertTrue(is_consistent(fixed))
        self.assertGreater(signed_volume(coords, fixed), 0)

    def test_a_fully_inverted_mesh_is_turned_back_outward(self):
        coords, indices = meshgen.box(0, 0, 0, 2, 4, 6)
        inverted = list(indices)
        for n in range(len(indices) // 3):
            inverted = flip_triangle(inverted, n)
        # Consistent, but wound inward: negative volume.
        self.assertTrue(is_consistent(inverted))
        self.assertLess(signed_volume(coords, inverted), 0)

        fixed, _ = mesh_repair.make_consistent(coords, inverted)
        self.assertTrue(is_consistent(fixed))
        self.assertAlmostEqual(signed_volume(coords, fixed), 48.0)

    def test_repair_never_changes_the_triangle_count(self):
        coords, indices = meshgen.cone(0, 0, 0, 3.0, 6, 0.0, segments=24)
        broken = flip_triangle(flip_triangle(indices, 2), 7)
        fixed, _ = mesh_repair.make_consistent(coords, broken)
        self.assertEqual(len(fixed), len(indices))

    def test_each_separate_shell_is_oriented_outward_independently(self):
        # Two boxes far apart, the second one entirely inverted.
        c1, i1 = meshgen.box(0, 0, 0, 2, 2, 2)
        c2, i2 = meshgen.box(50, 0, 0, 2, 2, 2)
        offset = len(c1) // 3
        coords = list(c1) + list(c2)
        indices = list(i1) + [i + offset for i in i2]
        for n in range(len(i1) // 3, len(indices) // 3):
            indices = flip_triangle(indices, n)

        fixed, _ = mesh_repair.make_consistent(coords, indices)
        self.assertTrue(is_consistent(fixed))
        # Both shells outward means the total volume is both boxes, not zero.
        self.assertAlmostEqual(signed_volume(coords, fixed), 16.0)

    def test_the_input_list_is_not_mutated(self):
        coords, indices = meshgen.box(0, 0, 0, 2, 4, 6)
        broken = flip_triangle(indices, 1)
        before = list(broken)
        mesh_repair.make_consistent(coords, broken)
        self.assertEqual(broken, before)


class TestVolume(unittest.TestCase):
    def test_volume_matches_the_analytic_box(self):
        coords, indices = meshgen.box(0, 0, 0, 2, 4, 6)
        self.assertAlmostEqual(mesh_repair.volume(coords, indices), 48.0)

    def test_volume_is_reported_positive_for_an_inverted_mesh(self):
        coords, indices = meshgen.box(0, 0, 0, 2, 4, 6)
        inverted = list(indices)
        for n in range(len(indices) // 3):
            inverted = flip_triangle(inverted, n)
        self.assertAlmostEqual(mesh_repair.volume(coords, inverted), 48.0)


class TestNonManifoldDetection(unittest.TestCase):
    """Some defects cannot be repaired by re-winding and must be reported.

    A real 230k-triangle model turned out to have 7 edges with four faces
    meeting on them. No consistent orientation exists at such an edge, so
    Fusion reports isOriented=False no matter how the winding is fixed, and a
    boolean against it produces a corrupt result.
    """

    def test_a_clean_mesh_has_no_non_manifold_edges(self):
        coords, indices = meshgen.box(0, 0, 0, 2, 4, 6)
        self.assertEqual(mesh_repair.find_non_manifold_edges(indices), [])

    def test_a_clean_cylinder_has_no_non_manifold_edges(self):
        coords, indices = meshgen.cylinder(0, 0, 0, 5, radius=2, segments=24)
        self.assertEqual(mesh_repair.find_non_manifold_edges(indices), [])

    def test_an_edge_shared_by_three_faces_is_reported(self):
        coords, indices = meshgen.box(0, 0, 0, 2, 4, 6)
        # A stray fin hanging off an existing edge: vertices 0 and 1 already
        # share an edge, so adding a face on them makes it three-sided.
        extra_vertex = len(coords) // 3
        coords = list(coords) + [10.0, 10.0, 10.0]
        indices = list(indices) + [0, 1, extra_vertex]

        found = mesh_repair.find_non_manifold_edges(indices)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0], (0, 1))

    def test_winding_repair_still_reports_the_flip_count_it_managed(self):
        coords, indices = meshgen.box(0, 0, 0, 2, 4, 6)
        broken = flip_triangle(indices, 3)
        fixed, flips = mesh_repair.make_consistent(coords, broken)
        self.assertEqual(flips, 1)
        self.assertEqual(mesh_repair.find_non_manifold_edges(fixed), [])

    def test_open_edges_are_not_mistaken_for_non_manifold_ones(self):
        # A single triangle: three edges, each used once. Open, not branching.
        coords = [0, 0, 0, 1, 0, 0, 0, 1, 0]
        indices = [0, 1, 2]
        self.assertEqual(mesh_repair.find_non_manifold_edges(indices), [])


if __name__ == "__main__":
    unittest.main()
