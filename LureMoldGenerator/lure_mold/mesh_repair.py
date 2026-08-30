"""Fixing inconsistently wound meshes.

Downloaded STLs are very often watertight but inconsistently wound: some
triangles face out, some face in. Fusion reports such a body as
``isOriented = False`` with ``volume = 0.0`` -- the inward and outward faces
cancel out.

This matters more than it sounds. A mesh boolean against a body like that does
not raise an error; it silently produces a corrupt result. Measured on a real
230k-triangle model: cutting it out of a block of known volume 22277.378 cm3
"succeeded" and left the block reporting a volume of 0.0.

So the winding is repaired here, in plain Python, before the triangles are ever
handed back to Fusion. The pipeline already rebuilds the cavity tool bodies
from these triangles, so fixing them here is enough -- the user's own body is
never modified.

Pure math: nothing here imports Fusion.
"""


def _directed_edges(indices, n):
    a, b, c = indices[3 * n], indices[3 * n + 1], indices[3 * n + 2]
    return ((a, b), (b, c), (c, a))


def _signed_volume(coords, indices, triangles):
    """Six times the signed volume of the given triangle indices."""
    total = 0.0
    for n in triangles:
        a, b, c = indices[3 * n], indices[3 * n + 1], indices[3 * n + 2]
        ax, ay, az = coords[3 * a], coords[3 * a + 1], coords[3 * a + 2]
        bx, by, bz = coords[3 * b], coords[3 * b + 1], coords[3 * b + 2]
        cx, cy, cz = coords[3 * c], coords[3 * c + 1], coords[3 * c + 2]
        total += (
            ax * (by * cz - bz * cy)
            - ay * (bx * cz - bz * cx)
            + az * (bx * cy - by * cx)
        )
    return total


def make_consistent(coords, indices):
    """Return (indices, flip_count) with every shell wound consistently outward.

    Walks the mesh one connected shell at a time. Two triangles sharing an edge
    agree only if they traverse that edge in opposite directions; where they do
    not, the neighbour is flipped. Once a shell is internally consistent its
    signed volume says whether the whole thing is inside out, and if so it is
    reversed.

    Each shell is handled separately, so a model made of several closed pieces
    comes out with all of them facing outward.
    """
    out = list(indices)
    triangle_count = len(out) // 3
    if triangle_count == 0:
        return out, 0

    # Undirected edge -> triangles touching it.
    edges = {}
    for n in range(triangle_count):
        for u, v in _directed_edges(out, n):
            edges.setdefault((u, v) if u < v else (v, u), []).append(n)

    def flip(n):
        out[3 * n + 1], out[3 * n + 2] = out[3 * n + 2], out[3 * n + 1]

    flips = 0
    visited = [False] * triangle_count

    for seed in range(triangle_count):
        if visited[seed]:
            continue

        visited[seed] = True
        shell = [seed]
        stack = [seed]
        while stack:
            current = stack.pop()
            for u, v in _directed_edges(out, current):
                key = (u, v) if u < v else (v, u)
                for other in edges.get(key, ()):
                    if other == current or visited[other]:
                        continue
                    # Consistent neighbours traverse the shared edge the other
                    # way round. Matching direction means it is inverted.
                    if (u, v) in _directed_edges(out, other):
                        flip(other)
                        flips += 1
                    visited[other] = True
                    shell.append(other)
                    stack.append(other)

        # The shell is coherent now; make sure it faces outward.
        if _signed_volume(coords, out, shell) < 0:
            for n in shell:
                flip(n)
                flips += 1

    return out, flips


def find_non_manifold_edges(indices):
    """Edges shared by more than two faces, as sorted (low, high) pairs.

    These cannot be fixed by re-winding. At an edge where four faces meet
    there is no consistent orientation to find, so Fusion keeps reporting
    isOriented=False however the triangles are flipped, and a boolean against
    the body yields a corrupt result. The only fix is topological surgery,
    which is what Fusion's own Mesh > Prepare > Repair does.

    Edges used only once are boundaries of an open mesh, not branching, and
    are deliberately not reported here -- isClosed already covers those.
    """
    counts = {}
    for t in range(0, len(indices), 3):
        a, b, c = indices[t], indices[t + 1], indices[t + 2]
        for u, v in ((a, b), (b, c), (c, a)):
            key = (u, v) if u < v else (v, u)
            counts[key] = counts.get(key, 0) + 1
    return sorted(key for key, count in counts.items() if count > 2)


def volume(coords, indices):
    """Absolute mesh volume, in whatever units the coordinates use."""
    return abs(_signed_volume(coords, indices, range(len(indices) // 3))) / 6.0
