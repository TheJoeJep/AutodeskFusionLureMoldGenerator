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
# Default prominence bar, as a fraction of the deepest fill distance.
PROMINENCE_FRACTION = 0.15
# How far around a feature exact nearest-points are worked out, in cells.
# Two is enough to give the propagation sweep something correct to carry.
SEED_BAND = 2


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


def make_grid(min_x, min_y, max_x, max_y, cell, max_nodes=None):
    """A grid covering the given area, coarsened if it would be too fine."""
    width = max(max_x - min_x, cell)
    depth = max(max_y - min_y, cell)
    ceiling = MAX_NODES if max_nodes is None else max_nodes

    for _ in range(64):
        nx = max(int(math.ceil(width / cell)), 1)
        ny = max(int(math.ceil(depth / cell)), 1)
        if (nx + 1) * (ny + 1) <= ceiling:
            return Grid(min_x, min_y, cell, nx, ny)
        cell *= 1.3

    return Grid(min_x, min_y, cell, 1, 1)


def new_mask(grid):
    return bytearray(grid.node_count)


class Nearest:
    """Exact closest points on the features, one per grid node.

    A binary mask can only record which grid *nodes* a feature covers, so the
    distance that falls out of it is distance-to-the-nearest-marked-node. That
    is quantised: the marked node closest to the true outline sits anywhere
    from zero to a full cell inside it, and which one wins changes as the
    boundary weaves between nodes. The error is therefore not a constant
    offset but a ripple of nearly a whole cell.

    On a real mold that measured as 1.1mm of ripple across a 3.4mm ramp -- a
    third of the ramp's depth -- which is the corrugation that showed up
    running down every slope. Supersampling does not rescue it; the ripple
    only falls off linearly with the cell.

    So features record where their closest point actually *is*, to floating
    point, for nodes within SEED_BAND cells. `distance_field` then carries
    those points outwards and the ramp comes out smooth.

    A second bug falls out of the same fix. A feature thinner than one cell can
    slip between two rows of nodes and mark nothing at all: a 1mm vent channel
    on a 1.12mm grid was invisible about half the time, so the relief kept no
    land along it and the recess swallowed the channel partway to the block
    face. Exact distances do not care whether a node landed inside.
    """

    def __init__(self, grid):
        count = grid.node_count
        self.px = [0.0] * count
        self.py = [0.0] * count
        self.d2 = [_BIG] * count

    def offer(self, index, x, y, qx, qy):
        """Record (qx, qy) as this node's closest feature point, if it is."""
        dx, dy = x - qx, y - qy
        d2 = dx * dx + dy * dy
        if d2 < self.d2[index]:
            self.d2[index] = d2
            self.px[index] = qx
            self.py[index] = qy

    def inside(self, index, x, y):
        """This node is within a feature, so its distance is zero."""
        self.d2[index] = 0.0
        self.px[index] = x
        self.py[index] = y


def marked_at(grid, mask, x, y):
    i, j = grid.nearest(x, y)
    return bool(mask[grid.index(i, j)])


def mark_disc(grid, mask, cx, cy, radius, nearest=None):
    """Mark every node inside a circle -- pegs, and top-entry ports."""
    if radius <= 0:
        return
    reach = radius + (SEED_BAND * grid.cell if nearest is not None else 0.0)
    lo_i = max(int(math.floor((cx - reach - grid.min_x) / grid.cell)), 0)
    hi_i = min(int(math.ceil((cx + reach - grid.min_x) / grid.cell)), grid.nx)
    lo_j = max(int(math.floor((cy - reach - grid.min_y) / grid.cell)), 0)
    hi_j = min(int(math.ceil((cy + reach - grid.min_y) / grid.cell)), grid.ny)

    hit = False
    for j in range(lo_j, hi_j + 1):
        y = grid.y(j)
        dy = y - cy
        for i in range(lo_i, hi_i + 1):
            x = grid.x(i)
            dx = x - cx
            index = grid.index(i, j)
            distance = math.hypot(dx, dy)
            if distance <= radius:
                mask[index] = 1
                hit = True
                if nearest is not None:
                    nearest.inside(index, x, y)
            elif nearest is not None:
                scale = radius / distance
                nearest.offer(index, x, y, cx + dx * scale, cy + dy * scale)

    if not hit and nearest is None:
        _mark_nearest(grid, mask, cx, cy)


