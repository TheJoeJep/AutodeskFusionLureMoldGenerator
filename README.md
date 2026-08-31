# Lure Mold Generator

An Autodesk Fusion add-in that turns a lure model into a printable two-part
soft-plastic injection mold. Import a lure, press Generate, get two watertight
halves with cavities, alignment pegs, injection sprues and vents already placed.

It works out the rest for itself: which way the lure should lie, where the mold
should split so nothing gets trapped, how big the block needs to be, and where
the pegs and ports can go without fouling anything.

Built and verified against Fusion **2704.1.53**.

---

## Installing

### Ask Claude to do it

If you use Claude Code, clone the repo, point it at the folder and say:

> Install this Fusion add-in for me.

[CLAUDE.md](CLAUDE.md) tells it exactly what to do and what to avoid.

### Or do it by hand

**1. Copy the `LureMoldGenerator` folder** into Fusion's add-ins directory:

| | |
|---|---|
| Windows | `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\` |
| macOS | `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/` |

On Windows you can run `sync-addin.ps1` instead, which does the copy for you:

```powershell
powershell -ExecutionPolicy Bypass -File .\sync-addin.ps1
```

Copy it -- do not symlink or junction it. Fusion's add-in scanner is not
dependable about following reparse points.

**2. Turn it on in Fusion.** **Utilities > Add-Ins > Scripts and Add-Ins**, open
the **Add-Ins** tab, select **LureMoldGenerator**, click **Run**, and tick
**Run on Startup**.

The add-in must be loaded by Fusion this way. Registering the command from a
script produces a button that looks installed but whose handler never fires --
it silently does nothing.

### Requirements

Autodesk Fusion (developed against **2704.1.53**). Nothing else -- no pip
installs, no paid extensions. The mesh pipeline deliberately avoids
mesh-to-solid conversion, which would need the Product Design Extension.

## Using it

1. Import a lure model (STL/OBJ/3MF) into a Fusion design.
2. In the **UTILITIES** tab, find the **LURE MOLD** group and click
   **Lure Mold Generator**. (It is also in Utilities > Add-Ins.)
3. If the document holds exactly one mesh body it is selected for you. Adjust
   whatever you want -- a ghost overlay redraws live as you type, showing both
   halves where they will actually end up: laid out flat and side by side, with
   the cavity footprints, sealing lands, channels and pegs marked.
4. Press **Generate**.

The result lands in a component called **Lure Mold**, containing `Mold Bottom`
and `Mold Top`, laid out side by side and resting flat with their cavities and
pegs facing upwards -- which is also the orientation you want for printing.

To change something, reopen the command: every field comes back with what you
used last time for that lure, and generating again replaces the previous mold.

### Exporting for printing

Right-click the **Lure Mold** component in the browser > **Save As Mesh**. A
component exports as a single file containing both halves, which is why the
mold is grouped into one.

**Merge halves into one body** is on by default, so the mold arrives as a
single body ready to export. The halves are laid out flat *first* and then
fused, so you still get them side by side, not closed. Untick it if you would
rather keep them separate to hide one and inspect the other.

---

## Settings

| Group | Setting | Default |
|---|---|---|
| Lure | Finished length | detected size |
| Lure | Flip nose/tail | off |
| Grid | Columns x Rows | 1 x 1 |
| Wall thickness | Along length (X) / Across width (Y) / Above and below (Z) | 10 mm |
| Alignment pegs | Number of pegs | 2 |
| Alignment pegs | Diameter / Height | 5 mm |
| Alignment pegs | Fit clearance | 0.2 mm |
| Alignment pegs | Lead-in chamfer | 0.6 mm |
| Printer | Check it fits the bed | on |
| Printer | Bed width / depth | 220 / 220 mm |
| Printer | Fit the grid to the bed | off |
| Printer | Plastisol density | 1.02 g/cm3 |
| Injection | Injection port | Edge |
| Injection | Sprue diameter | 4 mm |
| Injection | Funnel diameter | 8 mm |
| Injection | Runner diameter | 6 mm |
| Injection | Add vents / Vent diameter | on / 1.0 mm |
| Vent placement | Positions | Automatic |
| Vent placement | Direction | Along the parting line |
| Mesh preparation | Repair the mesh first | on |
| Mesh preparation | Reduce the triangle count | on |
| Mesh preparation | Triangle limit | 25,000 |
| Mesh preparation | Remove loose pieces from the mold | on |
| Parting plane | Find the best split automatically | on |
| Parting plane | Split offset from centre | auto-filled |
| Parting face relief | Recess the face away from features | on |
| Parting face relief | Flat band around features | 4 mm |
| Parting face relief | Recess depth | 2 mm |
| Parting face relief | Slope angle | 50 deg |
| Output | Lay halves out flat | on |
| Output | Merge halves into one body | on |

---

## How it decides things

### Where the mold splits

Not assumed to be the middle. A grid of rays is fired down through the lure and
the height that most of the model *straddles* wins -- a span of solid sitting
wholly on one side of the plane is a feature that gets sealed in and can never
release.

On a real turtle STL, a centred split leaves **62%** of the model able to
release; the chosen split, at the fin line, manages **93%**. The readout tells
you the percentage. Untick *Find the best split automatically* to set it by
hand.

Because the split need not be central, **the two halves are usually different
thicknesses.** The turtle gives an 11.9 mm lid over a 33.7 mm base.

### Injection port

Four choices, and the default matches how commercial molds are actually built:

- **Edge** *(default)* -- a tapered channel on the parting line, cut into both
  halves so they close into a full round port. Only the outermost column has a
  clear run to a face, so a single column injects entirely from the edge; extra
  columns fall back to top entry with a warning.
- **Runner** -- one sprue feeds a channel down the middle with every cavity
  gated off it, so the whole shot pulls out as a single tree. Needs exactly two
  columns, facing each other across the channel. The far end of the runner is
  capped a wall's thickness short of the face.
- **Top** -- a vertical funnel down through the lid. Works at any grid
  position, so it is the fallback for cavities an edge channel cannot reach.
- **None** -- no injection hole at all.

### Vents

**Every pocket gets its own vent, not just the tail.** A figure with four
raised limbs traps air at each one; a single vent at the far end would leave
three short shots. Filling is simulated from the gate outward -- distance
measured *through* the cavity, not straight-line -- and the last places to fill
are the local maxima of that distance. Those are exactly where air ends up, so
that is where the vents go.

A local maximum on its own is not enough -- where a limb passes close to the
gate the field forms a ridge, and every point along it looks like a maximum. A
real pocket is distinguished by how far you have to descend from it before
reaching higher ground: a limb tip has tens of millimetres, a ridge ripple has
one. Anything shallower than about 2.5 mm is ignored, and vents closer than 7%
of the lure's size merge.

Each is routed along the parting line to the nearest face it can actually
reach, so it opens up when the mold does and can be cleaned out. Only a cavity
boxed in on every side falls back to a vertical riser -- with a warning,
because a riser is a blind hole full of set plastic.

The injection mode governs the sprue, not the vent.

#### Placing them yourself

Detection is good, not omniscient. **Positions** offers three modes:

- **Automatic** -- the pockets it finds, and nothing else.
- **Automatic, plus my own points** -- keeps those and adds yours.
- **My own points only** -- yours alone.

Switch to a manual mode and the table fills with what detection found, so you
start by editing real numbers rather than from a blank sheet. **Reset to
detected** puts them back. Coordinates are millimetres from the middle of the
cavity, in the lure's own frame: X along its length, Y across it. They are
mirrored automatically for a cavity turned to face a central runner, and they
are *not* rescaled by the finished length -- they mean what the preview shows.

#### Venting straight up

**Direction** switches every vent from a channel on the parting line to a hole
straight up through the top half. Along the parting line is the better default
-- the channel splits open with the mold, so it wipes clean -- but it can only
reach a trap that lies on the split. **A curly-tail worm is the case that
needs the other one**: the curl rises well above the parting plane, and no
channel lying on that plane comes anywhere near the air caught in the tip. The
riser leaves a hole to clear out after printing, which is the price of reaching
the trap at all.

### Will it print, and what will it weigh?

The readout answers both before you generate anything.

**Printed size is not the block size.** Laid out flat, what goes on the plate
is both halves side by side: `2 x block_y + gap`, not `block_y`. A 114 x 53 mm
block prints as 114 x 117 mm -- nearly square, from a block that is more than
two to one. Reporting the block is how you end up generating a mold that cannot
physically print, so the readout gives the printed footprint and warns when it
will not fit the bed.

**Fit the grid to the bed** turns that round: instead of typing Columns and
Rows and finding out, enter the bed size and it fits as many cavities as will
print in one go. On a 256 x 256 mm bed a 100 mm bait comes out 2 x 2.

**Shot weight** is the number anglers actually talk in. The cavity volume is
already known exactly, so grams per bait, grams for the whole shot, and grams
of feed -- the sprue, gates, runner and vents you trim off and put back in the
pot -- all come for free. Plain plastisol runs about 1.02 g/cm3, which is a
gallon to 8.5 lb; salt-loaded plastic is heavier, so it is a setting.

### What gets checked

A downloaded model is often more than the shape you can see, and every extra
piece gets subtracted from the block along with the lure. Before anything is
cut, the mesh is broken into its connected pieces and three kinds are thrown
out:

- a lure whose **finished length** is under 5 mm or over 500 mm, which almost
  always means the model was exported in the wrong unit -- metres arrive a
  thousand times too small, inches about 25 times too big.

And of the mesh itself:

- **specks** under 1% of the main shape -- debris left by somebody else's
  boolean. Each would have cut a pocket of its own somewhere in a wall.
- **pieces buried inside the shape** -- separately modelled eyes, an interior
  armature. Each would cut a pocket the plastic cannot reach and the bait
  cannot come out of.
- **sealed pockets**, where the mesh encloses space with no way in or out.

A lure genuinely can be two pieces -- a body and a separate tail -- so nothing
else goes, and you are told exactly what was ignored. Your own body is never
modified; this only affects what gets cut.

Afterwards the finished halves are checked too. A mold half should be one solid
lump; a chunk left joined to nothing prints as a lump rattling around in the
cavity. Those are **cut away automatically** using their own surface as the
tool, and reported with their size. Sealed pockets in a half are reported but
not touched -- they usually mean part of the lure sits entirely on one side of
the split, and the fix is a different parting offset.

Finally, the volume that actually disappeared is compared against what the
cavities should account for. A mesh boolean against a bad mesh does not fail --
it returns corrupt geometry, and has been measured leaving a block of known
volume reporting zero -- so this catches the whole class for almost nothing.

### Parting face relief

The two halves only need to seal against each other *near* the cavity and the
ports. Everything else can be recessed, which means far less area has to print
dead flat -- and a small flat land is much easier to get right than a whole
face.

A flat band is kept around every feature: each cavity, the sprue and its
channel, the vent, the runner, and each peg. Beyond that band the face eases
down to the recess depth, then runs flat.

**Eases, not ramps.** A straight ramp turns a corner where it leaves the land
and another where it meets the floor, and a corner sampled on a grid can only
zig-zag along it from one grid point to the next. The slope is a smoothstep
instead, so it starts and finishes flat and there is no corner to sample. It
prints better for the same reason it looks better: no abrupt change of angle
for the slicer to step over.

**The band follows the real outline, not a box around it.** That matters more
than it sounds: a figure with spread limbs filled only 52% of its own bounding
box, so a box-shaped land wasted half the flat area on the gaps between its
arms and legs and started the slope nowhere near the shape. Features are
rasterised onto a grid, a distance field is swept out from them, and the face
height follows from that distance.

**Distance is measured to the features themselves, not to the grid.** Measuring
to the nearest marked grid point is quantised by up to a whole cell, and the
error ripples as the outline weaves between the points -- which came out as
corrugation running down every slope, a third of the ramp's depth on a real
mold. Each feature instead records where its closest point actually is, and
those points are carried outwards across the grid. The same change fixed a
second fault: a vent channel narrower than one cell could fall between two rows
of grid points, mark nothing, and so keep no land around itself, and the recess
then swallowed the channel partway to the block face.

Ramp length is `1.5 x depth / tan(angle)`, so at the 50 degree default a 2 mm
recess eases over 2.52 mm. The angle is the *steepest* part of the ease, which
is why it needs half as much room again as a straight wall at the same angle. **The land plus that ramp has to fit inside the wall
thickness**, or there is nothing left to recess -- with the default 10 mm
margins a 4 mm land leaves only a narrow band. You get a warning when it does
not fit.

### Alignment pegs

Pin and hole both get a lead-in chamfer so they find each other instead of
catching on a printed edge, and the hole is **Fit clearance** wider than the
pin -- 0.2 mm on the diameter by default, so 0.1 mm per side. That is tight for
FDM; 0.3-0.4 mm is more forgiving once you have test-printed.

Two pegs go to diagonally opposite corners; four go to all four corners. Any
position colliding with a cavity, an injection sprue, a vent or the runner is
rejected automatically, and you are told if fewer fit than you asked for.

Collision is tested against the lure's **real outline**, using the same
distance field as the relief. That matters: a figure with spread limbs leaves
its bounding-box corners wide open, but a box test calls them occupied and
throws every candidate away -- which produced molds with no pegs at all.

### Mesh preparation

Runs on a **copy**, so your imported body is never modified, and the copy is
swept away on the next regenerate.

- **Repair** runs Fusion's one-touch repair, fixing topology that cannot be
  mended by re-winding triangles.
- **Reduce** brings the triangle count down to the limit using adaptive
  reduction, which preserves curvature where it matters.

**On the triangle limit:** boolean cost scales with triangles x cavities. A
100 mm lure at 25,000 triangles has roughly 0.9 mm facets -- already finer than
a 0.4 mm nozzle resolves. Measured: 25k triangles took 13.5 s for one cavity;
six cavities at 25k did not finish inside a minute. So 25,000 suits 1-2
cavities; drop to ~10,000 for a six-cavity runner mold. You get a warning when
triangles x cavities passes 60,000.

### Other things worth knowing

- **Nose detection is a guess.** The bulkier end is taken as the nose. Use
  *Flip nose/tail* when it guesses wrong.
- **Vents below 0.8 mm will not print** on FDM -- the hole closes up.
- **Undercuts are reported, not blocked.** Soft plastic usually releases anyway.

---

## Layout

```
LureMoldGenerator/
  LureMoldGenerator.py         add-in entry point (purges its own modules)
  LureMoldGenerator.manifest
  lure_mold/
    layout.py                  block, cavity, peg, sprue, vent, runner positions
    mesh_audit.py              separate pieces, sealed pockets, loose islands
    meshgen.py                 watertight box / cylinder / cone primitives
    orient.py                  principal axes via area-weighted PCA
    parting.py                 where to split, by ray casting
    relief.py                  shape-following parting-face relief
    mesh_repair.py             winding repair and non-manifold detection
    lure_analysis.py           validate, orient, detect nose, find undercuts
    mesh_prep.py               Fusion's repair and reduce, on a copy
    mold_builder.py            builds the mold in Fusion
    preview.py                 the live ghost overlay
    store.py                   settings remembered on the lure body
    ui_command.py              the dialog
  resources/LureMoldGenerator/ toolbar icons
