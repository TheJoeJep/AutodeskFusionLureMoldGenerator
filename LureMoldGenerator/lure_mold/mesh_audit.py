"""Finding the ways a mesh will not make a usable mold.

Two related problems, one piece of machinery.

**Stray shells in the lure.** Downloaded models routinely carry more than the
shape you can see: a speck of debris left by a boolean, an interior armature,
a pair of eyeballs modelled as separate solids sitting inside the head. Every
one of those gets subtracted from the block along with the lure, so a speck
carves a phantom pocket somewhere in the wall and a buried blob carves a void
the plastic can never reach and the part can never come out of.

**Loose islands in the finished mold.** A mold half is a block with things cut
out of it. Cut enough away and a chunk of the block can end up joined to
nothing -- it prints as a lump rattling around in the cavity. It is also the
symptom of a boolean that went wrong, so it is worth catching either way.

Both come down to the same question: which parts of this triangle soup are
actually attached to which? Once the pieces are separated, a signed volume says
whether each is solid or a sealed pocket, and a ray cast says whether it is
buried inside another.

Pure math: nothing here imports Fusion.
"""

import math
from dataclasses import dataclass

from . import mesh_repair

# Vertices closer than this are the same vertex. A mesh boolean returns
# coincident vertices as identical floats, so this only has to absorb the
# rounding in a file that has been through a text format.
WELD_TOLERANCE = 1e-4

# A shell smaller than this share of the biggest one is debris, not a shape.
TINY_SHELL_FRACTION = 0.01

# Working out what is buried inside what costs a ray cast against every
# triangle of each candidate container. That is nothing for a handful of
# shells and quadratic misery for a mesh made of debris, so past this many
# pieces the question is not asked and the size rule carries the load.
MAX_NESTING_SHELLS = 40

# A ray direction with no round number in it, so a cast is very unlikely to
# graze an edge or a vertex, where a crossing could be counted twice or not at
# all.
_PROBE = (0.9601, 0.1307, 0.2459)


@dataclass(frozen=True)
class Shell:
    """One connected piece of a mesh."""

    triangles: tuple
    volume: float  # signed: negative means the surface faces inwards
    bounds: tuple  # (min_x, min_y, min_z, max_x, max_y, max_z)
    center: tuple
    inside_of: object = None  # index of the shell enclosing this one

    @property
    def is_void(self):
        """A sealed pocket rather than a solid.

        The inner surface of a hollow solid faces into the material, so it
        encloses a negative volume. Nothing can reach that space and nothing
        can get out of it.
        """
        return self.volume < 0.0


@dataclass(frozen=True)
class Dropped:
    """A shell that was thrown away, and why."""

    reason: str  # "tiny" | "buried" | "void"
    volume: float
    center: tuple


