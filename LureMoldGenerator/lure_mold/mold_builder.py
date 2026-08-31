"""Building the mold in Fusion.

Everything is generated as watertight triangle meshes by meshgen and combined
with Fusion's mesh booleans. There are no sketches or extrudes, so nothing
depends on solid-modelling edge cases and the timeline stays clean.

The block halves, pegs, sprues and vents are all exact: a box tessellates
losslessly, and the round features are generated at a facet count we choose.
Only the lure cavity carries the resolution of the user's own mesh, which is
exactly as it should be.

Fusion's API works in CENTIMETRES. Layout maths is in millimetres. The
conversion happens in one place: MM.
"""

import dataclasses

import adsk.core
import adsk.fusion

from . import layout as layout_mod
from . import lure_analysis
from . import mesh_prep
from . import meshgen
from . import relief

MM = 0.1  # millimetres -> centimetres

ATTR_GROUP = "LureMoldGenerator"
ATTR_GENERATED = "generated"

COMPONENT_NAME = "Lure Mold"
BOTTOM_NAME = "Mold Bottom"
TOP_NAME = "Mold Top"

ROUND_SEGMENTS = 48
CUT = adsk.fusion.MeshCombineOperationTypes.CutMeshCombineType
PEG_HOLE_RELIEF = 0.5  # mm of extra hole depth, per spec 6.2.1
SPRUE_BREAKOUT = 1.0  # mm the edge channel starts outside the block face
# What a mold gets renamed to once we have given up on deleting it.
RETIRED_SUFFIX = " (old - delete me)"


class BuildResult:
    def __init__(self, bottom, top, layout, warnings):
        self.bottom = bottom
        self.top = top
        self.layout = layout
        self.warnings = warnings


def _add_mesh(component, coords_mm, indices, name):
    """Add a mesh body, converting millimetres to Fusion's centimetres."""
    coords_cm = meshgen.scale(coords_mm, MM)
    body = component.meshBodies.addByTriangleMeshData(coords_cm, indices, [], [])
    body.name = name
    return body


def _mark_generated(design, body):
    body.attributes.add(ATTR_GROUP, ATTR_GENERATED, "1")


def fresh_component(design, notes=None):
    """Return an empty component to build into, replacing any previous mold.

    Everything is generated inside its own component rather than loose in the
    root. In a parametric design you cannot delete a body the timeline depends
    on, so 'regenerate' means dropping the whole previous component and
    starting again -- which is clean, and keeps the user's own lure untouched.

    Part Design documents allow only one component, so there is nowhere to put
    it and the build falls back to the root. Anything it cannot then delete is
    at least switched off, because two molds sitting in the same place look
    like one mold with inexplicable geometry.
    """
    root = design.rootComponent
    for i in reversed(range(root.occurrences.count)):
        occurrence = root.occurrences.item(i)
        try:
            if occurrence.component.name == COMPONENT_NAME:
                occurrence.deleteMe()
        except Exception:
            pass

    try:
        occurrence = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
        occurrence.component.name = COMPONENT_NAME
        return occurrence.component
    except Exception:
        # Part Design documents are limited to a single component, so there
        # is nowhere to put one. Build in the root instead and clear away
        # whatever the last run left behind.
        left = _clear_previous_bodies(root, design)
        if left and notes is not None:
            notes.append(
                "This document only allows one component, so the mold is "
                "built loose in the root and the timeline will not let go of "
                "the older ones. %d %s marked \"%s\" and switched off; "
                "%s safe to delete by hand, and %s slow every rebuild down "
                "until you do. A normal Design document avoids this - there "
                "each regenerate throws the previous mold away."
                % (
                    left,
                    "body is" if left == 1 else "bodies are",
                    RETIRED_SUFFIX.strip(),
                    "it is" if left == 1 else "they are",
                    "it will" if left == 1 else "they will",
                )
            )
        return root


def is_generated_body(name):
    """Is this one of ours, from an earlier run?

    The merged mold is named after the component, so matching only the two
    half names missed it entirely: in a Part Design document every regenerate
    stacked another finished mold on top of the last, all of them visible.
    """
    return (
        name in (BOTTOM_NAME, TOP_NAME)
        or name.startswith(COMPONENT_NAME)
        or name.startswith(("peg_", "sprue_", "vent_", "cavity_", "runner"))
    )


