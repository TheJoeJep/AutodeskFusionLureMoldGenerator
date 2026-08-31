"""Shape-following relief for the parting face.

The face only needs to be flat near the cavity and the ports. Recessing the
rest means far less area has to print dead flat.

The first attempt kept a *bounding box* around each feature. On anything with
spread limbs that is far too generous -- a real figure filled only 52% of its
own bounding box, so half the flat land was wasted on the gaps between its
arms and legs, and the slope started nowhere near the shape.

This works from distance instead. Features are rasterised onto a grid, a
distance field is swept out from them, and the face height at each grid node
follows from that distance: flat within the land, then ramping down to the
recess depth. The result hugs whatever shape it is given.

The cutter is built as everything *above* that surface (below, for the top
half), so it can simply be subtracted from a plain block half. Nothing has
zero thickness anywhere, which keeps the mesh booleans happy.

Pure math: nothing here imports Fusion.
"""

import math

# Ceiling on grid nodes, so a big mold with a fine cell cannot explode into
# millions of triangles. The cell is coarsened to fit.
MAX_NODES = 200000
# Anything this far from a feature is "far away" as far as the field cares.
FAR = 1.0e6


class Grid:
    """A regular sampling grid over the parting face, sampled at its nodes."""

    def __init__(self, min_x, min_y, cell, nx, ny):
        self.min_x = min_x
        self.min_y = min_y
        self.cell = cell
        self.nx = nx
        self.ny = ny

    @property
    def node_count(self):
        return (self.nx + 1) * (self.ny + 1)

    def index(self, i, j):
        return j * (self.nx + 1) + i

    def x(self, i):
        return self.min_x + i * self.cell

    def y(self, j):
        return self.min_y + j * self.cell

    def nearest(self, x, y):
        i = int(round((x - self.min_x) / self.cell))
        j = int(round((y - self.min_y) / self.cell))
        i = min(max(i, 0), self.nx)
        j = min(max(j, 0), self.ny)
        return i, j


def make_grid(min_x, min_y, max_x, max_y, cell):
    """A grid covering the given area, coarsened if it would be too fine."""
    width = max(max_x - min_x, cell)
    depth = max(max_y - min_y, cell)

    for _ in range(64):
        nx = max(int(math.ceil(width / cell)), 1)
        ny = max(int(math.ceil(depth / cell)), 1)
        if (nx + 1) * (ny + 1) <= MAX_NODES:
            return Grid(min_x, min_y, cell, nx, ny)
        cell *= 1.3

    return Grid(min_x, min_y, cell, 1, 1)


def new_mask(grid):
    return bytearray(grid.node_count)


def marked_at(grid, mask, x, y):
    i, j = grid.nearest(x, y)
    return bool(mask[grid.index(i, j)])


def mark_disc(grid, mask, cx, cy, radius):
    """Mark every node inside a circle -- pegs, and top-entry ports."""
    if radius <= 0:
        return
    lo_i = max(int(math.floor((cx - radius - grid.min_x) / grid.cell)), 0)
    hi_i = min(int(math.ceil((cx + radius - grid.min_x) / grid.cell)), grid.nx)
    lo_j = max(int(math.floor((cy - radius - grid.min_y) / grid.cell)), 0)
    hi_j = min(int(math.ceil((cy + radius - grid.min_y) / grid.cell)), grid.ny)

    for j in range(lo_j, hi_j + 1):
        dy = grid.y(j) - cy
        for i in range(lo_i, hi_i + 1):
            dx = grid.x(i) - cx
            if dx * dx + dy * dy <= radius * radius:
                mask[grid.index(i, j)] = 1


def mark_rect(grid, mask, cx, cy, lx, ly):
    """Mark every node inside an axis-aligned rectangle -- channels."""
    half_x, half_y = abs(lx) / 2, abs(ly) / 2
    lo_i = max(int(math.floor((cx - half_x - grid.min_x) / grid.cell)), 0)
    hi_i = min(int(math.ceil((cx + half_x - grid.min_x) / grid.cell)), grid.nx)
    lo_j = max(int(math.floor((cy - half_y - grid.min_y) / grid.cell)), 0)
    hi_j = min(int(math.ceil((cy + half_y - grid.min_y) / grid.cell)), grid.ny)

    for j in range(lo_j, hi_j + 1):
        if abs(grid.y(j) - cy) > half_y:
            continue
        for i in range(lo_i, hi_i + 1):
            if abs(grid.x(i) - cx) <= half_x:
                mask[grid.index(i, j)] = 1


