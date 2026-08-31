# What is left to build

A sweep of the whole add-in, written after the mesh audit landed. Ordered by
what would change most for someone actually pouring baits, not by what is
interesting to build.

Nothing here is committed to. It is a map of the gaps.

---

## Where it stands

Solid, and verified on real models:

- Orientation, parting-plane choice, cavity cutting, relief, pegs, sprues,
  runners, vents, laying out flat, merging.
- Vent placement per trapped pocket, with manual override and a vertical
  option.
- Mesh audit both ways: stray shells out of the lure, loose islands out of the
  mold, sealed pockets reported, cut volume sanity-checked.
- 304 tests, all outside Fusion.

The mold that comes out is **geometrically** right. Most of what follows is
about making it a thing you can actually use at the bench.

---

## 1. Nothing holds the mold shut -- DONE

Four bolt holes by default, sized for M4, running down the two long edges
rather than at the corners: a long mold bows open in the middle, and the
corners are the only four spots clear of everything, so bolts and pegs would
have been fighting over them.

The head is counterbored into the top half and a hex pocket in the bottom traps
the nut, so one spanner does the job and the mold closes flat. The readout
gives the bolt length to buy, measured under the head.

Still open, and worth doing on top:

- **A perimeter lip** -- a step round the parting face, male on one half and
  female on the other. It locates better than two pegs and resists the peel
  the flashing comes from. A straightforward offset of the block outline.
- **Clip relief** -- a shallow notch on two edges for a bulldog or spring
  clamp, for people who would rather not buy hardware.


## 2. Will it fit the printer? -- DONE

Bed size under Printer, a warning when it does not fit, and **Fit the grid to
the bed** to work Columns and Rows out instead of typing them.

The readout now gives the **printed** footprint rather than the block, which was
the real trap: laid out flat both halves sit side by side, so it is
`2 x block_y + gap`. A 114 x 53 block prints as 114 x 117 -- nearly square from
a block that is over two to one.

Still open: the bed size is stored per lure body along with everything else, so
it has to be re-entered per model. It is a property of the machine, not the
bait. Worth a document-level or user-level store of its own.

## 3. Tell me what the bait weighs -- DONE

Grams per bait, grams for the shot, and grams of feed, in the readout. Density
is a setting, defaulting to 1.02 g/cm3 -- a gallon of plastisol to about 8.5 lb
-- because salt-loaded plastic runs a good deal heavier.


## 4. A Check button

A build costs 50 seconds and up. Every diagnostic in the pipeline — mesh
audit, undercuts, release percentage, peg placement, thin walls — could run in
about a second without cutting anything, and say whether it is worth pressing
Generate.

**Effort:** small; the analysis path is already separate from the build path.

## 5. The robustness holes still open

The mesh audit closed the big one. These are what is left:

- **Self-intersecting meshes.** The single most common cause of a corrupt
  boolean, and not detected. Fusion's repair usually fixes it, but "usually" is
  doing work in that sentence. A uniform spatial hash plus a triangle/triangle
  test would catch it before the build rather than after.
  *Effort: moderate. It is the only item here that is real geometry code.*

- ~~**Scale sanity.**~~ DONE. Under 5 mm or over 500 mm finished gets a
  warning naming the likely cause, since STL and OBJ carry no units at all.

- **Degenerate triangles.** Zero-area faces and duplicate vertices make
  booleans noisy. Cheap to count, cheap to report.

- **Wall thickness, measured rather than assumed.** Margins govern the wall
  between the cavity and the outside, and that is warned on. Not checked: the
  floor under the deepest point of the cavity, the material between a cavity
  and a channel, and the wall between adjacent cavities in a grid. The
  silhouette distance field can answer all three.

- **Undercuts are reported as a number, not a place.** "93% releases cleanly"
  is honest but not actionable. The rays that fail are already computed —
  drawing them in the preview overlay would show you *which* fin is the
  problem.

## 6. Injection, for people who pour a lot

- **More than one gate** on a long bait, so it fills from both ends.
- **Balanced runners** — equal flow length to every cavity, so they fill
  together instead of the near ones packing out first.
- **Overflow wells** past the last cavity to catch the cold slug.
- **Gate shape** — a tab or fan gate breaks off more cleanly than a round one.

**Effort:** moderate, and only worth it for multi-cavity users.

## 7. Model handling

- **Different lures in one mold.** Today it is N x M of the same body. A mixed
  plate is a bigger change: `compute_layout` assumes one `LureDims`.
- **A size family from one model** — 3", 4" and 5" of the same bait in one
  mold, which is just per-cavity scaling.
- **Roll control.** PCA picks the split plane, and the ray-cast scoring picks
  the height, but there is no way to rotate the lure about its own long axis.
  For a bait whose best split is not the principal plane, that is the missing
  handle.

## 8. Output and housekeeping

- **Export from the dialog** — STL or 3MF, both halves, correct orientation,
  one click. The grouping work for this is already done.
- **Clean up old molds.** In a Part Design document the timeline will not
  release previous molds, and they accumulate: the test document reached ten of
  them, about 600,000 triangles, and that alone took a build from 50 to 76
  seconds. Deleting the timeline features rather than the bodies may work —
  worth an experiment, because right now the only advice is "use a different
  document type".
- **Named presets** — worm, craw, swimbait — rather than settings remembered
  per body.

---

## What I would do next

Weight readout, bed fit and scale sanity are **done**.

Clamping bolts are **done** too.

**The Check button is next.** There is enough worth checking now -- mesh audit,
undercuts, release percentage, peg and bolt placement, bed fit -- that waiting
out a build to find any of it is the wrong shape for the tool.

**Then a perimeter lip.** Bolts hold the halves together; a step around the
parting face stops them peeling apart between the bolts, which is where the
last of the flashing comes from.