def _clear_previous_bodies(component, design=None):
    """Get the generated body names free again for a rebuild.

    Deleting is preferred, but a body produced by a feature cannot be removed
    while the timeline depends on it. Renaming is the fallback -- it frees the
    name so the new mold is not silently suffixed -- and it is switched off as
    well, so a mold nobody can delete cannot be mistaken for the current one.

    In a parametric design deletion is never going to work, and asking anyway
    is not free: a failed deleteMe on a 50,000 triangle body cost fifteen
    seconds apiece, which on a document with a few old molds in it was most of
    the build. So do not ask. A body already retired is not asked about again
    either, for the same reason.

    Returns how many had to be left behind.
    """
    parametric = True
    try:
        parametric = (
            design.designType == adsk.fusion.DesignTypes.ParametricDesignType
        )
    except Exception:
        pass

    left = 0
    for index in reversed(range(component.meshBodies.count)):
        body = component.meshBodies.item(index)
        if not is_generated_body(body.name):
            continue
        if body.name.endswith(RETIRED_SUFFIX):
            left += 1  # dealt with on an earlier run, and still in the way
            continue
        if not parametric:
            try:
                body.deleteMe()
                continue
            except Exception:
                pass
        left += 1
        try:
            body.isLightBulbOn = False
        except Exception:
            pass
        try:
            body.name = body.name + RETIRED_SUFFIX
        except Exception:
            pass
    return left


def _combine(component, target, tools, operation, keep_tools):
    """One mesh boolean against a batch of tool bodies."""
    tools = [t for t in tools if t is not None]
    if not tools:
        return target
    features = component.features.meshCombineFeatures
    combine_input = features.createInput(target, tools)
    combine_input.meshCombineOperationType = operation
    combine_input.algorithmType = (
        adsk.fusion.MeshCombineAlgorithmTypes.EnhancedMeshCombineAlgorithmType
    )
    combine_input.isKeepToolBodies = keep_tools
    features.add(combine_input)
    return _find_by_name(component, target.name)


def _place(component, body, placement):
    """Move a finished half into its laid-out position.

    The flip is a 180 degree rotation about X, which turns the top half over so
    its cavity faces upwards. Translation is applied after the rotation, which
    is exactly how layout.HalfPlacement defines it.
    """
    if body is None:
        return None

    collection = adsk.core.ObjectCollection.create()
    collection.add(body)

    sign = -1.0 if placement.flip else 1.0
    matrix = adsk.core.Matrix3D.create()
    matrix.setWithArray([
        1.0, 0.0, 0.0, placement.dx * MM,
        0.0, sign, 0.0, placement.dy * MM,
        0.0, 0.0, sign, placement.dz * MM,
        0.0, 0.0, 0.0, 1.0,
    ])

    move_input = component.features.moveFeatures.createInput2(collection)
    move_input.defineAsFreeMove(matrix)
    component.features.moveFeatures.add(move_input)
    return _find_by_name(component, body.name)


def _find_by_name(component, name):
    for i in range(component.meshBodies.count):
        body = component.meshBodies.item(i)
        if body.name == name:
            return body
    return None


def scaled_footprint(lure, scaled_length):
    """The lure's outline-distance function, adjusted for any rescaling."""
    if lure.length <= 0:
        return lure.footprint_distance
    factor = scaled_length / lure.length
    if abs(factor - 1.0) < 1e-9:
        return lure.footprint_distance

    def distance(dx, dy):
        return lure.footprint_distance(dx / factor, dy / factor) * factor

    return distance


def scaled_vent_points(lure, settings, scaled_length):
    """Where each trapped pocket is, scaled with the lure. None if vents off."""
    if not settings.vents_enabled:
        return None
    nose = lure.nose_at_positive_x != settings.flip_lure
    points = lure.vent_points(nose)
    if not points:
        return None
    factor = scaled_length / lure.length if lure.length > 0 else 1.0
    return [(x * factor, y * factor) for x, y in points]


def resolve_parting(settings, lure, scaled_length):
    """Fill in the automatic parting offset, scaled with the lure."""
    if not getattr(settings, "parting_auto", True):
        return settings
    offset = lure.suggested_parting_mm
    if lure.length > 0:
        offset *= scaled_length / lure.length
    return dataclasses.replace(settings, parting_offset=offset)



