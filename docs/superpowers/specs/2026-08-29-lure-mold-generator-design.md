# Fishing Lure Mold Generator — Design Spec

**Date:** 2026-08-29 (last revised 2026-08-30)
**Status:** Implemented and in use. Sections marked *Changed during implementation* record where measurement overturned the original design; those notes are kept deliberately, because the mistakes are instructive.
**Target:** Autodesk Fusion add-in (verified against Fusion **2704.1.53**)

---

## 1. Goal

A Fusion add-in that turns a dropped-in lure model into a finished, printable two-part injection mold, automatically.

The user does two things: import their lure mesh, and press Generate. Everything else — orientation, sizing, cavity cutting, splitting, alignment pegs, injection sprue, vents — is computed.

Target process is **soft plastic (plastisol)** hand injection. Output is **printable mesh halves**, ready to export as STL/3MF.

## 2. User workflow

1. User imports a lure model (STL/OBJ/3MF) into a Fusion document.
2. User opens **Lure Mold Generator** from the toolbar.
3. The dialog opens. If the document contains exactly one mesh body, it is pre-selected. All settings are pre-filled with defaults (or with the user's last-used settings for this lure, if it has been generated before).
4. As the user edits any value, a live ghost overlay redraws instantly showing mold outline, cavity footprints, peg positions, sprue and vent positions, plus a text readout of the resulting mold size.
5. User presses **Generate**. The mold is built (seconds).
6. To change something: reopen the command. Every field is pre-filled, the lure is still selected. Change values, press Generate. The previous mold is replaced.

## 3. Non-goals (explicitly out of scope for v1)

- **Plastisol shrink compensation.** Dropped by user request — not something lure makers account for.
- **Curved / non-planar parting surfaces.** The parting surface is a flat plane, though its *height* is now chosen automatically (§6.6).
- **Hard resin baits, lead jigs, wire and hook slots.** Soft plastic only.
- **Runners for more than two columns.** The central runner (§6.4) needs exactly two columns facing each other. Wider grids fall back to edge injection.
- **Automatic export.** Exporting is left to Fusion's own Save As Mesh; the mold is grouped into one component so that writes a single file.
- **`CustomFeature` timeline integration.** See §7 — deferred to phase 2, but the code is structured so it can be added without a rewrite.

## 4. Architecture

```
LureMoldGenerator/
├── LureMoldGenerator.py          entry point: run() / stop()
├── LureMoldGenerator.manifest
└── lure_mold/                    a package, not flat modules: Fusion add-ins
    ├── layout.py                 PURE MATH — block, cavity, peg, sprue, vent, runner
    ├── meshgen.py                PURE MATH — watertight box/cylinder/cone
    ├── orient.py                 PURE MATH — principal axes by area-weighted PCA
    ├── parting.py                PURE MATH — where to split, by ray casting
    ├── relief.py                 PURE MATH — shape-following face relief
    ├── mesh_repair.py            PURE MATH — winding repair, non-manifold detection
    ├── lure_analysis.py          validate, orient, detect nose, find undercuts
    ├── mesh_prep.py              Fusion's own repair and reduce, on a copy
    ├── mold_builder.py           builds the mold in Fusion
    ├── preview.py                CustomGraphics live overlay
    ├── store.py                  settings persisted on the lure body
    └── ui_command.py             command definition and event handlers
tests/                            216 tests, stdlib unittest, no pytest,
├── test_layout.py                all run OUTSIDE Fusion
├── test_meshgen.py
├── test_orient.py
├── test_parting.py
├── test_mesh_repair.py
└── test_module_contracts.py
```

The package rather than flat modules is deliberate: every Fusion add-in shares
one Python namespace, so a bare `layout.py` would collide with any other add-in
that has one.

**The critical boundary is `layout.py`.** It imports nothing from Fusion. It takes plain numbers in (lure dimensions, margins, grid size, peg count, sprue size) and returns plain data out (block dimensions, cavity centers, peg positions, sprue and vent positions). All the fiddly geometry logic lives there, which means it is testable with ordinary pytest and no CAD in the loop. That is where the bugs will be, so that is where the tests go.

Five modules now hold that pure core: `layout`, `meshgen`, `orient`, `parting` and `mesh_repair`. A test enforces that none of them ever grows an `adsk` import.

Everything else is a thin shell: `lure_analysis`, `mesh_prep` and `mold_builder` translate between Fusion objects and plain numbers; `ui_command` collects values; `preview` draws.

**The four Fusion-side modules are a known blind spot.** They import `adsk`, so no unit test can import them, and a rename once shipped a stale `plan.half_thickness` that crashed the dialog on open while all 112 tests passed. `test_module_contracts.py` closes that: it parses those files as source and checks every attribute taken off a plan / cavity / settings object against the real dataclass fields.

## 5. Pipeline

1. **Prepare** the mesh (§6.8) — Fusion's own repair, then reduction to a triangle limit, both on a copy so the user's body is untouched.
2. **Validate** — `isClosed` is fatal; inconsistent winding is *repaired*, not rejected, because it is near-universal in downloaded STLs. Branching (non-manifold) edges are fatal and reported with a count, since no amount of re-winding fixes them. Never attempt booleans on a bad mesh: Fusion does not fail on one, it silently returns a corrupt result.
3. **Orient** — `orient.principal_axes` computes the lure's natural axes from an
   exact area-weighted surface covariance. Map them: **longest → X (length),
   middle → Y (height), shortest → Z**.

   *Changed during implementation.* `orientedMinimumBoundingBox` was specified
   here, but measurement showed it is only approximate: on a test lure of known
   size 100 x 30 x 12mm it returned 100 x 28.8 x 14.6 — a box 17% larger than
   the true one, meaning it had picked the wrong axes. That would seat the lure
   crooked and cut the cavity at an angle. The PCA replacement recovers
   100.00 x 30.00 x 12.00 exactly.

   Sorting the axes by extent is deliberate: it makes the parting plane normal
   to the lure's *shortest* axis, which is how real lure molds split (a
   paddle-tail swimbait splits into left and right halves) and which minimises
   undercut.
