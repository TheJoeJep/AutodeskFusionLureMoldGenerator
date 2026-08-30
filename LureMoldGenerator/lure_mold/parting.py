"""Choosing where to split the mold.

The parting plane was originally fixed at the middle of the lure's bounding
box. That is right for a roughly symmetric worm and wrong for anything with a
flat feature at one level: on a turtle it slices through the shell dome and
leaves the fins and head wholly below the plane, sealed into the bottom half
with material closed over them. They can never come out.

The plane that works is the one most of the model *straddles*. Fire a grid of
rays straight down through the lure and record where each one enters and leaves
solid material. For a candidate height h, a ray is happy when its solid is a
single span that contains h -- then the part above h lifts out upwards and the
part below drops out downwards. A span sitting entirely on one side of h is a
trapped feature, which is exactly what the fins were.

Scoring every candidate height against the same set of rays makes the search
cheap: the ray casting is done once.

Pure math: nothing here imports Fusion.
"""

# How finely the model is sampled, and how many heights are tried.
DEFAULT_RAYS_X = 60
DEFAULT_RAYS_Y = 60
DEFAULT_CANDIDATES = 80

# Triangles smaller than this in XY are edge-on to the rays and contribute
# nothing but numerical noise.
_AREA_EPSILON = 1e-12


def _bucket_triangles(coords, indices, x0, y0, cell_x, cell_y, nx, ny):
    """Index triangles by the XY grid cells their bounding box covers.

    Without this, every ray would be tested against every triangle -- on a
    100k-triangle model with a 60x60 ray grid that is 360 million tests.
    """
    buckets = {}
    for t in range(0, len(indices), 3):
        xs = []
        ys = []
        for k in range(3):
            v = indices[t + k] * 3
            xs.append(coords[v])
            ys.append(coords[v + 1])

        lo_x = int((min(xs) - x0) // cell_x)
        hi_x = int((max(xs) - x0) // cell_x)
        lo_y = int((min(ys) - y0) // cell_y)
        hi_y = int((max(ys) - y0) // cell_y)
        lo_x = max(lo_x, 0)
        lo_y = max(lo_y, 0)
        hi_x = min(hi_x, nx - 1)
        hi_y = min(hi_y, ny - 1)

        for i in range(lo_x, hi_x + 1):
            for j in range(lo_y, hi_y + 1):
                buckets.setdefault((i, j), []).append(t)
    return buckets


def _spans(hits, epsilon):
    """Turn crossing heights into (enter, leave) solid spans.

    Crossings landing within `epsilon` of each other are collapsed to one. A
    ray passing exactly along an edge shared by two triangles hits both at the
    same height, which would otherwise read as an extra span -- on a plain box
    that made every ray down a face diagonal look like two separate solids.
    """
    hits.sort()
    merged = []
    for z in hits:
        if not merged or z - merged[-1] > epsilon:
            merged.append(z)
    if len(merged) % 2:
        return []
    return [(merged[i], merged[i + 1]) for i in range(0, len(merged) - 1, 2)]


def ray_columns(coords, indices, rays_x=DEFAULT_RAYS_X, rays_y=DEFAULT_RAYS_Y):
    """Cast a grid of downward rays; return the solid spans found by each.

    Each entry is a list of (enter_z, leave_z) pairs, lowest first. Rays that
    miss the model come back empty.
    """
    xs = coords[0::3]
    ys = coords[1::3]
    if not xs:
        return []

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = max_x - min_x
    depth = max_y - min_y
    if width <= 0 or depth <= 0:
        return []

    zs = coords[2::3]
    epsilon = max(max(zs) - min(zs), 1.0) * 1e-7

    cell_x = width / rays_x
    cell_y = depth / rays_y
    buckets = _bucket_triangles(
        coords, indices, min_x, min_y, cell_x, cell_y, rays_x, rays_y
    )

    columns = []
    for i in range(rays_x):
        px = min_x + cell_x * (i + 0.5)
        for j in range(rays_y):
            py = min_y + cell_y * (j + 0.5)

            hits = []
            for t in buckets.get((i, j), ()):
                a = indices[t] * 3
                b = indices[t + 1] * 3
                c = indices[t + 2] * 3
                ax, ay, az = coords[a], coords[a + 1], coords[a + 2]
                bx, by, bz = coords[b], coords[b + 1], coords[b + 2]
                cx, cy, cz = coords[c], coords[c + 1], coords[c + 2]

                # Barycentric test in XY; the ray is vertical so Z drops out.
                area = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
                if abs(area) < _AREA_EPSILON:
                    continue
                u = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / area
                v = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / area
                w = 1.0 - u - v
                if u < 0.0 or v < 0.0 or w < 0.0:
                    continue
                hits.append(u * az + v * bz + w * cz)

            columns.append(_spans(hits, epsilon))
    return columns


def score_at(columns, height):
    """Fraction of rays whose solid is a single span containing `height`."""
    hit = 0
    good = 0
    for spans in columns:
        if not spans:
            continue
        hit += 1
        if len(spans) == 1 and spans[0][0] <= height <= spans[0][1]:
            good += 1
    if hit == 0:
        return 0.0
    return good / hit


def best_parting_z(
    coords,
    indices,
    rays_x=DEFAULT_RAYS_X,
    rays_y=DEFAULT_RAYS_Y,
    candidates=DEFAULT_CANDIDATES,
):
    """Return (height, score) for the plane most of the model straddles.

    Ties are broken toward the middle of the model, which keeps the two halves
    closer in thickness when the choice does not otherwise matter.
    """
    columns = ray_columns(coords, indices, rays_x, rays_y)
    zs = coords[2::3]
    if not zs or not columns:
        return 0.0, 0.0

    low, high = min(zs), max(zs)
    middle = (low + high) / 2.0
    if high - low <= 0:
        return middle, 0.0

    best_z = middle
    best_score = -1.0
    for n in range(candidates + 1):
        height = low + (high - low) * n / candidates
        score = score_at(columns, height)
        if score > best_score + 1e-9 or (
            abs(score - best_score) <= 1e-9
            and abs(height - middle) < abs(best_z - middle)
        ):
            best_z = height
            best_score = score

    return best_z, max(best_score, 0.0)