tests/                         run outside Fusion, no CAD needed
docs/                          design spec and troubleshooting
sync-addin.ps1                 copy source into Fusion's add-ins folder
```

`layout.py`, `meshgen.py`, `orient.py`, `mesh_repair.py`, `parting.py` and
`relief.py`
import nothing from Fusion. That is deliberate: all the fiddly geometry lives
there and is covered by tests that run in plain Python, and a test enforces
that they never grow a Fusion import.

The four modules that *do* import Fusion cannot be exercised by those tests, so
`test_module_contracts.py` reads them as source instead and checks every
attribute they take off a plan / cavity / settings object against the real
dataclass fields. That catches renames, which is how a stale
`plan.half_thickness` once shipped and crashed the dialog on open.

## Tests

```bash
python -m unittest discover -s tests -v
```

285 tests, no dependencies beyond the standard library.

| File | Covers |
|---|---|
| `test_layout.py` | block sizing, cavities, pegs, ports, runner, vents, parting offset, flat layout |
| `test_meshgen.py` | primitives, watertightness, winding, axis conversion, reversed channels |
| `test_orient.py` | principal axes, extents, projection |
| `test_parting.py` | ray casting, span detection, split scoring |
| `test_mesh_repair.py` | winding repair, volume, non-manifold detection |
| `test_relief.py` | rasterising, distance field, height ramp, cutter mesh |
| `test_module_contracts.py` | Fusion-side modules match the dataclasses; pure modules stay pure; build steps stay in order |

## Design notes

**Everything is mesh.** The block halves, pegs, sprues, runner and vents are
generated as exact triangle meshes and combined with Fusion's mesh booleans. No
sketches, no extrudes. A box tessellates losslessly, so the mold body is exact;
only the cavity carries the resolution of your own lure mesh. It also avoids
needing the paid Product Design Extension, which mesh-to-solid conversion of
organic shapes requires.

**The lure is never split.** Subtracting the whole lure from each half-block
gives each half exactly its own cavity.

**Orientation does not use Fusion's `orientedMinimumBoundingBox`.** That
built-in is only approximate -- on a test lure of known size 100 x 30 x 12 mm it
reported 100 x 28.8 x 14.6, a box 17% too large, which would have seated the
lure crooked. `orient.py` computes the axes from an exact area-weighted surface
covariance and recovers 100.00 x 30.00 x 12.00.

**Tool bodies are consumed, not deleted.** A parametric timeline will not let
you delete a body a feature depends on, so each boolean consumes its own
scratch geometry. Regenerating drops the whole previous component and rebuilds.

---

## Editing the code

The add-in is installed as a **real copy**, not a junction -- Fusion's add-in
scanner is not dependable about following reparse points. So edits here do not
reach Fusion until you sync:

```powershell
powershell -ExecutionPolicy Bypass -File .\sync-addin.ps1
```

Then **stop and re-run** the add-in in Scripts and Add-Ins. The entry point
purges its own modules from `sys.modules` on start and stop, so a restart now
genuinely reloads the code -- without that, Fusion keeps executing whatever it
loaded first, however many times you restart.

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) when something behaves
oddly, and
[docs/superpowers/specs/2026-08-29-lure-mold-generator-design.md](docs/superpowers/specs/2026-08-29-lure-mold-generator-design.md)
for why the design is the way it is.

## Installing elsewhere

Copy `LureMoldGenerator/` into:

```
%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\
```

then enable it under **Utilities > Add-Ins**.
