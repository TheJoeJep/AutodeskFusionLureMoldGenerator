"""Unit tests for principal-axis orientation.

Fusion's orientedMinimumBoundingBox is only approximate -- on a rotated
ellipsoid it picks axes that are visibly wrong, which would tilt the lure
inside the mold. These tests pin down an exact alternative.

Runs OUTSIDE Fusion:
    python -m unittest discover -s tests -v
"""

import math
import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "LureMoldGenerator")
)

from lure_mold import meshgen  # noqa: E402
from lure_mold import orient  # noqa: E402


def ellipsoid(rx, ry, rz, nu=48, nv=24):
    verts = [(0.0, 0.0, rz)]
    for i in range(1, nv):
        theta = math.pi * i / nv
        st, ct = math.sin(theta), math.cos(theta)
        for j in range(nu):
            phi = 2 * math.pi * j / nu
            verts.append((rx * st * math.cos(phi), ry * st * math.sin(phi), rz * ct))
    verts.append((0.0, 0.0, -rz))
    bottom = len(verts) - 1

    def ring(i, j):
        return 1 + (i - 1) * nu + (j % nu)

    idx = []
    for j in range(nu):
        idx += [0, ring(1, j), ring(1, j + 1)]
    for i in range(1, nv - 1):
        for j in range(nu):
            a, b = ring(i, j), ring(i, j + 1)
            c, d = ring(i + 1, j), ring(i + 1, j + 1)
            idx += [a, c, b, b, c, d]
    for j in range(nu):
        idx += [bottom, ring(nv - 1, j + 1), ring(nv - 1, j)]
    return [v for vert in verts for v in vert], idx


def rotate(coords, yaw, pitch):
    out = []
    for i in range(0, len(coords), 3):
        x, y, z = coords[i], coords[i + 1], coords[i + 2]
        x, y = x * math.cos(yaw) - y * math.sin(yaw), x * math.sin(yaw) + y * math.cos(yaw)
        x, z = x * math.cos(pitch) - z * math.sin(pitch), x * math.sin(pitch) + z * math.cos(pitch)
        out += [x, y, z]
    return out


class TestPrincipalAxes(unittest.TestCase):
    def test_axes_are_orthonormal(self):
        coords, indices = ellipsoid(5.0, 1.5, 0.6)
        axes, _ = orient.principal_axes(coords, indices)
        for axis in axes:
            self.assertAlmostEqual(math.sqrt(sum(c * c for c in axis)), 1.0, places=9)
        for a in range(3):
            for b in range(a + 1, 3):
                dot = sum(axes[a][k] * axes[b][k] for k in range(3))
                self.assertAlmostEqual(dot, 0.0, places=9)

    def test_axes_are_right_handed(self):
        coords, indices = ellipsoid(5.0, 1.5, 0.6)
        (ax, ay, az), _ = orient.principal_axes(coords, indices)
        cross = (
            ax[1] * ay[2] - ax[2] * ay[1],
            ax[2] * ay[0] - ax[0] * ay[2],
            ax[0] * ay[1] - ax[1] * ay[0],
        )
        self.assertAlmostEqual(sum(cross[k] * az[k] for k in range(3)), 1.0, places=9)

    def test_extents_are_sorted_longest_first(self):
        coords, indices = ellipsoid(5.0, 1.5, 0.6)
        _, extents = orient.principal_axes(coords, indices)
        self.assertGreaterEqual(extents[0], extents[1])
        self.assertGreaterEqual(extents[1], extents[2])

    def test_recovers_true_extents_of_an_axis_aligned_ellipsoid(self):
        coords, indices = ellipsoid(5.0, 1.5, 0.6)
        _, extents = orient.principal_axes(coords, indices)
        self.assertAlmostEqual(extents[0], 10.0, places=2)
        self.assertAlmostEqual(extents[1], 3.0, places=2)
        self.assertAlmostEqual(extents[2], 1.2, places=2)

    def test_recovers_true_extents_after_an_arbitrary_rotation(self):
        # This is the case Fusion's own bounding box gets wrong.
        coords, indices = ellipsoid(5.0, 1.5, 0.6)
        spun = rotate(coords, math.radians(37), math.radians(20))
        _, extents = orient.principal_axes(spun, indices)
        self.assertAlmostEqual(extents[0], 10.0, places=2)
        self.assertAlmostEqual(extents[1], 3.0, places=2)
        self.assertAlmostEqual(extents[2], 1.2, places=2)

    def test_orientation_is_stable_across_many_rotations(self):
        coords, indices = ellipsoid(5.0, 1.5, 0.6)
        for yaw_deg, pitch_deg in [(0, 0), (13, 71), (90, 45), (137, 12), (200, 300)]:
            spun = rotate(coords, math.radians(yaw_deg), math.radians(pitch_deg))
            _, extents = orient.principal_axes(spun, indices)
            self.assertAlmostEqual(extents[0], 10.0, places=2)
            self.assertAlmostEqual(extents[2], 1.2, places=2)

    def test_box_extents_are_recovered(self):
        coords, indices = meshgen.box(0, 0, 0, 6.0, 4.0, 2.0)
        _, extents = orient.principal_axes(coords, indices)
        self.assertAlmostEqual(extents[0], 6.0, places=6)
        self.assertAlmostEqual(extents[1], 4.0, places=6)
        self.assertAlmostEqual(extents[2], 2.0, places=6)


class TestProjection(unittest.TestCase):
    def test_project_centres_the_shape_on_the_origin(self):
        coords, indices = ellipsoid(5.0, 1.5, 0.6)
        moved = meshgen.translate(coords, 100.0, -40.0, 7.0)
        axes, _ = orient.principal_axes(moved, indices)
        local = orient.project(moved, axes)
        for k in range(3):
            values = local[k::3]
            self.assertAlmostEqual((min(values) + max(values)) / 2, 0.0, places=6)

    def test_projected_shape_is_axis_aligned_with_the_expected_sizes(self):
        coords, indices = ellipsoid(5.0, 1.5, 0.6)
        spun = rotate(coords, math.radians(37), math.radians(20))
        axes, _ = orient.principal_axes(spun, indices)
        local = orient.project(spun, axes)
        for k, expected in enumerate((10.0, 3.0, 1.2)):
            values = local[k::3]
            self.assertAlmostEqual(max(values) - min(values), expected, places=2)


if __name__ == "__main__":
    unittest.main()