def mark_rect(grid, mask, cx, cy, lx, ly, nearest=None):
    """Mark every node inside an axis-aligned rectangle -- channels."""
    half_x, half_y = abs(lx) / 2, abs(ly) / 2
    reach = SEED_BAND * grid.cell if nearest is not None else 0.0
    lo_i = max(int(math.floor((cx - half_x - reach - grid.min_x) / grid.cell)), 0)
    hi_i = min(int(math.ceil((cx + half_x + reach - grid.min_x) / grid.cell)), grid.nx)
    lo_j = max(int(math.floor((cy - half_y - reach - grid.min_y) / grid.cell)), 0)
    hi_j = min(int(math.ceil((cy + half_y + reach - grid.min_y) / grid.cell)), grid.ny)

    hit = False
    for j in range(lo_j, hi_j + 1):
        y = grid.y(j)
        qy = min(max(y, cy - half_y), cy + half_y)
        for i in range(lo_i, hi_i + 1):
            x = grid.x(i)
            qx = min(max(x, cx - half_x), cx + half_x)
            index = grid.index(i, j)
            if qx == x and qy == y:
                mask[index] = 1
                hit = True
                if nearest is not None:
                    nearest.inside(index, x, y)
            elif nearest is not None:
                nearest.offer(index, x, y, qx, qy)

    if not hit and nearest is None:
        _mark_nearest(grid, mask, cx, cy)


def _mark_nearest(grid, mask, x, y):
    """Mark the node closest to a feature that fell between the nodes.

    A channel narrower than one cell can miss every node and disappear from the
    mask entirely -- which on a real mold left a vent with no land around it,
    so the recess swallowed it partway to the block face.
    """
    i, j = grid.nearest(x, y)
    mask[grid.index(i, j)] = 1


def _segment_nearest(px, py, ax, ay, bx, by):
    """Closest point to (px, py) on the segment ab, and its squared distance."""
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    vv = vx * vx + vy * vy
    if vv <= 1e-18:
        return wx * wx + wy * wy, ax, ay
    t = (wx * vx + wy * vy) / vv
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    qx, qy = ax + t * vx, ay + t * vy
    dx, dy = px - qx, py - qy
    return dx * dx + dy * dy, qx, qy


def _summed_area(grid, mask):
    """Prefix sums of a mask, so "is this whole box marked?" costs four reads."""
    width = grid.nx + 1
    height = grid.ny + 1
    stride = width + 1
    table = [0] * (stride * (height + 1))
    for j in range(height):
        row = j * width
        above = j * stride
        here = (j + 1) * stride
        running = 0
        for i in range(width):
            running += mask[row + i]
            table[here + i + 1] = table[above + i + 1] + running
    return table, stride