def weld_map(coords, tolerance=WELD_TOLERANCE):
    """Index remap giving vertices at the same position a single index.

    Without this, two pieces that share a face but not their vertex numbering
    look separate, and everything downstream is wrong. Points are snapped to a
    grid, so two vertices either side of a grid line stay distinct -- which
    only over-reports, never under-reports, and does not happen with the
    identical floats a boolean produces.
    """
    if tolerance <= 0:
        return None
    scale = 1.0 / tolerance
    first_at = {}
    remap = [0] * (len(coords) // 3)
    for vertex in range(len(remap)):
        key = (
            int(round(coords[3 * vertex] * scale)),
            int(round(coords[3 * vertex + 1] * scale)),
            int(round(coords[3 * vertex + 2] * scale)),
        )
        found = first_at.get(key)
        if found is None:
            first_at[key] = vertex
            remap[vertex] = vertex
        else:
            remap[vertex] = found
    return remap


def shells(coords, indices, weld_tolerance=WELD_TOLERANCE):
    """The connected pieces of a mesh, as tuples of triangle numbers.

    Ordered by where each piece first appears, so the result is stable.
    """
    count = len(indices) // 3
    if count == 0:
        return []

    remap = weld_map(coords, weld_tolerance)
    parent = list(range(len(coords) // 3))

    def find(a):
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:  # path compression
            parent[a], a = root, parent[a]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for n in range(count):
        a, b, c = indices[3 * n], indices[3 * n + 1], indices[3 * n + 2]
        if remap is not None:
            a, b, c = remap[a], remap[b], remap[c]
        union(a, b)
        union(a, c)

    groups = {}
    for n in range(count):
        a = indices[3 * n]
        if remap is not None:
            a = remap[a]
        groups.setdefault(find(a), []).append(n)
    return [tuple(members) for members in groups.values()]


def _bounds_and_center(coords, indices, triangles):
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for n in triangles:
        for corner in range(3):
            vertex = indices[3 * n + corner] * 3
            for axis in range(3):
                value = coords[vertex + axis]
                if value < lo[axis]:
                    lo[axis] = value
                if value > hi[axis]:
                    hi[axis] = value
    bounds = (lo[0], lo[1], lo[2], hi[0], hi[1], hi[2])
    center = tuple((lo[axis] + hi[axis]) / 2.0 for axis in range(3))
    return bounds, center


def _encloses(outer, inner):
    """Bounding-box containment -- cheap, and only a necessary condition."""
    return all(
        outer[axis] <= inner[axis] and inner[axis + 3] <= outer[axis + 3]
        for axis in range(3)
    )


def contains_point(coords, indices, triangles, point):
    """Is a point inside this closed shell?

    Cast a ray and count crossings: an odd number means inside. Moller and
    Trumbore's ray/triangle test, with a deliberately awkward direction so the
    ray does not run along an edge.
    """
    ox, oy, oz = point
    dx, dy, dz = _PROBE
    crossings = 0

    for n in triangles:
        a, b, c = indices[3 * n], indices[3 * n + 1], indices[3 * n + 2]
        ax, ay, az = coords[3 * a], coords[3 * a + 1], coords[3 * a + 2]
        e1x = coords[3 * b] - ax
        e1y = coords[3 * b + 1] - ay
        e1z = coords[3 * b + 2] - az
        e2x = coords[3 * c] - ax
        e2y = coords[3 * c + 1] - ay
        e2z = coords[3 * c + 2] - az

        px = dy * e2z - dz * e2y
        py = dz * e2x - dx * e2z
        pz = dx * e2y - dy * e2x
        det = e1x * px + e1y * py + e1z * pz
        if -1e-12 < det < 1e-12:
            continue
        inv = 1.0 / det

        tx, ty, tz = ox - ax, oy - ay, oz - az
        u = (tx * px + ty * py + tz * pz) * inv
        if u < 0.0 or u > 1.0:
            continue

        qx = ty * e1z - tz * e1y
        qy = tz * e1x - tx * e1z
        qz = tx * e1y - ty * e1x
        v = (dx * qx + dy * qy + dz * qz) * inv
        if v < 0.0 or u + v > 1.0:
            continue

        if (e2x * qx + e2y * qy + e2z * qz) * inv > 1e-9:
            crossings += 1

    return crossings % 2 == 1


def classify(coords, indices, weld_tolerance=WELD_TOLERANCE, nesting=True):
    """Every piece of a mesh, biggest first, with what it is.

    `nesting` works out which pieces are buried inside which, at the cost of a
    ray cast against every triangle of each candidate container. Skip it when
    the answer is not needed -- a mold half is 75,000 triangles.
    """
    pieces = []
    for triangles in shells(coords, indices, weld_tolerance):
        bounds, center = _bounds_and_center(coords, indices, triangles)
        pieces.append(
            Shell(
                triangles=triangles,
                volume=mesh_repair._signed_volume(coords, indices, triangles) / 6.0,
                bounds=bounds,
                center=center,
            )
        )

    pieces.sort(key=lambda shell: shell.volume, reverse=True)
    if not nesting or len(pieces) < 2 or len(pieces) > MAX_NESTING_SHELLS:
        return pieces

    # Smallest enclosing shell wins, so a blob inside a hollow head is reported
    # against the head rather than the whole fish.
    resolved = []
    for index, shell in enumerate(pieces):
        best = None
        for other, candidate in enumerate(pieces):
            if other == index or candidate.volume <= 0.0:
                continue
            if not _encloses(candidate.bounds, shell.bounds):
                continue
            if best is not None and pieces[best].volume <= candidate.volume:
                continue
            if contains_point(coords, indices, candidate.triangles, shell.center):
                best = other
        resolved.append(
            Shell(
                triangles=shell.triangles,
                volume=shell.volume,
                bounds=shell.bounds,
                center=shell.center,
                inside_of=best,
            )
        )
    return resolved


def compact(coords, indices):
    """Drop vertices that no triangle uses.

    Trimming triangles is not enough on its own. Anything that measures a mesh
    by walking its coordinates -- extents, principal axes, where the centre of
    the frame sits -- still sees the vertices of the pieces just thrown away.
    That is not hypothetical: dropping a speck sitting 120mm off the tail left
    a real 85mm lure measuring 138mm long, and every downstream number with it.
    """
    remap = {}
    out_coords, out_indices = [], []
    for old in indices:
        fresh = remap.get(old)
        if fresh is None:
            fresh = remap[old] = len(out_coords) // 3
            out_coords += [coords[3 * old], coords[3 * old + 1], coords[3 * old + 2]]
        out_indices.append(fresh)
    return out_coords, out_indices


def keep_usable_shells(coords, indices, tiny_fraction=TINY_SHELL_FRACTION):
    """Throw away the parts of a lure mesh that would ruin the mold.

    Returns (coords, indices, dropped) -- coordinates included, and compacted,
    because handing back trimmed indices alongside the original coordinates is
    a trap that has already been fallen into once.

    A lure genuinely can be more than one piece -- a body and a separate tail
    -- so only three things go: sealed pockets, pieces buried inside another,
    and specks far too small to be a shape.

    Never returns nothing. A mesh made entirely of specks is somebody's lure at
    a scale we did not expect, and cutting no cavity at all is worse than
    cutting a small one.
    """
    pieces = classify(coords, indices)
    if len(pieces) < 2:
        return list(coords), list(indices), []

    biggest = max(shell.volume for shell in pieces)
    floor = tiny_fraction * biggest

    keep, dropped = [], []
    for index, shell in enumerate(pieces):
        if shell.is_void:
            reason = "void"
        elif shell.inside_of is not None:
            reason = "buried"
        elif index > 0 and shell.volume < floor:
            reason = "tiny"
        else:
            keep.append(shell)
            continue
        dropped.append(
            Dropped(reason=reason, volume=shell.volume, center=shell.center)
        )

    if not keep:
        keep = [pieces[0]]
        dropped = [d for d in dropped if d.center != pieces[0].center]

    wanted = set()
    for shell in keep:
        wanted.update(shell.triangles)
    trimmed = []
    for n in sorted(wanted):
        trimmed += [indices[3 * n], indices[3 * n + 1], indices[3 * n + 2]]
    kept_coords, kept_indices = compact(coords, trimmed)
    return kept_coords, kept_indices, dropped


def loose_pieces(coords, indices, weld_tolerance=WELD_TOLERANCE):
    """(islands, voids) in a finished mold half.

    An island is a solid lump joined to nothing -- it falls out of the print.
    A void is a sealed pocket with no way in or out. The largest piece is the
    mold itself and is never either.

    Nesting is not worked out: a lump sitting in the middle of the block is
    inside its bounding box and is an island all the same.
    """
    pieces = classify(coords, indices, weld_tolerance, nesting=False)
    islands = [s for s in pieces[1:] if not s.is_void]
    voids = [s for s in pieces if s.is_void]
    return islands, voids


def extract(coords, indices, shell):
    """One shell as a mesh of its own, with the unused vertices dropped.

    A connected piece of a closed mesh is itself closed, so this comes out
    watertight and can be handed straight to a boolean as a tool body -- which
    is how an island is cut away rather than merely reported.
    """
    remap = {}
    out_coords, out_indices = [], []
    for n in shell.triangles:
        for corner in range(3):
            vertex = indices[3 * n + corner]
            fresh = remap.get(vertex)
            if fresh is None:
                fresh = remap[vertex] = len(out_coords) // 3
                out_coords += [
                    coords[3 * vertex], coords[3 * vertex + 1], coords[3 * vertex + 2]
                ]
            out_indices.append(fresh)
    return out_coords, out_indices
