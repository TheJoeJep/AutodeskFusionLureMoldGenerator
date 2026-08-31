# Troubleshooting

Every entry here is a failure that actually happened while building this
add-in, with what it looked like and what it turned out to be. Several were
baffling until the cause was found, which is the reason for writing them down.

---

## The add-in

### The toolbar button does nothing at all

**Looks like:** the button is there, clicking it produces no dialog, no error,
nothing.

**Cause:** the command was registered by a script rather than loaded by Fusion
as an add-in. Everything looks right -- the command definition is valid, the
control is present, the handler is referenced -- but `commandCreated` never
fires.

**Fix:** load it properly. **Utilities > Add-Ins > Scripts and Add-Ins >
Add-Ins tab > LureMoldGenerator > Run.**

### Pressing Generate closes the dialog and does nothing

**Cause:** `args.isValidResult = True` was being set in the `executePreview`
handler. That tells Fusion the preview *is* the finished result, so the
`execute` event is never fired at all.

**Fix:** already fixed. A custom-graphics preview must never set it. If this
ever comes back, that flag is the first place to look.

### An error names a line of code that does not match the error

**Looks like:** a traceback quoting `plan.top_thickness` while raising
`AttributeError: no attribute 'half_thickness'`.

**Cause:** Fusion prints the source line by reading the file *now*, but is
executing a module loaded into memory earlier. **Stopping and re-running an
add-in does not clear Python's module cache.** Every code change you sync is
ignored, no matter how many times you reload.

**Fix:** the entry point now purges `lure_mold.*` from `sys.modules` on both
start and stop, so a restart genuinely reloads. If you are ever unsure whether
Fusion is running current code, restart Fusion outright.

### Edits to the source do not change anything

The add-in is installed as a **copy**, not a link. Run `sync-addin.ps1`, then
stop and re-run the add-in.

---

## Meshes

### "This mesh has N non-manifold edges"

**Means:** the model has edges where more than two faces meet. There is no
consistent inside and outside at such an edge, so it cannot be cut out of a
block. Fusion reports the body as `isOriented = False` with `volume = 0.0` no
matter what.

**Fix:** MESH tab > **Prepare > Repair**, choose **Watertight**, run it on the
body. Leaving **Repair the mesh first** ticked in the dialog does this for you.

**Reverse Normal will not help.** It flips every normal together and cannot
mend topology. A real 230k-triangle model turned out to have exactly 7 bad
edges out of 345,164 -- enough for Fusion to give up entirely.

### "This mesh is not closed"

Genuine holes. There is no interior to subtract. Mesh > Prepare > Repair with
the Watertight option.

### The cavity comes out wrong but nothing errored

Inconsistent winding. Fusion does **not** fail a boolean against a badly wound
mesh -- it silently produces a corrupt result. Measured: cutting such a body
out of a block of known volume 22,277.378 cm3 "succeeded" and left the block
reporting volume 0.0 and not watertight.

The add-in repairs winding automatically before cutting, so this should not
reach you. If a cavity still looks wrong, check the lure body's volume in
Fusion -- a reported 0.0 is the tell.

---

## Building

### "Failed to create component: Part Design documents can only contain one component"

**Means:** you are working in a Part Design document, which Fusion restricts to
a single component, so the mold has nowhere to go.

**Fix:** the add-in falls back to building in the root, so it will still work.
But use a normal **Design** document (File > New Design) if you want the
halves grouped for single-file export.

### Builds get slower every time in a Part Design document

Each regenerate leaves the previous mold behind -- see the Part Design entry
above -- and every one of those is a 50,000+ triangle body the document has to
carry. Delete the bodies marked "(old - delete me)" in the browser. They are
hidden and nothing depends on them.

The add-in no longer *asks* to delete them itself: in a parametric design a
failed `deleteMe()` on a body that size costs about fifteen seconds, and
retrying on each stale mold was 89 seconds of a 143 second build. It renames
them and switches them off instead.

Working in a normal Design document avoids all of this, because there the whole
mold component is deleted and rebuilt each time.

### The build takes forever or times out

Boolean cost scales with **triangles x cavities**. Measured on a 25,000
triangle turtle: 13.5 s for one cavity, and six cavities did not finish inside
a minute.

**Fix:** lower **Triangle limit** under Mesh preparation. 25,000 suits 1-2
cavities; ~10,000 is plenty for a six-cavity runner mold and loses nothing a
printer could resolve. The dialog warns when triangles x cavities passes
60,000.

### A gate or channel is filled in instead of hollow

**Cause, historically:** a channel running in the negative direction built an
**inside-out** cone, and an inverted tool body does not cut. It showed up as
one column of a runner mold gating correctly and the other staying solid.

