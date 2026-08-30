"""Pure-Python triangle mesh primitives.

Like layout.py, this imports nothing from Fusion. It produces watertight,
outward-wound triangle soups in the flat form Fusion's
``MeshBodies.addByTriangleMeshData`` expects:

    coords  -- [x0, y0, z0, x1, y1, z1, ...]
    indices -- [a0, b0, c0, a1, b1, c1, ...]

Everything the mold is made of -- the block halves, the alignment pegs, the
sprue cones, the vent risers -- is built from these primitives and then
combined with Fusion's mesh booleans. Keeping the geometry generation here
means it can be tested without CAD in the loop.

All units are whatever the caller uses consistently; the builder works in
centimetres because that is what the Fusion API expects.
"""

import math


def box(cx, cy, cz, lx, ly, lz):
    """An axis-aligned box centred on (cx, cy, cz)."""
    x0, x1 = cx - lx / 2, cx + lx / 2
    y0, y1 = cy - ly / 2, cy + ly / 2
    z0, z1 = cz - lz / 2, cz + lz / 2

    verts = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    faces = [
        (0, 3, 2), (0, 2, 1),  # -Z
        (4, 5, 6), (4, 6, 7),  # +Z
        (0, 1, 5), (0, 5, 4),  # -Y
        (1, 2, 6), (1, 6, 5),  # +X
        (2, 3, 7), (2, 7, 6),  # +Y
        (3, 0, 4), (3, 4, 7),  # -X
    ]
    coords = [c for v in verts for c in v]
    indices = [i for f in faces for i in f]
    return coords, indices


def box_frustum(cx, cy, z0, lx0, ly0, z1, lx1, ly1):
    """A box whose XY size differs at each end, centred on (cx, cy).

    Used for the relief ramp: a flat sealing land around a feature that widens
    as it drops away from the parting plane, so the wall between land and
    recess sits at the chosen angle.
    """
    if z1 < z0:
        z0, z1 = z1, z0
        lx0, lx1 = lx1, lx0
        ly0, ly1 = ly1, ly0

    def corners(z, lx, ly):
        hx, hy = lx / 2, ly / 2
        return [
            (cx - hx, cy - hy, z),
            (cx + hx, cy - hy, z),
            (cx + hx, cy + hy, z),
            (cx - hx, cy + hy, z),
        ]

    verts = corners(z0, lx0, ly0) + corners(z1, lx1, ly1)
    faces = [
        (0, 3, 2), (0, 2, 1),  # -Z
        (4, 5, 6), (4, 6, 7),  # +Z
        (0, 1, 5), (0, 5, 4),  # -Y
        (1, 2, 6), (1, 6, 5),  # +X
        (2, 3, 7), (2, 7, 6),  # +Y
        (3, 0, 4), (3, 4, 7),  # -X
    ]
    coords = [c for v in verts for c in v]
    indices = [i for f in faces for i in f]
    return coords, indices


def cone(cx, cy, z0, r0, z1, r1, segments=48):
    """A cone or frustum on the Z axis. Either radius may be zero (an apex)."""
    if r0 <= 0 and r1 <= 0:
        raise ValueError("a cone needs at least one non-zero radius")
    if segments < 3:
        raise ValueError("a cone needs at least 3 segments")

    # Channels are specified from wherever they start to wherever they end, so
    # z1 < z0 arrives routinely -- a cavity gating into a central runner from
    # the right, for one. Built as given, the faces would wind inward and the
    # body would be inside-out; an inverted tool body does not cut, it just
    # leaves the channel filled. Normalising here fixes it for every caller.
    if z1 < z0:
        z0, z1 = z1, z0
        r0, r1 = r1, r0

    verts = []

    def add(x, y, z):
        verts.append((x, y, z))
        return len(verts) - 1

    def make_ring(z, r):
        return [
            add(
                cx + r * math.cos(2 * math.pi * j / segments),
                cy + r * math.sin(2 * math.pi * j / segments),
                z,
            )
            for j in range(segments)
        ]

    ring0 = make_ring(z0, r0) if r0 > 0 else None
    cap0 = add(cx, cy, z0) if r0 > 0 else None
    apex0 = None if r0 > 0 else add(cx, cy, z0)

    ring1 = make_ring(z1, r1) if r1 > 0 else None
    cap1 = add(cx, cy, z1) if r1 > 0 else None
    apex1 = None if r1 > 0 else add(cx, cy, z1)

    faces = []
    for j in range(segments):
        k = (j + 1) % segments

        if ring0 is not None:
            faces.append((cap0, ring0[k], ring0[j]))  # -Z cap
        if ring1 is not None:
            faces.append((cap1, ring1[j], ring1[k]))  # +Z cap

        if ring0 is not None and ring1 is not None:
            faces.append((ring0[j], ring0[k], ring1[j]))
            faces.append((ring0[k], ring1[k], ring1[j]))
        elif ring1 is not None:  # apex at the bottom
            faces.append((apex0, ring1[k], ring1[j]))
        else:  # apex at the top
            faces.append((ring0[j], ring0[k], apex1))

    coords = [c for v in verts for c in v]
    indices = [i for f in faces for i in f]
    return coords, indices


def cylinder(cx, cy, z0, z1, radius, segments=48):
    """A cylinder on the Z axis, spanning z0 to z1."""
    return cone(cx, cy, z0, radius, z1, radius, segments=segments)


def axis_z_to_x(coords):
    """Turn a Z-axis primitive into an X-axis one.

    Sprue and runner channels run along the mold's length, but cone() and
    cylinder() build along Z. This is a 90 degree rotation about Y --
    (x, y, z) -> (z, y, -x) -- which has determinant +1, so the winding and
    therefore the outward-facing normals are preserved.
    """
    out = []
    for i in range(0, len(coords), 3):
        x, y, z = coords[i], coords[i + 1], coords[i + 2]
        out += [z, y, -x]
    return out


def axis_z_to_y(coords):
    """Turn a Z-axis primitive into a Y-axis one.

    A central runner runs across the mold rather than along it. This is a 90
    degree rotation about X -- (x, y, z) -> (x, z, -y) -- determinant +1, so
    the winding survives.
    """
    out = []
    for i in range(0, len(coords), 3):
        x, y, z = coords[i], coords[i + 1], coords[i + 2]
        out += [x, z, -y]
    return out


def rotate_z_180(coords):
    """Turn a shape end-for-end about the Z axis.

    Used to face one column of cavities back toward a central runner. This is
    a rotation, (x, y, z) -> (-x, -y, z), NOT a mirror -- a mirrored lure would
    come out as the wrong hand.
    """
    out = []
    for i in range(0, len(coords), 3):
        out += [-coords[i], -coords[i + 1], coords[i + 2]]
    return out


def translate(coords, dx, dy, dz):
    """Return a translated copy of a flat coordinate list."""
    out = list(coords)
    for i in range(0, len(out), 3):
        out[i] += dx
        out[i + 1] += dy
        out[i + 2] += dz
    return out


def scale(coords, factor):
    """Return a uniformly scaled copy of a flat coordinate list."""
    return [c * factor for c in coords]