4. **Scale** to the user's target length, if specified. Uniform scale only.
5. **Detect nose** — compare cross-sectional bulk in the outer 15% at each end of X; the bulkier end is the nose. A **Flip lure direction** checkbox overrides this, because the heuristic will sometimes be wrong.
6. **Undercut check** — cast a grid of rays along Z through the lure using `MeshBody.calculateCollisionsWithRay`. Any ray with more than 2 intersections indicates an undercut that will lock in a rigid mold. Warn, do not block — soft plastic often releases from mild undercuts anyway.
7. **Choose the parting plane** (§6.6), then **compute layout** — `layout.py`, pure math (§6).
8. **Build every part as a watertight triangle mesh** via `meshgen` — block halves, peg pins, peg holes, sprue cones, vent risers.

   *Changed during implementation.* The spec called for parametric solids followed by `TessellateFeatures`. Generating the primitives directly is simpler and strictly more robust: no sketches, no extrudes, no taper API, the geometry becomes pure testable maths, and a box is exact either way. `TessellateFeatures` also refuses bodies created inside a `BaseFeature`, which the solid route required.
9. **Mesh-boolean** using `MeshCombineFeatures` with `CutMeshCombineType` / `JoinMeshCombineType` and `EnhancedMeshCombineAlgorithmType`. Tool bodies are passed as a plain Python list (not an `ObjectCollection`), and `isKeepToolBodies=False` lets each boolean *consume* its scratch geometry — a parametric timeline will not permit deleting a body a feature depends on, so consumption is the only way to leave the document clean.
10. **Organize** — everything is built inside a component named `Lure Mold` holding `Mold Bottom` and `Mold Top`. Regenerating deletes that whole component and rebuilds, which is why regeneration needs no body deletion.
11. **Lay out flat** (§6.7) — move both halves side by side, cavities upwards.

**Key simplification:** the lure is never split. Subtracting the *whole* lure from each half-block automatically gives each half exactly its own cavity.

## 6. Layout math (`layout.py`)

After orientation the lure bounding box is `L` (length, X) x `W` (height, Y) x `T` (thickness, Z).

### 6.1 Block and cavities

