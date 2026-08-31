"""Turning a user's imported lure mesh into clean numbers.

Validates the mesh, finds its natural orientation, re-expresses its triangles in
a canonical frame (length -> X, height -> Y, thickness -> Z, centred on the
origin), works out which end is the nose, and checks for undercuts.

The canonical frame is what layout.py and mold_builder.py both assume. Note that
the Fusion API works in CENTIMETRES; this module converts to millimetres on the
way out, because millimetres are what the dialog and layout maths use.
"""

import math

import adsk.core
import adsk.fusion

from . import mesh_repair
from . import parting
from . import relief
from . import orient

CM_TO_MM = 10.0
# Matches layout.SPRUE_INSET_FRACTION: where the gate meets the cavity.
SPRUE_INSET_FRACTION = 0.15

# Fraction of the length at each end sampled when deciding which end is the nose.
NOSE_SAMPLE_FRACTION = 0.15
# Resolution of the undercut ray grid.
UNDERCUT_SAMPLES_LONG = 40
UNDERCUT_SAMPLES_WIDE = 12
# Fraction of rays that must show an undercut before it is worth mentioning.
UNDERCUT_REPORT_THRESHOLD = 0.02
# Grid used for the silhouette distance field that peg placement consults.
# Fine enough to resolve a limb, coarse enough to stay quick.
FOOTPRINT_CELLS = 160
FOOTPRINT_PAD = 30.0
# Two vents closer together than this are treated as the same pocket.
VENT_SEPARATION_FRACTION = 0.12
MIN_VENT_SEPARATION = 4.0


class LureError(Exception):
    """Raised when a mesh cannot be used to build a mold."""


class OrientedLure:
    """A lure's triangles in the canonical frame, in millimetres."""

    def __init__(self, coords_mm, indices, length, height, thickness,
                 nose_at_positive_x, axes, center_mm, repaired_faces=0,
                 volume_mm3=0.0, suggested_parting_mm=0.0, parting_score=0.0,
                 centred_parting_score=0.0, footprint_grid=None,
                 footprint_field=None, footprint_mask=None):
        self.coords_mm = coords_mm
        self.indices = indices
        self.length = length
        self.height = height
        self.thickness = thickness
        self.nose_at_positive_x = nose_at_positive_x
        self.axes = axes
        self.center_mm = center_mm
        self.repaired_faces = repaired_faces
        self.volume_mm3 = volume_mm3
        self.suggested_parting_mm = suggested_parting_mm
        self.parting_score = parting_score
        self.centred_parting_score = centred_parting_score
        self._footprint_grid = footprint_grid
        self._footprint_field = footprint_field
        self._footprint_mask = footprint_mask
        self._vent_cache = {}

    def vent_points(self, nose_at_positive_x):
        """Every spot in the cavity where air ends up trapped, in local mm.

        One vent per cavity is not enough for anything with limbs: a figure
        with four raised arms and legs traps air at each one. Filling is
        simulated from the gate, and the last places to fill -- the local
        maxima of distance measured *through* the shape -- are what need
        venting.
        """
        key = bool(nose_at_positive_x)
        if key in self._vent_cache:
            return self._vent_cache[key]

        points = []
        if self._footprint_grid is not None and self.length > 0:
            gate_x = (1.0 if key else -1.0) * (
                self.length / 2 - SPRUE_INSET_FRACTION * self.length
            )
            separation = max(
                VENT_SEPARATION_FRACTION * max(self.length, self.height),
                MIN_VENT_SEPARATION,
            )
            field = relief.geodesic_field(
                self._footprint_grid, self._footprint_mask, [(gate_x, 0.0)]
            )
            points = relief.find_pockets(
                self._footprint_grid, self._footprint_mask, field, separation
            )

        self._vent_cache[key] = points
        return points

    def footprint_distance(self, dx, dy):
        """How far a point is from the lure's real outline, in mm.

        Coordinates are relative to the cavity centre. Falls back to a large
        value when no field was built, which reads as "nothing in the way".
        """
        if self._footprint_grid is None:
            return 1.0e6
        return relief.sample(self._footprint_grid, self._footprint_field, dx, dy)


def validate(mesh_body):
    """Check a mesh can be used at all.

    Inconsistent winding is NOT rejected here -- it is extremely common in
    downloaded STLs and is repaired in analyze(). Only genuine holes are fatal,
    because a boolean against an open surface has no well-defined inside.
    """
    if mesh_body is None:
        raise LureError("No lure body selected.")
    if not mesh_body.isClosed:
        raise LureError(
            "This mesh is not closed - it has holes or gaps, so there is no "
            "inside to subtract from the block.\n\n"
            "Fix it with Mesh > Prepare > Repair (use the Watertight option), "
            "then run this again."
        )


def _frame_center(coords, axes):
    """The world-space point that maps to the origin of the local frame."""
    center = [0.0, 0.0, 0.0]
    for axis in axes:
        values = [
            coords[i] * axis[0] + coords[i + 1] * axis[1] + coords[i + 2] * axis[2]
            for i in range(0, len(coords), 3)
        ]
        middle = (min(values) + max(values)) / 2.0
        for k in range(3):
            center[k] += middle * axis[k]
    return center


