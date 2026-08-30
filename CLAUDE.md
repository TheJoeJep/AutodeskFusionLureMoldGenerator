# Working on this repo

Notes for Claude (or any coding agent) picking this project up. Read this
before touching the code or trying to install it for someone.

---

## What this is

An Autodesk Fusion add-in that turns a lure mesh into a printable two-part
soft-plastic injection mold. Written and verified against Fusion **2704.1.53**.

The user-facing guide is [README.md](README.md). Design reasoning lives in
[docs/superpowers/specs/](docs/superpowers/specs/). Failure modes and their
causes are in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — check there
before debugging anything odd, most surprises are already documented.

---

## Installing it for a user

If someone asks you to set this up, do this:

1. Copy the `LureMoldGenerator/` folder into Fusion's add-ins directory:
   - **Windows:** `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\`
   - **macOS:** `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/`
2. Tell them to enable it: **Utilities → Add-Ins → Scripts and Add-Ins →
   Add-Ins tab → LureMoldGenerator → Run**, ticking **Run on Startup**.

`sync-addin.ps1` does step 1 on Windows.

**Copy the folder; do not symlink or junction it.** Fusion's add-in scanner is
not dependable about following reparse points. This was tried and abandoned.

**You cannot install it by running a script through the Fusion MCP.** You can
register the command that way and it will *look* installed — the definition is
valid, the toolbar button appears, the handler is referenced — but
`commandCreated` never fires and the button silently does nothing. Fusion has
to load the add-in itself. Hours were lost to this; do not repeat it.

---

## The three traps that will waste your time

**1. Restarting the add-in does not reload the code.** Fusion does not clear
`sys.modules`, so it keeps executing whatever it imported first no matter how
many times the user hits Stop/Run. The symptom is baffling: a traceback quotes
the *new* source line beside an error that only exists in the *old* code.
`LureMoldGenerator.py` purges its own package on both `run()` and `stop()` to
fix this — do not remove that. If in doubt, restart Fusion.

**2. A mesh boolean against a bad mesh does not fail.** It returns corrupt
geometry. Measured: cutting a badly-wound body out of a block of known volume
22,277.378 cm3 "succeeded" and left the block reporting volume 0.0. Always
validate before, and assert `isClosed` on both halves after.

**3. A parametric timeline forbids deleting a body a feature depends on.**
`deleteMe()` no-ops. Consume tool bodies instead (`isKeepToolBodies=False`),
and build into a component you can delete wholesale to regenerate. In a **Part
Design** document Fusion allows only one component, so the add-in falls back to
the root — and then stale molds accumulate because they cannot be deleted.
Suggest a normal Design document.

More of these in the spec, section 14.

---

## Architecture: the pure core is the point

```
lure_mold/
  layout.py         PURE  block, cavities, pegs, ports, runner, parting offset
  meshgen.py        PURE  watertight box / cylinder / cone / frustum primitives
  orient.py         PURE  principal axes by area-weighted PCA
  parting.py        PURE  where to split, by ray casting
  relief.py         PURE  shape-following parting-face relief
  mesh_repair.py    PURE  winding repair, non-manifold detection
  lure_analysis.py        validate, orient, detect nose, undercuts   [imports adsk]
  mesh_prep.py            Fusion's repair and reduce, on a copy      [imports adsk]
  mold_builder.py         builds the mold                            [imports adsk]
  preview.py              live ghost overlay                         [imports adsk]
  ui_command.py           the dialog                                 [imports adsk]
  store.py                settings on the lure body                  [imports adsk]
```

**The six PURE modules must never import `adsk`.** All the fiddly geometry
lives there so it can be tested with plain Python and no CAD. A test enforces
this. Put new geometry logic there, not in the builder.

The Fusion-side modules cannot be imported by tests at all, which is a real
blind spot: a rename once shipped a stale `plan.half_thickness` that crashed
the dialog on open while every test passed.
`tests/test_module_contracts.py` closes it by parsing those files as source and
checking attributes against the dataclasses, plus asserting a few
order-dependent build steps. Extend it when you add a step whose order matters.

---

## Working on it

```bash
python -m unittest discover -s tests -v          # 191 tests, stdlib only
powershell -ExecutionPolicy Bypass -File .\sync-addin.ps1
```

Then stop and re-run the add-in in Fusion.

**TDD is the convention here and it has paid off repeatedly.** Write the
failing test, watch it fail for the right reason, then implement. Several real
bugs — an inside-out cone, a peg driven through the fill port, a runner open at
both ends — were caught this way, and a couple that shipped were exactly the
cases with no test.

When a guard test matters, prove it is not vacuous by reintroducing the bug it
was written to catch and watching it fail.

### Testing geometry

Unit tests cover the pure core. For the Fusion side, drive it through the
Fusion MCP: import the modules with `sys.modules` purged first, build, then
assert on real numbers — body count, volumes, `isClosed`, bounding boxes. Mesh
booleans are accurate enough that predicted volumes match to four decimal
places, so assert on volume rather than eyeballing a screenshot.

```python
for n in [m for m in sys.modules if m.startswith("lure_mold")]:
    del sys.modules[n]
```

Test with real, messy STLs. A turtle, a Mjolnir and a figure each exposed a
different class of bug that no clean test lure would have found.

### A recurring mistake worth naming

**Bounding boxes were the wrong tool twice.** Both the parting-face relief and
the peg-collision test started out using the lure's bounding box. That is fine
for a worm, which nearly fills its box, and wrong for anything with limbs — a
real figure filled only 52% of its box, so the relief wasted half the sealing
land and peg placement rejected corners that were wide open, producing molds
with no pegs at all.

`relief.silhouette_field()` gives distance to the real outline and is cached
with the analysis. Use it for anything that asks "is this spot clear?".

---

## Conventions

- Fusion's API is in **centimetres**; the layout maths is in **millimetres**.
  Convert at one boundary only (`MM = 0.1` in the builder).
- Angle inputs come back from Fusion in **radians** whatever unit is displayed.
- Warn rather than fail where a mold is still usable; block only when the
  result would be silently wrong.
- Every warning should say what to change, not just what is wrong.