```
cell_x = L + 2 * margin_x
cell_y = W + 2 * margin_y
cell_z = T + 2 * margin_z

block_x = N * cell_x          # N = columns, along X
block_y = M * cell_y          # M = rows, along Y
block_z = cell_z

top_thickness    = (T/2 - parting_offset) + margin_z
bottom_thickness = (T/2 + parting_offset) + margin_z   # see 6.6
```

The parting plane is at `z = 0`. That is where the halves meet, but it is *not* generally the lure's mid-thickness: the lure is slid by `-parting_offset` so the chosen split lands there (§6.6). The two halves therefore usually differ in thickness, while `block_z` is unaffected.

Cavity centers, for `i` in `0..N-1`, `j` in `0..M-1`:

```
cx = -block_x/2 + cell_x * (i + 0.5)
cy = -block_y/2 + cell_y * (j + 0.5)
cz = 0
```

Consequence worth knowing: the wall between two adjacent cavities is `2 * margin` (each cell contributes its own margin), while the wall from a cavity to the outside of the block is `1 * margin`. This follows directly from "margin means clearance around each lure" and is intentional.

### 6.2 Alignment pegs

Candidate positions, in priority order:

1. The four block corners, inset from each edge by `peg_radius + edge_clearance`.
2. The midpoints of the four block edges, inset the same way.
3. The interstitial points in the gaps between cavities.

Selection rules:

- **count == 2** (the default): two **diagonally opposite corners**.

  *Changed during implementation.* This was originally the two edge-midpoints
  on the block's longest dimension. That is exactly where an edge sprue and
  vent break out of the end faces, so a 2-peg mold put a peg straight through
  its own fill port every time. Opposite corners are just as far apart and
  clear of the ports.
- **otherwise**: walk the priority list in order (corners, then edge midpoints,
  then the corridors between cavities).

Every candidate is rejected if it collides with any cavity footprint (the lure XY bounding box plus clearance), **any sprue, or any vent**. Using the bounding box rather than the true silhouette is deliberately conservative.

*Changed during implementation.* Cavity collision was a bounding-box test, and it was far too blunt: a figure whose silhouette fills 52% of its box leaves the corners wide open, yet the box calls them occupied, so every candidate was rejected and the mold came out with **no pegs at all**. `compute_layout` now takes an optional `cavity_distance(dx, dy)` -- distance to the real outline in cavity-local coordinates -- supplied from the same silhouette field the relief uses and cached with the analysis. Without it the bounding box is still the fallback, so the pure module stays testable on its own.

An edge channel is treated as a rectangular band along X between the cavity and the block face, which over-estimates the tapered end; a top-entry port is treated as a disc. The sprue and vent checks were specified from the start but missed in the first implementation, which is how pegs came to be driven through fill ports.

Geometry: a pin on the **bottom** half, `peg_height` tall, extruded up from the parting plane. A matching hole in the **top** half of diameter `peg_diameter + peg_clearance`, depth `peg_height + 0.5mm` for relief.

### 6.2.1 Internal constants

These are not exposed in the dialog. They are fixed in `layout.py` and are listed here so the implementation has no undefined values:

| Constant | Value | Meaning |
|---|---|---|
| `edge_clearance` | 2.0mm | Minimum material between a peg and the outside of the block |
| `cavity_clearance` | 1.0mm | Minimum gap between a peg and any cavity footprint |
| `sprue_inset` | 15% of `L` | How far inboard from the nose tip the sprue sits |
| `vent_inset` | 5% of `L` | How far inboard from the tail tip the vent sits |
| `peg_hole_relief` | 0.5mm | Extra hole depth beyond `peg_height` |
| `LAYOUT_GAP` | 10.0mm | Space between the halves when laid out flat |

### 6.3 Injection sprue

**Reverted to edge entry after seeing real molds.** The reasoning below was
wrong, and is kept because the mistake is instructive.

Three modes, set in the dialog:

- **Edge (default)** — a tapered channel lying ON the parting plane, running
  from the block's face into the cavity nose. It cuts BOTH halves, so the two
  half-round grooves close into a full round port that takes an injector
  nozzle. This is what commercial soft-plastic molds actually do.
- **Top** — the vertical funnel described below. Works at any grid position, so
  it is the fallback for cavities an edge channel cannot reach.
- **None** — no injection hole at all.