RELIEF_CELL_DIVISOR = 7.0   # samples across the ramp, so the slope reads smooth
RELIEF_CELL_MIN = 0.35      # mm, floor on cell size
RELIEF_CELL_MAX = 1.00      # mm, ceiling on cell size
RELIEF_MAX_NODES = 45000    # the cutter is what the mesh booleans chew through


def apply_relief(component, plan, settings, lure_coords, lure_indices):
    """Recess the parting face everywhere except a land around each feature.

    The land follows the real silhouette, not a bounding box around it. A
    bounding box is hopeless on anything with spread limbs -- one real figure
    filled only 52% of its own box, so half the sealing land was wasted on the
    gaps between its arms and legs and the slope started nowhere near the
    shape.

    So: rasterise every feature onto a grid, sweep a distance field out from
    them, and build a cutter bounded by the resulting surface.
    """
    if not getattr(settings, "relief_enabled", False):
        return []
    depth = settings.relief_depth
    land = settings.relief_land
    if depth <= 0 or land < 0:
        return []

    run = layout_mod.relief_run(depth, settings.relief_angle)

    # Fine enough that the ramp is not a staircase, coarse enough that the
    # cutter stays a sane size.
    cell = run / RELIEF_CELL_DIVISOR if run > 0 else RELIEF_CELL_MAX
    cell = min(max(cell, RELIEF_CELL_MIN), RELIEF_CELL_MAX)

    pad = 2.0
    grid = relief.make_grid(
        -plan.block_x / 2 - pad, -plan.block_y / 2 - pad,
        plan.block_x / 2 + pad, plan.block_y / 2 + pad,
        cell, RELIEF_MAX_NODES,
    )
    mask = relief.new_mask(grid)
    # Measure to the features themselves rather than to the nodes they happen
    # to cover. See relief.Nearest: without this the ramp corrugates, and any
    # channel narrower than a cell can vanish from the relief altogether.
    nearest = relief.Nearest(grid)

    # Every cavity, as its true outline.
    for cavity in plan.cavities:
        coords = (
            meshgen.rotate_z_180(lure_coords) if cavity.rotated else lure_coords
        )
        relief.mark_triangles(
            grid, mask, coords, lure_indices,
            cavity.center.x, cavity.center.y, nearest,
        )

        if cavity.sprue is not None:
            if cavity.sprue_entry is not None:
                relief.mark_rect(
                    grid, mask,
                    (cavity.sprue.x + cavity.sprue_entry.x) / 2,
                    (cavity.sprue.y + cavity.sprue_entry.y) / 2,
                    abs(cavity.sprue_entry.x - cavity.sprue.x) + settings.funnel_diameter,
                    abs(cavity.sprue_entry.y - cavity.sprue.y) + settings.funnel_diameter,
                    nearest,
                )
            else:
                relief.mark_disc(grid, mask, cavity.sprue.x, cavity.sprue.y,
                                 settings.funnel_diameter / 2, nearest)

        for vent in cavity.vents:
            if vent.entry is not None:
                relief.mark_rect(
                    grid, mask,
                    (vent.point.x + vent.entry.x) / 2,
                    (vent.point.y + vent.entry.y) / 2,
                    abs(vent.entry.x - vent.point.x) + settings.vent_diameter,
                    abs(vent.entry.y - vent.point.y) + settings.vent_diameter,
                    nearest,
                )
            else:
                relief.mark_disc(grid, mask, vent.point.x, vent.point.y,
                                 settings.vent_diameter / 2, nearest)

    if plan.runner is not None:
        run_ = plan.runner
        relief.mark_rect(
            grid, mask, run_.x, (run_.y_from + run_.y_to) / 2,
            run_.diameter, abs(run_.y_to - run_.y_from) + run_.diameter,
            nearest,
        )

    for peg in plan.pegs:
        relief.mark_disc(grid, mask, peg.x, peg.y,
                         settings.peg_diameter / 2 + settings.peg_clearance,
                         nearest)

    field = relief.distance_field(grid, mask, nearest)

    notes = []
    cap = max(plan.top_thickness, plan.bottom_thickness) + depth + 5.0
    for half_name, sign in ((BOTTOM_NAME, -1.0), (TOP_NAME, 1.0)):
        half = _find_by_name(component, half_name)
        if half is None:
            continue
        try:
            coords, indices = relief.terrain(
                grid, field, land, depth, run, sign, cap
            )
            tag = "bottom" if sign < 0 else "top"
            cutter = _add_mesh(component, coords, indices, "relief_%s" % tag)
            _combine(component, half, [cutter], CUT, keep_tools=False)
        except Exception:
            notes.append(
                "Could not relieve the %s parting face; it is left flat."
                % ("bottom" if sign < 0 else "top")
            )

    return notes