def _detect_nose(coords_mm, length):
    """The bulkier end of the lure is taken to be the nose."""
    band = length * NOSE_SAMPLE_FRACTION
    hi_sum = hi_n = 0.0
    lo_sum = lo_n = 0.0
    for i in range(0, len(coords_mm), 3):
        x, y, z = coords_mm[i], coords_mm[i + 1], coords_mm[i + 2]
        radius = math.hypot(y, z)
        if x >= length / 2 - band:
            hi_sum += radius
            hi_n += 1
        elif x <= -length / 2 + band:
            lo_sum += radius
            lo_n += 1
    hi = hi_sum / hi_n if hi_n else 0.0
    lo = lo_sum / lo_n if lo_n else 0.0
    return hi >= lo


def analyze(mesh_body):
    """Validate and re-express a lure mesh in the canonical frame.

    Uses orient.principal_axes rather than Fusion's orientedMinimumBoundingBox.
    The built-in is only approximate -- on a rotated test lure of known size
    100 x 30 x 12mm it reported 100 x 28.8 x 14.6, which would have seated the
    lure crooked in the mold and cut the cavity at an angle.
    """
    validate(mesh_body)

    # MeshBody.mesh is a PolygonMesh; triangleNodeIndices gives it to us
    # already triangulated, whatever the source file contained.
    mesh = mesh_body.mesh
    indices = list(mesh.triangleNodeIndices)
    world_mm = [c * CM_TO_MM for c in mesh.nodeCoordinatesAsDouble]

    # Downloaded meshes are frequently watertight but inconsistently wound.
    # Fusion reports those as isOriented=False with volume 0.0, and a boolean
    # against one silently produces a corrupt result rather than failing. Fix
    # the winding on our copy of the triangles; the user's body is untouched.
    repaired = 0
    if not mesh_body.isOriented:
        # Branching edges cannot be fixed by re-winding, and a boolean against
        # them corrupts the result rather than failing, so stop here instead.
        branching = mesh_repair.find_non_manifold_edges(indices)
        if branching:
            raise LureError(
                "This mesh has %d non-manifold edge%s - places where more than "
                "two faces meet along the same edge. There is no consistent "
                "inside and outside at those edges, so it cannot be cut out of "
                "a block.\n\n"
                "Fix it in Fusion: switch to the MESH tab, then "
                "Prepare > Repair, choose the Watertight repair type, and run "
                "it on this body. Then try again.\n\n"
                "(Mesh > Modify > Reverse Normal will not help - it flips every "
                "normal together and cannot mend the topology.)"
                % (len(branching), "" if len(branching) == 1 else "s")
            )
        indices, repaired = mesh_repair.make_consistent(world_mm, indices)

    volume_mm3 = mesh_repair.volume(world_mm, indices)
    if volume_mm3 <= 0.0:
        raise LureError(
            "This mesh encloses no volume, so it cannot be subtracted from a "
            "block. It may be a flat surface or otherwise degenerate."
        )

    axes, extents = orient.principal_axes(world_mm, indices)
    coords_mm = orient.project(world_mm, axes)
    length, height, thickness = extents

    # Where should the mold actually split? Not necessarily the middle: on a
    # turtle the middle cuts through the shell and strands the fins.
    columns = parting.ray_columns(coords_mm, indices)
    suggested, score = parting.best_parting_z(coords_mm, indices)
    centred = parting.score_at(columns, 0.0)

    cell = max(length, height) / FOOTPRINT_CELLS
    grid, field, footprint = relief.silhouette_field(
        coords_mm, indices, FOOTPRINT_PAD, max(cell, 0.2)
    )

    return OrientedLure(
        coords_mm=coords_mm,
        indices=indices,
        length=length,
        height=height,
        thickness=thickness,
        nose_at_positive_x=_detect_nose(coords_mm, length),
        axes=axes,
        center_mm=_frame_center(world_mm, axes),
        repaired_faces=repaired,
        volume_mm3=volume_mm3,
        suggested_parting_mm=suggested,
        parting_score=score,
        centred_parting_score=centred,
        footprint_grid=grid,
        footprint_field=field,
        footprint_mask=footprint,
    )


def find_undercuts(mesh_body, oriented):
    """Fraction of rays through the lure that hit more than two surfaces.

    A ray along the parting-plane normal should enter once and leave once. More
    crossings mean the shape re-enters itself in that direction, which is an
    undercut that will lock in a rigid mold.
    """
    ax, ay, az = oriented.axes
    center = [c / CM_TO_MM for c in oriented.center_mm]
    lx = oriented.length / CM_TO_MM
    ly = oriented.height / CM_TO_MM
    lz = oriented.thickness / CM_TO_MM

    start_offset = lz / 2 + max(lz, 1.0)
    direction = adsk.core.Vector3D.create(az[0], az[1], az[2])

    total = 0
    undercut = 0
    for i in range(UNDERCUT_SAMPLES_LONG):
        u = (-0.5 + (i + 0.5) / UNDERCUT_SAMPLES_LONG) * lx
        for j in range(UNDERCUT_SAMPLES_WIDE):
            v = (-0.5 + (j + 0.5) / UNDERCUT_SAMPLES_WIDE) * ly
            origin = adsk.core.Point3D.create(
                center[0] + ax[0] * u + ay[0] * v - az[0] * start_offset,
                center[1] + ax[1] * u + ay[1] * v - az[1] * start_offset,
                center[2] + ax[2] * u + ay[2] * v - az[2] * start_offset,
            )
            hits = mesh_body.calculateCollisionsWithRay(origin, direction)
            if not hits:
                continue
            total += 1
            if len(hits) > 2:
                undercut += 1

    if total == 0:
        return 0.0
    return undercut / total
