"""Pure-math mold layout core.

This module deliberately imports NOTHING from Fusion. It takes plain numbers in
and returns plain data out, so every bit of the fiddly geometry logic can be
unit-tested with ordinary Python and no CAD in the loop.

Coordinate convention (after the lure has been oriented):
    X = lure length, Y = lure height, Z = lure thickness.
    The parting plane is Z = 0, at the lure's mid-thickness.
    The block is centred on the origin in X and Y.
"""

import math
from dataclasses import dataclass

# Internal constants (spec section 6.2.1). Not exposed in the dialog.
EDGE_CLEARANCE = 2.0  # minimum material between a peg and the block edge
CAVITY_CLEARANCE = 1.0  # minimum gap between a peg and any cavity footprint
SPRUE_INSET_FRACTION = 0.15  # how far inboard from the nose tip the sprue sits
VENT_INSET_FRACTION = 0.05  # how far inboard from the tail tip the vent sits
MIN_PRINTABLE_MARGIN = 2.0  # thinner walls than this will not print well
MIN_PRINTABLE_VENT = 0.8  # narrower vents than this close up when printed
LAYOUT_GAP = 10.0  # space left between the two halves when laid out flat
MAX_VENTS_PER_CAVITY = 8  # a sanity bound, not a design limit
# The relief ramp angle is clamped away from 0 and 90 degrees: at 0 the ramp
# would be infinitely long, at 90 it would be a vertical step.
MIN_RELIEF_ANGLE = 5.0
MAX_RELIEF_ANGLE = 85.0


@dataclass(frozen=True)
class LureDims:
    """Oriented bounding-box dimensions of the lure, in mm."""

    length: float
    height: float
    thickness: float
    nose_at_positive_x: bool = True


@dataclass(frozen=True)
class MoldSettings:
    """Everything the user can set in the dialog."""

    target_length: float = 0.0  # 0 means "leave the lure at its natural size"
    # Where the mold splits, in mm from the lure's mid-thickness. Positive
    # moves the split upwards. Resolved automatically unless parting_auto is
    # off -- see parting.py for how the automatic value is found.
    parting_offset: float = 0.0
    parting_auto: bool = True
    columns: int = 1
    rows: int = 1
    margin_x: float = 10.0
    margin_y: float = 10.0
    margin_z: float = 10.0
    peg_count: int = 2
    peg_diameter: float = 5.0
    peg_height: float = 5.0
    peg_clearance: float = 0.2  # added to the hole DIAMETER, so half per side
    peg_chamfer: float = 0.6  # lead-in on the pin tip and the hole mouth
    sprue_diameter: float = 4.0
    funnel_diameter: float = 8.0
    # "edge" runs a channel along the parting line out through the block's
    # face, which is how real soft-plastic molds are injected. "runner" feeds
    # a single channel down the middle with the cavities gated off it, so the
    # whole shot pulls out as one tree. "top" drops a vertical funnel through
    # the top half. "none" leaves no hole at all.
    injection_mode: str = "edge"
    runner_diameter: float = 6.0
    vents_enabled: bool = True
    vent_diameter: float = 1.0
    flip_lure: bool = False
    lay_out_flat: bool = True
    # Mesh preparation, done on a copy so the user's body is untouched.
    auto_repair: bool = True
    reduce_faces: bool = True
    target_faces: int = 25000
    # Merge the two halves into a single body so the mold exports as one
    # piece. They are laid out flat first, so the result is still both halves
    # side by side, just as a single body.
    combine_halves: bool = True
    # Relief: keep the parting face flat only near the cavity, ports and pegs,
    # and recess everything else. A narrow sealing land prints far better than
    # a whole face that has to be dead flat.
    relief_enabled: bool = True
    relief_land: float = 4.0  # flat band kept around each feature, mm
    relief_depth: float = 2.0  # how far the recessed area drops, mm
    relief_angle: float = 50.0  # wall angle between land and recess, degrees


@dataclass(frozen=True)
class Point2:
    x: float
    y: float