def build(design, lure_body, settings):
    """Generate both mold halves. Returns a BuildResult."""
    # The component is made first so mesh preparation has somewhere to put its
    # working copy -- that way the copy is swept away on the next regenerate.
    setup_notes = []
    component = fresh_component(design, setup_notes)
    prepared, prep_notes = mesh_prep.prepare(component, lure_body, settings)
    lure = lure_analysis.analyze(prepared)

    # Scale the lure to the requested finished length.
    coords_mm = lure.coords_mm
    length = lure.length
    height = lure.height
    thickness = lure.thickness
    target = getattr(settings, "target_length", 0.0)
    if target and target > 0 and abs(target - length) > 1e-9:
        factor = target / length
        coords_mm = meshgen.scale(coords_mm, factor)
        length, height, thickness = length * factor, height * factor, thickness * factor

    dims = layout_mod.LureDims(
        length=length,
        height=height,
        thickness=thickness,
        nose_at_positive_x=lure.nose_at_positive_x,
    )

    settings = resolve_parting(settings, lure, length)
    plan = layout_mod.compute_layout(
        dims, settings,
        cavity_distance=scaled_footprint(lure, length),
        vent_points=scaled_vent_points(lure, settings, length),
    )
    warnings = setup_notes + list(prep_notes) + list(plan.warnings)

    slow = mesh_prep.slow_build_warning(
        len(lure.indices) // 3, len(plan.cavities)
    )
    if slow:
        warnings.append(slow)

    # Slide the lure so the chosen split lands on z = 0, where the halves meet.
    if abs(plan.parting_offset) > 1e-9:
        coords_mm = meshgen.translate(coords_mm, 0.0, 0.0, -plan.parting_offset)

    top_t = plan.top_thickness
    bottom_t = plan.bottom_thickness

    # --- the two block halves -------------------------------------------
    bottom_coords, bottom_idx = meshgen.box(
        0, 0, -bottom_t / 2, plan.block_x, plan.block_y, bottom_t
    )
    top_coords, top_idx = meshgen.box(
        0, 0, top_t / 2, plan.block_x, plan.block_y, top_t
    )
    bottom = _add_mesh(component, bottom_coords, bottom_idx, BOTTOM_NAME)
    top = _add_mesh(component, top_coords, top_idx, TOP_NAME)

    # --- relieve the parting face ----------------------------------------
    # Before anything is added to or cut from the halves. The cutter removes
    # everything above its surface, so run after the pegs are joined and it
    # would shear them off.
    warnings += apply_relief(component, plan, settings, coords_mm, lure.indices)

    # --- alignment pegs --------------------------------------------------
    peg_radius = settings.peg_diameter / 2
    hole_radius = (settings.peg_diameter + settings.peg_clearance) / 2

    pins = []
    holes = []
    for n, peg in enumerate(plan.pegs):
        # A lead-in chamfer on both halves of the pair: the pin tip tapers
        # in, the hole mouth flares out, so they find each other instead of
        # catching on a printed edge.
        chamfer = max(min(getattr(settings, "peg_chamfer", 0.0),
                          peg_radius * 0.6, settings.peg_height * 0.4), 0.0)

        pin_profile = [(0.0, 0.0), (peg_radius, 0.0)]
        if chamfer > 0:
            pin_profile += [
                (peg_radius, settings.peg_height - chamfer),
                (peg_radius - chamfer, settings.peg_height),
            ]
        else:
            pin_profile.append((peg_radius, settings.peg_height))
        pin_profile.append((0.0, settings.peg_height))

        pin_coords, pin_idx = meshgen.lathe(
            peg.x, peg.y, pin_profile, ROUND_SEGMENTS
        )
        pins.append(_add_mesh(component, pin_coords, pin_idx, "peg_pin_%d" % n))

        hole_depth = settings.peg_height + PEG_HOLE_RELIEF
        hole_profile = [(0.0, -0.01), (hole_radius + chamfer, -0.01)]
        if chamfer > 0:
            hole_profile.append((hole_radius, chamfer))
        hole_profile += [(hole_radius, hole_depth), (0.0, hole_depth)]

        hole_coords, hole_idx = meshgen.lathe(
            peg.x, peg.y, hole_profile, ROUND_SEGMENTS
        )
        holes.append(_add_mesh(component, hole_coords, hole_idx, "peg_hole_%d" % n))

    def channel(inner, entry, r_inner, r_entry, name, breakout):
        """A tapered channel on the parting plane from `inner` to `entry`.

        Works along X or Y, whichever the channel actually runs, so the same
        code serves an edge sprue, a runner gate and a sideways vent. Cut from
        BOTH halves so the two half-round grooves close into a full port.
        """
        along_x = abs(entry.x - inner.x) >= abs(entry.y - inner.y)
        start, finish = (
            (inner.x, entry.x) if along_x else (inner.y, entry.y)
        )
        if breakout:
            finish += SPRUE_BREAKOUT if finish > start else -SPRUE_BREAKOUT
        coords, idx = meshgen.cone(
            0.0, 0.0, start, r_inner, finish, r_entry, ROUND_SEGMENTS
        )
        if along_x:
            coords = meshgen.translate(
                meshgen.axis_z_to_x(coords), 0.0, inner.y, 0.0
            )
        else:
            coords = meshgen.translate(
                meshgen.axis_z_to_y(coords), inner.x, 0.0, 0.0
            )
        return _add_mesh(component, coords, idx, name)

    def breaks_out(entry):
        """True when the channel ends on a block face rather than a runner."""
        return (
            abs(abs(entry.x) - plan.block_x / 2) < 1e-6
            or abs(abs(entry.y) - plan.block_y / 2) < 1e-6
        )

    # --- sprues and vents -------------------------------------------------
    # An edge sprue is a tapered channel lying ON the parting plane, running
    # from the block's face into the cavity nose. It has to cut BOTH halves so
    # the two half-round grooves close up into a full round port -- that is how
    # real soft-plastic molds take an injector nozzle.
    edge_sprues = []
    top_sprues = []
    edge_vents = []
    vents = []
    for n, cavity in enumerate(plan.cavities):
        if cavity.sprue is not None:
            if cavity.sprue_entry is not None:
                # A gate into a runner keeps the sprue bore all the way; only
                # a channel ending on a block face opens out into a funnel.
                out = breaks_out(cavity.sprue_entry)
                edge_sprues.append(
                    channel(
                        cavity.sprue, cavity.sprue_entry,
                        settings.sprue_diameter / 2,
                        settings.funnel_diameter / 2 if out
                        else settings.sprue_diameter / 2,
                        "sprue_edge_%d" % n,
                        breakout=out,
                    )
                )
            else:
                coords, idx = meshgen.cone(
                    cavity.sprue.x, cavity.sprue.y,
                    0.0, settings.sprue_diameter / 2,
                    top_t, settings.funnel_diameter / 2,
                    ROUND_SEGMENTS,
                )
                top_sprues.append(
                    _add_mesh(component, coords, idx, "sprue_top_%d" % n)
                )

        for v, vent in enumerate(cavity.vents):
            if vent.entry is not None:
                edge_vents.append(
                    channel(
                        vent.point, vent.entry,
                        settings.vent_diameter / 2,
                        settings.vent_diameter / 2,
                        "vent_edge_%d_%d" % (n, v),
                        breakout=True,
                    )
                )
            else:
                vent_coords, vent_idx = meshgen.cylinder(
                    vent.point.x, vent.point.y, 0.0, top_t,
                    settings.vent_diameter / 2, ROUND_SEGMENTS,
                )
                vents.append(
                    _add_mesh(component, vent_coords, vent_idx,
                              "vent_top_%d_%d" % (n, v))
                )

    # --- the central runner, if there is one -----------------------------
    if plan.runner is not None:
        run = plan.runner
        far = run.y_to + (SPRUE_BREAKOUT if run.y_to > run.y_from else -SPRUE_BREAKOUT)
        bar_coords, bar_idx = meshgen.cone(
            0.0, 0.0, run.y_from, run.diameter / 2,
            far, settings.funnel_diameter / 2, ROUND_SEGMENTS,
        )
        bar_coords = meshgen.translate(
            meshgen.axis_z_to_y(bar_coords), run.x, 0.0, 0.0
        )
        edge_sprues.append(_add_mesh(component, bar_coords, bar_idx, "runner"))

    # --- one lure instance per cavity ------------------------------------
    # A cavity facing a central runner is turned end-for-end. That is a
    # rotation, not a mirror, so the lure does not come out the wrong hand.
    instances = []
    turned = meshgen.rotate_z_180(coords_mm)
    for n, cavity in enumerate(plan.cavities):
        source = turned if cavity.rotated else coords_mm
        placed = meshgen.translate(source, cavity.center.x, cavity.center.y, 0.0)
        instances.append(
            _add_mesh(component, placed, lure.indices, "cavity_%d" % n)
        )

    cut = adsk.fusion.MeshCombineOperationTypes.CutMeshCombineType
    join = adsk.fusion.MeshCombineOperationTypes.JoinMeshCombineType

    # --- combine ---------------------------------------------------------
    # isKeepToolBodies=False lets each boolean consume its tools, which is how
    # the scratch geometry disappears. A parametric timeline will not let us
    # delete those bodies afterwards, so they must be consumed, not deleted.
    # The lure instances are the exception: they cut both halves, so they are
    # kept for the first cut and consumed by the second.
    if pins:
        bottom = _combine(component, bottom, pins, join, keep_tools=False)
    through = edge_sprues + edge_vents
    if through:
        bottom = _combine(component, bottom, through, cut, keep_tools=True)
    bottom = _combine(component, bottom, instances, cut, keep_tools=True)

    top = _combine(component, top, holes + top_sprues + vents, cut, keep_tools=False)

    # Earlier cuts invalidated our handles, so fetch the shared tools again.
    if through:
        again = [_find_by_name(component, b.name) for b in through]
        top = _combine(component, top, again, cut, keep_tools=False)
    instances = [
        _find_by_name(component, "cavity_%d" % n) for n in range(len(plan.cavities))
    ]
    top = _combine(component, top, instances, cut, keep_tools=False)

    mesh_prep.tidy_up(component)

    # --- lay the halves out flat, cavities upwards -----------------------
    # This MUST happen before any merge. Merging replaces the two named bodies
    # with one, so a placement step running afterwards finds nothing to move
    # and silently leaves the mold closed.
    if getattr(settings, "lay_out_flat", True):
        try:
            _place(component, _find_by_name(component, BOTTOM_NAME),
                   plan.bottom_placement)
            _place(component, _find_by_name(component, TOP_NAME),
                   plan.top_placement)
        except Exception:
            warnings.append(
                "Could not lay the halves out flat; they are left in their "
                "closed position, one above the other."
            )

    # --- optionally fuse the halves into a single body --------------------
    if getattr(settings, "combine_halves", False):
        try:
            bottom = _find_by_name(component, BOTTOM_NAME)
            top = _find_by_name(component, TOP_NAME)
            if bottom is not None and top is not None:
                _combine(
                    component, bottom, [top],
                    adsk.fusion.MeshCombineOperationTypes.MergeMeshCombineType,
                    keep_tools=False,
                )
                merged = _find_by_name(component, BOTTOM_NAME)
                if merged is not None:
                    merged.name = COMPONENT_NAME
            else:
                warnings.append(
                    "Could not merge the halves into one body - one of them "
                    "was missing."
                )
        except Exception:
            warnings.append("Could not merge the halves into one body.")

    bottom = _find_by_name(component, BOTTOM_NAME)
    top = _find_by_name(component, TOP_NAME)
    for body in (bottom, top):
        if body is not None:
            _mark_generated(design, body)

    if bottom is not None and not bottom.isClosed:
        warnings.append(
            "The bottom half did not come out watertight. It may not slice "
            "correctly - check the lure mesh for self-intersections."
        )
    if top is not None and not top.isClosed:
        warnings.append(
            "The top half did not come out watertight. It may not slice "
            "correctly - check the lure mesh for self-intersections."
        )

    return BuildResult(bottom=bottom, top=top, layout=plan, warnings=warnings)