def mark_triangles(grid, mask, coords, indices, dx=0.0, dy=0.0):
    """Mark the silhouette of a mesh, projected straight down onto the grid.

    This is the whole point of the module: the land follows the real outline
    rather than the box around it.
    """
    for t in range(0, len(indices), 3):
        a, b, c = indices[t] * 3, indices[t + 1] * 3, indices[t + 2] * 3
        ax, ay = coords[a] + dx, coords[a + 1] + dy
        bx, by = coords[b] + dx, coords[b + 1] + dy
        cx, cy = coords[c] + dx, coords[c + 1] + dy

        area = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(area) < 1e-12:
            continue

        lo_i = max(int(math.floor((min(ax, bx, cx) - grid.min_x) / grid.cell)), 0)
        hi_i = min(int(math.ceil((max(ax, bx, cx) - grid.min_x) / grid.cell)), grid.nx)
        lo_j = max(int(math.floor((min(ay, by, cy) - grid.min_y) / grid.cell)), 0)
        hi_j = min(int(math.ceil((max(ay, by, cy) - grid.min_y) / grid.cell)), grid.ny)

        for j in range(lo_j, hi_j + 1):
            py = grid.y(j)
            for i in range(lo_i, hi_i + 1):
                index = grid.index(i, j)
                if mask[index]:
                    continue
                px = grid.x(i)
                u = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / area
                v = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / area
                if u >= 0.0 and v >= 0.0 and u + v <= 1.0:
                    mask[index] = 1


_BIG = 1.0e12


def _squared_1d(values):
    """Felzenszwalb and Huttenlocher's 1D squared-distance transform.

    Lower envelope of the parabolas rooted at each sample. Linear time, and
    exact -- which is the whole point of using it.
    """
    n = len(values)
    if n == 0:
        return []

    out = [0.0] * n
    hull = [0] * n            # which parabola is lowest in each stretch
    boundary = [0.0] * (n + 1)  # where the lowest parabola changes
    k = 0
    hull[0] = 0
    boundary[0] = -_BIG
    boundary[1] = _BIG

    for q in range(1, n):
        while True:
            p = hull[k]
            crossing = ((values[q] + q * q) - (values[p] + p * p)) / (2.0 * q - 2.0 * p)
            if crossing <= boundary[k]:
                k -= 1
                if k < 0:
                    k = 0
                    hull[0] = q
                    boundary[0] = -_BIG
                    boundary[1] = _BIG
                    break
                continue
            k += 1
            hull[k] = q
            boundary[k] = crossing
            boundary[k + 1] = _BIG
            break

    k = 0
    for q in range(n):
        while boundary[k + 1] < q:
            k += 1
        offset = q - hull[k]
        out[q] = offset * offset + values[hull[k]]
    return out


def distance_field(grid, mask):
    """Exact Euclidean distance from every node to the nearest marked one.

    This started as a two-pass chamfer sweep, which is cheaper but measures
    diagonals badly: distance varied by over a millimetre around a circle,
    giving the contours an octagonal bias that showed up as visible ridges
    down the relief slope. An exact transform is barely slower and the ramp
    comes out smooth.
    """
    width = grid.nx + 1
    height = grid.ny + 1

    squared = [0.0 if value else _BIG for value in mask]

    for j in range(height):
        row = j * width
        squared[row:row + width] = _squared_1d(squared[row:row + width])

    for i in range(width):
        column = _squared_1d([squared[j * width + i] for j in range(height)])
        for j in range(height):
            squared[j * width + i] = column[j]

    cell = grid.cell
    return [
        FAR if value >= _BIG else math.sqrt(value) * cell for value in squared
    ]


