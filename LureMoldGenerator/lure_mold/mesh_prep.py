"""Getting a downloaded mesh fit to cut with.

Two jobs, both of which the user would otherwise do by hand in the Mesh
workspace before running this at all:

**Repair.** Downloaded STLs arrive with holes, inconsistent winding and
branching edges. Fusion's own one-touch repair fixes topology that re-winding
in Python cannot, so it is worth running first.

**Reduce.** Boolean cost scales with triangles times cavities. A 230k-triangle
model takes seconds per cavity; six of them does not finish in a minute. The
cavity surface is limited by the printer anyway -- on a 100mm lure, 25k
triangles is roughly a 0.9mm facet, already finer than a 0.4mm nozzle resolves.

Both run on a *copy* inside the mold component, so the user's own body is never
altered and the copy disappears on the next regenerate.
"""

import adsk.core
import adsk.fusion

WORKING_NAME = "source mesh (prepared)"

# Past roughly this many triangles across all cavities, the booleans crawl.
SLOW_BUILD_TRIANGLES = 60000


def _find(component, name):
    for i in range(component.meshBodies.count):
        body = component.meshBodies.item(i)
        if body.name == name:
            return body
    return None


def prepare(component, mesh_body, settings):
    """Return (body_to_use, notes).

    Falls back to the original body silently if a step is not needed, and
    reports honestly if one is attempted and fails rather than pretending it
    worked.
    """
    notes = []
    triangles = mesh_body.mesh.triangleCount

    want_repair = getattr(settings, "auto_repair", True) and not (
        mesh_body.isClosed and mesh_body.isOriented
    )
    target = int(getattr(settings, "target_faces", 0) or 0)
    want_reduce = (
        getattr(settings, "reduce_faces", True) and target > 0 and triangles > target
    )

    if not want_repair and not want_reduce:
        return mesh_body, notes

    # Work on a copy so the user's mesh is left exactly as they imported it.
    mesh = mesh_body.mesh
    working = component.meshBodies.addByTriangleMeshData(
        list(mesh.nodeCoordinatesAsDouble), list(mesh.triangleNodeIndices), [], []
    )
    working.name = WORKING_NAME

    if want_repair:
        try:
            features = component.features.meshRepairFeatures
            repair_input = features.createInput(working)
            repair_input.meshRepairType = (
                adsk.fusion.MeshRepairTypes.OneTouchFixMeshRepairType
            )
            features.add(repair_input)
            working = _find(component, WORKING_NAME) or working
            notes.append("Repaired the mesh before cutting.")
        except Exception:
            notes.append(
                "Automatic mesh repair failed. If the cavity comes out wrong, "
                "run Mesh > Prepare > Repair on the lure by hand."
            )

    if want_reduce and working.mesh.triangleCount > target:
        before = working.mesh.triangleCount
        try:
            features = component.features.meshReduceFeatures
            reduce_input = features.createInput(working)
            reduce_input.meshReduceTargetType = (
                adsk.fusion.MeshReduceTargetTypes.FaceCountMeshReduceTargetType
            )
            reduce_input.meshReduceMethodType = (
                adsk.fusion.MeshReduceMethodTypes.AdaptiveReduceType
            )
            reduce_input.facecount = target
            features.add(reduce_input)
            working = _find(component, WORKING_NAME) or working
            notes.append(
                "Reduced the lure from %s to %s triangles for the booleans."
                % (f"{before:,}", f"{working.mesh.triangleCount:,}")
            )
        except Exception:
            notes.append(
                "Could not reduce the mesh from %s triangles, so this build "
                "may be slow." % f"{before:,}"
            )

    return working, notes


def tidy_up(component):
    """Get the working copy out of the way once its triangles have been read.

    A body produced by a feature cannot be deleted while the timeline depends
    on it, so hiding is the fallback. Either way it lives inside the mold
    component and is swept away on the next regenerate.
    """
    working = _find(component, WORKING_NAME)
    if working is None:
        return
    try:
        working.deleteMe()
    except Exception:
        try:
            working.isLightBulbOn = False
        except Exception:
            pass


def slow_build_warning(triangles, cavities):
    """Warn before a build that is going to take a long time."""
    total = triangles * cavities
    if total <= SLOW_BUILD_TRIANGLES:
        return None
    return (
        "%s triangles across %d cavit%s - this build may take a while. Lower "
        "the triangle limit under Mesh preparation to speed it up."
        % (f"{total:,}", cavities, "y" if cavities == 1 else "ies")
    )