@dataclass(frozen=True)
class Runner:
    """A channel down the middle of the mold feeding every cavity.

    Runs along Y at `x`, from the closed end `y_from` to `y_to`, where it
    breaks out of the block face and the injector seats.
    """

    x: float
    y_from: float
    y_to: float
    diameter: float


@dataclass(frozen=True)
class Vent:
    """One vent. `entry` is where it breaks out, or None for a top riser."""

    point: Point2
    entry: Point2 | None


@dataclass(frozen=True)
class Cavity:
    center: Point2
    # True when this cavity is turned end-for-end so its nose faces the
    # runner. Always False unless a central runner is in use.
    rotated: bool
    # Where the sprue meets the cavity, at the nose. None when there is no
    # injection hole at all.
    sprue: Point2 | None
    # Where an edge channel breaks out of the block. None for top injection,
    # for no injection, or when this cavity has no clear run to an edge.
    sprue_entry: Point2 | None
    # Every pocket that needs to breathe. A shape with several limbs traps
    # air at each one, so one vent per cavity is not enough.
    vents: tuple = ()

    @property
    def vent(self):
        """The primary vent, for callers that only care about one."""
        return self.vents[0].point if self.vents else None

    @property
    def vent_entry(self):
        return self.vents[0].entry if self.vents else None


@dataclass(frozen=True)
class HalfPlacement:
    """How one finished half is moved to lie flat, cavity upwards.

    The flip is a 180 degree rotation about the X axis, applied first; the
    translation follows. Flipping about X is the natural "open the book"
    motion for a mold that parts on the XY plane.
    """

    flip: bool
    dx: float
    dy: float
    dz: float


@dataclass(frozen=True)
class MoldLayout:
    block_x: float
    block_y: float
    block_z: float
    top_thickness: float
    bottom_thickness: float
    parting_offset: float
    cavities: tuple
    pegs: tuple
    runner: object
    warnings: tuple
    bottom_placement: HalfPlacement
    top_placement: HalfPlacement


def _nearest_face(point, column, row, settings, block_x, block_y):
    """Where a vent at `point` should break out, or None if it cannot.

    A channel can only escape through a face with nothing between it and the
    cavity, which means the outermost column or row in that direction. Of the
    faces that qualify, the nearest wins -- that keeps the channel short and,
    for a tail vent on a single cavity, picks the end face as before.
    """
    options = []
    if column == 0:
        options.append((abs(point.x + block_x / 2), Point2(-block_x / 2, point.y)))
    if column == settings.columns - 1:
        options.append((abs(block_x / 2 - point.x), Point2(block_x / 2, point.y)))
    if row == 0:
        options.append((abs(point.y + block_y / 2), Point2(point.x, -block_y / 2)))
    if row == settings.rows - 1:
        options.append((abs(block_y / 2 - point.y), Point2(point.x, block_y / 2)))

    if not options:
        return None
    options.sort(key=lambda pair: pair[0])
    return options[0][1]


def relief_run(depth, angle_degrees):
    """Horizontal distance a relief ramp covers while dropping `depth`.

    The angle is measured from the parting plane, so a 45 degree wall runs out
    as far as it drops. Clamped away from 0 and 90, where the ramp would be
    infinitely long or a vertical step.
    """
    if depth <= 0:
        return 0.0
    angle = min(max(angle_degrees, MIN_RELIEF_ANGLE), MAX_RELIEF_ANGLE)
    return depth / math.tan(math.radians(angle))


