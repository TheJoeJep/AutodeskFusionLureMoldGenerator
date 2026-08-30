"""Finding a lure's natural axes.

Fusion's ``orientedMinimumBoundingBox`` is only approximate. On a rotated
ellipsoid it returns axes that are noticeably off -- it reported a true
100 x 30 x 12 lure as 100 x 28.8 x 14.6, a box 17% larger than the real one.
Used as-is that would seat the lure at an angle inside the mold and cut the
cavity crooked.

This module computes the axes directly instead, from an area-weighted
covariance of the mesh surface. For a shape with any symmetry -- which lures
broadly have -- this recovers the true axes exactly.

Sorting is by measured extent, not by eigenvalue, so the longest direction is
always X and the thinnest is always Z. The thinnest direction becomes the
parting-plane normal, which is how real lure molds split.

Pure math: nothing here imports Fusion.
"""

import math

JACOBI_SWEEPS = 24
JACOBI_TOLERANCE = 1e-14


def _area_weighted_covariance(coords, indices):
    """Exact covariance of the mesh surface.

    Each triangle contributes its true second-moment integral rather than an
    approximation based on its centroid. That distinction matters: a centroid
    approximation is only accurate for small triangles, and gets a coarsely
    tessellated shape badly wrong -- on a 12-triangle box it reported the long
    axis as 6.92 instead of 6.0, picking a diagonal instead of an edge.

    For a triangle with vertices v0, v1, v2, area A and s = v0 + v1 + v2:

        integral of p p^T dA = (A/12) * (v0 v0^T + v1 v1^T + v2 v2^T + s s^T)

    Weighting by area also keeps the result independent of tessellation
    density, so the crowded poles of a sphere do not skew the axes.
    """
    total_area = 0.0
    first = [0.0, 0.0, 0.0]
    second = [[0.0] * 3 for _ in range(3)]

    for t in range(0, len(indices), 3):
        ia, ib, ic = indices[t] * 3, indices[t + 1] * 3, indices[t + 2] * 3
        v0 = (coords[ia], coords[ia + 1], coords[ia + 2])
        v1 = (coords[ib], coords[ib + 1], coords[ib + 2])
        v2 = (coords[ic], coords[ic + 1], coords[ic + 2])

        ux, uy, uz = v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]
        wx, wy, wz = v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]
        nx = uy * wz - uz * wy
        ny = uz * wx - ux * wz
        nz = ux * wy - uy * wx
        area = 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)
        if area <= 0.0:
            continue

        s = (v0[0] + v1[0] + v2[0], v0[1] + v1[1] + v2[1], v0[2] + v1[2] + v2[2])
        total_area += area
        for i in range(3):
            first[i] += area * s[i] / 3.0
            for j in range(3):
                second[i][j] += (area / 12.0) * (
                    v0[i] * v0[j] + v1[i] * v1[j] + v2[i] * v2[j] + s[i] * s[j]
                )

    if total_area <= 0.0:
        raise ValueError("mesh has no surface area")

    mean = [f / total_area for f in first]
    return [
        [second[i][j] / total_area - mean[i] * mean[j] for j in range(3)]
        for i in range(3)
    ]


def _jacobi_eigenvectors(matrix):
    """Eigenvectors of a symmetric 3x3 matrix by cyclic Jacobi rotation.

    Returns the three eigenvectors as rows. Small and dependency-free, which
    matters because this has to run inside Fusion's embedded Python.
    """
    a = [row[:] for row in matrix]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]

    for _ in range(JACOBI_SWEEPS):
        off = sum(a[i][j] ** 2 for i in range(3) for j in range(3) if i != j)
        if off < JACOBI_TOLERANCE:
            break
        for p in range(2):
            for q in range(p + 1, 3):
                if abs(a[p][q]) < 1e-300:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                sign = 1.0 if theta >= 0 else -1.0
                t = sign / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c

                for k in range(3):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(3):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
                for k in range(3):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p] = c * vkp - s * vkq
                    v[k][q] = s * vkp + c * vkq

    return [[v[0][i], v[1][i], v[2][i]] for i in range(3)]


def _normalise(vec):
    length = math.sqrt(sum(c * c for c in vec))
    if length == 0.0:
        raise ValueError("zero-length axis")
    return [c / length for c in vec]


def _extent(coords, axis):
    values = [
        coords[i] * axis[0] + coords[i + 1] * axis[1] + coords[i + 2] * axis[2]
        for i in range(0, len(coords), 3)
    ]
    return max(values) - min(values)


def principal_axes(coords, indices):
    """Return ((x_axis, y_axis, z_axis), (x_extent, y_extent, z_extent)).

    Axes are orthonormal and right-handed, ordered longest extent first.
    """
    cov = _area_weighted_covariance(coords, indices)
    vectors = [_normalise(vec) for vec in _jacobi_eigenvectors(cov)]

    ranked = sorted(
        (( _extent(coords, vec), vec) for vec in vectors),
        key=lambda pair: pair[0],
        reverse=True,
    )
    extents = [item[0] for item in ranked]
    axes = [item[1] for item in ranked]

    # Force a right-handed frame so nothing comes out mirrored.
    ax, ay, az = axes
    cross = (
        ax[1] * ay[2] - ax[2] * ay[1],
        ax[2] * ay[0] - ax[0] * ay[2],
        ax[0] * ay[1] - ax[1] * ay[0],
    )
    if sum(cross[k] * az[k] for k in range(3)) < 0:
        az = [-c for c in az]

    return (ax, ay, az), tuple(extents)


def project(coords, axes):
    """Re-express coordinates in the given frame, centred on the origin."""
    ax, ay, az = axes
    local = []
    for i in range(0, len(coords), 3):
        x, y, z = coords[i], coords[i + 1], coords[i + 2]
        local.append(x * ax[0] + y * ax[1] + z * ax[2])
        local.append(x * ay[0] + y * ay[1] + z * ay[2])
        local.append(x * az[0] + y * az[1] + z * az[2])

    for k in range(3):
        values = local[k::3]
        middle = (min(values) + max(values)) / 2.0
        for i in range(k, len(local), 3):
            local[i] -= middle
    return local