An edge channel only escapes if nothing sits between the cavity and the face it
runs toward. With every lure facing the same way that is true only for the
outermost column, so a single column (any number of rows) injects entirely from
the edge. With more columns, the blocked cavities fall back to top entry and
the user is warned. A central runner spine would remove that limit; it is not
implemented.

#### The original, superseded reasoning

Edge entry (a half-round channel along the parting plane out to the mold's side) is the traditional approach, and it is what was originally agreed. It does not survive the N x M grid requirement: cavities are tiled in X, so any cavity that is not in an outer column has no straight path to a block edge — its channel would run directly into its neighbour. Edge entry only works for `N <= 2`.

**Top entry works for any grid position**, so v1 uses it:

- A vertical tapered cone in the **top half only**, from the top face down to the parting plane at `z = 0`.
- Entrance diameter `funnel_diameter` at the top face, tapering to `sprue_diameter` at the parting plane. The taper lets the injector nozzle seat, and lets the sprue release when the mold opens.
- Positioned at the nose end of each cavity, inset inboard from the nose tip by `sprue_inset` (default 15% of `L`, clamped so it stays over the lure).
- Running it all the way down to `z = 0` guarantees it connects to the cavity regardless of how thin the lure is at that point.

The mold is injected top-down while clamped. This is a normal technique for printed plastisol molds.

Edge entry is a reasonable phase-2 addition as a mode switch, constrained to `N <= 2`.

### 6.4 Central runner

Real multi-cavity soft-plastic molds do not give every cavity its own port.
They run one channel down the middle, gate each cavity into it, and feed the
lot from a single sprue -- the whole shot then pulls out as one tree.

Selected with **Injection port > Runner**. It needs **exactly two columns**, so
the cavities can face each other across the channel; any other column count
warns and falls back to edge injection.

- The runner lies on the parting plane at `x = 0`, running the full block
  depth in Y and breaking out of one Y face, where it opens into the funnel.
- Column 0 keeps the lure's base orientation; column 1 is turned 180 degrees
  about Z so its nose also faces the middle. That is a **rotation, not a
  mirror** -- mirroring would produce a lure of the wrong hand.
- Each cavity gates into the runner rather than a block face, so its channel
  keeps the sprue bore all the way instead of opening into a funnel.
- Every tail now points outward, so all vents reach an outer X face.

### 6.5 Vents lie on the parting line

A vent is routed along the split face wherever possible, in preference order:
out of the tail's own X face, then sideways out of the nearest Y face, and only
if the cavity is boxed in on every side does it fall back to a vertical riser
(with a warning). A channel on the split opens up when the mold does and can be
cleaned; a riser is a blind hole full of set plastic.

The injection mode governs the *sprue*, not the vent -- vents lie on the
parting line even with top injection.

### 6.5.1 One vent per trapped pocket

*Changed during implementation.* Each cavity used to get a single vent at the
tail. That is wrong for anything with limbs: a figure with four raised arms and
legs traps air at each of them, and three would have come out short.

Filling is simulated instead. A geodesic field is swept out from the gate
through the cavity silhouette -- distance *through* the shape, so a limb that
doubles back is further than it looks -- and the local maxima of that field are
the last places to fill, which is precisely where air ends up. Each becomes a
vent.

**A local maximum alone is not a pocket.** Where a limb passes close to the
gate, the fill field forms a ridge and every node along it is a maximum in its
own 8-neighbourhood; a real figure produced a chain of five down one arm. What
marks a genuine pocket is **topographic prominence** -- how far you must
descend from a peak before you can climb to higher ground. A limb tip has tens
of millimetres of it, a ridge ripple has 1.4mm. Prominence is computed by
flooding downwards with a union-find, and the highest point of each shell is
given unbounded prominence, as convention requires.

The bar is `max(0.04 x size, 2.5mm)` -- **absolute, not a fraction of the
deepest point.** A fraction was tried and dropped both hands off a figure: a
raised arm passing near the gate has a shallow basin (6mm) yet still traps air,
while the deepest pocket was 75mm away.

Maxima are thinned so none land within `max(0.07 x size, 3mm)` of each other,
and the thinning is applied to the *settled* positions: a raw maximum sits at
the far **corner** of a limb tip rather than its centre, so each peak is
replaced by the centroid of the near-maximal nodes around it. Checking
separation before that settling let two vents end up 1.1mm apart on a real
model. Settling floods the connected near-maximal patch rather than averaging
over a radius, so it stops at the neck of a limb instead of drifting back into
the body.

The separation is deliberately small: at 0.12 the two feet of a real figure sat
9.3mm apart and one was silently discarded as a duplicate.

Each vent routes to the nearest block face it can reach; `Cavity.vents` is a
list, with `vent`/`vent_entry` kept as properties for the primary one.

### 6.6 Where the mold splits

The parting plane was originally fixed at the middle of the lure's bounding
box. That is fine for a roughly symmetric worm and wrong for anything with a
flat feature at one level. On a turtle it slices through the shell dome and
leaves the fins and head entirely below the plane, sealed into the bottom half
with material closed over them -- they can never release.

**The plane is now chosen by measurement.** A grid of rays is fired down
through the lure and each one's solid spans are recorded. For a candidate
height `h`, a ray is satisfied when its solid is a *single span containing h*:
the part above `h` then lifts out upwards and the part below drops out
downwards. A span lying wholly on one side of `h` is a trapped feature. The
height scoring best wins, ties breaking toward the middle.

Casting the rays once and scoring every candidate against the same data keeps
this cheap: 108k triangles, 60x60 rays, 80 candidate heights, 0.5 s.

Measured on a real turtle STL: a centred split satisfies **62.1%** of rays; the
chosen split, +10.9mm toward the plastron where the fins lie, satisfies
**93.2%**. The remaining 7% is genuine undercut that no flat plane can fix.

Consequence: **the two halves are no longer the same thickness.**
`top_thickness = (T/2 - offset) + margin_z` and
`bottom_thickness = (T/2 + offset) + margin_z`; their sum is unchanged, so
`block_z` still equals `T + 2 * margin_z`. Each half is laid out flat by
lifting it by its own thickness.

The offset is clamped to +/- T/2 so there is always lure on both sides, and it
can be set by hand -- **Parting plane > Find the best split automatically**,
unticked, then type an offset. The automatic value is mirrored into that field
so switching to manual starts from where auto left off.

### 6.7 Laying the halves out flat

The halves are built in their mating position, one above the other, because
that is the only position in which the cavity cut is correct. Once cut, they
are moved apart so the user can actually see what was made -- which is also the
orientation you print in.

The bottom half occupies `z` in `[-half, 0]` with its cavity already facing up
at `z = 0`, so it only needs lifting by `half`.

The top half occupies `[0, half]` with its cavity facing *down*. Rotating it
180 degrees about X puts it in `[-half, 0]` with the cavity facing up, after
which the same lift applies. Rotating about X rather than Y is the natural
"open the book" motion for a mold parting on the XY plane.

Both are then offset along Y by `+/-(block_y + LAYOUT_GAP) / 2`, leaving them
symmetric about the origin, resting on `z = 0`, cavities and pegs upwards.

`LAYOUT_GAP` is 10mm (internal constant, §6.2.1).

This is applied with `MoveFeatures.createInput2` plus `defineAsFreeMove`, and
is controlled by the **Lay halves out flat** checkbox, which defaults to on.
Turning it off leaves the halves closed, for checking the fit.

### 6.9 Parting face relief

The halves only need to seal near the cavity and the ports. Relieving the rest
leaves a narrow sealing land, which prints far better than a face that must be
dead flat across its whole area.

*Changed during implementation.* The first version kept a **bounding box**
around each feature and cut a plate minus those boxes. It was visibly wrong on
anything with spread limbs: a real figure filled only 52% of its own bounding
box, so half the sealing land sat in the gaps between its arms and legs and
the slope began nowhere near the shape.

It is now a **height field**, in `relief.py`. Every feature is rasterised onto
a grid -- cavities by their true projected silhouette, channels and pegs
analytically -- a distance field is swept out from them with a two-pass chamfer
transform, and the face height at each node follows from that distance: flat
within the land, then ramping down over `relief_run(depth, angle) =
depth / tan(angle)` to the recess depth.

The cutter is the solid bounded by that surface and a cap well clear of it, so
it is subtracted from a plain block half and nothing ever has zero thickness.
It is built for the bottom half and mirrored for the top, because deriving one
winding and reflecting it is far less error-prone than keeping two consistent
by hand.

The distance transform is an **exact** Euclidean one (Felzenszwalb and
Huttenlocher's parabola-envelope method), not the chamfer sweep used at first.
A chamfer measures diagonals badly -- distance varied by 1.04mm around a circle
-- which gave the contours an octagonal bias and put visible ridges down the
slope. Exact is barely slower and the ramp comes out smooth.

Cell size is the ramp width over three, clamped to 0.35-1.20mm, with a hard
ceiling on node count. On a real mold that took the finished body from ~32k to
~96k triangles -- the price of a land that actually follows the shape.

**Ordering matters:** relief runs on the plain block halves, before the pegs,
ports and cavities go in. The cutter removes everything above its surface, so
running it after the pins are joined would shear them off. A source-order test
asserts this.

The angle is clamped to 5-85 degrees, outside which the ramp is either
unbounded or a vertical step. Because the land plus its ramp must fit inside
the wall thickness, the layout warns when it does not -- otherwise the setting
appears to do nothing.

### 6.8 Mesh preparation

Two steps the user would otherwise do by hand in the Mesh workspace, both run
on a **copy** inside the mold component so the imported body is never altered
and the copy is swept away on the next regenerate.

**Repair** runs Fusion's `OneTouchFixMeshRepairType`. It mends topology that
re-winding in Python cannot -- notably the branching edges that make a mesh
unusable however its triangles are flipped.

**Reduce** uses `MeshReduceFeatures` with `FaceCountMeshReduceTargetType` and
`AdaptiveReduceType`, down to a user-set triangle limit, default **25,000**.

Reasoning for that default. Boolean cost scales with triangles times cavities:
measured on a 25,000-triangle turtle, one cavity took 13.5 s and six did not
finish inside a minute. Against that, a 100mm lure has roughly 10,000 mm2 of
surface, so 25,000 triangles is about a 0.9mm facet -- already finer than a
0.4mm nozzle resolves. 25,000 therefore suits one or two cavities; a six-cavity
runner mold wants nearer 10,000. Rather than guess, the build warns when
triangles times cavities passes 60,000.

Neither step is silent: what was repaired and what the triangle count went from
and to are reported in the result.

## 7. Regeneration and live preview

**Live, while typing** — `executePreview` plus `CustomGraphics` (both verified present). Every value change redraws a ghost overlay: mold outline, cavity footprints, peg positions, sprue and vent positions, and a size readout. This is a wireframe overlay, not cut geometry, so it is effectively instant.

**On Generate** — the mesh boolean runs. This is the only slow step and it is unavoidable; cutting a real lure mesh from a block costs seconds no matter what triggers it.

The split is deliberate: everything a user actually sits and tunes (wall thickness, grid, peg count, sprue placement) is in the live layer. Only the final cut is in the slow layer.

**Re-editing** — settings are stored as document attributes on the lure mesh body (`MeshBody.attributes`, verified). Reopening the command pre-fills every field from those attributes with the lure still selected. Generating again deletes the previously generated bodies (tracked by attribute) and rebuilds.

A `CustomFeature` would put the mold in the timeline as a double-clickable feature. It is deferred: a full recompute costs the same seconds either way, and mesh features inside a custom-feature compute handler are the least-proven corner of the API. The pipeline is a pure function of (lure, settings), so adding it later is additive.

## 8. The dialog

Native `CommandInputs` — chosen over an HTML palette for unit-aware value inputs (the user can type `10 mm` or `0.4 in`), native body selection, and the `executePreview` event.

| Group | Input | Type | Default |
|---|---|---|---|
| Lure | Lure body | selection (mesh body) | auto-selected if exactly one |
| Lure | Target length | value (mm) | detected length |
| Lure | Flip lure direction | bool | off |
| Grid | Columns (N) | integer spinner | 1 |
| Grid | Rows (M) | integer spinner | 1 |
| Margins | Margin X | value (mm) | 10 |
| Margins | Margin Y | value (mm) | 10 |
| Margins | Margin Z | value (mm) | 10 |
| Pegs | Peg count | integer spinner | 2 |
| Pegs | Peg diameter | value (mm) | 5.0 |
| Pegs | Peg height | value (mm) | 5.0 |
| Pegs | Peg clearance | value (mm) | 0.2 |
| Injection | Sprue diameter | value (mm) | 4.0 |
| Injection | Funnel diameter | value (mm) | 8.0 |
| Injection | Vents | bool | on |
| Injection | Runner diameter | value (mm) | 6.0 |
| Mesh preparation | Repair the mesh first | bool | on |
| Mesh preparation | Reduce the triangle count | bool | on |
| Mesh preparation | Triangle limit | integer spinner | 25000 |
| Injection | Vent diameter | value (mm) | 1.0 |
| Parting plane | Find the best split automatically | bool | on |
| Parting plane | Split offset from centre | value (mm) | auto-filled |
| Parting face relief | Recess the face away from features | bool | on |
| Parting face relief | Flat band around features | value (mm) | 4.0 |
| Parting face relief | Recess depth | value (mm) | 2.0 |
| Parting face relief | Slope angle | value (deg) | 50 |
| Output | Lay halves out flat | bool | on |
| Output | Merge halves into one body | bool | on |
| — | Result readout | text box (read-only) | live |

Readout format: `Mold: 124.0 x 86.0 x 31.0 mm — 6 cavities`

## 9. Error handling

Every failure produces a specific, actionable message. No silent failures, no generic "operation failed".

| Condition | Behaviour |
|---|---|
| No mesh body in document | Block generate, explain what to import |
| Mesh not closed | **Block** - name the defect, point at Mesh > Prepare > Repair |
| Inconsistent winding | **Repair silently** - near-universal in downloaded STLs |
| Non-manifold (branching) edges | **Block** - report the count; re-winding cannot fix topology |
| Zero enclosed volume | Block - nothing to subtract |
| Undercut detected | Warn, allow - soft plastic often releases anyway |
| Parting plane leaves much of the lure trapped | Report the percentage that releases |
| Peg does not fit without collision | Place as many as fit, report how many and why |
| Cavity cannot reach a face to inject | Fall back to top entry, warn how many |
| Cavity boxed in on every side for venting | Fall back to a riser, warn it needs drilling |
| Margins so small the walls are unprintable | Warn below 2mm |
| Vent diameter below 0.8mm | Warn about FDM printability |
| Triangles x cavities large enough to crawl | Warn before building, suggest a lower limit |
| Part Design document (one component only) | Fall back to building in the root |
| Half comes out not watertight | Report it - the mold will not slice |

## 10. Testing

**216 tests, stdlib `unittest`, no dependencies, all outside Fusion.**

| File | Covers |
|---|---|
| `test_layout.py` | block sizing, cavities, pegs, ports, runner, vents, parting offset, flat layout |
| `test_meshgen.py` | primitives, watertightness, winding, axis conversion, reversed channels |
| `test_orient.py` | principal axes, extents, projection |
| `test_parting.py` | ray casting, span detection, split scoring |
| `test_mesh_repair.py` | winding repair, volume, non-manifold detection |
| `test_module_contracts.py` | Fusion-side modules match the dataclasses; pure modules stay pure |

Two habits earned their keep. **Watertightness is asserted by directed-edge
counting** rather than eyeballing: every directed edge must appear exactly
once, which catches holes, duplicated faces and flipped winding in one check.
And **a guard test is re-run against deliberately broken code** to prove it is
not vacuous - done for the module-contract test by reintroducing the exact
rename it was written to catch.

**Geometry is validated in live Fusion through the MCP connection.** Generate,
then assert on body count, per-body volume, `isClosed` on both halves, and that
removed volume matches prediction. A mesh cut has matched a predicted volume to
four decimal places.

**Manual** - real lure STLs through the whole flow. A turtle, a Mjolnir and a
senko have each exposed a different class of bug.

## 11. Risks

| Risk | Mitigation |
|---|---|
| Real-world lure STLs are non-manifold or leaky | Validate up front, auto-repair, refuse clearly rather than producing garbage |
| Nose/tail heuristic guesses wrong | Flip checkbox in the UI |
| Undercuts make a mold that will not release | Ray-cast detection with a warning |
| Mesh boolean is slow on dense lures | Reduced to a triangle limit before building (6.8); warned when still large |
| Peg auto-placement collides in dense grids | Conservative bbox collision test, partial placement with a report |
| Fusion silently returns corrupt geometry rather than failing | Validate before, assert `isClosed` on both halves after |
| Fusion runs stale code after an add-in restart | Entry point purges its own modules from `sys.modules` |

## 12. Phase 2 candidates

- Runners for grids wider than two columns (angled cavities off a central spine, as commercial 10-cavity molds use)
- `CustomFeature` timeline integration, so the mold is a re-editable feature
- One-click STL/3MF export from the dialog
- Curved parting surfaces, for shapes a flat plane cannot release
- Clamping bolt holes
- Hard resin / lead jig presets, wire and hook slots
- Separate inter-cavity spacing control, independent of margin

## 13. Verified API surface

Confirmed present in the running Fusion 2704.1.53:

`MeshBodies.add` / `addByTriangleMeshData` · `MeshBody.boundingBox` · `orientedMinimumBoundingBox` · `isClosed` · `isOriented` · `volume` · `calculateCollisionsWithRay` · `MeshRepairFeatures` · `MeshReduceFeatures` · `MeshCombineFeatures` (`CutMeshCombineType`, `EnhancedMeshCombineAlgorithmType`) · `MeshPlaneCutFeatures` · `MeshConvertFeatures` · `TessellateFeatures` · `CombineFeatures` · `SplitBodyFeatures` · `ScaleFeatures` · `ExtrudeFeatures` · `HoleFeatures` · `RevolveFeatures` · `RectangularPatternFeatures` · `Command.executePreview` / `inputChanged` / `validateInputs` · `Component.customGraphicsGroups` · `CustomGraphicsBRepBody` / `Lines` / `Text` · `Design.attributes` / `MeshBody.attributes` · `CommandInputs.addValueInput` / `addIntegerSpinnerCommandInput` / `addSelectionInput` / `addBoolValueInput` / `addTextBoxCommandInput` / `addGroupCommandInput` / `addTabCommandInput`

Added since: `MeshRepairFeatures` (`OneTouchFixMeshRepairType`) - `MeshReduceFeatures` (`FaceCountMeshReduceTargetType`, `AdaptiveReduceType`) - `MoveFeatures.createInput2` with `defineAsFreeMove` (works on mesh bodies) - `MeshCombineOperationTypes.MergeMeshCombineType` - `ExportManager.createSTLExportOptions` (accepts a Component, hence one file per component).

Noted but unused: `MeshConvertFeatures` organic mode requires the paid Product Design Extension. The mesh-output pipeline avoids needing it at all.

## 14. Traps found the hard way

Recorded because each cost a failed run, and none is obvious from the docs.

- **The API works in centimetres.** Convert at one boundary only.
- **`MeshBody.mesh` is a `PolygonMesh`**, not a `TriangleMesh`; use `.triangleNodeIndices`.
- **A mesh boolean against a bad mesh does not fail.** It returns corrupt geometry. Cutting a badly-wound body out of a block of known volume 22,277.378 cm3 "succeeded" and left the block reporting volume 0.0.
- **`orientedMinimumBoundingBox` is approximate** - 17% too large on a rotated test lure, with the wrong axes.
- **A parametric timeline forbids deleting a body a feature depends on.** `deleteMe()` no-ops. Consume tool bodies instead (`isKeepToolBodies=False`).
- **`args.isValidResult = True` in `executePreview` suppresses the execute event entirely.** The OK button appears to do nothing.
- **A command registered from a script never fires its `commandCreated` handler.** Fusion must load the add-in itself.
- **Restarting an add-in does not clear `sys.modules`.** Fusion keeps running the first code it loaded; tracebacks then quote new source beside old errors.
- **Part Design documents allow exactly one component.**
- **Order matters between merging and placing.** Merging the halves replaces the two named bodies with one, so a placement step running afterwards finds nothing and silently no-ops. Laying out must come first; a source-order test now asserts it.
- **Angle inputs come back in radians** whatever unit the dialog shows, so a degrees setting needs converting on both read and write.
- **A cone built with `z1 < z0` comes out inside-out**, and an inverted tool body does not cut - it leaves the channel filled. That one was ours, not Fusion's, and is why `meshgen.cone` normalises direction.