def _peg_candidates(block_x, block_y, cell_x, cell_y, columns, rows, inset, peg_count):
    """Candidate peg positions in priority order.

    Corners first, then edge midpoints, then the corridors between cavities --
    except for the two special cases the spec calls out.
    """
    edge_x = block_x / 2 - inset
    edge_y = block_y / 2 - inset

    corners = [
        Point2(-edge_x, -edge_y),
        Point2(edge_x, -edge_y),
        Point2(edge_x, edge_y),
        Point2(-edge_x, edge_y),
    ]
    midpoints = [
        Point2(-edge_x, 0.0),
        Point2(edge_x, 0.0),
        Point2(0.0, -edge_y),
        Point2(0.0, edge_y),
    ]

    # Corridors between adjacent cavities.
    interior_xs = [-block_x / 2 + cell_x * i for i in range(1, columns)]
    interior_ys = [-block_y / 2 + cell_y * j for j in range(1, rows)]
    interstitial = [Point2(ix, iy) for ix in interior_xs for iy in interior_ys]
    interstitial += [Point2(ix, sy) for ix in interior_xs for sy in (-edge_y, edge_y)]
    interstitial += [Point2(sx, iy) for iy in interior_ys for sx in (-edge_x, edge_x)]

    if peg_count == 2:
        # Diagonally opposite corners. Edge midpoints were tried first here
        # originally, but an edge sprue and vent break out at the centre of the
        # end faces -- exactly where those pegs land -- so they always clashed
        # with the fill port. Opposite corners are just as far apart and clear
        # of the ports.
        ordered = [corners[0], corners[2]] + corners + midpoints + interstitial
    else:
        ordered = corners + midpoints + interstitial

    seen = set()
    unique = []
    for point in ordered:
        key = (round(point.x, 6), round(point.y, 6))
        if key not in seen:
            seen.add(key)
            unique.append(point)
    return unique


def _hits_cavity(point, cavities, lure, peg_radius, cavity_distance=None):
    """True if a peg would foul a cavity.

    Given `cavity_distance` -- distance from a point to the lure's real
    outline, in cavity-local coordinates -- the true silhouette is used. Without
    it this falls back to the bounding box, which is badly over-cautious on
    anything with spread limbs: a figure leaves its box corners wide open, but
    the box says they are inside the cavity, so every peg gets rejected and the
    mold comes out with none.
    """
    clearance = CAVITY_CLEARANCE + peg_radius

    if cavity_distance is not None:
        for cavity in cavities:
            dx = point.x - cavity.center.x
            dy = point.y - cavity.center.y
            if cavity.rotated:
                dx, dy = -dx, -dy
            if cavity_distance(dx, dy) < clearance:
                return True
        return False

    half_x = lure.length / 2 + clearance
    half_y = lure.height / 2 + clearance
    for cavity in cavities:
        if (
            abs(point.x - cavity.center.x) < half_x
            and abs(point.y - cavity.center.y) < half_y
        ):
            return True
    return False


def _hits_runner(point, runner, peg_radius):
    """True if a peg would foul the central runner channel."""
    if runner is None:
        return False
    low, high = sorted((runner.y_from, runner.y_to))
    clearance = CAVITY_CLEARANCE + peg_radius
    return (
        abs(point.x - runner.x) <= runner.diameter / 2 + clearance
        and low - clearance <= point.y <= high + clearance
    )


def _hits_port(point, cavities, settings, peg_radius):
    """True if a peg would foul an injection sprue or a vent.

    An edge channel sweeps a band along X between the cavity and the block
    face; treat it as a rectangle, which is conservative at the tapered end.
    A top-entry port is a circle. Either way a peg driven through one makes
    the mold unusable, so these are hard rejections.
    """
    clearance = CAVITY_CLEARANCE + peg_radius

    def band(inner, entry, radius):
        low, high = sorted((inner.x, entry.x))
        return (
            low - clearance <= point.x <= high + clearance
            and abs(point.y - entry.y) <= radius + clearance
        )

    def disc(centre, radius):
        return (
            abs(point.x - centre.x) <= radius + clearance
            and abs(point.y - centre.y) <= radius + clearance
        )

    for cavity in cavities:
        if cavity.sprue is not None:
            radius = settings.funnel_diameter / 2
            if cavity.sprue_entry is not None:
                if band(cavity.sprue, cavity.sprue_entry, radius):
                    return True
            elif disc(cavity.sprue, radius):
                return True

        for vent in cavity.vents:
            radius = settings.vent_diameter / 2
            if vent.entry is not None:
                if band(vent.point, vent.entry, radius):
                    return True
            elif disc(vent.point, radius):
                return True

    return False


