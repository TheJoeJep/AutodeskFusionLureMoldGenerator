"""Unit tests for the mesh audit -- separate pieces, voids, loose islands.

The mold is a block with things cut out of it. Cut enough away and a chunk of
the block can end up attached to nothing, which prints as a loose lump rattling
around in the cavity. The lure mesh has its own version of the same problem:
downloaded models routinely carry stray shells, and each one carves a phantom
pocket somewhere in the block.

Runs OUTSIDE Fusion:
    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "LureMoldGenerator")
)

from lure_mold import mesh_audit, meshgen  # noqa: E402


def merge(*meshes):
    """Concatenate several (coords, indices) meshes into one soup."""
    coords, indices = [], []
    for piece_coords, piece_indices in meshes:
        offset = len(coords) // 3
        coords += list(piece_coords)
        indices += [i + offset for i in piece_indices]
    return coords, indices


def inside_out(mesh):
    """Reverse the winding, as the inner surface of a hollow solid has it."""
    coords, indices = mesh
    flipped = []
    for t in range(0, len(indices), 3):
        flipped += [indices[t], indices[t + 2], indices[t + 1]]
    return coords, flipped


class TestSeparatePieces(unittest.TestCase):
    def test_one_box_is_one_piece(self):
        coords, indices = meshgen.box(0, 0, 0, 10, 10, 10)
        self.assertEqual(len(mesh_audit.shells(coords, indices)), 1)

    def test_two_boxes_far_apart_are_two_pieces(self):
        coords, indices = merge(
            meshgen.box(0, 0, 0, 10, 10, 10),
            meshgen.box(50, 0, 0, 4, 4, 4),
        )
        self.assertEqual(len(mesh_audit.shells(coords, indices)), 2)

    def test_every_triangle_lands_in_exactly_one_piece(self):
        coords, indices = merge(
            meshgen.box(0, 0, 0, 10, 10, 10),
            meshgen.box(50, 0, 0, 4, 4, 4),
            meshgen.box(-50, 0, 0, 2, 2, 2),
        )
        pieces = mesh_audit.shells(coords, indices)
        seen = sorted(t for piece in pieces for t in piece)
        self.assertEqual(seen, list(range(len(indices) // 3)))

    def test_pieces_meeting_at_shared_vertices_count_as_one(self):
        # Two boxes built from the same corner coordinates: welding joins them,
        # so what looks like two soups is really one solid.
        box = meshgen.box(0, 0, 0, 10, 10, 10)
        coords, indices = merge(box, box)
        self.assertEqual(len(mesh_audit.shells(coords, indices)), 1)

    def test_without_welding_duplicate_vertices_look_separate(self):
        # The reason welding is not optional. If this ever stops being true,
        # the test above is no longer proving anything.
        box = meshgen.box(0, 0, 0, 10, 10, 10)
        coords, indices = merge(box, box)
        self.assertEqual(
            len(mesh_audit.shells(coords, indices, weld_tolerance=0.0)), 2
        )


class TestClassifyingPieces(unittest.TestCase):
    def test_a_solid_piece_has_positive_volume(self):
        coords, indices = meshgen.box(0, 0, 0, 10, 10, 10)
        pieces = mesh_audit.classify(coords, indices)
        self.assertEqual(len(pieces), 1)
        self.assertAlmostEqual(pieces[0].volume, 1000.0)
        self.assertFalse(pieces[0].is_void)

    def test_an_inward_wound_piece_is_a_void(self):
        # A hollow solid: the inner surface faces into the material, so its
        # signed volume comes out negative. That is trapped space -- nothing
        # can reach it and nothing can get out of it.
        coords, indices = merge(
            meshgen.box(0, 0, 0, 20, 20, 20),
            inside_out(meshgen.box(0, 0, 0, 5, 5, 5)),
        )
        pieces = mesh_audit.classify(coords, indices)
        self.assertEqual(len(pieces), 2)
        voids = [p for p in pieces if p.is_void]
        self.assertEqual(len(voids), 1)
        self.assertAlmostEqual(voids[0].volume, -125.0)

    def test_pieces_come_back_biggest_first(self):
        coords, indices = merge(
            meshgen.box(50, 0, 0, 2, 2, 2),
            meshgen.box(0, 0, 0, 10, 10, 10),
            meshgen.box(-50, 0, 0, 5, 5, 5),
        )
        volumes = [p.volume for p in mesh_audit.classify(coords, indices)]
        self.assertEqual(volumes, sorted(volumes, reverse=True))

    def test_a_piece_records_where_it_is(self):
        coords, indices = merge(
            meshgen.box(0, 0, 0, 10, 10, 10),
            meshgen.box(50, 0, 0, 4, 4, 4),
        )
        small = mesh_audit.classify(coords, indices)[1]
        self.assertAlmostEqual(small.center[0], 50.0)
        self.assertAlmostEqual(small.center[1], 0.0)
        self.assertEqual(small.bounds[0], 48.0)
        self.assertEqual(small.bounds[3], 52.0)

    def test_a_piece_buried_inside_another_is_flagged(self):
        # Not inward-wound, just a separate blob sitting inside the body --
        # badly modelled eyes, an interior armature. It carves a pocket the
        # plastic can never reach.
        coords, indices = merge(
            meshgen.box(0, 0, 0, 40, 40, 40),
            meshgen.box(0, 0, 0, 4, 4, 4),
        )
        pieces = mesh_audit.classify(coords, indices)
        self.assertEqual(len(pieces), 2)
        self.assertIsNone(pieces[0].inside_of)
        self.assertEqual(pieces[1].inside_of, 0)

    def test_a_piece_inside_the_bounding_box_but_outside_the_shape(self):
        # A cone, so the corners of its bounding box are empty space. Testing
        # bounds alone would call this buried; only a ray cast knows better.
        # (Verified non-vacuous: forcing the cast to say "inside" fails this.)
        coords, indices = merge(
            meshgen.cone(0, 0, -10, 20.0, 10, 0.0),
            meshgen.box(14, 14, 6, 3, 3, 3),
        )
        pieces = mesh_audit.classify(coords, indices)
        self.assertEqual(len(pieces), 2)
        corner = [p for p in pieces if p.center[0] > 1][0]
        self.assertIsNone(corner.inside_of)

    def test_a_piece_genuinely_inside_a_cone_is_buried(self):
        # The other half of the pair above: same shape, and this time the blob
        # really is in the solid part.
        coords, indices = merge(
            meshgen.cone(0, 0, -10, 20.0, 10, 0.0),
            meshgen.box(0, 0, -6, 3, 3, 3),
        )
        pieces = mesh_audit.classify(coords, indices)
        self.assertEqual(len(pieces), 2)
        self.assertEqual(pieces[1].inside_of, 0)


class TestKeepingTheRealShape(unittest.TestCase):
    def big_with_speck_and_void(self):
        return merge(
            meshgen.box(0, 0, 0, 40, 40, 40),          # the lure
            meshgen.box(200, 0, 0, 1, 1, 1),           # a stray speck
            meshgen.box(0, 0, 0, 3, 3, 3),             # a blob buried inside
            meshgen.box(100, 0, 0, 20, 20, 20),        # a genuine second piece
        )

    def test_a_stray_speck_is_dropped(self):
        coords, indices = self.big_with_speck_and_void()
        kept_coords, kept, dropped = mesh_audit.keep_usable_shells(coords, indices)
        self.assertTrue(any(d.reason == "tiny" for d in dropped))
        remaining = mesh_audit.classify(kept_coords, kept)
        self.assertNotIn(
            1.0, [round(p.volume, 6) for p in remaining], "the speck survived"
        )

    def test_a_buried_blob_is_dropped(self):
        coords, indices = self.big_with_speck_and_void()
        _, _, dropped = mesh_audit.keep_usable_shells(coords, indices)
        self.assertTrue(any(d.reason == "buried" for d in dropped))

    def test_a_genuine_second_piece_is_kept(self):
        # A lure really can be two pieces -- a body and a separate tail. Only
        # specks and buried blobs go.
        coords, indices = self.big_with_speck_and_void()
        kept_coords, kept, _ = mesh_audit.keep_usable_shells(coords, indices)
        volumes = sorted(p.volume for p in mesh_audit.classify(kept_coords, kept))
        self.assertEqual([round(v) for v in volumes], [8000, 64000])

    def test_dropped_pieces_leave_no_coordinates_behind(self):
        """The bug this cost us: trimming triangles is not enough.

        A speck 200mm off the tail was dropped from the triangle list but its
        vertices stayed in the coordinate array, and principal_axes measures
        coordinates. A real 85mm lure came out 138mm long.
        """
        coords, indices = self.big_with_speck_and_void()
        kept_coords, kept, _ = mesh_audit.keep_usable_shells(coords, indices)
        self.assertLess(
            max(kept_coords[0::3]), 115.0,
            "a dropped speck's vertices are still in the coordinates",
        )
        self.assertEqual(len(kept_coords) // 3, len(set(kept)))

    def test_compacting_preserves_the_shape(self):
        coords, indices = merge(
            meshgen.box(0, 0, 0, 10, 10, 10),
            meshgen.box(50, 0, 0, 4, 4, 4),
        )
        keep = mesh_audit.classify(coords, indices)[0]
        only = [indices[3 * n + c] for n in keep.triangles for c in range(3)]
        tight_coords, tight = mesh_audit.compact(coords, only)
        self.assertEqual(len(tight_coords) // 3, 8)
        self.assertAlmostEqual(
            mesh_audit.classify(tight_coords, tight)[0].volume, 1000.0
        )

    def test_a_single_clean_shape_is_left_alone(self):
        coords, indices = meshgen.box(0, 0, 0, 10, 10, 10)
        kept_coords, kept, dropped = mesh_audit.keep_usable_shells(coords, indices)
        self.assertEqual(kept, indices)
        self.assertEqual(kept_coords, coords)
        self.assertEqual(dropped, [])

    def test_it_never_drops_everything(self):
        # Three specks and nothing else: dropping "everything too small" would
        # leave no lure at all, which is worse than a speck.
        coords, indices = merge(
            meshgen.box(0, 0, 0, 1, 1, 1),
            meshgen.box(9, 0, 0, 1, 1, 1),
            meshgen.box(18, 0, 0, 1, 1, 1),
        )
        kept_coords, kept, _ = mesh_audit.keep_usable_shells(coords, indices)
        self.assertTrue(mesh_audit.classify(kept_coords, kept))


class TestLooseIslands(unittest.TestCase):
    """A finished mold half should be one solid lump and nothing else."""

    def test_a_plain_block_has_no_islands(self):
        coords, indices = meshgen.box(0, 0, 0, 50, 50, 20)
        islands, voids = mesh_audit.loose_pieces(coords, indices)
        self.assertEqual(islands, [])
        self.assertEqual(voids, [])

    def test_a_detached_lump_is_an_island(self):
        coords, indices = merge(
            meshgen.box(0, 0, 0, 50, 50, 20),
            meshgen.box(10, 0, 0, 3, 3, 3),
        )
        islands, voids = mesh_audit.loose_pieces(coords, indices)
        self.assertEqual(len(islands), 1)
        self.assertAlmostEqual(islands[0].volume, 27.0)
        self.assertEqual(voids, [])

    def test_a_sealed_pocket_is_a_void_not_an_island(self):
        coords, indices = merge(
            meshgen.box(0, 0, 0, 50, 50, 20),
            inside_out(meshgen.box(0, 0, 0, 4, 4, 4)),
        )
        islands, voids = mesh_audit.loose_pieces(coords, indices)
        self.assertEqual(islands, [])
        self.assertEqual(len(voids), 1)

    def test_the_biggest_piece_is_never_called_an_island(self):
        coords, indices = merge(
            meshgen.box(10, 0, 0, 3, 3, 3),
            meshgen.box(0, 0, 0, 50, 50, 20),
        )
        islands, _ = mesh_audit.loose_pieces(coords, indices)
        self.assertEqual(len(islands), 1)
        self.assertAlmostEqual(islands[0].volume, 27.0)

    def test_an_island_can_be_handed_back_as_its_own_mesh(self):
        # So it can be cut away with an ordinary boolean, using its own
        # surface as the tool body.
        coords, indices = merge(
            meshgen.box(0, 0, 0, 50, 50, 20),
            meshgen.box(10, 0, 0, 3, 3, 3),
        )
        islands, _ = mesh_audit.loose_pieces(coords, indices)
        piece_coords, piece_indices = mesh_audit.extract(
            coords, indices, islands[0]
        )
        self.assertEqual(len(piece_indices) // 3, 12)
        self.assertAlmostEqual(
            mesh_audit.classify(piece_coords, piece_indices)[0].volume, 27.0
        )
        # ...and it must be watertight, or the boolean will corrupt the half.
        self.assertEqual(len(mesh_audit.shells(piece_coords, piece_indices)), 1)


if __name__ == "__main__":
    unittest.main()