def geodesic_field(grid, mask, sources):
    """Distance from the gate to every part of the cavity, measured through it.

    Not straight-line distance: the melt has to travel along the shape, so a
    limb doubling back is further away than it looks. Dijkstra over the marked
    nodes with eight-way steps.

    Unreachable and unmarked nodes come back as FAR.
    """
    import heapq

    width = grid.nx + 1
    height = grid.ny + 1
    field = [FAR] * (width * height)

    straight = grid.cell
    diagonal = grid.cell * math.sqrt(2.0)
    steps = (
        (1, 0, straight), (-1, 0, straight),
        (0, 1, straight), (0, -1, straight),
        (1, 1, diagonal), (1, -1, diagonal),
        (-1, 1, diagonal), (-1, -1, diagonal),
    )

    queue = []
    for x, y in sources:
        i, j = grid.nearest(x, y)
        # Snap onto the shape if the gate sits just outside it.
        if not mask[grid.index(i, j)]:
            found = None
            for radius in range(1, 12):
                for dj in range(-radius, radius + 1):
                    for di in range(-radius, radius + 1):
                        ni, nj = i + di, j + dj
                        if 0 <= ni <= grid.nx and 0 <= nj <= grid.ny:
                            if mask[grid.index(ni, nj)]:
                                found = (ni, nj)
                                break
                    if found:
                        break
                if found:
                    break
            if not found:
                continue
            i, j = found
        index = grid.index(i, j)
        if field[index] > 0.0:
            field[index] = 0.0
            heapq.heappush(queue, (0.0, index))

    while queue:
        distance, index = heapq.heappop(queue)
        if distance > field[index]:
            continue
        i = index % width
        j = index // width
        for di, dj, cost in steps:
            ni, nj = i + di, j + dj
            if ni < 0 or nj < 0 or ni > grid.nx or nj > grid.ny:
                continue
            neighbour = nj * width + ni
            if not mask[neighbour]:
                continue
            candidate = distance + cost
            if candidate < field[neighbour]:
                field[neighbour] = candidate
                heapq.heappush(queue, (candidate, neighbour))

    return field


def find_pockets(grid, mask, field, min_separation, floor_fraction=0.25):
    """Where air ends up trapped: the last places in the cavity to fill.

    Local maxima of the fill distance, taken deepest first and thinned so no
    two land within `min_separation` of each other. Shallow ones are dropped
    -- a bump a short way from the gate is not a pocket.
    """
    width = grid.nx + 1
    reachable = [v for v in field if v < FAR]
    if not reachable:
        return []

    deepest = max(reachable)
    if deepest <= 0.0:
        return []
    floor = deepest * floor_fraction

    candidates = []
    for j in range(grid.ny + 1):
        for i in range(grid.nx + 1):
            index = j * width + i
            value = field[index]
            if value >= FAR or value < floor:
                continue
            peak = True
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    if di == 0 and dj == 0:
                        continue
                    ni, nj = i + di, j + dj
                    if ni < 0 or nj < 0 or ni > grid.nx or nj > grid.ny:
                        continue
                    other = field[nj * width + ni]
                    if other < FAR and other > value:
                        peak = False
                        break
                if not peak:
                    break
            if peak:
                candidates.append((value, grid.x(i), grid.y(j)))

    candidates.sort(reverse=True)

    # The single furthest node is usually a *corner* of a limb tip, not its
    # centre -- a rectangular arm has its far corners further away than the
    # middle of its end. Averaging the near-maximal nodes around each peak puts
    # the vent on the centreline, which is where it belongs.
    band = max(2.0 * grid.cell, 0.5)

    def settle(value, x, y):
        total_x = total_y = 0.0
        count = 0
        reach = int(math.ceil(min_separation / grid.cell))
        ci, cj = grid.nearest(x, y)
        for dj in range(-reach, reach + 1):
            for di in range(-reach, reach + 1):
                ni, nj = ci + di, cj + dj
                if ni < 0 or nj < 0 or ni > grid.nx or nj > grid.ny:
                    continue
                other = field[nj * width + ni]
                if other >= FAR or other < value - band:
                    continue
                total_x += grid.x(ni)
                total_y += grid.y(nj)
                count += 1
        if count == 0:
            return x, y
        return total_x / count, total_y / count

    # Settle first, then check separation. The other way round, two peaks that
    # start far apart can drift onto nearly the same spot -- a real figure came
    # back with two vents 1.1mm from each other.
    chosen = []
    for value, x, y in candidates:
        sx, sy = settle(value, x, y)
        if any(
            math.hypot(sx - px, sy - py) < min_separation for px, py in chosen
        ):
            continue
        chosen.append((sx, sy))
    return chosen