def compute_layout(
    lure: LureDims, settings: MoldSettings, cavity_distance=None,
    vent_points=None,
) -> MoldLayout:
    """Compute the full mold layout from lure dimensions and user settings."""
    cell_x = lure.length + 2 * settings.margin_x
    cell_y = lure.height + 2 * settings.margin_y
    cell_z = lure.thickness + 2 * settings.margin_z

    block_x = settings.columns * cell_x
    block_y = settings.rows * cell_y

    # Where the split sits, measured from the lure's mid-thickness. Clamped so
    # there is always lure on both sides of it.
    limit = lure.thickness / 2
    parting = max(-limit, min(limit, getattr(settings, "parting_offset", 0.0)))
    top_thickness = (limit - parting) + settings.margin_z
    bottom_thickness = (limit + parting) + settings.margin_z

    # The detected nose direction, flipped if the user overrode it.
    nose_positive = lure.nose_at_positive_x != settings.flip_lure
    nose_sign = 1.0 if nose_positive else -1.0

    # Offsets from the cavity centre, clamped so both stay over the lure.
    half_length = lure.length / 2
    sprue_offset = min(
        half_length - SPRUE_INSET_FRACTION * lure.length, half_length
    )
    vent_offset = min(half_length - VENT_INSET_FRACTION * lure.length, half_length)

    mode = getattr(settings, "injection_mode", "edge")

    # A central runner only makes sense with two columns facing each other.
    runner = None
    runner_notes = []
    if mode == "runner":
        if settings.columns == 2:
            # The closed end stops a wall's thickness short of the face, so
            # the channel is capped there instead of opening out both ends.
            runner = Runner(
                x=0.0,
                y_from=-block_y / 2 + settings.margin_y,
                y_to=block_y / 2,
                diameter=settings.runner_diameter,
            )
        else:
            runner_notes.append(
                "A central runner needs exactly 2 columns so the cavities can "
                "face each other across it - you have %d. Falling back to edge "
                "injection." % settings.columns
            )
            mode = "edge"
    # An edge channel can only escape if nothing sits between the cavity and
    # the face it runs towards. With every lure pointing the same way that is
    # true only for the outermost column.
    nose_column = settings.columns - 1 if nose_positive else 0
    tail_column = 0 if nose_positive else settings.columns - 1
    nose_edge_x = nose_sign * block_x / 2
    tail_edge_x = -nose_sign * block_x / 2

    cavities = []
    blocked = 0
    boxed_in = 0
    for i in range(settings.columns):
        for j in range(settings.rows):
            cx = -block_x / 2 + cell_x * (i + 0.5)
            cy = -block_y / 2 + cell_y * (j + 0.5)

            # With a runner the two columns face each other, so each column
            # has its own nose direction; otherwise every lure faces the same
            # way. Column 0 sits left of the runner and keeps the base
            # orientation; column 1 is turned to face back toward it.
            rotated = False
            if runner is not None:
                sign = 1.0 if cx < runner.x else -1.0
                rotated = sign != nose_sign
            else:
                sign = nose_sign

            if mode == "none":
                sprue = None
                sprue_entry = None
            else:
                sprue = Point2(cx + sign * sprue_offset, cy)
                if runner is not None:
                    sprue_entry = Point2(runner.x, cy)
                elif mode == "edge" and i == nose_column:
                    sprue_entry = Point2(nose_edge_x, cy)
                else:
                    sprue_entry = None
                    if mode == "edge":
                        blocked += 1

            # A vent lies on the parting line so it opens up when the mold
            # does and can be cleaned out; a vertical riser is a blind hole
            # full of set plastic. Each pocket gets its own, routed to the
            # nearest face it can actually reach.
            vents = []
            if settings.vents_enabled:
                if vent_points:
                    points = [
                        Point2(cx - px, cy - py) if rotated
                        else Point2(cx + px, cy + py)
                        for px, py in vent_points[:MAX_VENTS_PER_CAVITY]
                    ]
                else:
                    points = [Point2(cx - sign * vent_offset, cy)]

                for point in points:
                    entry = _nearest_face(
                        point, i, j, settings, block_x, block_y
                    )
                    if entry is None:
                        boxed_in += 1
                    vents.append(Vent(point=point, entry=entry))

            cavities.append(
                Cavity(
                    center=Point2(cx, cy),
                    rotated=rotated,
                    sprue=sprue,
                    sprue_entry=sprue_entry,
                    vents=tuple(vents),
                )
            )
    cavities = tuple(cavities)

    warnings = list(runner_notes)

    if boxed_in:
        warnings.append(
            "%d cavit%s cannot vent along the parting line - they are boxed in "
            "on every side. Those get a vertical riser through the top half "
            "instead, which has to be drilled out after printing."
            % (boxed_in, "y" if boxed_in == 1 else "ies")
        )

    if blocked:
        warnings.append(
            "%d of %d cavities cannot be injected from the edge - with every "
            "lure facing the same way, only the outermost column has a clear "
            "run to the face. Those cavities get a top-entry sprue instead. "
            "Use 1 column to inject them all from the edge."
            % (blocked, len(cavities))
        )

    thinnest = min(settings.margin_x, settings.margin_y, settings.margin_z)
    if thinnest < MIN_PRINTABLE_MARGIN:
        warnings.append(
            f"Smallest margin is {thinnest:g}mm - walls thinner than "
            f"{MIN_PRINTABLE_MARGIN:g}mm are fragile and print poorly."
        )

    if getattr(settings, "relief_enabled", False) and settings.relief_depth > 0:
        needed = settings.relief_land + relief_run(
            settings.relief_depth, settings.relief_angle
        )
        wall = min(settings.margin_x, settings.margin_y)
        if needed >= wall:
            warnings.append(
                "The relief land (%.1fmm) plus its %.1fmm ramp does not fit "
                "inside the %.1fmm wall, so little or none of the parting face "
                "gets recessed. Reduce the land, or increase the margins."
                % (settings.relief_land, needed - settings.relief_land, wall)
            )

    if settings.vents_enabled and settings.vent_diameter < MIN_PRINTABLE_VENT:
        warnings.append(
            f"Vent diameter {settings.vent_diameter:g}mm is below "
            f"{MIN_PRINTABLE_VENT:g}mm - a hole that narrow closes up when "
            "printed and will not vent."
        )

    peg_radius = settings.peg_diameter / 2
    inset = peg_radius + EDGE_CLEARANCE
    pegs = []
    for candidate in _peg_candidates(
        block_x, block_y, cell_x, cell_y,
        settings.columns, settings.rows, inset, settings.peg_count,
    ):
        if len(pegs) >= settings.peg_count:
            break
        if _hits_cavity(candidate, cavities, lure, peg_radius, cavity_distance):
            continue
        if _hits_port(candidate, cavities, settings, peg_radius):
            continue
        if _hits_runner(candidate, runner, peg_radius):
            continue
        pegs.append(candidate)

    if len(pegs) < settings.peg_count:
        if not pegs:
            warnings.append(
                f"Could not place any of the {settings.peg_count} alignment pegs - "
                "the cavities leave no clear space. Increase the margins or "
                "reduce the peg diameter."
            )
        else:
            warnings.append(
                f"Placed {len(pegs)} of {settings.peg_count} alignment pegs - "
                "no more non-colliding positions are available."
            )

    # Lay the two halves out side by side, both resting on z = 0 with their
    # cavities facing up so the user can see what they just made.
    #
    # The bottom half occupies z in [-bottom, 0] with its cavity at z = 0, so
    # it only needs lifting. The top half occupies [0, top] with its cavity
    # facing down; flipping it about X puts it in [-top, 0] with the cavity
    # facing up, after which the same idea applies. Each half is lifted by its
    # own thickness, which differ once the parting plane is off-centre.
    offset = (block_y + LAYOUT_GAP) / 2

    return MoldLayout(
        block_x=block_x,
        block_y=block_y,
        block_z=cell_z,
        top_thickness=top_thickness,
        bottom_thickness=bottom_thickness,
        parting_offset=parting,
        cavities=cavities,
        pegs=tuple(pegs),
        runner=runner,
        warnings=tuple(warnings),
        bottom_placement=HalfPlacement(
            flip=False, dx=0.0, dy=-offset, dz=bottom_thickness
        ),
        top_placement=HalfPlacement(
            flip=True, dx=0.0, dy=offset, dz=top_thickness
        ),
    )