def mark_triangles(grid, mask, coords, indices, dx=0.0, dy=0.0, nearest=None):
    """Mark the silhouette of a mesh, projected straight down onto the grid.

    This is the whole point of the module: the land follows the real outline
    rather than the box around it.

    With a `Nearest` the outline is measured exactly as well, for nodes just
    outside it. That has to be a second pass, because it needs to know which
    nodes the silhouette already covers: a triangle buried inside the shape
    cannot be the closest thing to anything outside, and rejecting those for
    one summed-area lookup each is what keeps the exact pass affordable on a
    25,000 triangle mesh.
    """
    own = new_mask(grid) if nearest is not None else mask

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
                    own[index] = 1

    if nearest is None:
        return

    table, stride = _summed_area(grid, own)
    width = grid.nx + 1
    band = SEED_BAND

    for t in range(0, len(indices), 3):
        a, b, c = indices[t] * 3, indices[t + 1] * 3, indices[t + 2] * 3
        ax, ay = coords[a] + dx, coords[a + 1] + dy
        bx, by = coords[b] + dx, coords[b + 1] + dy
        cx, cy = coords[c] + dx, coords[c + 1] + dy

        lo_i = max(int(math.floor((min(ax, bx, cx) - grid.min_x) / grid.cell)) - band, 0)
        hi_i = min(int(math.ceil((max(ax, bx, cx) - grid.min_x) / grid.cell)) + band, grid.nx)
        lo_j = max(int(math.floor((min(ay, by, cy) - grid.min_y) / grid.cell)) - band, 0)
        hi_j = min(int(math.ceil((max(ay, by, cy) - grid.min_y) / grid.cell)) + band, grid.ny)
        if hi_i < lo_i or hi_j < lo_j:
            continue

        # Is every node around this triangle already inside the silhouette?
        # Then nothing outside can be closest to it. That rejects the great
        # majority of a solid mesh's triangles for four array reads.
        covered = (
            table[(hi_j + 1) * stride + hi_i + 1]
            - table[lo_j * stride + hi_i + 1]
            - table[(hi_j + 1) * stride + lo_i]
            + table[lo_j * stride + lo_i]
        )
        if covered == (hi_i - lo_i + 1) * (hi_j - lo_j + 1):
            continue

        for j in range(lo_j, hi_j + 1):
            py = grid.y(j)
            row = j * width
            for i in range(lo_i, hi_i + 1):
                index = row + i
                if own[index]:
                    continue
                px = grid.x(i)
                best, qx, qy = _segment_nearest(px, py, ax, ay, bx, by)
                d2, ex, ey = _segment_nearest(px, py, bx, by, cx, cy)
                if d2 < best:
                    best, qx, qy = d2, ex, ey
                d2, ex, ey = _segment_nearest(px, py, cx, cy, ax, ay)
                if d2 < best:
                    best, qx, qy = d2, ex, ey
                if best < nearest.d2[index]:
                    nearest.d2[index] = best
                    nearest.px[index] = qx
                    nearest.py[index] = qy

    for index, value in enumerate(own):
        if value:
            mask[index] = 1
            nearest.inside(index, grid.x(index % width), grid.y(index // width))


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


def _propagate(grid, nearest):
    """Carry each node's closest feature point out across the whole grid.

    Danielsson's two sweeps: a node's nearest feature is nearly always the one
    its neighbour found, so offering each neighbour's point in turn settles the
    field in two passes. Unlike a distance-only sweep this stays accurate well
    away from the features, because what travels is the point, not a step
    length that has to be rounded to the lattice.
    """
    width = grid.nx + 1
    height = grid.ny + 1
    px, py, d2 = nearest.px, nearest.py, nearest.d2
    xs = [grid.x(i) for i in range(width)]
    ys = [grid.y(j) for j in range(height)]

    def relax(index, x, y, other):
        if d2[other] >= _BIG:
            return
        qx, qy = px[other], py[other]
        ax, ay = x - qx, y - qy
        value = ax * ax + ay * ay
        if value < d2[index]:
            d2[index] = value
            px[index] = qx
            py[index] = qy

    forward = ((-1, 0), (-1, -1), (0, -1), (1, -1))
    backward = ((1, 0), (1, 1), (0, 1), (-1, 1))

    def sweep(rows, columns, offsets, trailing):
        for j in rows:
            y = ys[j]
            row = j * width
            for i in columns:
                index = row + i
                if d2[index] <= 0.0:
                    continue
                x = xs[i]
                for di, dj in offsets:
                    ni, nj = i + di, j + dj
                    if 0 <= ni < width and 0 <= nj < height:
                        relax(index, x, y, nj * width + ni)
            for i in reversed(columns):
                index = row + i
                if d2[index] <= 0.0:
                    continue
                ni = i + trailing
                if 0 <= ni < width:
                    relax(index, xs[i], y, row + ni)

    sweep(range(height), list(range(width)), forward, 1)
    sweep(range(height - 1, -1, -1), list(range(width - 1, -1, -1)), backward, -1)


def distance_field(grid, mask, nearest=None):
    """Euclidean distance from every node to the nearest feature.

    Without a `Nearest` this measures to the closest marked *node*, which it
    does exactly -- Felzenszwalb and Huttenlocher's transform, not the chamfer
    sweep it replaced, whose octagonal bias varied by over a millimetre around
    a circle. Good enough for peg placement, which only asks whether a spot is
    roughly clear.

    Given a `Nearest` it measures to the features themselves, which is what the
    relief ramp needs; see that class for why measuring to nodes is not enough.
    """
    if nearest is not None:
        _propagate(grid, nearest)
        return [
            FAR if value >= _BIG else math.sqrt(value) for value in nearest.d2
        ]

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


def find_pockets(grid, mask, field, min_separation, min_prominence=None):
    """Where air ends up trapped: the last places in the cavity to fill.

    A local maximum on its own is not enough. Where a limb passes close to the
    gate the fill field forms a ridge, and every node along it is a maximum in
    its own 8-neighbourhood -- a real figure produced a chain of them down one
    arm. What marks a genuine pocket is **prominence**: how far you must
    descend from a peak before you can climb to higher ground. A limb tip has
    metres of it; a ridge ripple has a fraction of a millimetre.

    Prominence is computed by flooding downwards with a union-find: process
    nodes highest first, and when two basins meet, the lower of their two peaks
    is drowned at that level, so its prominence is fixed there and then.

    Returns positions deepest first, thinned so none land within
    `min_separation` of another.
    """
    width = grid.nx + 1
    order = [index for index, value in enumerate(field) if value < FAR]
    if not order:
        return []
    order.sort(key=lambda index: field[index], reverse=True)

    if min_prominence is None:
        min_prominence = PROMINENCE_FRACTION * field[order[0]]

    size = len(field)
    parent = [-1] * size      # -1 until the node has been flooded
    summit = [0] * size       # basin root -> its highest node
    prominence = {}

    def find(a):
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:   # path compression
            parent[a], a = root, parent[a]
        return root

    for index in order:
        value = field[index]
        i = index % width
        j = index // width
        parent[index] = index
        summit[index] = index

        for dj in (-1, 0, 1):
            for di in (-1, 0, 1):
                if di == 0 and dj == 0:
                    continue
                ni, nj = i + di, j + dj
                if ni < 0 or nj < 0 or ni > grid.nx or nj > grid.ny:
                    continue
                neighbour = nj * width + ni
                if parent[neighbour] < 0:
                    continue  # lower down, not flooded yet

                a = find(index)
                b = find(neighbour)
                if a == b:
                    continue
                peak_a, peak_b = summit[a], summit[b]
                if field[peak_a] >= field[peak_b]:
                    higher, lower = peak_a, peak_b
                else:
                    higher, lower = peak_b, peak_a
                # The lower summit drowns at the level we have reached.
                prominence.setdefault(lower, field[lower] - value)
                parent[b] = a
                summit[a] = higher

    # By convention the highest point of a shell has unbounded prominence:
    # there is no higher ground to reach, and it always needs a vent.
    for index in order:
        if parent[index] == index:
            prominence[summit[index]] = FAR

    candidates = sorted(
        (
            (field[index], index)
            for index, height in prominence.items()
            if height >= min_prominence
        ),
        reverse=True,
    )

    # A raw maximum sits at the far *corner* of a limb tip, not its centre.
    # Take the centroid of the connected near-maximal patch around it, which
    # stops at the neck of the limb instead of drifting back into the body.
    band = max(3.0 * grid.cell, 1.0)

    def settle(index):
        value = field[index]
        floor = value - band
        seen = {index}
        stack = [index]
        total_x = total_y = 0.0
        count = 0
        while stack and count < 4000:
            current = stack.pop()
            ci, cj = current % width, current // width
            total_x += grid.x(ci)
            total_y += grid.y(cj)
            count += 1
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    ni, nj = ci + di, cj + dj
                    if ni < 0 or nj < 0 or ni > grid.nx or nj > grid.ny:
                        continue
                    other = nj * width + ni
                    if other in seen:
                        continue
                    height = field[other]
                    if height >= FAR or height < floor:
                        continue
                    seen.add(other)
                    stack.append(other)
        if count == 0:
            return grid.x(index % width), grid.y(index // width)
        return total_x / count, total_y / count

    # Settle first, then check separation. The other way round, two peaks that
    # start far apart can drift onto nearly the same spot -- a real figure came
    # back with two vents 1.1mm from each other.
    chosen = []
    for _, index in candidates:
        x, y = settle(index)
        if any(
            math.hypot(x - px, y - py) < min_separation for px, py in chosen
        ):
            continue
        chosen.append((x, y))
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

    Flat within the land, then easing down over `run` to the recess depth.
    Zero or negative, measured from the parting plane.

    The ease is a smoothstep rather than a straight ramp. A straight one leaves
    a crease where it meets the land and another where it meets the floor, and
    a crease sampled on a grid can only zig-zag along it from one node to the
    next. Rounding both ends removes the creases, and a slope with no abrupt
    change of angle is kinder to a slicer as well. `relief_run` widens the ramp
    to suit, so its steepest part still stands at the angle asked for.
    """
    if distance <= land:
        return 0.0
    if run <= 0.0:
        return -depth
    t = (distance - land) / run
    if t >= 1.0:
        return -depth
    return -depth * t * t * (3.0 - 2.0 * t)


def terrain(grid, field, land, depth, run, sign, cap):
    """A watertight cutter bounded by the relief surface.

    For the bottom half (`sign` -1) it is everything from the surface upwards
    to `cap`; subtracting it recesses the face. For the top half it is the
    mirror image. Because the solid always has real thickness -- the cap is
    well clear of the surface -- there are no degenerate faces for the boolean
    to trip over.

    Only the underside carries any shape. The cap is a flat rectangle, so it
    is a fan from its own centre over the boundary ring rather than a copy of
    the grid: on a typical mold that is around 500 triangles instead of 34,000,
    and since this body is what the two big mesh booleans chew through, it cut
    a two minute build back to well under one.
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

    def surface(i, j):
        return j * width + i

    # The boundary, counter-clockwise seen from above. Taken in that order,
    # a wall quad from one ring node to the next faces outwards all the way
    # round, and a cap triangle from the centre faces up.
    ring = (
        [(i, 0) for i in range(grid.nx + 1)]
        + [(grid.nx, j) for j in range(1, grid.ny + 1)]
        + [(i, grid.ny) for i in range(grid.nx - 1, -1, -1)]
        + [(0, j) for j in range(grid.ny - 1, 0, -1)]
    )

    ring_surface = [surface(i, j) for i, j in ring]
    ring_cap = []
    for i, j in ring:
        ring_cap.append(len(verts))
        verts.append((grid.x(i), grid.y(j), cap_z))
    centre = len(verts)
    verts.append((
        (grid.x(0) + grid.x(grid.nx)) / 2.0,
        (grid.y(0) + grid.y(grid.ny)) / 2.0,
        cap_z,
    ))

    faces = []

    def quad(a, b, c, d):
        faces.append((a, b, c))
        faces.append((a, c, d))

    for j in range(grid.ny):
        for i in range(grid.nx):
            # The relief surface itself, facing down.
            quad(surface(i, j), surface(i, j + 1),
                 surface(i + 1, j + 1), surface(i + 1, j))

    for k in range(len(ring)):
        n = (k + 1) % len(ring)
        quad(ring_surface[k], ring_surface[n], ring_cap[n], ring_cap[k])
        faces.append((centre, ring_cap[k], ring_cap[n]))

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