def sample(grid, field, x, y):
    i, j = grid.nearest(x, y)
    return field[grid.index(i, j)]


def silhouette_field(coords, indices, pad, cell):
    """Distance to a mesh's projected outline, over its own bounding box.

    Built once per lure and reused: peg placement needs to know how far a
    candidate is from the real shape, and a bounding box is far too blunt for
    that on anything with spread limbs.
    """
    xs = coords[0::3]
    ys = coords[1::3]
    if not xs:
        return None, None, None
    grid = make_grid(
        min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad, cell
    )
    mask = new_mask(grid)
    mark_triangles(grid, mask, coords, indices)
    return grid, distance_field(grid, mask), mask


def height_at(distance, land, depth, run):
    """Face height at a point that far from the nearest feature.

    Flat within the land, then ramping down over `run` to the recess depth.
    Zero or negative, measured from the parting plane.
    """
    if distance <= land:
        return 0.0
    if run <= 0.0:
        return -depth
    return -min(depth, (distance - land) / run * depth)


def terrain(grid, field, land, depth, run, sign, cap):
    """A watertight cutter bounded by the relief surface.

    For the bottom half (`sign` -1) it is everything from the surface upwards
    to `cap`; subtracting it recesses the face. For the top half it is the
    mirror image. Because the solid always has real thickness -- the cap is
    well clear of the surface -- there are no degenerate faces for the boolean
    to trip over.
    """
    width = grid.nx + 1
    depth_nodes = grid.ny + 1
    cap_z = abs(cap)

    # Always built for the bottom half -- surface below, cap above -- and
    # mirrored at the end if the top half was asked for. Deriving one winding
    # and reflecting it is far less error-prone than trying to keep two
    # consistent by hand.
    verts = []
    for j in range(depth_nodes):
        y = grid.y(j)
        for i in range(width):
            verts.append((
                grid.x(i), y,
                height_at(field[j * width + i], land, depth, run),
            ))

    surface_count = len(verts)
    for j in range(depth_nodes):
        y = grid.y(j)
        for i in range(width):
            verts.append((grid.x(i), y, cap_z))

    def surface(i, j):
        return j * width + i

    def cap_node(i, j):
        return surface_count + j * width + i

    faces = []

    def quad(a, b, c, d):
        faces.append((a, b, c))
        faces.append((a, c, d))

    for j in range(grid.ny):
        for i in range(grid.nx):
            # Surface faces down, cap faces up: opposite rotational orders.
            quad(surface(i, j), surface(i, j + 1),
                 surface(i + 1, j + 1), surface(i + 1, j))
            quad(cap_node(i, j), cap_node(i + 1, j),
                 cap_node(i + 1, j + 1), cap_node(i, j + 1))

    for i in range(grid.nx):
        quad(surface(i, 0), surface(i + 1, 0),
             cap_node(i + 1, 0), cap_node(i, 0))
        quad(surface(i + 1, grid.ny), surface(i, grid.ny),
             cap_node(i, grid.ny), cap_node(i + 1, grid.ny))
    for j in range(grid.ny):
        quad(surface(0, j + 1), surface(0, j),
             cap_node(0, j), cap_node(0, j + 1))
        quad(surface(grid.nx, j), surface(grid.nx, j + 1),
             cap_node(grid.nx, j + 1), cap_node(grid.nx, j))

    coords = [c for v in verts for c in v]
    indices = [i for f in faces for i in f]

    if sign > 0:
        coords = [
            value if k % 3 != 2 else -value for k, value in enumerate(coords)
        ]
        indices = [
            indices[t + offset]
            for t in range(0, len(indices), 3)
            for offset in (0, 2, 1)
        ]

    return coords, indices
