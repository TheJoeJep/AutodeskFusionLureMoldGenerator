"""Unit tests for automatic parting-plane placement.

The parting plane used to be fixed at the middle of the bounding box. On a
turtle that cuts through the shell dome and leaves the fins and head entirely
below the plane, buried in the bottom half with material closed over them --
they can never release.

The right plane is the one where the most of the model straddles it. A ray
whose solid lies wholly on one side is a trapped feature.

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

from lure_mold import meshgen, parting  # noqa: E402


def revolve(profile, segments=48):
    """Watertight solid of revolution from a (radius, z) profile.

    The profile must start and end on the axis (radius 0), running bottom to
    top. Handy for building test shapes with a known cross-section.
    """
    verts = []
    rings = []
    for radius, z in profile:
        if radius <= 0.0:
            verts.append((0.0, 0.0, z))
            rings.append(("point", len(verts) - 1))
        else:
            start = len(verts)
            for j in range(segments):
                angle = 2 * math.pi * j / segments
                verts.append((radius * math.cos(angle), radius * math.sin(angle), z))
            rings.append(("ring", start))

    faces = []
    for i in range(len(rings) - 1):
        kind_a, a = rings[i]
        kind_b, b = rings[i + 1]
        for j in range(segments):
            k = (j + 1) % segments
            if kind_a == "point" and kind_b == "ring":
                faces.append((a, b + k, b + j))
            elif kind_a == "ring" and kind_b == "point":
                faces.append((a + j, a + k, b))
            elif kind_a == "ring" and kind_b == "ring":
                faces.append((a + j, a + k, b + j))
                faces.append((a + k, b + k, b + j))

    coords = [c for v in verts for c in v]
    indices = [i for f in faces for i in f]
    return coords, indices


def turtle_like(segments=64):
    """A wide thin flange with a tall dome on top -- a turtle in miniature.

    Bounding box spans z -1 to 5, so its middle is z = 2, which slices through
    the dome and strands the flange. The flange is the correct parting level.
    """
    return revolve(
        [(0.0, -1.0), (10.0, -1.0), (10.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0)],
        segments,
    )


class TestRayColumns(unittest.TestCase):
    def test_a_box_gives_one_solid_span_per_ray(self):
        coords, indices = meshgen.box(0, 0, 0, 10, 10, 4)
        columns = parting.ray_columns(coords, indices, 12, 12)
        hits = [c for c in columns if c]
        self.assertTrue(hits)
        for spans in hits:
            self.assertEqual(len(spans), 1)
            low, high = spans[0]
            self.assertAlmostEqual(low, -2.0, places=6)
            self.assertAlmostEqual(high, 2.0, places=6)

    def test_rays_that_miss_the_model_are_empty(self):
        coords, indices = meshgen.cylinder(0, 0, -2, 2, radius=3, segments=32)
        columns = parting.ray_columns(coords, indices, 20, 20)
        self.assertTrue(any(not c for c in columns), "corners should miss")

    def test_a_stepped_shape_reports_the_right_spans(self):
        coords, indices = turtle_like()
        columns = parting.ray_columns(coords, indices, 40, 40)
        spans = [c[0] for c in columns if len(c) == 1]
        lows = [low for low, _ in spans]
        for low in lows:
            self.assertAlmostEqual(low, -1.0, places=4)


class TestPartingChoice(unittest.TestCase):
    def test_a_symmetric_box_parts_through_its_middle(self):
        coords, indices = meshgen.box(0, 0, 0, 10, 10, 4)
        z, score = parting.best_parting_z(coords, indices)
        self.assertGreater(score, 0.99)
        self.assertLess(abs(z), 2.0)

    def test_a_sphere_parts_near_its_equator(self):
        coords, indices = revolve(
            [(0.0, -5.0)]
            + [(5.0 * math.sin(math.pi * i / 24), -5.0 * math.cos(math.pi * i / 24))
               for i in range(1, 24)]
            + [(0.0, 5.0)],
            48,
        )
        z, score = parting.best_parting_z(coords, indices)
        self.assertGreater(score, 0.99)
        self.assertLess(abs(z), 1.5)

    def test_the_turtle_shape_parts_at_the_flange_not_the_middle(self):
        coords, indices = turtle_like()
        z, score = parting.best_parting_z(coords, indices)
        # The flange occupies z -1 to 0; the bounding-box middle is z = 2.
        self.assertGreater(z, -1.0)
        self.assertLess(z, 0.0)
        self.assertGreater(score, 0.95)

    def test_the_bounding_box_middle_is_measurably_worse(self):
        coords, indices = turtle_like()
        columns = parting.ray_columns(coords, indices, 60, 60)
        best_z, best_score = parting.best_parting_z(coords, indices)
        middle_score = parting.score_at(columns, 2.0)
        # The flange is most of the footprint, so the middle strands most of it.
        self.assertLess(middle_score, 0.4)
        self.assertGreater(best_score, middle_score + 0.5)

    def test_score_at_rejects_planes_outside_the_solid(self):
        coords, indices = meshgen.box(0, 0, 0, 10, 10, 4)
        columns = parting.ray_columns(coords, indices, 12, 12)
        self.assertGreater(parting.score_at(columns, 0.0), 0.99)
        self.assertEqual(parting.score_at(columns, 50.0), 0.0)

    def test_a_shape_with_a_through_hole_never_scores_perfectly(self):
        # A tube: every ray through the wall is fine, but the bore is hollow,
        # so rays there cross two separate spans however the plane is placed.
        outer, oi = meshgen.cylinder(0, 0, -3, 3, radius=6, segments=48)
        inner, ii = meshgen.cylinder(0, 0, -4, 4, radius=3, segments=48)
        # Flip the inner cylinder inward to make it a cavity.
        flipped = list(ii)
        for n in range(len(ii) // 3):
            flipped[3 * n + 1], flipped[3 * n + 2] = flipped[3 * n + 2], flipped[3 * n + 1]
        offset = len(outer) // 3
        coords = list(outer) + list(inner)
        indices = list(oi) + [i + offset for i in flipped]

        columns = parting.ray_columns(coords, indices, 40, 40)
        multi = [c for c in columns if len(c) > 1]
        self.assertTrue(multi, "the bore should produce two spans on some rays")


if __name__ == "__main__":
    unittest.main()