**Fix:** already fixed -- `meshgen.cone` normalises either direction. If a
channel is ever solid again, check the winding of the tool body first: its
signed volume should be positive.

### The runner opens at both ends

**Cause, historically:** the runner ran from `-block_y/2` to `+block_y/2`, and
`-block_y/2` *is* the block face.

**Fix:** already fixed -- the closed end stops a wall's thickness short.

### "Lay halves out flat" is ticked but the mold comes out closed

**Cause, historically:** the merge step ran *before* the flat layout. Merging
replaces `Mold Bottom` and `Mold Top` with a single body, so the placement step
that followed found nothing to move and did nothing -- silently, because both
lookups simply returned nothing.

**Fix:** already fixed -- laying out now happens first, then the optional
merge. A test asserts that order in the source, since it is not a kind of
mistake a type checker can catch.

### The slope around the cavity is jagged, ridged or corrugated

Fixed, but worth knowing what it was, because the same trap is easy to fall
back into. The distance field was measured to the nearest marked *grid node*
rather than to the feature itself. That is quantised by up to a whole cell, and
the error ripples as the outline weaves between nodes -- on a real mold, 1.1mm
of ripple across a 3.4mm ramp. `relief.Nearest` records each feature's exact
closest point instead.

If you see it again, check that `apply_relief` is still passing its `nearest`
to every `mark_*` call and to `distance_field`. Without it they silently fall
back to the node-distance version, which is correct for peg placement and far
too coarse for a ramp.

### One vent stops partway to the block face

Also fixed. The vent's flat land is marked as a thin rectangle, and a rectangle
narrower than one grid cell can fall between two rows of nodes and mark
nothing. The relief then kept no land along that vent, the recess dropped the
face by the full depth, and the channel -- which sits on the parting plane --
was left cutting air. With a 1mm vent on a 1.12mm grid it was roughly a coin
flip per vent.

Exact distances made it moot, and `mark_rect`/`mark_disc` now also guarantee at
least one node when used without them.

### The relief warns that the land plus ramp does not fit

The ramp eases in and out rather than running straight, so it needs half as
much room again as a straight wall at the same angle:
`1.5 x depth / tan(angle)`. A 4mm recess at 50 degrees runs 5.0mm, and with a
2mm land that wants a 7mm+ wall.

Give it more margin, reduce the depth, or steepen the angle. The mold still
builds either way -- it just reaches nearly full depth right at the wall.

### The parting face relief does not seem to do anything

The flat land plus its ramp has to fit inside the wall thickness. With the
default 10 mm margins and a 4 mm land, only a narrow band is left to recess;
with a land close to the margin, none is. You get a warning when it does not
fit -- reduce the land, or increase the margins.

Ramp length is `1.5 x depth / tan(angle)`: 2.52 mm at the 50 degree default
with a 2 mm recess. See the entry above for why it is not simply
`depth / tan(angle)`.

### Fewer pegs than I asked for, or none at all

Every candidate position is rejected if it collides with a cavity, a sprue, a
vent or the runner. With large funnels or thin margins there may genuinely be
nowhere left. Increase the margins, reduce the peg diameter, or accept fewer.

**If the corners look obviously empty and it still says no room**, that was a
bug, now fixed: collision used to be tested against the lure's bounding box
rather than its outline, so a shape with spread limbs had its wide-open corners
called occupied. Make sure you are running current code.

---

## Results

### The fins / head / tail of my model are buried in the mold

The parting plane is in the wrong place for that shape. It is chosen
automatically, but check the readout: it reports what percentage of the lure
releases cleanly. Below about 90% is worth looking at.

Untick **Find the best split automatically** and set **Split offset from
centre** by hand. The automatic value is mirrored into that field, so you start
from wherever auto left off.

Some shapes are undercut whatever the plane -- soft plastic will usually still
release, but a deep undercut can lock a rigid mold shut.

### The lure sits crooked in the mold

Orientation is computed from the mesh's principal axes, which needs a sound
mesh. If the model is badly damaged, repair it first.

### I only see the old mold, not the new settings

The previous mold is hidden while the dialog is open so the ghost preview is
unambiguous. If you cancel, it comes back. If you generate, it is replaced.

### Both halves are different thicknesses

Expected. The parting plane is rarely central, so the lid and base differ. The
readout shows both, and the total block height is unaffected.

---

## Verifying by hand

Useful checks when something looks wrong, run through the Fusion MCP console:

```python
body.isClosed        # False -> holes
body.isOriented      # False -> winding or non-manifold edges
body.volume          # 0.0 with isClosed True -> winding problem
body.mesh.triangleCount
```

Both finished halves should report `isClosed = True`. If either does not, the
mold will not slice correctly and the lure mesh is the place to look.
